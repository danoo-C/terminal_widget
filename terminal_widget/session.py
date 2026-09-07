"""The shell session: a PTY, a VT emulator, and the thread that pumps them.

Threading rule for this module: the PTY is read on a background thread, but
the pyte screen is only ever touched on the GUI thread. Bytes cross the
boundary as a queued Qt signal, so the renderer never has to lock anything
while painting.
"""

from __future__ import annotations

import pyte
from PySide6.QtCore import QObject, QThread, Signal

from .pty import PtyBackend, open_pty
from .screen import ScrollbackScreen

#: Sane floor so a mis-measured font can never ask for a 0-column screen.
MIN_COLS = 8
MIN_ROWS = 2


class _ReaderThread(QThread):
    """Blocks on PTY reads and hands the text to the GUI thread."""

    dataReady = Signal(str)
    finished_reading = Signal()

    def __init__(self, backend: PtyBackend, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend = backend
        self._stopping = False

    def run(self) -> None:
        while not self._stopping:
            try:
                data = self._backend.read()
            except EOFError:
                break
            except Exception:
                break
            if data:
                self.dataReady.emit(data)
        if not self._stopping:
            self.finished_reading.emit()

    def stop(self) -> None:
        """Ask the loop to exit; the blocking read unblocks when the PTY closes."""
        self._stopping = True


class TerminalSession(QObject):
    """One shell, its screen, and the plumbing between them."""

    #: The screen changed and wants repainting.
    screenUpdated = Signal()
    #: The shell exited.
    ended = Signal()

    def __init__(
        self,
        cols: int,
        rows: int,
        scrollback: int = 0,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        cols, rows = max(MIN_COLS, cols), max(MIN_ROWS, rows)
        self.screen = ScrollbackScreen(cols, rows, scrollback)
        self.stream = pyte.Stream(self.screen)
        self._backend: PtyBackend | None = None
        self._reader: _ReaderThread | None = None
        self._alive = False

    @property
    def cols(self) -> int:
        return self.screen.columns

    @property
    def rows(self) -> int:
        return self.screen.lines

    @property
    def alive(self) -> bool:
        return self._alive

    def start(
        self,
        argv: list[str],
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        """Spawn ``argv`` and begin pumping its output into the screen."""
        self._backend = open_pty()
        self._backend.spawn(argv, self.cols, self.rows, env, cwd)
        self._alive = True

        self._reader = _ReaderThread(self._backend, self)
        self._reader.dataReady.connect(self._on_data)
        self._reader.finished_reading.connect(self._on_ended)
        self._reader.start()

    def _on_data(self, text: str) -> None:
        # GUI thread: safe to touch the screen.
        self.stream.feed(text)
        self.screenUpdated.emit()

    def _on_ended(self) -> None:
        self._alive = False
        self.ended.emit()

    def write(self, data: str) -> None:
        if self._backend is not None:
            self._backend.write(data)

    def set_scrollback(self, scrollback: int) -> None:
        """Change how many lines of scrollback are kept, without a restart."""
        self.screen.set_scrollback(scrollback)

    def resize(self, cols: int, rows: int) -> None:
        """Resize screen and PTY together, so the shell redraws correctly."""
        cols, rows = max(MIN_COLS, cols), max(MIN_ROWS, rows)
        if (cols, rows) == (self.cols, self.rows):
            return
        self.screen.resize(rows, cols)
        if self._backend is not None:
            self._backend.resize(cols, rows)
        self.screenUpdated.emit()

    def stop(self) -> None:
        """Tear the session down; safe to call more than once."""
        if self._reader is not None:
            self._reader.stop()
        if self._backend is not None:
            self._backend.terminate()
            self._backend = None
        if self._reader is not None:
            self._reader.wait(1000)
            self._reader = None
        self._alive = False
