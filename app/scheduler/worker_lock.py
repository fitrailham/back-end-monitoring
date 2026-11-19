"""Hold an OS file lock for the lifetime of one scheduler in this checkout."""

from contextlib import contextmanager
import errno
import os
from pathlib import Path

from app.database import open_db, use_worker_connection


class SchedulerAlreadyRunning(RuntimeError):
    pass


@contextmanager
def postgres_scheduler_lock(statement_timeout_seconds):
    """Hold the PostgreSQL lease on the same session used for scheduler writes."""
    with open_db() as connection:
        connection.autocommit = True
        connection.execute("SELECT set_config('statement_timeout', %s, false)",
                           (f"{statement_timeout_seconds}s",))
        acquired = connection.execute(
            "SELECT pg_try_advisory_lock(%s, %s) AS acquired", (0x4943474E, 1)
        ).fetchone()["acquired"]
        if not acquired:
            raise SchedulerAlreadyRunning("Scheduler lain sedang berjalan pada database ini")
        with use_worker_connection(connection, statement_timeout_seconds):
            yield


@contextmanager
def scheduler_lock(path: Path):
    # Do not delete the lock file: OS locks are released automatically on process exit.
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise
            raise SchedulerAlreadyRunning("Scheduler lain sedang berjalan untuk proyek ini") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
