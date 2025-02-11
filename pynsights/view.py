"""
    Trace modules, classes, methods, and functions and send events to JavaScript client.
"""

import json
import webbrowser
from pynsights.constants import *

modulenames = []
typenames = []
callsites = []
calls = []
cpus = []
heap = []
gcs = []
memories = []
annotations = []
duration = 0
lastCall = {}
when = 0

template_file = __file__.replace("view.py", "index.html")


def show_progress(percent, end=""):
    print(f"\rPynsights: rendering, {percent}% done", end=end)


def read_dump(input_file):
    done = 0
    with open(input_file) as fp:
        lines = fp.readlines()
        one_percent = round(len(lines) / 100)
        for n, line in enumerate(lines):
            handle_line(line)
            if not one_percent or n % one_percent == 0:
                if done % 10 == 0:
                    show_progress(done)
                done += 1
        flushCallSites()
    show_progress(100, "\n")


def flushCallSites():
    for callsite in lastCall:
        when, count = lastCall[callsite]
        calls.append((when, callsite, count))
    lastCall.clear()

def addCall(when, callsite):
    count = 0
    if callsite in lastCall:
        lastWhen, count = lastCall[callsite]
        if when - lastWhen > 500:
            calls.append((lastWhen, callsite, count))
    lastCall[callsite] = when, count + 1

def handle_line(line):
    global duration, when
    items = line[:-1].split()
    kind = int(items[0])
    if kind == EVENT_MODULE:
        _, parent, module = items
        if module == "__init__":
            module = parent
        modulenames.append((parent, module))
    elif kind == EVENT_CPU:
        cpu, cpu_system = float(items[1]), float(items[2])
        cpus.append((when, cpu, cpu_system))
    elif kind == EVENT_MEMORY:
        memory = float(items[1])
        memories.append((when, memory))
    elif kind == EVENT_TYPE:
        typename = items[1]
        typenames.append(typename)
    elif kind == EVENT_HEAP:
        counts = json.loads(" ".join(items[1:]))
        heap.append((when, counts))
    elif kind == EVENT_GC:
        gc_duration, collected, uncollectable = int(items[1]), int(items[2]), int(items[3])
        gcs.append((when, gc_duration, collected, uncollectable))
    elif kind == EVENT_CALLSITE:
        callsite = int(items[1]), int(items[2])
        callsites.append(callsite)
    elif kind == EVENT_CALL:
        callsite = int(items[1])
        addCall(when, callsite)
        duration = when
    elif kind == EVENT_ANNOTATE:
        message = " ".join(items[1:])
        annotations.append((when, message))
        duration = when
        flushCallSites()
    elif kind == EVENT_ENTER:
        message = " ".join(items[1:])
        annotations.append((when, "Enter %s" % message))
        duration = when
        flushCallSites()
    elif kind == EVENT_EXIT:
        message = " ".join(items[1:])
        annotations.append((when, "Exit %s" % message))
        duration = when
        flushCallSites()
    elif kind == EVENT_TIMESTAMP:
        when = int(items[1])


def render(output):
    with open(template_file) as fin:
        template = fin.read()
    html = template\
        .replace("/*DURATION*/", str(duration) + " //") \
        .replace("/*MODULENAMES*/", json.dumps(modulenames, indent=4) + " //") \
        .replace("/*CALLSITES*/", json.dumps(callsites) + " //") \
        .replace("/*CALLS*/", json.dumps(calls) + " //") \
        .replace("/*CPUS*/", json.dumps(cpus) + " //") \
        .replace("/*ANNOTATIONS*/", json.dumps(annotations) + " //") \
        .replace("/*HEAP*/", "[\n    " + ",\n    ".join(json.dumps(snapshot) for snapshot in heap) + "\n] //") \
        .replace("/*GC*/", "[\n    " + ",\n    ".join(json.dumps(gc) for gc in gcs) + "\n] //") \
        .replace("/*TYPES*/", json.dumps(typenames) + " //") \
        .replace("/*MEMORIES*/", json.dumps(memories, indent=4) + " //")
    with open(output, "w") as fout:
        fout.write(html)


def open_ui(output):
    print("Opening", output)
    webbrowser.open("file://" + str(output.resolve()))


def view(input_file, output=None, open_browser=False):
    read_dump(input_file)
    if output is None:
        output = input_file.with_suffix(".html")
    render(output)
    if open_browser:
        open_ui(output)
