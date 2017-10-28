from __future__ import print_function
from __future__ import unicode_literals
from __future__ import division

import os
import re
import subprocess
import xml.etree.ElementTree as ET
import signal
import sys
import threading

from collections import deque, namedtuple

# Define unicode in python 3
if isinstance(__builtins__, dict):
    unicode = __builtins__.get('unicode', str)
else:
    unicode = getattr(__builtins__, 'unicode', str)

Ok = namedtuple('Ok', ['val', 'msg'])
Err = namedtuple('Err', ['err', 'revert_state', 'loc_s', 'loc_e'])

Inl = namedtuple('Inl', ['val'])
Inr = namedtuple('Inr', ['val'])

EditId = namedtuple('EditId', ['id'])
StateId = namedtuple('StateId', ['id'])
Option = namedtuple('Option', ['val'])

OptionState = namedtuple('OptionState', ['sync', 'depr', 'name', 'value'])
OptionValue = namedtuple('OptionValue', ['val'])

Status = namedtuple('Status', ['path', 'proofname', 'allproofs', 'proofnum'])

Goals = namedtuple('Goals', ['fg', 'bg', 'shelved', 'given_up'])
Goal = namedtuple('Goal', ['id', 'hyp', 'ccl'])
Evar = namedtuple('Evar', ['info'])

def parse_response(xml):
    assert xml.tag == 'value'
    if xml.get('val') == 'good':
        return Ok(parse_value(xml[0]), None)
    elif xml.get('val') == 'fail':
        # Don't print the error immediately : let the caller decide wether
        # it must be printed and in which way
        return parse_error(xml)
    else:
        assert False, 'expected "good" or "fail" in <value>'

def parse_value(xml):
    if xml.tag == 'unit':
        return ()
    elif xml.tag == 'bool':
        if xml.get('val') == 'true':
            return True
        elif xml.get('val') == 'false':
            return False
        else:
            assert False, 'expected "true" or "false" in <bool>'
    elif xml.tag == 'string':
        return xml.text or ''
    elif xml.tag == 'int':
        return int(xml.text)
    elif xml.tag == 'edit_id':
        return EditId(int(xml.get('val')))
    elif xml.tag == 'state_id':
        return StateId(int(xml.get('val')))
    elif xml.tag == 'list':
        return [parse_value(c) for c in xml]
    elif xml.tag == 'option':
        if xml.get('val') == 'none':
            return Option(None)
        elif xml.get('val') == 'some':
            return Option(parse_value(xml[0]))
        else:
            assert False, 'expected "none" or "some" in <option>'
    elif xml.tag == 'pair':
        return tuple(parse_value(c) for c in xml)
    elif xml.tag == 'union':
        if xml.get('val') == 'in_l':
            return Inl(parse_value(xml[0]))
        elif xml.get('val') == 'in_r':
            return Inr(parse_value(xml[0]))
        else:
            assert False, 'expected "in_l" or "in_r" in <union>'
    elif xml.tag == 'option_state':
        sync, depr, name, value = map(parse_value, xml)
        return OptionState(sync, depr, name, value)
    elif xml.tag == 'option_value':
        return OptionValue(parse_value(xml[0]))
    elif xml.tag == 'status':
        path, proofname, allproofs, proofnum = map(parse_value, xml)
        return Status(path, proofname, allproofs, proofnum)
    elif xml.tag == 'goals':
        return Goals(*map(parse_value, xml))
    elif xml.tag == 'goal':
        return Goal(*map(parse_value, xml))
    elif xml.tag == 'evar':
        return Evar(*map(parse_value, xml))
    elif xml.tag == 'xml' or xml.tag == 'richpp':
        return ''.join(xml.itertext())

def parse_error(xml):
    loc_s = xml.get('loc_s')
    loc_e = xml.get('loc_e')
    msg = xml.find("richpp")
    if msg is not None:
        msg = parse_value(msg).strip()
    else:
        msg = "Err: unknown"
    revert_state = xml.find("state_id")
    if revert_state is not None:
        revert_state = parse_value(revert_state)
    if loc_s is not None:
        return Err(msg, revert_state, int(loc_s), int(loc_e))
    else:
        return Err(msg, revert_state, None, None)

