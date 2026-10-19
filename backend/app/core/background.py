"""Running work after a response has been sent.

The MVP runs ingestion in this process, on the event loop, because the job is
minutes of CPU at worst and a queue would be infrastructure bought before the
problem exists.

What matters is that the *boundary* survives that decision. Callers submit a
unit of work to a :class:`TaskRunner` and learn nothing about where it runs, so
replacing this with a distributed worker is implementing the protocol again --
not rewriting the service. The honest limits of the in-process version are
written down in :class:`BackgroundTaskRunner` rather than discovered later.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Final, Protocol

logger: Final = logging.getLogger(__name__)

#: A unit of deferred work. A factory rather than a coroutine so that nothing
#: is started until the runner decides to start it -- an un-awaited coroutine
#: handed to a runner that never runs it is a warning and a silent no-op.
TaskFactory = Callable[[], Awaitable[None]]


class TaskRunner(Protocol):
    """Somewhere for work to happen after the request is over."""

    async def submit(self, name: str, factory: TaskFactory) -> None:
        """Arrange for ``factory`` to be run.

        Returns once the work is *accepted*, which is not the same as
        finished: an implementation may run it inline, on this loop, or on
        another machine.
        """
        ...


class BackgroundTaskRunner:
    """Runs work as asyncio tasks in this process.

    Deliberately simple, with limits worth stating plainly:

    * **Work does not survive a restart.** A job interrupted mid-flight stays
      in whatever state it had reached, which is why the pipeline records its
      stage as it goes and why a job can be retried.
    * **No cross-process coordination.** Two API processes could in principle
      run the same job; the one-active-job-per-document index in Postgres is
      what actually prevents duplicate work, not this class.
    * **No backpressure.** Submissions are not bounded, so a flood of uploads
      competes for one event loop.

    Each of those is a reason to move to a real queue, and none of them is a
    reason to have built one yet.
    """

    def __init__(self) -> None:
        # Strong references, because asyncio only holds weak ones: a task
        # nobody keeps can be garbage collected mid-run.
        self._tasks: set[asyncio.Task[None]] = set()

    async def submit(self, name: str, factory: TaskFactory) -> None:
        task = asyncio.create_task(self._guard(name, factory), name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @staticmethod
    async def _guard(name: str, factory: TaskFactory) -> None:
        """Run the work, making sure a failure is never silent.

        An exception in a background task that nobody awaits is reported only
        when the task is garbage collected, if at all. The pipeline records its
        own failures in the job row; this is the net under that.
        """
        try:
            await factory()
        except asyncio.CancelledError:
            logger.info("Background task %s cancelled", name)
            raise
        except Exception:
            logger.exception("Background task %s failed", name)

    @property
    def pending(self) -> int:
        return len(self._tasks)

    async def drain(self, timeout_seconds: float = 30.0) -> None:
        """Wait for in-flight work, for use at shutdown.

        Gives running jobs a chance to finish and record their outcome instead
        of being killed mid-stage and leaving a row that says 'running'
        forever.
        """
        if not self._tasks:
            return
        logger.info("Waiting for %d background task(s)", len(self._tasks))
        _done, pending = await asyncio.wait(set(self._tasks), timeout=timeout_seconds)
        if pending:
            logger.warning("%d background task(s) still running at shutdown", len(pending))


class InlineTaskRunner:
    """Runs work immediately, before :meth:`submit` returns.

    For tests, and for any caller that wants the pipeline to be synchronous.
    Keeping it here rather than in the test suite means the protocol has two
    real implementations, which is what keeps it honest.
    """

    async def submit(self, name: str, factory: TaskFactory) -> None:
        await factory()
