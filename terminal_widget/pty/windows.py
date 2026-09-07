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
        # pywinpty takes a command line, not an argv list. Anything needing
        # quoting should come through config as a "custom" command already
        # shaped the way Windows expects.
        command = argv[0] if len(argv) == 1 else " ".join(argv)
        self._proc = PtyProcess.spawn(
            command, cwd=cwd, dimensions=(rows, cols), env=env
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

    def terminate(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate(force=True)
            except OSError:
                pass
            self._proc = None
