import logging
import sys
from contextlib import contextmanager
from typing import Callable, Generator, Optional

from rich.console import Console
from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

LOGGER = logging.getLogger("next_pass")


def _progress_enabled() -> bool:
    """Enable the live bar only on a real interactive terminal.

    ``sys.__stdout__`` is used (not ``sys.stdout``) because main() replaces
    ``sys.stdout``/``sys.stderr`` with a Tee that mirrors to a log file; ANSI
    control codes from the live bar must never reach that log file.
    """
    stream = sys.__stdout__
    try:
        return bool(stream) and stream.isatty()
    except Exception:
        return False


@contextmanager
def overpass_progress(
    num_satellites: int,
) -> Generator["ProgressController", None, None]:
    """Context manager yielding a :class:`ProgressController`.

    When disabled (non-TTY), yields a no-op controller so callers stay
    agnostic and existing logging is untouched.
    """
    if not _progress_enabled() or num_satellites <= 0:
        yield ProgressController(None, None)
        return

    console = Console(file=sys.__stdout__)

    # Route logging through rich so warnings/errors surface cleanly above the
    # live region instead of shredding it. Restored on exit.
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    rich_handler = RichHandler(console=console, show_path=False, rich_tracebacks=True)
    root.handlers = [rich_handler]

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )
    master = progress.add_task("Overpasses", total=num_satellites)
    try:
        with progress:
            yield ProgressController(progress, master)
    finally:
        root.handlers = saved_handlers


class ProgressController:
    """Owns the master task and hands out per-satellite sub-tasks."""

    def __init__(self, progress: Optional[Progress], master_task):
        self._progress = progress
        self._master = master_task

    @contextmanager
    def satellite(self, name: str) -> Generator[Callable[[str], None], None, None]:
        """Add a sub-task for one satellite, yield its ``step_cb`` callable.

        The callable updates the sub-task description. On exit the sub-task is
        removed and the master bar advances by one. When disabled, yields a
        no-op callable and does nothing else.
        """
        if self._progress is None:
            yield lambda label: None
            return

        task_id = self._progress.add_task(f"{name}: starting", total=None)

        def step_cb(label: str) -> None:
            self._progress.update(task_id, description=f"{name}: {label}")

        try:
            yield step_cb
        finally:
            self._progress.remove_task(task_id)
            self._progress.update(self._master, advance=1)
