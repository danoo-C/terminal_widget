"""Linux/macOS PTY backend, built on ptyprocess."""

from __future__ import annotations

import os

from ptyprocess import PtyProcessUnicode

from .base import PtyBackend


class PosixPty(PtyBackend):
    def __init__(self) -> None:
        self._proc: PtyProcessUnicode | None = None

    def spawn(self, argv: list[str], cols: int, rows: int) -> None:
        env = dict(os.environ)
        # Advertise a terminal we can actually emulate, and drop any
        # inherited size so the shell asks the PTY instead.
        env["TERM"] = "xterm-256color"
        env.pop("LINES", None)
        env.pop("COLUMNS", None)
        self._proc = PtyProcessUnicode.spawn(
            argv, dimensions=(rows, cols), env=env
        )

    def read(self, size: int = 4096) -> str:
        if self._proc is None:
            raise EOFError("not spawned")
        try:
            return self._proc.read(size)
        except EOFError:
            raise
        except OSError as exc:
            # The slave side closing surfaces as EIO on Linux; that is a
            # normal end-of-session, not a failure.
            raise EOFError(str(exc)) from exc

    def write(self, data: str) -> None:
        if self._proc is not None and self._proc.isalive():
            try:
                self._proc.write(data)
            except (OSError, EOFError):
                pass  # shell exited between our check and the write

    def resize(self, cols: int, rows: int) -> None:
        if self._proc is not None and self._proc.isalive():
            try:
                self._proc.setwinsize(rows, cols)
            except OSError:
                pass

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.isalive()

    def terminate(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate(force=True)
            except OSError:
                pass
            self._proc = None
