import json

from terminal_widget.config import (
    OPACITY_BACKGROUND,
    Config,
    resolve_shell,
)


def test_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    cfg = Config(x=10, y=20, width=800, height=600, opacity=55)
    cfg.save(path)
    assert Config.load(path) == cfg


def test_unknown_keys_are_ignored(tmp_path):
    """A config written by a newer version must not crash an older widget."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"x": 5, "from_the_future": True}))
    assert Config.load(path).x == 5


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    """The widget must always start; a broken config is not fatal."""
    path = tmp_path / "config.json"
    path.write_text("{ this is not json")
    assert Config.load(path) == Config()


def test_missing_file_falls_back_to_defaults(tmp_path):
    assert Config.load(tmp_path / "absent.json") == Config()


def test_clamping_rejects_nonsense():
    c = Config(width=1, height=1, opacity=9999, opacity_mode="sideways").clamped()
    assert c.width >= 120 and c.height >= 80
    assert c.opacity == 100
    assert c.opacity_mode == OPACITY_BACKGROUND


def test_save_is_atomic(tmp_path):
    """No .tmp file should survive a successful save."""
    path = tmp_path / "config.json"
    Config().save(path)
    assert path.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_resolve_shell_always_returns_something():
    assert resolve_shell(Config(shell="definitely-not-a-shell"))


def test_custom_command_is_split():
    argv = resolve_shell(Config(shell="custom", custom_command="bash -l"))
    assert argv == ["bash", "-l"]


def test_blank_custom_command_falls_back():
    """An empty custom command must not spawn an empty argv."""
    assert resolve_shell(Config(shell="custom", custom_command="   "))
