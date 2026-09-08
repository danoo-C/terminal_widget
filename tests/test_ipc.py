"""Tests for the widget <-> settings link."""

import time

import pytest
from PySide6.QtCore import QCoreApplication

from terminal_widget.config import Config
from terminal_widget.ipc import SettingsClient, WidgetServer, _config_from, _LineReader


def pump(predicate, timeout=5.0):
    """Spin the event loop until predicate holds or we give up."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


# -- Framing ----------------------------------------------------------


def test_reader_splits_messages():
    r = _LineReader()
    assert len(r.feed(b'{"t":"a"}\n{"t":"b"}\n')) == 2


def test_reader_reassembles_split_chunks():
    """TCP-style streams split wherever they like; a message must survive
    arriving in pieces."""
    r = _LineReader()
    assert r.feed(b'{"t":"geo') == []
    assert r.feed(b'metry","x":1}\n')[0]["x"] == 1


def test_reader_holds_incomplete_trailer():
    r = _LineReader()
    assert r.feed(b'{"t":"a"}\n{"t":"inc') == [{"t": "a"}]


def test_reader_skips_garbage_without_dying():
    """One malformed line must not poison the rest of the stream."""
    r = _LineReader()
    msgs = r.feed(b'not json\n{"t":"ok"}\n')
    assert msgs == [{"t": "ok"}]


def test_reader_ignores_non_objects():
    assert _LineReader().feed(b"[1,2,3]\n") == []


# -- Config decoding --------------------------------------------------


def test_config_from_ignores_unknown_fields():
    cfg = _config_from({"x": 7, "invented_by_a_newer_version": True})
    assert cfg is not None and cfg.x == 7


def test_config_from_rejects_non_dict():
    assert _config_from("nope") is None
    assert _config_from(None) is None


def test_config_from_clamps():
    cfg = _config_from({"opacity": 9999})
    assert cfg is not None
    assert cfg.opacity == 100


# -- Round trip -------------------------------------------------------


@pytest.fixture
def sock(tmp_path):
    """An isolated socket path, so the suite never collides with a widget the
    user actually has running -- and works where the default location is not
    writable."""
    return str(tmp_path / "sock")


@pytest.fixture
def server(qapp, sock):
    s = WidgetServer()
    if not s.start(sock):
        pytest.skip(f"environment forbids binding a local socket: {s.error}")
    yield s
    s.stop()


def test_the_settings_app_introducing_itself_enables_config_mode(server, qapp, sock):
    """The settings app says hello by pushing its config, which is the first
    thing it does on connect. The bare connection is deliberately not enough:
    a second widget process connects too, only to ask us to show ourselves,
    and that must not put the widget into config mode."""
    modes = []
    server.configModeChanged.connect(modes.append)
    client = SettingsClient()
    client.start(sock)
    assert pump(lambda: client.is_connected)
    assert modes == []
    client.send_config(Config())
    assert pump(lambda: modes == [True])
    client.stop()
    assert pump(lambda: modes == [True, False])


def test_disconnect_returns_to_locked_mode(server, qapp, sock):
    """A settings app that dies must look exactly like one that quit, or the
    widget would be stranded in config mode."""
    modes = []
    server.configModeChanged.connect(modes.append)
    client = SettingsClient()
    client.start(sock)
    assert pump(lambda: client.is_connected)
    client.send_config(Config())
    assert pump(lambda: modes == [True])
    client._socket.abort()  # simulate a crash, not a clean quit
    assert pump(lambda: modes[-1] is False)


def test_config_travels_to_the_widget(server, qapp, sock):
    got = []
    server.configReceived.connect(got.append)
    client = SettingsClient()
    client.start(sock)
    assert pump(lambda: client.is_connected)
    client.send_config(Config(x=42, y=43, opacity=60))
    assert pump(lambda: bool(got))
    assert (got[0].x, got[0].y, got[0].opacity) == (42, 43, 60)
    client.stop()


def test_geometry_travels_to_the_settings_app(server, qapp, sock):
    client = SettingsClient()
    seen = []
    client.geometryReceived.connect(lambda *a: seen.append(a))
    client.start(sock)
    assert pump(lambda: client.is_connected)
    server.send_geometry(11, 22, 333, 444)
    assert pump(lambda: bool(seen))
    assert seen[0] == (11, 22, 333, 444)
    client.stop()


def test_second_settings_app_is_rejected(server, qapp, sock):
    """Only one settings app at a time; two would fight over the geometry."""
    first = SettingsClient()
    first.start(sock)
    assert pump(lambda: first.is_connected)
    second = SettingsClient()
    second.start(sock)
    assert pump(lambda: not second.is_connected, timeout=2.0)
    assert first.is_connected
    first.stop()
    second.stop()


# -- One widget per config --------------------------------------------


def test_the_socket_name_is_per_config_file():
    """The one-widget rule is enforced per config, so two widgets deliberately
    pointed at different files can still both run."""
    from pathlib import Path

    from terminal_widget.ipc import socket_name

    a = socket_name(Path("/tmp/one.json"))
    b = socket_name(Path("/tmp/two.json"))
    assert a != b
    assert socket_name(Path("/tmp/one.json")) == a
    # A socket path has a length limit; the config path is hashed, not embedded.
    assert len(a) < 100


def test_a_second_widget_cannot_take_the_lock(tmp_path):
    """The regression test for the bug this pass exists for, and the whole
    feature in five lines. Needs no socket, so unlike the tests above it runs
    everywhere."""
    from PySide6.QtCore import QLockFile

    from terminal_widget.ipc import instance_lock

    cfg = tmp_path / "config.json"
    first = instance_lock(cfg)
    assert first.error() == QLockFile.LockError.NoError
    second = instance_lock(cfg)
    assert second.error() == QLockFile.LockError.LockFailedError
    first.unlock()
    third = instance_lock(cfg)
    assert third.error() == QLockFile.LockError.NoError
    third.unlock()


def test_two_config_files_get_a_lock_each(tmp_path):
    """`--config` stays usable for trying a second layout."""
    from PySide6.QtCore import QLockFile

    from terminal_widget.ipc import instance_lock

    a = instance_lock(tmp_path / "one.json")
    b = instance_lock(tmp_path / "two.json")
    assert a.error() == QLockFile.LockError.NoError
    assert b.error() == QLockFile.LockError.NoError
    a.unlock()
    b.unlock()


def test_a_lock_is_not_stolen_from_a_widget_that_has_been_up_a_while(tmp_path):
    """QLockFile's default is to treat a thirty-second-old lock as stale. A
    desktop widget is up for weeks, so the age test has to be off and only the
    pid test left."""
    from terminal_widget.ipc import instance_lock

    held = instance_lock(tmp_path / "config.json")
    assert held.staleLockTime() == 0
    held.unlock()


def test_asking_a_widget_to_show_itself_does_not_open_config_mode(server, qapp, sock):
    """What a refused second launch does. It must raise the widget without
    putting it into config mode -- which would let the desktop be dragged
    around, and would write the config on the way back out."""
    from terminal_widget.ipc import request_show

    modes, shows = [], []
    server.configModeChanged.connect(modes.append)
    server.showRequested.connect(lambda: shows.append(True))
    assert request_show(sock)
    assert pump(lambda: shows == [True])
    assert modes == []


def test_a_widget_with_settings_open_needs_no_raising(server, qapp, sock):
    """The awkward case, and why it needs no machinery: a settings app holds
    the one client slot, so a second widget's request is dropped. It has
    nothing to do anyway -- config mode has already lifted the widget into the
    normal window order and called bring_to_front()."""
    client = SettingsClient()
    client.start(sock)
    assert pump(lambda: client.is_connected)
    from terminal_widget.ipc import request_show

    request_show(sock)
    assert pump(lambda: True)
    assert client.is_connected  # the newcomer did not displace it
    client.stop()


def test_the_settings_app_can_ask_the_widget_to_quit(server, qapp, sock):
    """What Restart is built on."""
    quits = []
    server.quitRequested.connect(quits.append)
    client = SettingsClient()
    client.start(sock)
    assert pump(lambda: client.is_connected)
    client.send_quit(restart=True)
    assert pump(lambda: quits == [True])
    client.send_quit()
    assert pump(lambda: quits == [True, False])
    client.stop()


def test_widget_is_running_tells_a_live_widget_from_a_dead_one(server, qapp, sock):
    from terminal_widget.ipc import widget_is_running

    assert widget_is_running(sock) is True
    server.stop()
    assert widget_is_running(sock) is False
