"""The interface every autostart backend implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class AutostartBackend(ABC):
    """Enables or disables launching the widget when the user logs in.

    The operating system is the single source of truth. There is no
    ``autostart`` field in the config file, deliberately: two records of the
    same fact drift apart, and then the settings checkbox lies.
    """

    @abstractmethod
    def is_supported(self) -> tuple[bool, str]:
        """(supported, reason). The reason is shown when not supported."""

    @abstractmethod
    def is_enabled(self) -> bool:
        """Whether an autostart entry currently exists."""

    @abstractmethod
    def enable(self, executable: Path, args: list[str] | None = None) -> None:
        """Create or replace the entry. Safe to call when already enabled."""

    @abstractmethod
    def disable(self) -> None:
        """Remove the entry. Safe to call when already disabled."""

    @abstractmethod
    def describe(self) -> str:
        """Where the entry lives, for display in the settings app."""
