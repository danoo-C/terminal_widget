"""Windows PTY backend, built on pywinpty (ConPTY).

Untested on this machine -- written against the pywinpty API and needs a
run on real Windows before it can be trusted.
"""

from __future__ import annotations

import os

from .base import PtyBackend


class WindowsPty(PtyBackend):
    def __init__(self) -> None:
        self._proc = None

    def spawn(
        self,
        argv: list[str],
        cols: int,
        rows: int,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        # Imported late, and unresolvable off Windows: pywinpty only installs
        # there, so a type checker running on Linux cannot see it.
        from winpty import PtyProcess  # type: ignore[import-not-found]

        env = env or dict(os.environ)
        # A list, never a joined string. Given a str, pywinpty re-splits it
        # with shlex, which keeps the quote characters inside the tokens, and
        # then hands them to list2cmdline, which escapes them into the child's
        # argv as literal quotes. Given a list it skips shlex entirely, and
        # list2cmdline is the exact inverse of the parser config.py used.
        self._proc = PtyProcess.spawn(
            list(argv), cwd=cwd, dimensions=(rows, cols), env=env
        )

    def read(self, size: int = 4096) -> str:
        if self._proc is None:
            raise EOFError("not spawned")
        try:
            data = self._proc.read(size)
        except EOFError:
            raise
        except OSError as exc:
            raise EOFError(str(exc)) from exc
        if not data and not self._proc.isalive():
            raise EOFError("shell exited")
        return data

    def write(self, data: str) -> None:
        if self._proc is not None and self._proc.isalive():
            try:
                self._proc.write(data)
            except (OSError, EOFError):
                pass

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
        if self._proc is None:
            return None
        try:
            # Unlike ptyprocess, pywinpty reads this straight off the PTY
            # rather than caching it in isalive(), so it needs no reap first.
            return self._proc.exitstatus
        except Exception:
            return None  # WinptyError is not an OSError, and this is a Qt slot

    def terminate(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate(force=True)
            except OSError:
                pass
            self._proc = None
