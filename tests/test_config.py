import json
from pathlib import Path

from terminal_widget.config import (
    OPACITY_BACKGROUND,
    OPACITY_WINDOW,
    STACKING_CHOICES,
    STACKING_DESKTOP,
    STACKING_TOP,
    Config,
    resolve_shell,
    working_dir_for,
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


def test_separate_history_redirects_histfile():
    """Commands typed into the widget must not rewrite the login shell's
    history file."""
    from terminal_widget.config import environment_for, history_path

    env = environment_for(Config(separate_history=True))
    assert env["HISTFILE"] == str(history_path())


def test_shared_history_leaves_histfile_alone():
    from terminal_widget.config import environment_for

    env = environment_for(Config(separate_history=False))
    assert "HISTFILE" not in env or env.get("HISTFILE") != str(
        __import__("terminal_widget.config", fromlist=["x"]).history_path()
    )


def test_environment_announces_a_terminal_we_emulate():
    from terminal_widget.config import environment_for

    assert environment_for(Config())["TERM"] == "xterm-256color"


def test_environment_drops_stale_size():
    """LINES/COLUMNS inherited from the launching terminal would override
    the PTY's real size."""
    import os

    from terminal_widget.config import environment_for

    os.environ["LINES"] = "99"
    os.environ["COLUMNS"] = "99"
    try:
        env = environment_for(Config())
        assert "LINES" not in env and "COLUMNS" not in env
    finally:
        os.environ.pop("LINES", None)
        os.environ.pop("COLUMNS", None)


# -- Opacity floors ---------------------------------------------------


def test_background_mode_allows_full_transparency():
    """Background-only fades nothing but the backdrop, so 0 is the point of
    it: glyphs left floating on the desktop."""
    cfg = Config(opacity=0, opacity_mode=OPACITY_BACKGROUND).clamped()
    assert cfg.opacity == 0


def test_window_mode_stops_before_the_widget_disappears():
    cfg = Config(opacity=0, opacity_mode=OPACITY_WINDOW).clamped()
    assert cfg.opacity == 10


def test_a_nonsense_mode_falls_back_before_the_floor_is_applied():
    """The floor depends on the mode, so clamping in the other order would
    hold a background-mode config at 10%."""
    cfg = Config(opacity=0, opacity_mode="sideways").clamped()
    assert cfg.opacity_mode == OPACITY_BACKGROUND
    assert cfg.opacity == 0


# -- Scrollback -------------------------------------------------------


def test_scrollback_clamps_to_its_range():
    assert Config(scrollback=-5).clamped().scrollback == 0
    assert Config(scrollback=10**9).clamped().scrollback == 50000


# -- Working directory ------------------------------------------------


def test_working_dir_defaults_to_the_shells_own_choice():
    assert working_dir_for(Config()) is None


def test_working_dir_expands_a_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert working_dir_for(Config(working_dir="~")) == str(tmp_path)


def test_working_dir_expands_environment_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("SOMEWHERE", str(tmp_path))
    assert working_dir_for(Config(working_dir="$SOMEWHERE")) == str(tmp_path)


def test_a_missing_working_dir_is_ignored(tmp_path):
    """A folder that has been renamed must not stop the widget starting:
    the backends chdir in the child, so a bad path kills the shell before
    it can say why."""
    assert working_dir_for(Config(working_dir=str(tmp_path / "gone"))) is None


def test_a_file_is_not_a_working_dir(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("x")
    assert working_dir_for(Config(working_dir=str(target))) is None


def test_clamped_does_not_touch_the_filesystem_for_working_dir():
    """clamped() runs on every IPC message, which is every keystroke in the
    settings app; it must not stat anything."""
    assert Config(working_dir="/nowhere/at/all").clamped().working_dir == (
        "/nowhere/at/all"
    )


# -- Window layer -----------------------------------------------------


def test_the_default_layer_is_behind_other_windows():
    """The premise of the thing: furniture, not an application."""
    assert Config().stacking == STACKING_DESKTOP


def test_an_unknown_layer_falls_back_to_the_desktop():
    assert Config(stacking="sideways").clamped().stacking == STACKING_DESKTOP


def test_every_offered_layer_survives_clamping():
    """A layer the dropdown can offer but clamped() throws away would look
    like the setting simply not working."""
    for key, _label in STACKING_CHOICES:
        assert Config(stacking=key).clamped().stacking == key


def test_the_layer_round_trips_through_the_file(tmp_path):
    path = tmp_path / "config.json"
    Config(stacking=STACKING_TOP).save(path)
    assert Config.load(path).stacking == STACKING_TOP


def test_a_config_written_before_the_setting_existed_takes_the_default(tmp_path):
    """Existing installs move to the desktop layer on upgrade. That is a
    deliberate behaviour change, so it is stated here rather than discovered."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"x": 5, "y": 6}))
    assert Config.load(path).stacking == STACKING_DESKTOP


# -- Windows command lines --------------------------------------------
#
# The bug these pin is issues.md issue 2: a custom command was passed to
# pywinpty as a string, which re-split it with shlex.split(posix=False) --
# a tokenizer that leaves the quote characters *in* the token -- and then
# escaped them into the child's argv as literal quotes. Everything here
# runs on Linux, because the parser is a pure function.


def split(line):
    from terminal_widget.config import split_windows_command_line

    return split_windows_command_line(line)


def test_the_documented_argv_cases():
    """The four examples Microsoft documents for CommandLineToArgvW, each
    prefixed with a program name because argv[0] parses by other rules."""
    assert split('prog "abc" d e') == ["prog", "abc", "d", "e"]
    assert split(r'prog a\\\b d"e f"g h') == ["prog", r"a\\\b", "de fg", "h"]
    assert split(r'prog a\\\"b c d') == ["prog", r"a\"b", "c", "d"]
    assert split(r'prog a\\\\"b c" d e') == ["prog", r"a\\b c", "d", "e"]


def test_the_command_that_started_all_this():
    """The quoted argument arrives as one token, without its quotes -- which
    is the whole fix. With them it reached zsh as a command *name*."""
    assert split('wsl -d kali-linux -- zsh -c "fastfetch; exec zsh"') == [
        "wsl",
        "-d",
        "kali-linux",
        "--",
        "zsh",
        "-c",
        "fastfetch; exec zsh",
    ]


def test_a_quoted_program_path_with_spaces_is_one_argument():
    assert split(r'"C:\Program Files\Git\bin\bash.exe" -l') == [
        r"C:\Program Files\Git\bin\bash.exe",
        "-l",
    ]


def test_backslashes_are_path_separators_not_escapes():
    """POSIX splitting would eat these, which is why shlex is not used."""
    assert split(r"C:\Windows\System32\wsl.exe -d kali-linux") == [
        r"C:\Windows\System32\wsl.exe",
        "-d",
        "kali-linux",
    ]


def test_the_other_quoting_shape():
    assert split('cmd.exe /c "echo hello & pause"') == [
        "cmd.exe",
        "/c",
        "echo hello & pause",
    ]


def test_surrounding_whitespace_and_emptiness():
    assert split("  cmd.exe  ") == ["cmd.exe"]
    assert split("") == []
    assert split("   ") == []


def test_it_round_trips_through_the_quoting_pywinpty_uses():
    """pywinpty rebuilds a command line with subprocess.list2cmdline, so the
    argv the child reconstructs has to be the argv we passed in."""
    import subprocess

    for line in (
        'wsl -d kali-linux -- zsh -c "fastfetch; exec zsh"',
        'cmd.exe /c "echo hello & pause"',
        r"C:\Windows\System32\wsl.exe -d kali-linux",
        r'prog a\\\\"b c" d e',
    ):
        argv = split(line)
        rebuilt = argv[0] + " " + subprocess.list2cmdline(argv[1:])
        assert split(rebuilt) == argv


def test_a_windows_custom_command_is_split_by_windows_rules(monkeypatch):
    """resolve_shell reads sys.platform at call time, so the Windows branch
    can be reached from here -- there is no other way to test it from WSL."""
    from terminal_widget import config as config_module

    monkeypatch.setattr(config_module.sys, "platform", "win32")
    argv = resolve_shell(
        Config(shell="custom", custom_command='wsl -- zsh -c "fastfetch; exec zsh"')
    )
    assert argv == ["wsl", "--", "zsh", "-c", "fastfetch; exec zsh"]


# -- Scroll to the top on start ---------------------------------------


def test_scrolling_to_the_top_on_start_is_off_by_default():
    assert Config().scroll_top_on_start is False


def test_scroll_to_top_round_trips_through_the_file(tmp_path):
    path = tmp_path / "config.json"
    Config(scroll_top_on_start=True).save(path)
    assert Config.load(path).scroll_top_on_start is True


def test_a_config_written_before_scroll_to_top_existed_keeps_its_behaviour(tmp_path):
    """Upgrading must not start moving people's viewports for them."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"x": 5, "y": 6}))
    assert Config.load(path).scroll_top_on_start is False


def test_an_unclosed_quote_does_not_become_a_nameless_program(monkeypatch):
    """A lone quote parses to one empty argument. Spawning that would be a
    program with no name; falling back to the default shell is the only
    substitution resolve_shell makes, and it is for this alone."""
    from terminal_widget import config as config_module

    assert split('"') == [""]
    monkeypatch.setattr(config_module.sys, "platform", "win32")
    assert resolve_shell(Config(shell="custom", custom_command='"')) != [""]
