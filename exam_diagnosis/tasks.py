"""In-process background task queue.

Parsing an exam (vision calls per page) and generating a report (several model calls, tens of
seconds on a reasoning-tier model) were both synchronous request handlers: the HTTP request sat
open for the whole run, a browser or proxy timeout killed nothing server-side but left the client
with no answer, and nothing could report progress. This queue moves that work onto worker
threads and hands the client a task id to poll.

Deliberately in-process (a ThreadPoolExecutor, no Redis/Celery): this is a local single-user
app, and a broker would be another daemon to install and keep running for no gain at this scale.
Task records live in the same SQLite-backed mapping as everything else, so a task's outcome
survives a restart even though the worker thread doesn't — see `recover_orphaned` for how a
restart mid-task is reported instead of leaving the task 'running' forever.
"""
from __future__ import annotations

import threading
from collections.abc import Callable, MutableMapping
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from typing import Any
from uuid import uuid4

ACTIVE_STATUSES = ('queued', 'running')


def _now() -> str:
    return datetime.utcnow().isoformat() + 'Z'


class TaskQueue:
    def __init__(self, store: MutableMapping[str, dict[str, Any]], workers: int = 1, after_task: Callable[[], None] | None = None):
        self.store = store
        self.workers = max(1, workers)
        # Called after every task, success or failure. The app uses it to flush the SQLite
        # mappings: the per-request middleware that normally does that never runs for work done
        # on a worker thread, so without this a task's results would sit in the in-memory cache
        # until some unrelated request happened to trigger a flush.
        self.after_task = after_task
        self._executor = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix='exam-diagnosis-task')
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, fn: Callable[[], Any], *, user_id: str, **context: Any) -> dict[str, Any]:
        """Queue `fn` and return the task record. `context` (job_id, run_id, ...) is stored on
        the record so callers can find the task for a given resource with `active_for`."""
        task_id = str(uuid4())
        task = {
            'id': task_id, 'kind': kind, 'user_id': user_id, 'status': 'queued',
            'error': None, 'result': None,
            'created_at': _now(), 'started_at': None, 'finished_at': None,
            **context,
        }
        self.store[task_id] = task
        with self._lock:
            self._futures[task_id] = self._executor.submit(self._run, task_id, fn)
        return task

    def _run(self, task_id: str, fn: Callable[[], Any]) -> None:
        task = self.store[task_id]
        task['status'] = 'running'
        task['started_at'] = _now()
        self.store[task_id] = task
        try:
            task['result'] = fn()
            task['status'] = 'completed'
        except Exception as exc:  # noqa: BLE001 — a task's failure must land on the record, never kill the worker
            task['status'] = 'failed'
            task['error'] = f'{type(exc).__name__}: {exc}'
        finally:
            task['finished_at'] = _now()
            self.store[task_id] = task
            if self.after_task is not None:
                try:
                    self.after_task()
                except Exception:  # noqa: BLE001
                    pass
            with self._lock:
                self._futures.pop(task_id, None)

    def get(self, task_id: str) -> dict[str, Any] | None:
        return self.store.get(task_id)

    def active_for(self, **match: Any) -> dict[str, Any] | None:
        """The queued/running task whose record carries all of `match` (e.g. job_id=...), if any."""
        for task in self.store.values():
            if task.get('status') in ACTIVE_STATUSES and all(task.get(key) == value for key, value in match.items()):
                return task
        return None

    def wait(self, task_id: str, timeout: float | None = None) -> dict[str, Any]:
        """Block until the task finishes (tests, and callers that want the old synchronous feel)."""
        with self._lock:
            future = self._futures.get(task_id)
        if future is not None:
            future.result(timeout=timeout)
        return self.store[task_id]

    def recover_orphaned(self) -> list[dict[str, Any]]:
        """Mark every task that was queued or running when the process last died as failed.

        Worker threads don't survive a restart, so any task record still 'running' at startup
        is a lie; saying so is the only honest option — the caller decides what to do about
        the job/run the task was working on. Returns the tasks that were marked."""
        recovered = []
        for task_id in list(self.store):
            task = self.store.get(task_id)
            if not task or task.get('status') not in ACTIVE_STATUSES:
                continue
            task['status'] = 'failed'
            task['error'] = 'process_restarted'
            task['finished_at'] = _now()
            self.store[task_id] = task
            recovered.append(task)
        return recovered