def build(tag, val=None, children=()):
    attribs = {'val': val} if val is not None else {}
    xml = ET.Element(tag, attribs)
    xml.extend(children)
    return xml

def encode_call(name, arg):
    return build('call', name, [encode_value(arg)])

def encode_value(v):
    if v == ():
        return build('unit')
    elif isinstance(v, bool):
        xml = build('bool', str(v).lower())
        xml.text = str(v)
        return xml
    elif isinstance(v, str) or isinstance(v, unicode):
        xml = build('string')
        xml.text = v
        return xml
    elif isinstance(v, int):
        xml = build('int')
        xml.text = str(v)
        return xml
    elif isinstance(v, EditId):
        return build('edit_id', str(v.id))
    elif isinstance(v, StateId):
        return build('state_id', str(v.id))
    elif isinstance(v, list):
        return build('list', None, [encode_value(c) for c in v])
    elif isinstance(v, Option):
        xml = build('option')
        if v.val is not None:
            xml.set('val', 'some')
            xml.append(encode_value(v.val))
        else:
            xml.set('val', 'none')
        return xml
    elif isinstance(v, Inl):
        return build('union', 'in_l', [encode_value(v.val)])
    elif isinstance(v, Inr):
        return build('union', 'in_r', [encode_value(v.val)])
    # NB: `tuple` check must be at the end because it overlaps with () and
    # namedtuples.
    elif isinstance(v, tuple):
        return build('pair', None, [encode_value(c) for c in v])
    else:
        assert False, 'unrecognized type in encode_value: %r' % (type(v),)

def ignore_sigint():
    signal.signal(signal.SIGINT, signal.SIG_IGN)

def escape(cmd):
    escaped = cmd.replace("&nbsp;", ' ') \
              .replace("&apos;", '\'') \
              .replace("&#40;", '(') \
              .replace("&#41;", ')')
    if isinstance(escaped, str):
        return escaped
    else:
        return escaped.encode('ascii', 'xmlcharrefreplace')

class Command(object):
    # Values for self.state
    # ----------------------
    # Command was sent to coqtop through an Add call, and coq acknowledged the
    # Add.
    SENT = 0
    # Either the worker that was processing this command died before finishing
    # processing the command, or coq rejected the Add call. The command will
    # never be finished.
    ABANDONED = 1
    # coqtop marked the command as processed through a feedback statement.
    PROCESSED = 2
    REVERTED = 3

    # Values for self.msg_type
    # ----------------------------
    # 
    NONE = 0
    # coqtop sent a warning level message for the command through a feedback
    # statement.
    WARNING = 1
    # coqtop sent an error level message for the command through a feedback
    # statement.
    ERROR = 2

    next_edit = -1

    def __init__(self, end):
        self.edit_id = EditId(Command.next_edit)
        Command.next_edit -= 1
        self.state_id = None
        self.state = self.SENT
        # A (line, col, byte) pair. line, col, and byte are 0-indexed. The end
        # position is one column after the last character included in the
        # command.
        assert(len(end) == 3)
        self.end = end
        self.msg_type = self.NONE
        # A byte offset relative to the start of this command where the warning
        # or error starts.
        self.msg_start_offset = None
        # A (line, col) pair where the warning/error state starts
        self.msg_start = None
        # A byte offset relative to the start of this command where the warning
        # or error stops.
        self.msg_stop_offset = None
        # A (line, col) pair where the warning/error state stops
        self.msg_stop = None
        # The worker that was last processing this command
        self.worker = None

