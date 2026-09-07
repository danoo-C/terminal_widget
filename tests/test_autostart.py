"""Autostart, against a redirected XDG_CONFIG_HOME so the real one is safe."""

from pathlib import Path

import pytest

from terminal_widget.autostart.xdg import XdgAutostart, entry_path

EXECUTABLE = Path("/opt/terminal-widget/venv/bin/terminal-widget")


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return XdgAutostart()


def test_disabled_before_anything_happens(xdg):
    assert not xdg.is_enabled()


def test_enable_then_enabled(xdg):
    xdg.enable(EXECUTABLE)
    assert xdg.is_enabled()


def test_entry_uses_an_absolute_path(xdg):
    """Autostart runs with a minimal environment; PATH will not contain the venv."""
    xdg.enable(EXECUTABLE)
    exec_line = next(
        line for line in entry_path().read_text().splitlines()
        if line.startswith("Exec=")
    )
    assert str(EXECUTABLE) in exec_line
    assert Path(exec_line.split("=", 1)[1].strip('"')).is_absolute()


def test_enable_twice_leaves_one_entry(xdg):
    xdg.enable(EXECUTABLE)
    xdg.enable(EXECUTABLE)
    assert len(list(entry_path().parent.iterdir())) == 1


def test_disable_removes_it(xdg):
    xdg.enable(EXECUTABLE)
    xdg.disable()
    assert not xdg.is_enabled()
    assert not entry_path().exists()


def test_disable_when_not_enabled_is_not_an_error(xdg):
    xdg.disable()
    xdg.disable()


def test_reflects_a_file_removed_behind_its_back(xdg):
    """The OS is the source of truth, so deleting the file must show through."""
    xdg.enable(EXECUTABLE)
    entry_path().unlink()
    assert not xdg.is_enabled()


def test_hidden_true_counts_as_disabled(xdg):
    """Some desktop environments disable an entry by rewriting it."""
    xdg.enable(EXECUTABLE)
    entry_path().write_text(entry_path().read_text() + "Hidden=true\n")
    assert not xdg.is_enabled()


def test_gnome_disabled_flag_counts_as_disabled(xdg):
    xdg.enable(EXECUTABLE)
    text = entry_path().read_text().replace(
        "X-GNOME-Autostart-enabled=true", "X-GNOME-Autostart-enabled=false"
    )
    entry_path().write_text(text)
    assert not xdg.is_enabled()


def test_enable_creates_the_directory(xdg):
    assert not entry_path().parent.exists()
    xdg.enable(EXECUTABLE)
    assert entry_path().parent.is_dir()


def test_paths_with_spaces_are_quoted(xdg):
    xdg.enable(Path("/opt/my apps/terminal-widget"))
    exec_line = next(
        line for line in entry_path().read_text().splitlines()
        if line.startswith("Exec=")
    )
    assert '"' in exec_line


def test_wsl_is_reported_unsupported(xdg, monkeypatch):
    monkeypatch.setattr("terminal_widget.autostart.xdg.is_wsl", lambda: True)
    supported, reason = xdg.is_supported()
    assert not supported
    assert "WSL" in reason
