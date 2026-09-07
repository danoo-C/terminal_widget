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
    assert _config_from({"opacity": 9999}).opacity == 100


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


def test_connection_enables_config_mode(server, qapp, sock):
    """The connection itself is the mode signal."""
    modes = []
    server.configModeChanged.connect(modes.append)
    client = SettingsClient()
    client.start(sock)
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
