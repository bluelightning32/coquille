from __future__ import print_function
from __future__ import unicode_literals
from __future__ import division

import os
import re
import subprocess
import xml.etree.ElementTree as ET
import signal
import sys

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
    # Command was sent to coqtop through an Add call, and coq acknowledged the
    # Add.
    SENT = 0
    # The worker that was processing this command died before finishing
    # processing the command. The command will never be finished.
    ABANDONED = 1
    # coqtop marked the command as processed through a feedback statement.
    PROCESSED = 2
    # coqtop sent a warning level message for the command through a feedback
    # statement.
    WARNING = 3
    # coqtop sent an error level message for the command through a feedback
    # statement.
    ERROR = 4

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
    def __init__(self):
        self.coqtop = None
        self.states = []
        # A list of states that were reverted. These stick around until the
        # error messages are cleared, to track where the errors are in the
        # reverted commands. This is important because sometimes coqtop forces
        # the state to get reverted.
        # the states to get reverted.
        self.reverted_states = []
        self.error_messages = []

    def kill_coqtop(self):
        if self.coqtop:
            try:
                self.coqtop.terminate()
                self.coqtop.communicate()
            except OSError:
                pass
            self.coqtop = None
            self.states = []
            self.clear_errors()

    def get_command(self, state_id):
        for s in self.states:
            if s.state_id == state_id:
                 return s
        return None

    def get_command_by_edit(self, edit_id):
        for s in self.states:
            if s.edit_id == edit_id:
                 return s
        return None

    def parse_feedback(self, xml):
        assert xml.tag == 'feedback'
        message = None
        if xml.get("object") == "state":
            state_id = parse_value(xml.find("state_id"))
            comm = self.get_command(state_id)
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
            else:
                level = Command.WARNING
            if comm:
                comm.state = level
                # The element type is option
                if messageNode[1].get("val") == "some":
                    loc = messageNode[1].find("loc")
                    comm.msg_start_offset = int(loc.get("start"))
                    comm.msg_stop_offset = int(loc.get("stop"))
            message = parse_value(messageNode.find("richpp"))
            self.error_messages.append(message)
        elif feedback_type == "processingin":
            if comm is not None:
                comm.worker = parse_value(feedback_content[0])
        elif feedback_type == "workerstatus":
            (worker, status) = parse_value(feedback_content[0])
            if status == "Dead":
                # The worker died. Mark all commands it was processing as
                # abandoned.
                for c in self.states:
                    if c.state == Command.SENT and c.worker == worker:
                        c.state = Command.ABANDONED
        elif feedback_type == "processed":
            # Only transition from SENT to PROCESSED. coqtop likes to send out
            # extra feedback messages. Do not let it transition the command
            # from WARNING to PROCESSED.
            #
            # Also note that sometimes coqtop sends processed feedback for
            # commands that it hasn't sent the Add reply for yet. Its difficult
            # to process the feedback when the correspondance between the
            # Command and state_id isn't known yet. So that feedback can be
            # ignored. coqtop will send another processed feedback after the
            # Add reply is sent.
            if comm and comm.state == Command.SENT:
                comm.state = Command.PROCESSED
        return message

    def parse_message(self, xml):
        assert xml.tag == 'message'
        level = xml.find('message_level')
        if level is not None:
            level = level.get('val')
        if level == 'warning' and self.states:
            comm = self.states[-1]
            if comm.state_id is None:
                # Attach the warning message to the command that is currently being
                # parsed.
                comm.state = Command.WARNING
        self.error_messages.append(parse_value(xml[2]))

    def process_response(self):
        fd = self.coqtop.stdout.fileno()
        data = u''
        while True:
            try:
                data += os.read(fd, 0x4000).decode("utf-8")
                try:
                    elt = ET.fromstring('<coqtoproot>' + escape(data) + '</coqtoproot>')
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
                            if vp.err not in self.error_messages:
                                self.error_messages.append(vp.err)
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

    def call(self, name, arg, encoding='utf-8', feedback_callback=None):
        xml = encode_call(name, arg)
        msg = ET.tostring(xml, encoding)
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
            assert isinstance(r, Ok)
            comm = Command((0, 0, 0))
            comm.edit_id = None
            comm.state_id = r.val
            comm.state = Command.PROCESSED
            self.states = [comm]
            return True
        except OSError as e:
            print("Error: couldn't launch coqtop:", e)
            return False

    def launch_coq(self, *args):
        return self.restart_coq(*args)

    def cur_state(self):
        return self.states[-1].state_id

    # Clear the error messages from old commands.
    #
    # The error state and location of non-reverted commands will stay, but
    # their error messages will be reverted.
    def clear_errors(self):
        self.reverted_states = []
        self.error_messages = []

    def get_errors(self):
        return "\n".join(self.error_messages)

    def advance(self, cmd, end, encoding = 'utf-8'):
        cur_state = self.cur_state()
        comm = Command(end)
        self.states.append(comm)
        r = self.call('Add', ((cmd, comm.edit_id.id),
                              (cur_state, True)), encoding)
        if r is None or isinstance(r, Err):
            self.states = self.states[:len(self.states)-1]
            self.reverted_states = [comm] + self.reverted_states
            return r
        comm.state_id = r.val[0]
        return r

    def rewind(self, step = 1, keep_states = False):
        assert step <= len(self.states)
        # At least one state, the root state, has to remain
        assert step < len(self.states)
        idx = len(self.states) - step
        if keep_states:
            self.reverted_states = self.states[idx:] + self.reverted_states
        self.states = self.states[0:idx]
        return self.call('Edit_at', self.cur_state())

    def query(self, cmd, encoding = 'utf-8'):
        r = self.call('Query', (cmd, self.cur_state()), encoding)
        return r

    def has_unchecked_commands(self):
        return any(c.state == Command.SENT for c in self.states)

    def goals(self, feedback_callback):
        vp = self.call('Goal', (), feedback_callback=feedback_callback)
        if isinstance(vp, Ok):
            return vp
        if vp.revert_state.id == 0:
            if vp.err not in self.error_messages:
                self.error_messages.append(vp.err)
            # Can't revert to state 0 (which is before the Init)
            return vp
        # An error occurred. Revert back to the state coqtop requested, and ask
        # for the goal again.
        revert_count = 0
        while (revert_count < len(self.states) and
               self.states[-1 - revert_count].state_id.id > vp.revert_state.id):
            revert_count += 1
        self.rewind(revert_count, keep_states = True)
        return self.call('Goal', (), feedback_callback=feedback_callback)

    def read_states(self):
        return self.states