class CoqTop(object):
    # bit fields for self.result
    COMMAND_CHANGED = 1
    MESSAGE_RECEIVED = 2
    SEND_DONE = 4

    def __init__(self):
        self.coqtop = None
        self.states = []
        # A list of states that were reverted. These stick around until the
        # error messages are cleared, to track where the errors are in the
        # reverted commands. This is important because sometimes coqtop forces
        # the state to get reverted.
        # the states to get reverted.
        self.messages = []

        self.lock = threading.Lock()
        self.result = 0
        self.has_result = threading.Condition(self.lock)
        self.send_thread = None

    def kill_coqtop(self):
        with self.lock:
            if self.coqtop:
                try:
                    self.coqtop.terminate()
                    self.coqtop.communicate()
                except OSError:
                    pass
                self.coqtop = None
                self.states = []
                self.reverted_index = 0
                self.messages = []

    def get_command_by_state_id(self, state_id):
        for s in self.states:
            if s.state_id == state_id:
                 return s
        return None

    def get_command_by_edit(self, edit_id):
        for s in self.states:
            if s.edit_id == edit_id:
                 return s
        return None

    def check_state_indexes(self):
        assert self.reverted_index <= len(self.states)

    def get_active_command_count(self):
        with self.lock:
            self.check_state_indexes()
            return self.reverted_index

    def get_last_active_command(self):
        with self.lock:
            self.check_state_indexes()
            if self.reverted_index > 0:
                return self.states[self.reverted_index - 1]
            else:
                return None

    def get_active_commands(self):
        with self.lock:
            self.check_state_indexes()
            return list(self.states[0:self.reverted_index])

    def get_commands(self):
        with self.lock:
            return list(self.states)

    def parse_feedback(self, xml):
        assert xml.tag == 'feedback'
        message = None
        if xml.get("object") == "state":
            state_id = parse_value(xml.find("state_id"))
            comm = self.get_command_by_state_id(state_id)
            if comm is None:
                # coqtop must be sending feedback for a statement before it
                # sent a reply to the Add command.
                comm = self.get_command_by_state_id(None)
        else:
            edit_id = parse_value(xml.find("edit_id"))
            comm = self.get_command_by_edit(edit_id)
        feedback_content = xml.find("feedback_content")
        feedback_type = feedback_content.get('val')
        if feedback_type == "message":
            messageNode = feedback_content.find("message")
            level = messageNode.find("message_level")
            level = level.get("val")
            if level == "error":
                level = Command.ERROR
            elif level == "warning":
                level = Command.WARNING
            else:
                level = Command.NONE
            if comm and level != Command.NONE:
                comm.msg_type = level
                # The element type is option
                if messageNode[1].get("val") == "some":
                    loc = messageNode[1].find("loc")
                    comm.msg_start_offset = int(loc.get("start"))
                    comm.msg_stop_offset = int(loc.get("stop"))
                # Only transition from SENT to PROCESSED.
                if comm.state == Command.SENT:
                    comm.state = Command.PROCESSED
                self.result |= self.COMMAND_CHANGED
            message = parse_value(messageNode.find("richpp"))
            self.messages.append(message)
            self.result |= self.MESSAGE_RECEIVED
            self.has_result.notify()
        elif feedback_type == "processingin":
            if comm is not None:
                comm.worker = parse_value(feedback_content[0])
        elif feedback_type == "workerstatus":
            (worker, status) = parse_value(feedback_content[0])
            if status == "Dead":
                # The worker died. Mark all commands it was processing as
                # abandoned.
                for c in self.states[0:self.reverted_index]:
                    if c.state == Command.SENT and c.worker == worker:
                        c.state = Command.ABANDONED
                self.result |= self.COMMAND_CHANGED
                self.has_result.notify()
        elif feedback_type == "processed":
            # Only transition from SENT to PROCESSED.
            if comm and comm.state == Command.SENT:
                comm.state = Command.PROCESSED
                self.result |= self.COMMAND_CHANGED
                self.has_result.notify()
        return message

    def parse_message(self, xml):
        assert xml.tag == 'message'
        level = xml.find('message_level')
        if level is not None:
            level = level.get('val')
        self.check_state_indexes()
        if level == 'warning' and self.reverted_index > 0:
            comm = self.states[self.reverted_index - 1]
            if comm.state_id is None:
                # Attach the warning message to the command that is currently being
                # parsed.
                comm.msg_type = Command.WARNING
        self.messages.append(parse_value(xml[2]))

    def process_response(self):
        fd = self.coqtop.stdout.fileno()
        data = u''
        while True:
            try:
                data += os.read(fd, 0x4000).decode("utf-8")
                try:
                    elt = ET.fromstring('<coqtoproot>' + escape(data) + '</coqtoproot>')
                    with self.lock:
                        # The data parsed correctly. Clear it so that it isn't
                        # parsed again when new data comes in.
                        data = ''
                        valueNode = None
                        messageNode = None
                        for c in elt:
                            if c.tag == 'value':
                                valueNode = c
                            if c.tag == 'message':
                                self.parse_message(c)
                            # Extract messages from feedbacks to handle errors
                            if c.tag == 'feedback':
                                messageNode = self.parse_feedback(c)
                        if valueNode is None:
                            return None
                        vp = parse_response(valueNode)
                        if messageNode is not None:
                            if isinstance(vp, Ok):
                                return Ok(vp.val, messageNode)
                            elif isinstance(vp, Err):
                                if vp.err not in self.messages:
                                    self.messages.append(vp.err)
                                # Override error message : coq provides one
                                return Err(messageNode, vp.revert_state,
                                           vp.loc_s, vp.loc_e)
                        return vp
                except ET.ParseError:
                    continue
            except OSError:
                # coqtop died
                return Err("coq died", 0, None, None)

    def get_answer(self, feedback_callback):
        answer = None
        while True:
            answer = self.process_response()
            if answer is not None:
                return answer
            # The only way None could have been returned is if feedback was
            # processed instead.
            if feedback_callback is not None:
                feedback_callback()

    def call(self, name, arg, feedback_callback=None):
        xml = encode_call(name, arg)
        msg = ET.tostring(xml, 'utf-8')
        self.send_cmd(msg)
        response = self.get_answer(feedback_callback)
        return response

    def send_cmd(self, cmd):
        self.coqtop.stdin.write(cmd)
        self.coqtop.stdin.flush()

    def restart_coq(self, *args):
        if self.coqtop: self.kill_coqtop()
        options = [ 'coqtop'
                  , '-ideslave'
                  , '-main-channel'
                  , 'stdfds'
                  , '-async-proofs'
                  , 'on'
                  ]
        try:
            with self.lock:
                if os.name == 'nt':
                    self.coqtop = subprocess.Popen(
                        options + list(args)
                      , stdin = subprocess.PIPE
                      , stdout = subprocess.PIPE
                      , stderr = subprocess.STDOUT
                    )
                else:
                    self.coqtop = subprocess.Popen(
                        options + list(args)
                      , stdin = subprocess.PIPE
                      , stdout = subprocess.PIPE
                      , preexec_fn = ignore_sigint
                    )

            r = self.call('Init', Option(None))
            with self.lock:
                assert isinstance(r, Ok)
                comm = Command((0, 0, 0))
                comm.edit_id = None
                comm.state_id = r.val
                comm.state = Command.PROCESSED
                self.states = [comm]
                self.reverted_index = len(self.states)
                self.check_state_indexes()
                return True
        except OSError as e:
            print("Error: couldn't launch coqtop:", e)
            return False

    def launch_coq(self, *args):
        return self.restart_coq(*args)

    def cur_state(self):
        self.check_state_indexes()
        return self.states[self.reverted_index - 1].state_id

    # Clear the messages from old commands.
    #
    # The error state and location of non-reverted commands will stay, but
    # their error messages will be reverted.
    def clear_messages(self):
        with self.lock:
            self.check_state_indexes()
            self.messages = []
            del self.states[self.reverted_index:]
            self.check_state_indexes()

    def get_messages(self):
        with self.lock:
            return "\n".join(self.messages)

    def advance(self, cmd, end):
        with self.lock:
            cur_state = self.cur_state()
            assert self.reverted_index == len(self.states)
            comm = Command(end)
            self.states.append(comm)
            self.reverted_index += 1
        r = self.call('Add', ((cmd, comm.edit_id.id),
                              (cur_state, True)))
        with self.lock:
            if r is None or isinstance(r, Err):
                comm.state = Command.ABANDONED
                if r is not None:
                    self.messages.append(r.err)
                    comm.msg_start_offset = int(r.loc_s)
                    comm.msg_stop_offset = int(r.loc_e)
                    comm.msg_type = Command.ERROR
                self.reverted_index -= 1
                return r
            comm.state_id = r.val[0]
            return r

    def rewind(self, step = 1, keep_states = False):
        with self.lock:
            # At least one state, the root state, has to remain
            assert step < self.reverted_index
            self.check_state_indexes()
            idx = self.reverted_index - step
            for c in self.states[idx:]:
                c.state = Command.REVERTED
            self.reverted_index = idx
            if not keep_states:
                del self.states[self.reverted_index:]
            self.check_state_indexes()
            rewind_state = self.cur_state()
        return self.call('Edit_at', rewind_state)

    def query(self, cmd):
        with self.lock:
            cur_state = self.cur_state()
        r = self.call('Query', (cmd, cur_state))
        return r

    def has_unchecked_commands(self):
        with self.lock:
            return any(c.state == Command.SENT for c in self.states[0:self.reverted_index])

    def goals(self, feedback_callback):
        vp = self.call('Goal', (), feedback_callback=feedback_callback)
        if isinstance(vp, Ok):
            return vp.val
        with self.lock:
            if vp.revert_state.id == 0:
                if vp.err not in self.messages:
                    self.messages.append(vp.err)
                # Can't revert to state 0 (which is before the Init). So revert
                # back to the end of the contiguously processed section.
                revert_to = 1
                while (revert_to + 1 < self.reverted_index and
                        self.states[revert_to + 1].state & Command.PROCESSED):
                    revert_to += 1
            else:
                # An error occurred. Revert back to the state coqtop requested, and ask
                # for the goal again.
                revert_to = self.reverted_index
                while (1 < revert_to and
                       self.states[revert_to - 1].state_id.id > vp.revert_state.id):
                    revert_to -= 1
        self.rewind(self.reverted_index - revert_to, keep_states = True)
        vp = self.call('Goal', (), feedback_callback=feedback_callback)
        if isinstance(vp, Ok):
            return vp.val
        else:
            if vp.err not in self.messages:
                self.messages.append(vp.err)
            return None

    def send_async(self, send_queue):
        """
        Tries to send every message in [send_queue] to Coq, stops at the first
        error.
        """
        assert self.send_thread == None
        def process_queue():
            try:
                for (message, end) in send_queue:
                    response = self.advance(message, end)
                    with self.lock:
                        self.result |= self.COMMAND_CHANGED
                        self.has_result.notify()

                        if response is None:
                            self.messages.append('ERROR: the Coq process died')
                            self.result |= self.MESSAGE_RECEIVED
                            self.has_result.notify()
                            return

                        if not isinstance(response, Ok):
                            if not isinstance(response, Err):
                                self.messages.append(
                                        "(ANOMALY) unknown answer: %s" %
                                        ET.tostring(response))
                                self.result |= self.MESSAGE_RECEIVED
                                self.has_result.notify()
                            break
            finally:
                with self.lock:
                    self.result |= self.SEND_DONE
                    self.has_result.notify()

        self.send_thread = threading.Thread(target=process_queue, name="send thread")
        self.send_thread.start()

    def wait_for_result(self):
        with self.lock:
            while self.result == 0:
                self.has_result.wait()
            result = self.result
            self.result = 0
            return result

    def finish_send(self):
        with self.lock:
            thread = self.send_thread
            self.send_thread = None
        thread.join()
