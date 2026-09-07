"""A pyte screen that keeps the lines which scroll off the top.

The widget has no scrollbar and no chrome to put one in, so scrollback is a
viewport over one absolute row space rather than a piece of UI. Every row the
terminal has ever shown has a number: :attr:`ScrollbackScreen.history_total`
counts the rows that have scrolled off, and is therefore the absolute number
of the top row of the live screen.

Everything that has to remember a place -- the viewport, a selection -- stores
absolute rows. That one decision is what keeps them correct across scrolling,
new output and resizing without any bookkeeping: the rows do not renumber, so
an anchor that pointed at a line still points at that line.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Mapping

import pyte
from pyte.screens import Char, Margins

#: DEC private modes meaning "an application has taken over the screen".
#: pyte has no alternate screen buffer, so a full-screen program's redraws land
#: in the normal buffer; without this guard every frame vim, less or htop paints
#: would pour into the scrollback and bury the shell history that belongs there.
#: pyte stores private modes shifted left by five bits -- same as DECCKM in
#: keys.py.
_ALT_SCREEN = frozenset({1049 << 5, 1047 << 5, 47 << 5})

#: Ceiling on the character-interning pool, so output that changes colour on
#: every cell cannot grow it without bound.
_POOL_LIMIT = 4096


@dataclass(frozen=True, order=True)
class Pos:
    """A character boundary: an absolute row, and a column between cells.

    ``col`` runs 0..columns inclusive -- it names a boundary, not a cell, so a
    selection can end after the last character. Ordering is lexicographic,
    which is exactly the order text is read in.
    """

    row: int
    col: int


class ScrollbackScreen(pyte.Screen):
    """A screen that remembers what scrolls past the top of it.

    Deliberately not :class:`pyte.HistoryScreen`. That class wraps every
    stream event through ``__getattribute__``, which runs on every attribute
    access on the screen -- including ``self.buffer`` inside the draw loop --
    and measured 2.6x slower than plain ``Screen`` on the same input. Its
    paging API is destructive besides: ``prev_page`` mutates the live buffer,
    and it snaps back to the bottom on every event.
    """

    def __init__(self, columns: int, lines: int, scrollback: int = 0) -> None:
        # These must exist before super().__init__, which calls reset() --
        # and reset() is overloaded here to clear the history.
        self.history: deque[Mapping[int, Char]] = deque(maxlen=max(0, int(scrollback)))
        self.history_total = 0
        self._pool: dict[Char, Char] = {}
        super().__init__(columns, lines)

    # -- Absolute row space ------------------------------------------

    @property
    def first_row(self) -> int:
        """The oldest row still retrievable."""
        return self.history_total - len(self.history)

    @property
    def last_row(self) -> int:
        """The bottom row of the live screen."""
        return self.history_total + self.lines - 1

    def line_at(self, row: int) -> Mapping[int, Char] | None:
        """The line at an absolute row, or None once it has been evicted."""
        j = row - self.first_row
        if 0 <= j < len(self.history):
            return self.history[j]
        y = row - self.history_total
        if 0 <= y < self.lines:
            return self.buffer[y]
        return None

    def text_between(self, start: Pos, end: Pos) -> str:
        """The text of the half-open range ``[start, end)``.

        Rows that have fallen out of the scrollback are skipped rather than
        raising: a selection outliving the lines it covers should shrink, not
        explode.
        """
        if end <= start:
            return ""
        rows = []
        for row in range(start.row, end.row + 1):
            line = self.line_at(row)
            if line is None:
                continue
            c0 = 0 if row != start.row else start.col
            c1 = self.columns if row != end.row else end.col
            c0 = max(0, min(c0, self.columns))
            c1 = max(0, min(c1, self.columns))
            # A wide glyph is stored in one cell with "" in the next, so
            # joining .data gives one character per glyph for free.
            rows.append("".join(line[x].data for x in range(c0, c1)).rstrip())
        return "\n".join(rows)

    # -- History -----------------------------------------------------

    def set_scrollback(self, scrollback: int) -> None:
        """Change how many lines are kept; deque.maxlen is read-only."""
        n = max(0, int(scrollback))
        if n == self.history.maxlen:
            return
        self.history = deque(self.history, maxlen=n)  # keeps the newest n

    def _push(self, line: Mapping[int, Char]) -> None:
        """Retire one line into the history.

        ``history_total`` counts rows that have scrolled off whether or not
        we kept them, so absolute row numbers stay meaningful even with
        scrollback turned off.
        """
        if self.history.maxlen:
            # Char is a hashable NamedTuple and a retired line is frozen, so
            # identical cells can share one object. Measured on 5000x70:
            # 56.7 MB -> 14.6 MB.
            pool = self._pool
            if len(pool) > _POOL_LIMIT:
                pool.clear()
            for x, char in tuple(line.items()):
                line[x] = pool.setdefault(char, char)  # type: ignore[index]
            self.history.append(line)
        self.history_total += 1

    # -- pyte overloads ----------------------------------------------

    def index(self) -> None:
        """Overloaded to keep the line that scrolls off the top.

        Only a full-screen scroll feeds the history, which is the rule xterm
        uses: a scrolling region is an application redrawing part of its own
        display, not the terminal moving on.
        """
        top, bottom = self.margins or Margins(0, self.lines - 1)
        whole_screen = top == 0 and bottom == self.lines - 1
        if (
            self.cursor.y == bottom
            and whole_screen
            and self.mode.isdisjoint(_ALT_SCREEN)
        ):
            # Safe to hand the object over: Screen.index() immediately
            # rebinds buffer[y] = buffer[y + 1] all the way down and pops
            # the bottom, so this line is unreachable from the buffer
            # afterwards and can never be mutated again.
            self._push(self.buffer[top])
        super().index()

    def resize(self, lines: int | None = None, columns: int | None = None) -> None:
        """Overloaded so shrinking the window does not throw lines away.

        pyte drops rows from the top with ``delete_lines``, which bypasses
        ``index`` and would never reach the history.
        """
        if lines is not None and lines < self.lines:
            for y in range(self.lines - lines):
                self._push(self.buffer[y])
        super().resize(lines, columns)

    def reset(self) -> None:
        """Overloaded to drop the history -- but never the row count.

        ``history_total`` is monotonic by contract. It is what every viewport
        and selection anchor is expressed against, so resetting it would
        silently relabel every position anyone is holding.
        """
        super().reset()
        self.history.clear()

    def erase_in_display(self, how: int = 0, *args: Any, **kwargs: Any) -> None:
        """Overloaded so ``ED 3`` clears the scrollback, as it is meant to."""
        super().erase_in_display(how, *args, **kwargs)
        if how == 3:
            self.history.clear()
