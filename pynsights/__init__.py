"""
Record inter-module calls and save events in a file
"""

import atexit
import inspect
import functools
import gc
import json
import os
import re
import sys
import threading
import time
import psutil
from pympler import muppy
from pympler import summary

from .constants import *

process = psutil.Process(os.getpid())

caller = inspect.stack()[-1]
if caller[1].endswith("runpy.py"):
    # prefer sys.argv[0]
    caller_module = sys.argv[0]
    if caller_module.endswith("__main__.py"):
        # use the directory name instead, which is the package name
        caller_module = os.path.dirname(caller_module)
else:
    caller_module = caller[1]

# keep only the base name without extension
caller_module = os.path.basename(caller_module.replace(".py", ""))

output_filename = "%s/pynsights_trace_%s.txt" % (os.path.expanduser('~'), caller_module)
output = None
filename_index = {}
callsite_index = {}
type_index = {}
lock = threading.Lock()
buffer = []
MAX_BUFFER_SIZE = 1000
call_count = 0
PATHSEP = re.compile(r"[/\\]")
start = time.time()
last_flush = 0
FLUSH_INTERVAL = 1.0
METRICS_INTERVAL = 0.5
HEAP_TIMER = 20
last_heap_snapshot = None
heap_timer = 1
tracing = False
metrics_monitor = None
gc_start = 0
last_when = 0



def getcpu():
    cpu_count = psutil.cpu_count()
    return process.cpu_percent() / cpu_count, psutil.cpu_percent()


class SkipCall(Exception):
    pass


def get_type_index(typename):
    if not typename in type_index:
        record(0, "%s %s\n" % (
            EVENT_TYPE,
            typename
        ))
        type_index[typename] = len(type_index)
    return type_index[typename]


def get_module_index(frame):
    filename = frame.f_code.co_filename
    if not filename in filename_index:
        record(0, "%s %s\n" % (
            EVENT_MODULE,
            get_module_name_index(filename)))
        filename_index[filename] = len(filename_index)
    return filename_index[filename]


def get_callsite_index(source, target):
    callsite = "%s>%s" % (source, target)
    if not callsite in callsite_index:
        record(0, "%s %s %s\n" % (
            EVENT_CALLSITE,
            source,
            target))
        callsite_index[callsite] = len(callsite_index)
    return callsite_index[callsite]


def get_module_name_index(filename):
    parts = re.split(PATHSEP, filename)
    if len(parts) == 1:
        basename = parts[0]
        if basename.startswith("<frozen "):
            parts = ["bootstrap", parts[0].replace("<frozen ", "").replace(">", "")]
        else:
            parts = [parts[0], parts[0]]
    group = parts[-2].replace(".py", "")
    module = parts[-1].replace(".py", "")
    if group == "pynsights" and module == "__init__":
        raise SkipCall("skip pynsights calls")
    return "%s %s" % (group, module)


def extract_call(frame):
    target = get_module_index(frame)
    source = get_module_index(frame.f_back)
    if source == target:
        raise SkipCall("ignore self calls")
    return source, target


def flush():
    global buffer
    lines, buffer = buffer, []
    for line in lines:
        output.write(line)
    output.flush()


def record(when, line):
    global last_when
    if when and when != last_when:
        buffer.append("%s %s\n" % (EVENT_TIMESTAMP, when))
    buffer.append(line)
    last_when = when


def process_call(frame, event, _):
    global call_count, last_flush
    try:
        if event == "call":
            source, target = extract_call(frame)
            now = time.time()
            when = round((now - start) * 1000)
            record(when, "%s %s\n" % (EVENT_CALL, get_callsite_index(source, target)))
            call_count += 1
            if now - last_flush > FLUSH_INTERVAL:
                flush()
                last_flush = now
    except AttributeError:
        pass # happens for bootstrap calls only
    except SkipCall:
        pass # not an interesting call
    except:
        import traceback
        traceback.print_exc()
    finally:
        return process_call


def measure_gc(phase, info):
    global gc_start
    when = round((time.time() - start) * 1000)
    if phase == "start":
        gc_start = when
    elif phase == "stop":
        duration = when - gc_start
        record(when, "%s %d %d %d\n" % (EVENT_GC, duration, info["collected"], info["uncollectable"]))


def measure_cpu(when):
    my_cpu, system_cpu = getcpu()
    record(when, "%s %.1f %.1f\n" % (EVENT_CPU, my_cpu, system_cpu))


def measure_memory(when):
    process = psutil.Process(os.getpid())
    memory = process.memory_info().rss
    record(when, "%s %.1f\n" % (EVENT_MEMORY, memory))


def measure_heap(when, force=False):
    global heap_timer
    heap_timer -= 1
    if not force and heap_timer:
        return
    heap_timer = HEAP_TIMER
    record_heap(when)


def record_heap(when):
    global last_heap_snapshot
    objects = muppy.get_objects()
    heap_snapshot = summary.summarize(objects)
    totalSize = 0
    totalCount = 0
    for _, count, size in heap_snapshot:
        totalCount += count
        totalSize += size
    if last_heap_snapshot:
        top20 = sorted(heap_snapshot, key = lambda count: count[2])[-20:]
        dump = [
            [ get_type_index(typename), count, size ]
            for typename, count, size in top20
        ] + [
            [ get_type_index("Total#Heap#Size#and#Count"), totalCount, totalSize ]
        ]
        record(when, "%s %s\n" % (EVENT_HEAP, json.dumps(dump)))
    last_heap_snapshot = heap_snapshot


def generate_metrics():
    while tracing:
        when = round((time.time() - start) * 1000)
        measure_memory(when)
        measure_cpu(when)
        measure_heap(when)
        time.sleep(METRICS_INTERVAL)


def start_metrics_monitor():
    global metrics_monitor
    if not metrics_monitor:
        metrics_monitor = threading.Thread(target=generate_metrics)
    metrics_monitor.start()


def start_tracing():
    global output, tracing
    if tracing: return
    tracing = True
    output = open(output_filename, "w")
    print("Pynsights: tracing started. See", output_filename)
    start_metrics_monitor()
    threading.setprofile(process_call)
    sys.setprofile(process_call)
    gc.callbacks.append(measure_gc)



def flush_counters():
    when = round((time.time() - start) * 1000)
    measure_cpu(when)
    measure_memory(when)
    measure_heap(when, True)


def stop_tracing():
    global output, tracing
    tracing = False
    threading.setprofile(None)
    sys.setprofile(None)
    flush_counters()
    if buffer and output:
        flush()
        print("Pynsights: tracing finished. Traced %d calls. See" % call_count, output_filename)
        output = None


def annotate(message, event=EVENT_ANNOTATE):
    now = time.time()
    when = round((now - start) * 1000)
    record(when, "%s %s\n" % (event, message))


def annotate_enter(func):
    annotate(str(func), EVENT_ENTER)


def annotate_exit(func):
    annotate(str(func), EVENT_EXIT)


def trace(func):
    """Decorates a function to record its execution."""

    @functools.wraps(func)
    def tracefunc_closure(*args, **kwargs):
        method = "%s.%s" % (func.__module__, func.__name__)
        annotate_enter(method)
        result = func(*args, **kwargs)
        annotate_exit(method)
        return result

    return tracefunc_closure

class Recorder(object):
    def __init__(self, file=None):
        global output_filename
        if file:
            output_filename = os.path.expanduser(file)

    def __enter__(self):
        start_tracing()

    def __exit__(self, type, value, traceback):
        stop_tracing()


atexit.register(stop_tracing)
