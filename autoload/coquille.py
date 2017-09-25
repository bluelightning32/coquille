from __future__ import print_function
from __future__ import unicode_literals
from __future__ import division

import vim

import itertools
import re
import xml.etree.ElementTree as ET
import coqtop as CT
import project_file

from collections import deque

import vimbufsync
vimbufsync.check_version([0,1,0], who="coquille")

# Define unicode in python 3
unicode = getattr(__builtins__, 'unicode', str)

# Cache whether vim has a bool type
vim_has_bool = vim.eval("exists('v:false')")

def vim_repr(value):
    "Converts a python value into a vim value"
    if isinstance(value, bool):
        if value:
            if vim_has_bool:
                return "v:true"
            else:
                return "1"
        else:
            if vim_has_bool:
                return "v:false"
            else:
                return "0"
    if isinstance(value, int) or isinstance(value, long):
        return str(value)
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, unicode):
        return value.replace("'", "''")
    return "unknown"

# Convert 0-based (line, col) pairs into 1-based lists
def make_vim_range(start, stop):
    return [[start[0] + 1, start[1] + 1], [stop[0] + 1, stop[1] + 1]]

# All the python side state associated with the vim source buffer
class BufferState(object):
    # Dict mapping source buffer id to BufferState
    source_mapping = {}

    @classmethod
    def lookup_bufid(cls, bufid):
        # For convenience, the vim script passes vim.eval("l:bufid") to this
        # function, and vim.eval() returns a string.
        bufid = int(bufid)
        if bufid in cls.source_mapping:
            state = cls.source_mapping[bufid]
        else:
            state = BufferState(vim.buffers[bufid])
            cls.source_mapping[bufid] = state
        if state.sync_vars():
            return state
        else:
            del cls.source_mapping[bufid]
            return None

    def __init__(self, source_buffer):
        self.source_buffer = source_buffer
        self.info_buffer = None
        self.goal_buffer = None
        #: See vimbufsync ( https://github.com/def-lkb/vimbufsync )
        self.saved_sync = None

        #: Keeps track of what have been checked by Coq, and what is waiting to be
        #: checked.
        self.send_queue = deque([])

        self.error_at = None

        self.coq_top = CT.CoqTop()

    def sync_vars(self):
        "Updates python member variables based on the vim variables"
        if not self.source_buffer.valid:
            return False
        if self.source_buffer.options["filetype"] != b"coq":
            return False
        goal_bufid = self.source_buffer.vars.get("coquille_goal_bufid", -1)
        if goal_bufid != -1:
            self.goal_buffer = vim.buffers[goal_bufid]
        else:
            self.goal_buffer = None
        info_bufid = self.source_buffer.vars.get("coquille_info_bufid", -1)
        if info_bufid != -1:
            self.info_buffer = vim.buffers[info_bufid]
        else:
            self.info_buffer = None
        return True

    ###################
    # synchronization #
    ###################

    def sync(self):
        curr_sync = vimbufsync.sync(self.source_buffer)
        if not self.saved_sync or curr_sync.buf() != self.saved_sync.buf():
            if len(self.coq_top.states) > 1:
                self._reset()
        else:
            (line, col) = self.saved_sync.pos()
            # vim indexes from lines 1, coquille from 0
            self.rewind_to(line - 1, col - 1)
        self.saved_sync = curr_sync

    def _reset(self):
        self.coq_top.kill_coqtop()
        self.send_queue = deque([])
        self.saved_sync = None
        self.error_at   = None
        self.reset_color()

    #####################
    # exported commands #
    #####################

    def kill_coqtop(self):
        if self is None:
            return
        self._reset()

    def goto_last_sent_dot(self):
        (line, col) = ((0,1) if not self.coq_top.states
                             else self.coq_top.states[-1].end)
        vim.current.window.cursor = (line + 1, col)

    def coq_rewind(self, steps=1):
        self.clear_info()

        # Do not allow the root state to be rewound
        if steps < 1 or len(self.coq_top.states) < 2:
            return

        if self.coq_top.coqtop is None:
            print("Error: Coqtop isn't running. Are you sure you called :CoqLaunch?")
            return

        response = self.coq_top.rewind(steps)

        if response is None:
            vim.command("call coquille#KillSession()")
            print('ERROR: the Coq process died')
            return

        self.refresh()

        # steps != 1 means that either the user called "CoqToCursor" or just started
        # editing in the "locked" zone. In both these cases we don't want to move
        # the cursor.
        if (steps == 1 and vim.eval('g:coquille_auto_move') == 'true'):
            self.goto_last_sent_dot()

    def coq_to_cursor(self):
        if self.coq_top.coqtop is None:
            print("Error: Coqtop isn't running. Are you sure you called :CoqLaunch?")
            return

        self.sync()

        (cline, ccol) = vim.current.window.cursor
        cline -= 1
        (line, col) = ((0,0) if not self.coq_top.states
                             else self.coq_top.states[-1].end)

        if cline < line or (cline == line and ccol < col):
            # Add 1 to the column to leave whatever is at the
            # cursor as sent.
            self.rewind_to(cline, ccol + 1)
        else:
            while True:
                r = self._get_message_range((line, col))
                if r is not None and r['stop'] <= (cline, ccol + 1):
                    line = r['stop'][0]
                    col  = r['stop'][1]
                    self.send_queue.append(r)
                else:
                    break

            self.send_until_fail()

    def coq_next(self):
        if self.coq_top.coqtop is None:
            print("Error: Coqtop isn't running. Are you sure you called :CoqLaunch?")
            return

        self.sync()

        (line, col) = ((0,0) if not self.coq_top.states
                             else self.coq_top.states[-1].end)
        message_range = self._get_message_range((line, col))

        if message_range is None: return

        self.send_queue.append(message_range)

        self.send_until_fail()

        if (vim.eval('g:coquille_auto_move') == 'true'):
            self.goto_last_sent_dot()

    def coq_raw_query(self, *args):
        self.clear_info()

        if self.coq_top.coqtop is None:
            print("Error: Coqtop isn't running. Are you sure you called :CoqLaunch?")
            return

        raw_query = ' '.join(args)

        encoding = vim.eval("&encoding") or 'utf-8'

        response = self.coq_top.query(raw_query, encoding)

        if response is None:
            vim.command("call coquille#KillSession()")
            print('ERROR: the Coq process died')
            return

        info_msg = self.coq_top.get_errors()
        self.show_info(info_msg)


    def launch_coq(self, *args):
        use_project_args = self.source_buffer.vars.get(
                "coquille_append_project_args",
                vim.vars.get("coquille_append_project_args", 0))
        if use_project_args:
            # Vim passes the args as a tuple
            args = list(args)
            args.extend(project_file.find_and_parse_file(
                self.source_buffer.name))
        return self.coq_top.restart_coq(*args)

    def debug(self):
        if self.coq_top.states:
            print("encountered dots = [")
            for (line, col) in self.coq_top.states:
                print("  (%d, %d) ; " % (line, col))
            print("]")

    #####################################
    # IDE tools: Goal, Infos and colors #
    #####################################

    def refresh(self):
        last_info = None
        def update():
            nonlocal last_info
            self.reset_color()
            vim.command('redraw')
            new_info = self.coq_top.get_errors()
            if last_info != new_info:
                self.show_info(new_info)
                last_info = new_info
        # It seems that coqtop needs some kind of call like Status or Goal to
        # trigger it to start processing all the commands that have been added.
        # So show_goal needs to be called before waiting for all the unchecked
        # commands finished.
        if self.show_goal(update):
            while self.coq_top.has_unchecked_commands():
                self.coq_top.process_response()
                update()
        update()

    def show_goal(self, feedback_callback):
        # Temporarily make the goal buffer modifiable
        modifiable = self.goal_buffer.options["modifiable"]
        self.goal_buffer.options["modifiable"] = True
        try:
            del self.goal_buffer[:]

            response = self.coq_top.goals(feedback_callback)

            if response is None:
                vim.command("call coquille#KillSession()")
                print('ERROR: the Coq process died')
                return False

            if isinstance(response, CT.Err):
                return False

            if response.val.val is None:
                self.goal_buffer[0] = 'No goals.'
                return True

            goals = response.val.val

            sub_goals = goals.fg

            nb_subgoals = len(sub_goals)
            plural_opt = '' if nb_subgoals == 1 else 's'
            self.goal_buffer[0] = '%d subgoal%s' % (nb_subgoals, plural_opt)
            self.goal_buffer.append([''])

            for idx, sub_goal in enumerate(sub_goals):
                _id = sub_goal.id
                hyps = sub_goal.hyp
                ccl = sub_goal.ccl
                if idx == 0:
                    # we print the environment only for the current subgoal
                    for hyp in hyps:
                        lst = map(lambda s: s.encode('utf-8'), hyp.split('\n'))
                        self.goal_buffer.append(list(lst))
                self.goal_buffer.append('')
                self.goal_buffer.append('======================== ( %d / %d )' % (idx+1 , nb_subgoals))
                lines = map(lambda s: s.encode('utf-8'), ccl.split('\n'))
                self.goal_buffer.append(list(lines))
                self.goal_buffer.append('')
        finally:
            self.goal_buffer.options["modifiable"] = modifiable
        return True

    def show_info(self, message):
        # Temporarily make the info buffer modifiable
        modifiable = self.info_buffer.options["modifiable"]
        self.info_buffer.options["modifiable"] = True
        try:
            del self.info_buffer[:]
            lst = []
            if message is not None:
                lst = list(map(lambda s: s.encode('utf-8'),
                               message.split('\n')))
            if len(lst) >= 1:
                # If self.info_buffers was a regular list, the del statement
                # above would have deleted all the lines. However with a vim
                # buffer, that actually leaves 1 blank line. So now for setting
                # the new contents, the very first line has to be overwritten,
                # then the rest can be appended.
                #
                # Also note that if info_buffer was a list, extend would be the
                # appropriate function. However info_buffer does not have an
                # extend function, and its append mostly behaves like extend.
                self.info_buffer[0] = lst[0]
                self.info_buffer.append(lst[1:])
        finally:
            self.info_buffer.options["modifiable"] = modifiable

    def clear_info(self):
        self.coq_top.clear_errors()
        self.show_info(None)

    def convert_offset(self, range_start, offset, range_end):
        message = self._between(range_start, range_end)
        (line, col) = _pos_from_offset(range_start[1], message, offset)
        return (line + range_start[0], col)

    def reset_color(self):
        sent = []
        checked = []
        warnings = []
        errors = []
        prev_end = None
        sent_start = None
        checked_start = None
        for c in itertools.chain(self.coq_top.states):
            if c.state in (CT.Command.SENT, CT.Command.ABANDONED):
                if sent_start is None:
                    # Start a sent range
                    sent_start = prev_end
            elif sent_start is not None:
                # Finish a sent range
                sent.append(make_vim_range(sent_start, prev_end))
                sent_start = None

            # Count the warning and error states as checked. A subrange will
            # also be marked as a warning or error, but that will override the
            # checked group.
            if c.state in (CT.Command.PROCESSED, 
                           CT.Command.WARNING, 
                           CT.Command.ERROR):
                if checked_start is None:
                    # Start a checked range
                    checked_start = prev_end
            elif checked_start is not None:
                # Finish a checked range
                checked.append(make_vim_range(checked_start, prev_end))
                checked_start = None
            prev_end = c.end
        if sent_start is not None:
            # Finish a sent range
            sent.append(make_vim_range(sent_start, prev_end))
        if checked_start is not None:
            # Finish a checked range
            checked.append(make_vim_range(checked_start, prev_end))
        prev_end = None
        for c in itertools.chain(self.coq_top.states,
                                 self.coq_top.reverted_states):
            if c.state in (CT.Command.WARNING, 
                           CT.Command.ERROR):
                # Normalize the start and stop positions, if it hasn't been done yet.
                if c.msg_start_offset is not None and c.msg_start is None:
                    c.msg_start = self.convert_offset(prev_end,
                                                      c.msg_start_offset,
                                                      c.end)
                if c.msg_stop_offset is not None and c.msg_stop is None:
                    c.msg_stop = self.convert_offset(prev_end,
                                                     c.msg_stop_offset,
                                                     c.end)
                start = c.msg_start
                stop = c.msg_stop
                if start == stop:
                    start = prev_end
                    stop = c.end
                if c.state == CT.Command.WARNING:
                    warnings.append(make_vim_range(start, stop))
                else:
                    errors.append(make_vim_range(start, stop))
            prev_end = c.end
        self.source_buffer.vars['coquille_sent'] = sent
        self.source_buffer.vars['coquille_checked'] = checked
        self.source_buffer.vars['coquille_warnings'] = warnings
        self.source_buffer.vars['coquille_errors'] = errors
        vim.command("call coquille#SyncBufferColors(%d)" %
                    self.source_buffer.number)

    def rewind_to(self, line, col):
        """ Go backwards to the specified position

        line and col are 0-based and point to the first position to
        remove from the sent region.
        """
        if self.coq_top.coqtop is None:
            print('Internal error: vimbufsync is still being called but coqtop\
                    appears to be down.')
            print('Please report.')
            return

        if self.coq_top.states and self.coq_top.states[-1].end <= (line, col):
            # The caller asked to rewind to a position after what has been
            # processed. This quick path exits without having to search the
            # state list.
            return

        predicate = lambda x: x.end <= (line, col)
        lst = filter(predicate, self.coq_top.states)
        steps = len(self.coq_top.states) - len(list(lst))
        if steps != 0:
            self.coq_rewind(steps)

    #############################
    # Communication with Coqtop #
    #############################

    def send_until_fail(self):
        """
        Tries to send every message in [send_queue] to Coq, stops at the first
        error.
        When this function returns, [send_queue] is empty.
        """
        self.clear_info()

        encoding = vim.eval('&fileencoding') or 'utf-8'

        while len(self.send_queue) > 0:
            message_range = self.send_queue.popleft()
            (eline, ecol) = message_range['stop']
            message = self._between(message_range['start'],
                                    (eline, ecol - 1))

            response = self.coq_top.advance(message,
                                            (eline, ecol), encoding)

            if response is None:
                vim.command("call coquille#KillSession()")
                print('ERROR: the Coq process died')
                return

            if isinstance(response, CT.Ok):
                optionnal_info = response.val[1]
            else:
                self.send_queue.clear()
                if isinstance(response, CT.Err):
                    loc_s = response.loc_s
                    if loc_s is not None:
                        loc_e = response.loc_e
                        (l, c) = message_range['start']
                        (l_start, c_start) = _pos_from_offset(c, message, loc_s)
                        (l_stop, c_stop)   = _pos_from_offset(c, message, loc_e)
                        self.error_at = ((l + l_start, c_start), (l + l_stop, c_stop))
                else:
                    print("(ANOMALY) unknown answer: %s" % ET.tostring(response))
                break
            
            self.reset_color()
            vim.command('redraw')

        self.refresh()

    #################
    # Miscellaneous #
    #################

    def _between(self, begin, end):
        """
        Returns a string corresponding to the portion of the buffer between the
        [begin] and [end] positions.
        """
        (bline, bcol) = begin
        (eline, ecol) = end
        acc = ""
        for line, str in enumerate(self.source_buffer[bline:eline + 1]):
            start = bcol if line == 0 else 0
            stop  = ecol + 1 if line == eline - bline else len(str)
            acc += str[start:stop] + '\n'
        return acc

    def _get_message_range(self, after):
        """ See [_find_next_chunk] """
        (line, col) = after
        end_pos = self._find_next_chunk(line, col)
        return { 'start':after , 'stop':end_pos } if end_pos is not None else None

    # A bullet is:
    # - One or more '-'
    # - One or more '+'
    # - One or more '*'
    # - Exactly 1 '{' (additional ones are parsed as separate statements)
    # - Exactly 1 '}' (additional ones are parsed as separate statements)
    bullets = re.compile("-+|\++|\*+|{|}")

    def _find_next_chunk(self, line, col):
        """
        Returns the position of the next chunk dot after a certain position.
        That can either be a bullet if we are in a proof, or "a string" terminated
        by a dot (outside of a comment, and not denoting a path).
        """
        blen = len(self.source_buffer)
        # We start by striping all whitespaces (including \n) from the beginning of
        # the chunk.
        while line < blen and self.source_buffer[line][col:].strip() == '':
            line += 1
            col = 0

        if line >= blen: return

        while self.source_buffer[line][col] == ' ': # FIXME: keeping the stripped line would be
            col += 1                                #   more efficient.

        # Then we check if the first character of the chunk is a bullet.
        # Intially I did that only when I was sure to be in a proof (by looking in
        # [encountered_dots] whether I was after a "collapsable" chunk or not), but
        #   1/ that didn't play well with coq_to_cursor (as the "collapsable chunk"
        #      might not have been sent/detected yet).
        #   2/ The bullet chars can never be used at the *beginning* of a chunk
        #      outside of a proof. So the check was unecessary.
        bullet_match = self.bullets.match(self.source_buffer[line], col)
        if bullet_match:
            return (line, bullet_match.end())

        # We might have a commentary before the bullet, we should be skiping it and
        # keep on looking.
        tail_len = len(self.source_buffer[line]) - col
        if ((tail_len - 1 > 0) and self.source_buffer[line][col] == '('
                and self.source_buffer[line][col + 1] == '*'):
            com_end = self._skip_comment(line, col + 2, 1)
            if not com_end: return
            (line, col) = com_end
            return self._find_next_chunk(line, col)


        # If the chunk doesn't start with a bullet, we look for a dot.
        dot = self._find_dot_after(line, col)
        if dot:
            # Return the position one after the dot
            return (dot[0], dot[1] + 1)
        else:
            return None

    def _find_dot_after(self, line, col):
        """
        Returns the position of the next "valid" dot after a certain position.
        Valid here means: recognized by Coq as terminating an input, so dots in
        comments, strings or ident paths are not valid.
        """
        if line >= len(self.source_buffer): return
        s = self.source_buffer[line][col:]
        dot_pos = s.find('.')
        com_pos = s.find('(*')
        str_pos = s.find('"')
        if com_pos == -1 and dot_pos == -1 and str_pos == -1:
            # Nothing on this line
            return self._find_dot_after(line + 1, 0)
        elif dot_pos == -1 or (com_pos > - 1 and dot_pos > com_pos) or (str_pos > - 1 and dot_pos > str_pos):
            if str_pos == -1 or (com_pos > -1 and str_pos > com_pos):
                # We see a comment opening before the next dot
                com_end = self._skip_comment(line, com_pos + 2 + col, 1)
                if not com_end: return
                (line, col) = com_end
                return self._find_dot_after(line, col)
            else:
                # We see a string starting before the next dot
                str_end = self._skip_str(line, str_pos + col + 1)
                if not str_end: return
                (line, col) = str_end
                return self._find_dot_after(line, col)
        elif dot_pos < len(s) - 1 and s[dot_pos + 1] != ' ':
            # Sometimes dot are used to access module fields, we don't want to stop
            # just after the module name.
            # Example: [Require Import Coq.Arith]
            return self._find_dot_after(line, col + dot_pos + 1)
        elif dot_pos + col > 0 and self.source_buffer[line][col + dot_pos - 1] == '.':
            # FIXME? There might be a cleaner way to express this.
            # We don't want to capture ".."
            if dot_pos + col > 1 and self.source_buffer[line][col + dot_pos - 2] == '.':
                # But we want to capture "..."
                return (line, dot_pos + col)
            else:
                return self._find_dot_after(line, col + dot_pos + 1)
        else:
            return (line, dot_pos + col)

    # TODO? factorize [_skip_str] and [_skip_comment]
    def _skip_str(self, line, col):
        """
        Used when we encountered the start of a string before a valid dot (see
        [_find_dot_after]).
        Returns the position of the end of the string.
        """
        while True:
            if line >= len(self.source_buffer): return
            s = self.source_buffer[line][col:]
            str_end = s.find('"')
            if str_end > -1:
                return (line, col + str_end + 1)
            line += 1
            col = 0

    def _skip_comment(self, line, col, nb_left):
        """
        Used when we encountered the start of a comment before a valid dot (see
        [_find_dot_after]).
        Returns the position of the end of the comment.
        """
        while nb_left > 0:
            if line >= len(self.source_buffer): return None
            s = self.source_buffer[line][col:]
            com_start = s.find('(*')
            com_end = s.find('*)')
            if com_end > -1 and (com_end < com_start or com_start == -1):
                col += com_end + 2
                nb_left -= 1
            elif com_start > -1:
                col += com_start + 2
                nb_left += 1
            else:
                line += 1
                col = 0
        return (line, col)

def _empty_range():
    return [ { 'line': 0, 'col': 0}, { 'line': 0, 'col': 0} ]

def _pos_from_offset(col, msg, offset):
    str = msg[:offset]
    lst = str.split('\n')
    line = len(lst) - 1
    col = len(lst[-1]) + (col if line == 0 else 0)
    return (line, col)
