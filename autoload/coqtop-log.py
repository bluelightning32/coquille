#!/usr/bin/env python

# To use this, rename coqtop into coqtop.real, then put this script in its
# place. This script will save all xml communication with coqtop in a log file.

from __future__ import print_function

import os
import subprocess
import sys
import threading
import xml.parsers.expat

xml_io = "-ideslave" in sys.argv

log = open("coq-xml-log-%d.txt" % os.getpid(), "w")
print("Running %s" % sys.argv, file=log)
log.flush()

# Fake the HTML entities. This does not link to the real html DTD so that expat
# does not try to download the real DTD.
coq_doc_type = """<!DOCTYPE html [
<!ENTITY nbsp ' '>
]>"""

def get_doc_end(xml_buffer):
    if not xml_io:
        if len(xml_buffer):
            return len(xml_buffer)
        else:
            return -1
    parser = xml.parsers.expat.ParserCreate()
    try:
        parser.Parse(coq_doc_type + xml_buffer, True)
        end = len(xml_buffer)
    except xml.parsers.expat.ExpatError as e:
        if e.message.startswith(xml.parsers.expat.errors.XML_ERROR_JUNK_AFTER_DOC_ELEMENT):
            end = parser.ErrorByteIndex - len(coq_doc_type)
        else:
            end = -1
    return end

def handle_input(name, from_fd, to_file):
    xml_buffer = ""
    while True:
        read = os.read(from_fd, 10000)
        if read == "":
            break
        xml_buffer += read
        while True:
            try:
                end = get_doc_end(xml_buffer)
            except xml.parsers.expat.ExpatError as e:
                print("%s: Error parsing %s: %s" % (name, xml_buffer, e),
                      file=log)
                log.flush()
                raise
            if end != -1:
                print("%s: %s" % (name, xml_buffer[0:end]), file=log)
                log.flush()
                to_file.write(xml_buffer[0:end])
                to_file.flush()
                xml_buffer = xml_buffer[end:]
            else:
                break
    to_file.close()

real_coq = subprocess.Popen(args=([sys.argv[0] + ".real"] + sys.argv[1:]), 
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE)

input_thread = threading.Thread(target=handle_input,
                                args=("input", sys.stdin.fileno(), real_coq.stdin))
output_thread = threading.Thread(target=handle_input,
                                 args=("output", real_coq.stdout.fileno(), sys.stdout))
# There is no good way to stop the input thread if real_coq exits early. So
# start it as a daemon thread and let it get force aborted at the end.
input_thread.daemon = True
input_thread.start()
output_thread.start()

real_coq.wait()

os.close(sys.stdin.fileno())
output_thread.join()

sys.exit(real_coq.returncode)
