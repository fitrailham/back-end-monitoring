"""Cooperative deadlines for scheduler work. Normal API requests have no job context."""
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic


class JobInterrupted(Exception):
    pass


class JobDeadlineExceeded(JobInterrupted):
    pass


class ShutdownRequested(JobInterrupted):
    pass


class WorkerConnectionLost(JobInterrupted):
    pass


_budget = ContextVar("scheduler_job_budget", default=None)


@contextmanager
def job_budget(stop, seconds):
    token = _budget.set((stop, monotonic() + seconds))
    try:
        remaining_time()
        yield
        remaining_time()
    finally:
        _budget.reset(token)


@contextmanager
def allow_job_audit():
    # The audit update has its own PostgreSQL statement timeout.
    token = _budget.set(None)
    try:
        yield
    finally:
        _budget.reset(token)


def remaining_time():
    budget = _budget.get()
    if budget is None:
        return None
    stop, deadline = budget
    if stop.is_set():
        raise ShutdownRequested("Scheduler dihentikan saat job aktif.")
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise JobDeadlineExceeded("Job mencapai batas waktu eksekusi.")
    return remaining
