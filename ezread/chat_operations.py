"""Scoped cancellation and last reported context usage; never stores paper content."""
import json
import threading
from pathlib import Path

_guard = threading.Lock()
_active = {}


class Cancellation:
    def __init__(self, shutdown):
        self.local = threading.Event()
        self.shutdown = shutdown

    def is_set(self):
        return self.local.is_set() or self.shutdown.is_set()


def begin(pid, request_id, shutdown):
    with _guard:
        if pid in _active:
            raise ValueError('这篇论文已有进行中的操作。')
        event = Cancellation(shutdown)
        _active[pid] = (request_id, event)
        return event


def stop(pid, request_id):
    with _guard:
        entry = _active.get(pid)
        if not entry or entry[0] != request_id:
            return False
        entry[1].local.set()
        return True


def finish(pid, request_id):
    with _guard:
        if pid in _active and _active[pid][0] == request_id:
            del _active[pid]


def usage_path(library, pid, generation):
    return Path(library) / pid / f'chat-usage-{int(generation)}.json'


def read_usage(library, pid, generation, thread_id):
    try:
        value = json.loads(usage_path(library,pid,generation).read_text(encoding='utf-8'))
        return value['usage'] if value['thread_id'] == thread_id else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_usage(library, pid, generation, thread_id, usage):
    path = usage_path(library,pid,generation)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'thread_id':thread_id,'usage':usage}),encoding='utf-8')
    temporary.replace(path)
