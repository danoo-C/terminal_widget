"""Pseudo-terminal backends.

Linux and Windows have entirely different PTY mechanisms, so each gets its
own module behind one interface. Both deal in ``str``, not bytes: decoding
is the backend's problem, because only it knows how to hold a partial
multi-byte character across reads.
"""

from __future__ import annotations

import sys

from .base import PtyBackend

__all__ = ["PtyBackend", "open_pty"]


def open_pty() -> PtyBackend:
    """Construct the PTY backend for the current platform."""
    if sys.platform == "win32":
        from .windows import WindowsPty

        return WindowsPty()
    from .posix import PosixPty

    return PosixPty()
