"""The link between the widget and the settings app.

A local socket -- a Unix domain socket on Linux, a named pipe on Windows --
carrying newline-delimited JSON.

The connection itself is the mode signal. While the settings app is
connected the widget is in config mode; the moment the socket drops it
returns to locked mode. That is deliberate: a settings app that crashes
looks exactly like one that quit, so the widget can never be stranded in
config mode with a dead partner.
"""

from __future__ import annotations

import getpass
import json
from dataclasses import asdict, fields

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .config import Config


def socket_name() -> str:
    """Per-user socket name, so two accounts don't collide on one machine."""
    try:
        user = getpass.getuser()
    except Exception:
        user = "default"
    return f"terminal_widget-{user}"


def _encode(payload: dict) -> bytes:
    return (json.dumps(payload) + "\n").encode("utf-8")


class _LineReader:
    """Reassembles newline-delimited JSON from arbitrary socket chunks."""

    def __init__(self) -> None:
        self._buf = b""

    def feed(self, chunk: bytes) -> list[dict]:
        self._buf += chunk
        messages = []
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                obj = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue  # ignore garbage rather than kill the connection
            if isinstance(obj, dict):
                messages.append(obj)
        return messages


class WidgetServer(QObject):
    """Widget side. Listens for the settings app."""

    #: True when the settings app connects, False when it goes away.
    configModeChanged = Signal(bool)
    #: A live settings update arrived.
    configReceived = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        self._client: QLocalSocket | None = None
        self._reader = _LineReader()
        self.error = ""

    def start(self, name: str | None = None) -> bool:
        """Listen for a settings app.

        ``name`` overrides the default per-user socket. An absolute path is
        used as the socket path directly, which lets tests isolate
        themselves and lets two independent widgets coexist.
        """
        name = name or socket_name()
        # A previous crash can leave the socket file behind; without this the
        # widget would never be reachable again until it was deleted by hand.
        QLocalServer.removeServer(name)
        if not self._server.listen(name):
            self.error = self._server.errorString()
            return False
        return True

    def _on_new_connection(self) -> None:
        socket = self._server.nextPendingConnection()
        if socket is None:
            return
        if self._client is not None:
            # Only one settings app at a time; the newcomer loses.
            socket.disconnectFromServer()
            return
        self._client = socket
        self._reader = _LineReader()
        socket.readyRead.connect(self._on_ready_read)
        socket.disconnected.connect(self._on_disconnected)
        self.configModeChanged.emit(True)

    def _on_ready_read(self) -> None:
        if self._client is None:
            return
        for msg in self._reader.feed(bytes(self._client.readAll().data())):
            if msg.get("t") == "config":
                config = _config_from(msg.get("d"))
                if config is not None:
                    self.configReceived.emit(config)

    def _on_disconnected(self) -> None:
        if self._client is not None:
            self._client.deleteLater()
            self._client = None
        self.configModeChanged.emit(False)

    def send_geometry(self, x: int, y: int, width: int, height: int) -> None:
        """Tell the settings app the user just dragged or resized us."""
        self._send({"t": "geometry", "x": x, "y": y, "w": width, "h": height})

    def _send(self, payload: dict) -> None:
        if self._client is not None and self._client.state() == QLocalSocket.LocalSocketState.ConnectedState:
            self._client.write(_encode(payload))
            self._client.flush()

    def stop(self) -> None:
        if self._client is not None:
            self._client.disconnectFromServer()
            self._client = None
        self._server.close()


class SettingsClient(QObject):
    """Settings app side. Keeps trying to reach a widget."""

    connected = Signal()
    disconnected = Signal()
    geometryReceived = Signal(int, int, int, int)

    #: How often to retry while no widget is running.
    RETRY_MS = 1000

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._socket = QLocalSocket(self)
        self._socket.connected.connect(self._on_connected)
        self._socket.disconnected.connect(self._on_disconnected)
        self._socket.errorOccurred.connect(self._on_error)
        self._socket.readyRead.connect(self._on_ready_read)
        self._reader = _LineReader()
        self._retry = QTimer(self)
        self._retry.setInterval(self.RETRY_MS)
        self._retry.timeout.connect(self._try_connect)
        self._wanted = False
        self._name = socket_name()

    @property
    def is_connected(self) -> bool:
        return self._socket.state() == QLocalSocket.LocalSocketState.ConnectedState

    def start(self, name: str | None = None) -> None:
        """Connect, and keep retrying so a widget started later is picked up."""
        self._wanted = True
        self._name = name or socket_name()
        self._try_connect()
        self._retry.start()

    def _try_connect(self) -> None:
        if not self._wanted or self.is_connected:
            return
        if self._socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            self._reader = _LineReader()
            self._socket.connectToServer(self._name)

    def _on_connected(self) -> None:
        self._retry.stop()
        self.connected.emit()

    def _on_disconnected(self) -> None:
        self.disconnected.emit()
        if self._wanted:
            self._retry.start()

    def _on_error(self, _error) -> None:
        # Nothing listening yet is the normal case, not a failure; the retry
        # timer will keep trying until a widget shows up.
        if self._socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
            self._socket.abort()

    def _on_ready_read(self) -> None:
        for msg in self._reader.feed(bytes(self._socket.readAll().data())):
            if msg.get("t") == "geometry":
                try:
                    self.geometryReceived.emit(
                        int(msg["x"]), int(msg["y"]), int(msg["w"]), int(msg["h"])
                    )
                except (KeyError, TypeError, ValueError):
                    continue

    def send_config(self, config: Config) -> None:
        """Push settings to the widget for live preview."""
        if not self.is_connected:
            return
        self._socket.write(_encode({"t": "config", "d": asdict(config)}))
        self._socket.flush()

    def stop(self) -> None:
        self._wanted = False
        self._retry.stop()
        if self._socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
            self._socket.disconnectFromServer()


def _config_from(data) -> Config | None:
    """Build a Config from wire data, ignoring anything we don't recognise."""
    if not isinstance(data, dict):
        return None
    known = {f.name for f in fields(Config)}
    try:
        return Config(**{k: v for k, v in data.items() if k in known}).clamped()
    except (TypeError, ValueError):
        return None
