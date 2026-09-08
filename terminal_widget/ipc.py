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
import hashlib
import json
import os
from dataclasses import asdict, fields
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .config import Config, config_path, lock_path

#: How long to wait for a probe to reach a running widget. Local sockets
#: either answer at once or not at all, so this is a timeout for a machine
#: under load rather than for the round trip.
PROBE_MS = 200


def socket_name(path: Path | None = None) -> str:
    """Socket name for one config file, for one user.

    Per-user so two accounts don't collide on one machine, and per-config-file
    because that is the granularity the one-widget rule is enforced at: two
    widgets sharing a config fight over it, two widgets on deliberately
    different ``--config`` files do not.
    """
    try:
        user = getpass.getuser()
    except Exception:
        user = "default"
    try:
        resolved = str(Path(path or config_path()).expanduser().resolve())
    except OSError:
        resolved = str(path or "")
    # Windows cannot canonicalise the case of a file that does not exist yet,
    # so C:\Users\... and c:\users\... would otherwise hash apart and hand
    # out two widgets for one config.
    resolved = os.path.normcase(resolved)
    # Hashed rather than embedded: a socket name has a length limit (107 bytes
    # for a Unix path) and a config path can be arbitrarily long.
    digest = hashlib.sha256(resolved.encode("utf-8", "replace")).hexdigest()[:12]
    return f"terminal_widget-{user}-{digest}"


def instance_lock(path: Path | None = None) -> QLockFile:
    """Take the one-widget lock for a config file. Held if ``error()`` is NoError.

    Two widgets sharing a config each hold a snapshot of it and each write the
    whole thing back on the way out, so the second to quit silently reverts
    everything the user changed in between. This is what stops that.

    ``setStaleLockTime(0)`` turns off the age test and leaves only the pid
    test. The default is thirty seconds, which would let a widget that has
    been up all week have its lock taken out from under it.
    """
    lock = QLockFile(str(lock_path(path)))
    lock.setStaleLockTime(0)
    lock.tryLock(0)
    return lock


def widget_is_running(name: str) -> bool:
    """Whether something is listening on ``name`` right now.

    The one way to tell a live widget from the socket file a crashed one left
    behind: the file is still there either way, but only a live widget accepts
    a connection.
    """
    socket = QLocalSocket()
    socket.connectToServer(name)
    alive = socket.waitForConnected(PROBE_MS)
    socket.abort()
    return alive


def request_show(name: str) -> bool:
    """Ask the widget listening on ``name`` to bring itself to the front.

    Used by a second widget process that has just been refused: launching is
    meant to hand you your widget, and the one already running is it.
    """
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(PROBE_MS):
        return False
    socket.write(_encode({"t": "show"}))
    socket.flush()
    socket.waitForBytesWritten(PROBE_MS)
    socket.disconnectFromServer()
    return True


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
    #: Somebody asked us to make ourselves visible -- a second launch, which
    #: hands the user the widget they already have instead of a new one.
    showRequested = Signal()
    #: The settings app asked us to shut down. True when it wants us back
    #: afterwards, which only we can arrange: nobody else knows when we have
    #: let go of the socket and the lock.
    quitRequested = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        self._client: QLocalSocket | None = None
        self._reader = _LineReader()
        self._config_mode = False
        self.error = ""
        #: The socket actually bound, so callers need not re-derive it.
        self.name = ""

    def start(self, name: str | None = None) -> bool:
        """Listen for a settings app.

        ``name`` overrides the default. An absolute path is used as the socket
        path directly, which lets tests isolate themselves.

        The socket is not the lock -- :func:`instance_lock` is, and we hold it
        by the time this runs. That is what makes removing whatever is at this
        name safe rather than a way for a second widget to unseat the first:
        holding the lock means any socket still here was left by a crash.
        """
        name = name or socket_name()
        self.name = name
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
            # Only one settings app at a time; the newcomer loses. That also
            # settles what happens to a second widget process asking us to
            # show ourselves while settings is open: nothing, and nothing is
            # needed. A connected settings app means config mode, and config
            # mode has already lifted us into the normal window order and
            # called bring_to_front(). We are as visible as the request could
            # have made us.
            socket.disconnectFromServer()
            return
        self._client = socket
        self._reader = _LineReader()
        socket.readyRead.connect(self._on_ready_read)
        socket.disconnected.connect(self._on_disconnected)

    def _on_ready_read(self) -> None:
        if self._client is None:
            return
        for msg in self._reader.feed(bytes(self._client.readAll().data())):
            kind = msg.get("t")
            if kind == "config":
                config = _config_from(msg.get("d"))
                if config is None:
                    continue
                if not self._config_mode:
                    # Config mode begins when the settings app introduces
                    # itself, which it does by pushing its config the moment
                    # it connects -- not at the bare connection, which anyone
                    # asking us to show ourselves also makes.
                    self._config_mode = True
                    self.configModeChanged.emit(True)
                self.configReceived.emit(config)
            elif kind == "show":
                self.showRequested.emit()
            elif kind == "quit":
                self.quitRequested.emit(bool(msg.get("restart")))

    def _on_disconnected(self) -> None:
        if self._client is not None:
            self._client.deleteLater()
            self._client = None
        if self._config_mode:
            # Still the disconnect that ends config mode, so a settings app
            # that crashed looks exactly like one that quit.
            self._config_mode = False
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
        self._send({"t": "config", "d": asdict(config)})

    def send_quit(self, restart: bool = False) -> None:
        """Ask the widget to shut down, and optionally to come back."""
        self._send({"t": "quit", "restart": restart})

    def _send(self, payload: dict) -> None:
        if not self.is_connected:
            return
        self._socket.write(_encode(payload))
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
