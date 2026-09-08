"""Linux/macOS PTY backend, built on ptyprocess."""

from __future__ import annotations

import os

from ptyprocess import PtyProcessUnicode

from .base import PtyBackend


class PosixPty(PtyBackend):
    def __init__(self) -> None:
        self._proc: PtyProcessUnicode | None = None

    def spawn(
        self,
        argv: list[str],
        cols: int,
        rows: int,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        self._proc = PtyProcessUnicode.spawn(
            argv, cwd=cwd, dimensions=(rows, cols), env=env or dict(os.environ)
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

    @property
    def exit_status(self) -> int | None:
        proc = self._proc
        if proc is None:
            return None
        try:
            # ptyprocess sets exitstatus inside isalive(), as it reaps the
            # child; asking without that call finds it still None after the
            # exit. PtyProcessError is not an OSError, and this runs in a Qt
            # slot, so the guard is deliberately broad: a status we cannot
            # read is a missing detail, never a reason to take the widget down.
            proc.isalive()
            return proc.exitstatus
        except Exception:
            return None

    def terminate(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate(force=True)
            except OSError:
                pass
            self._proc = None
