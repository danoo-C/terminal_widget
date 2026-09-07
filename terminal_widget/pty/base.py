"""The interface every PTY backend implements."""

from __future__ import annotations

from abc import ABC, abstractmethod


class PtyBackend(ABC):
    """A shell running on a pseudo-terminal.

    Reads return decoded text. ``read`` blocks until data is available and
    raises :class:`EOFError` once the shell has exited and its output is
    drained -- that is the signal the session has ended.
    """

    @abstractmethod
    def spawn(
        self,
        argv: list[str],
        cols: int,
        rows: int,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        """Start ``argv`` on a PTY sized ``cols`` x ``rows``.

        ``cwd`` is the directory to start in; None lets the shell choose.
        """

    @abstractmethod
    def read(self, size: int = 4096) -> str:
        """Block until output is available; raise EOFError when finished."""

    @abstractmethod
    def write(self, data: str) -> None:
        """Send input to the shell."""

    @abstractmethod
    def resize(self, cols: int, rows: int) -> None:
        """Tell the shell its window changed size."""

    @abstractmethod
    def is_alive(self) -> bool:
        """Whether the shell process is still running."""

    @abstractmethod
    def terminate(self) -> None:
        """Stop the shell, forcefully if it does not exit on its own."""
