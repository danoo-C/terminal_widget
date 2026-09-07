"""Settings app entry point: ``python -m terminal_widget.settings``.

Opening this app is what puts the widget into config mode; closing it is
what takes it out. There is no toggle anywhere in this UI, by design.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QIcon,
    QPalette,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import autostart
from ..config import (
    OPACITY_BACKGROUND,
    OPACITY_WINDOW,
    Config,
    available_shells,
    config_dir,
    config_path,
    min_opacity,
    working_dir_for,
)
from ..ipc import SettingsClient
from ..platform_info import DISPLAY_NAME, icon_path, widget_launcher
from .theme import muted_color, status_colors


class SettingsWindow(QWidget):
    def __init__(self, config: Config, path: Path | None) -> None:
        super().__init__()
        self._config = config
        self._path = path
        self._updating = False  # guards against feedback loops
        self._restyling = False
        #: Labels whose colour is computed rather than inherited, and the
        #: status line's meaning rather than its colour -- both so a theme
        #: change can be replayed onto them.
        self._muted: list[QLabel] = []
        self._status: tuple[str, str] = ("info", "")

        self.setWindowTitle(f"{DISPLAY_NAME} Settings")
        icon = icon_path()
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.setMinimumWidth(460)

        self.client = SettingsClient(self)
        self.client.connected.connect(self._on_connected)
        self.client.disconnected.connect(self._on_disconnected)
        self.client.geometryReceived.connect(self._on_geometry_from_widget)

        root = QVBoxLayout(self)
        root.addWidget(self._build_status())

        # The groups add up to more height than a short screen has, and a
        # squeezed QFormLayout overlaps its own rows rather than clipping.
        # Scrolling keeps every control reachable at any window height.
        inner = QWidget()
        stack = QVBoxLayout(inner)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.addWidget(self._build_geometry())
        stack.addWidget(self._build_shell())
        stack.addWidget(self._build_appearance())
        stack.addWidget(self._build_behaviour())
        stack.addWidget(self._build_config_location())
        stack.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # The viewport defaults to the Base role, which puts the settings on
        # a white (or near-black) panel inset in the dialog. Window keeps it
        # one surface -- and makes it the colour the hint text is computed
        # against.
        scroll.viewport().setBackgroundRole(QPalette.ColorRole.Window)
        root.addWidget(scroll, 1)

        root.addLayout(self._build_buttons())
        self.resize(560, 700)

        # Qt follows the system light/dark scheme, but has no palette role
        # for secondary text, so the hint labels have to be coloured by hand
        # -- and recoloured whenever the scheme changes underneath us.
        QGuiApplication.styleHints().colorSchemeChanged.connect(
            lambda *_: self._restyle()
        )
        self._restyle()

        self._load_into_ui()
        self._refresh_autostart()
        self.client.start()
        self._on_disconnected()

    # -- Theming -----------------------------------------------------

    def _hint(self, text: str = "") -> QLabel:
        """A secondary label, registered so themes can be replayed onto it."""
        label = QLabel(text)
        label.setWordWrap(True)
        self._muted.append(label)
        return label

    def _restyle(self) -> None:
        """Recolour everything whose colour is computed, not inherited.

        Setting a palette on a child marks it WA_SetPalette, so it stops
        following later application palette changes. That is precisely why
        the labels are kept in a list and re-coloured here rather than
        styled once at construction.
        """
        if self._restyling:
            return
        self._restyling = True
        try:
            color = muted_color(self.palette())
            for label in self._muted:
                self._recolor(label, color)
            self._paint_status()
        finally:
            self._restyling = False

    @staticmethod
    def _recolor(label: QLabel, color: QColor) -> None:
        palette = label.palette()
        palette.setColor(QPalette.ColorRole.WindowText, color)
        label.setPalette(palette)

    def _set_status(self, kind: str, text: str) -> None:
        """Say something in the status line. ``kind`` is ok, error or info."""
        self._status = (kind, text)
        self._paint_status()

    def _paint_status(self) -> None:
        kind, text = self._status
        self.status.setText(text)
        ok, error = status_colors(self.palette())
        self._recolor(
            self.status,
            {"ok": ok, "error": error}.get(kind, muted_color(self.palette())),
        )

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in (
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
            QEvent.Type.ThemeChange,
        ):
            self._restyle()

    # -- Sections ----------------------------------------------------

    def _build_status(self) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel()
        self.status.setWordWrap(True)
        row.addWidget(self.status, 1)
        return box

    def _build_geometry(self) -> QGroupBox:
        box = QGroupBox("Position and size")
        form = QFormLayout(box)

        self.spin_x = QSpinBox(minimum=-32000, maximum=32000, singleStep=10)
        self.spin_y = QSpinBox(minimum=-32000, maximum=32000, singleStep=10)
        self.spin_w = QSpinBox(minimum=120, maximum=32000, singleStep=10)
        self.spin_h = QSpinBox(minimum=80, maximum=32000, singleStep=10)
        for s in (self.spin_x, self.spin_y, self.spin_w, self.spin_h):
            s.setSuffix(" px")
            s.valueChanged.connect(self._on_ui_changed)

        pos = QHBoxLayout()
        pos.addWidget(QLabel("X"))
        pos.addWidget(self.spin_x)
        pos.addWidget(QLabel("Y"))
        pos.addWidget(self.spin_y)
        size = QHBoxLayout()
        size.addWidget(QLabel("W"))
        size.addWidget(self.spin_w)
        size.addWidget(QLabel("H"))
        size.addWidget(self.spin_h)

        form.addRow("Position", pos)
        form.addRow("Size", size)
        self.grid_label = self._hint()
        form.addRow("", self.grid_label)
        form.addRow(
            "", self._hint("Drag the widget to move it; drag its edges to resize.")
        )
        return box

    def _build_shell(self) -> QGroupBox:
        box = QGroupBox("Shell")
        form = QFormLayout(box)

        self.combo_shell = QComboBox()
        for key, label in available_shells():
            self.combo_shell.addItem(label, key)
        self.combo_shell.currentIndexChanged.connect(self._on_shell_changed)
        form.addRow("Terminal program", self.combo_shell)

        self.edit_custom = QLineEdit()
        self.edit_custom.setPlaceholderText("e.g. wsl.exe -d Ubuntu")
        self.edit_custom.textChanged.connect(self._on_ui_changed)
        form.addRow("Custom command", self.edit_custom)

        self.edit_workdir = QLineEdit()
        self.edit_workdir.setPlaceholderText("The shell's own default")
        self.edit_workdir.textChanged.connect(self._on_ui_changed)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse_workdir)
        where = QHBoxLayout()
        where.addWidget(self.edit_workdir, 1)
        where.addWidget(browse)
        form.addRow("Start in", where)

        self.shell_note = self._hint(
            "The shell and the directory it starts in are read when the "
            "widget launches, so changing either takes effect next time it "
            "starts. A directory that no longer exists is ignored."
        )
        form.addRow("", self.shell_note)
        return box

    def _browse_workdir(self) -> None:
        start = working_dir_for(self._config) or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Start the shell in", start)
        if chosen:
            self.edit_workdir.setText(chosen)  # fires _on_ui_changed

    def _build_appearance(self) -> QGroupBox:
        box = QGroupBox("Appearance")
        form = QFormLayout(box)

        self.slider_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_opacity.setRange(0, 100)
        self.label_opacity = QLabel()
        self.slider_opacity.valueChanged.connect(self._on_ui_changed)
        row = QHBoxLayout()
        row.addWidget(self.slider_opacity, 1)
        row.addWidget(self.label_opacity)
        form.addRow("Opacity", row)

        self.radio_bg = QRadioButton("Background only (text stays solid)")
        self.radio_win = QRadioButton("Entire window (text fades too)")
        self.radio_bg.toggled.connect(self._on_opacity_mode_changed)
        modes = QVBoxLayout()
        modes.addWidget(self.radio_bg)
        modes.addWidget(self.radio_win)
        form.addRow("Applies to", modes)
        form.addRow(
            "",
            self._hint(
                "Background only reaches 0%: the window disappears and the "
                "text stays. Entire window stops at 10%, below which there "
                "would be nothing left to see or click."
            ),
        )

        self.combo_font = QFontComboBox()
        self.combo_font.setFontFilters(QFontComboBox.FontFilter.MonospacedFonts)
        self.combo_font.currentFontChanged.connect(self._on_ui_changed)
        form.addRow("Font", self.combo_font)

        self.spin_font = QSpinBox(minimum=5, maximum=72)
        self.spin_font.setSuffix(" pt")
        self.spin_font.valueChanged.connect(self._on_ui_changed)
        form.addRow("Font size", self.spin_font)

        self.btn_fg = QPushButton()
        self.btn_bg = QPushButton()
        self.btn_fg.clicked.connect(lambda: self._pick_color("foreground"))
        self.btn_bg.clicked.connect(lambda: self._pick_color("background"))
        colors = QHBoxLayout()
        colors.addWidget(self.btn_fg)
        colors.addWidget(self.btn_bg)
        form.addRow("Colours", colors)
        return box

    def _build_behaviour(self) -> QGroupBox:
        box = QGroupBox("Behaviour")
        form = QFormLayout(box)

        self.spin_scrollback = QSpinBox(minimum=0, maximum=50000, singleStep=500)
        self.spin_scrollback.setSuffix(" lines")
        self.spin_scrollback.setSpecialValueText("Off")
        self.spin_scrollback.valueChanged.connect(self._on_ui_changed)
        form.addRow("Scrollback", self.spin_scrollback)
        form.addRow(
            "",
            self._hint(
                "How far back the mouse wheel can scroll. There is no "
                "scrollbar -- the wheel is the whole interface."
            ),
        )

        self.check_history = QCheckBox("Keep shell history separate")
        self.check_history.setToolTip(
            "Commands typed in the widget go to its own history file instead "
            "of the one your normal terminal uses."
        )
        self.check_history.toggled.connect(self._on_ui_changed)
        form.addRow("History", self.check_history)

        # Autostart is an OS-level action, not a config field: it is applied
        # the moment it is toggled and it never round-trips through Config.
        self.check_autostart = QCheckBox("Start automatically on login")
        self.check_autostart.toggled.connect(self._on_autostart_toggled)
        form.addRow("Startup", self.check_autostart)

        self.autostart_note = self._hint()
        form.addRow("", self.autostart_note)
        return box

    def _build_config_location(self) -> QGroupBox:
        box = QGroupBox("Configuration")
        form = QFormLayout(box)

        path_label = self._hint(str(self._path or config_path()))
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("File", path_label)

        button = QPushButton("Open config folder")
        button.clicked.connect(self._open_config_folder)
        form.addRow("", button)
        return box

    # -- Autostart ----------------------------------------------------

    def _refresh_autostart(self) -> None:
        """Show what the OS actually reports, not what we last set."""
        supported, reason = autostart.is_supported()
        launcher = widget_launcher()

        self.check_autostart.blockSignals(True)
        if not supported:
            self.check_autostart.setChecked(False)
            self.check_autostart.setEnabled(False)
            self.autostart_note.setText(reason)
        elif launcher is None:
            self.check_autostart.setChecked(autostart.is_enabled())
            self.check_autostart.setEnabled(False)
            self.autostart_note.setText(
                "Available once the widget is installed -- running from a "
                "source checkout gives no stable path to register."
            )
        else:
            self.check_autostart.setEnabled(True)
            self.check_autostart.setChecked(autostart.is_enabled())
            self.autostart_note.setText(autostart.describe())
        self.check_autostart.blockSignals(False)

    def _on_autostart_toggled(self, checked: bool) -> None:
        launcher = widget_launcher()
        if launcher is None:
            self._refresh_autostart()
            return
        try:
            if checked:
                autostart.enable(launcher)
            else:
                autostart.disable()
        except OSError as exc:
            # Never leave the checkbox claiming a state that is not real.
            self._set_status("error", f"Could not change autostart: {exc}")
        self._refresh_autostart()

    def _open_config_folder(self) -> None:
        directory = (self._path.parent if self._path else config_dir())
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._set_status("error", f"Could not create {directory}: {exc}")
            return
        url = QUrl.fromLocalFile(str(directory))
        if QDesktopServices.openUrl(url):
            return
        # Qt declines in some minimal environments; fall back to the tools
        # each platform ships with.
        import subprocess

        try:
            if sys.platform == "win32":
                os.startfile(str(directory))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(directory)])
            else:
                subprocess.Popen(["xdg-open", str(directory)])
        except (OSError, AttributeError) as exc:
            self._set_status("error", f"Could not open {directory}: {exc}")

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.btn_launch = QPushButton("Launch widget")
        self.btn_launch.clicked.connect(self._launch_widget)
        row.addWidget(self.btn_launch)
        row.addStretch(1)
        btn_save = QPushButton("Save")
        btn_save.clicked.connect(self._save)
        btn_close = QPushButton("Close")
        btn_close.setDefault(True)
        btn_close.clicked.connect(self.close)
        row.addWidget(btn_save)
        row.addWidget(btn_close)
        return row

    # -- UI <-> Config ------------------------------------------------

    def _load_into_ui(self) -> None:
        self._updating = True
        c = self._config
        self.spin_x.setValue(c.x)
        self.spin_y.setValue(c.y)
        self.spin_w.setValue(c.width)
        self.spin_h.setValue(c.height)

        index = self.combo_shell.findData(c.shell)
        self.combo_shell.setCurrentIndex(index if index >= 0 else 0)
        self.edit_custom.setText(c.custom_command)
        self.edit_workdir.setText(c.working_dir)

        self.radio_bg.setChecked(c.opacity_mode == OPACITY_BACKGROUND)
        self.radio_win.setChecked(c.opacity_mode == OPACITY_WINDOW)
        # The floor depends on the mode, so it has to be in place before the
        # value lands -- otherwise a saved 0% gets clamped up to 10 on load.
        self.slider_opacity.setMinimum(min_opacity(c.opacity_mode))
        self.slider_opacity.setValue(c.opacity)
        self.combo_font.setCurrentFont(QFont(c.font_family))
        self.spin_font.setValue(c.font_size)
        self.check_history.setChecked(c.separate_history)
        self.spin_scrollback.setValue(c.scrollback)
        self._updating = False
        self._refresh_derived()

    def _config_from_ui(self) -> Config:
        """Read the form back into a Config.

        ``replace`` rather than a fresh Config, because this runs on every
        keystroke and the result becomes the config that gets saved: anything
        the form does not edit -- the colours today, whatever field is added
        next -- has to survive, and a constructor call would quietly reset it
        to the default instead.
        """
        return replace(
            self._config,
            x=self.spin_x.value(),
            y=self.spin_y.value(),
            width=self.spin_w.value(),
            height=self.spin_h.value(),
            shell=self.combo_shell.currentData() or "default",
            custom_command=self.edit_custom.text(),
            working_dir=self.edit_workdir.text(),
            separate_history=self.check_history.isChecked(),
            opacity=self.slider_opacity.value(),
            opacity_mode=OPACITY_BACKGROUND if self.radio_bg.isChecked() else OPACITY_WINDOW,
            font_family=self.combo_font.currentFont().family(),
            font_size=self.spin_font.value(),
            scrollback=self.spin_scrollback.value(),
        )

    def _on_ui_changed(self, *_args) -> None:
        if self._updating:
            return
        self._config = self._config_from_ui()
        self._refresh_derived()
        self.client.send_config(self._config)  # live preview

    def _on_opacity_mode_changed(self, *_args) -> None:
        """Window mode cannot go as low as background mode; raise the floor.

        setMinimum before reading the form back, or the config is built from
        a value the slider is about to reject.
        """
        mode = OPACITY_BACKGROUND if self.radio_bg.isChecked() else OPACITY_WINDOW
        self.slider_opacity.setMinimum(min_opacity(mode))
        self._on_ui_changed()

    def _on_shell_changed(self, *_args) -> None:
        self.edit_custom.setEnabled(self.combo_shell.currentData() == "custom")
        self._on_ui_changed()

    def _refresh_derived(self) -> None:
        """Update the labels that are computed rather than entered."""
        self.label_opacity.setText(f"{self.slider_opacity.value()}%")
        self.edit_custom.setEnabled(self.combo_shell.currentData() == "custom")
        for button, value in (
            (self.btn_fg, self._config.foreground),
            (self.btn_bg, self._config.background),
        ):
            button.setText(value)
            button.setStyleSheet(
                f"background-color: {value}; "
                f"color: {'#000' if QColor(value).lightness() > 128 else '#fff'};"
            )
        # Pixels are what you enter, but a terminal is a character grid, so
        # show what the chosen size actually buys you.
        font = QFont(self.combo_font.currentFont().family(), self.spin_font.value())
        font.setStyleHint(QFont.StyleHint.Monospace)
        fm = QFontMetricsF(font)
        cw, ch = fm.horizontalAdvance("M"), fm.height()
        if cw > 0 and ch > 0:
            cols = max(1, int(self.spin_w.value() // cw))
            rows = max(1, int(self.spin_h.value() // ch))
            self.grid_label.setText(f"{cols} x {rows} cells")

    def _pick_color(self, which: str) -> None:
        current = QColor(getattr(self._config, which))
        chosen = QColorDialog.getColor(current, self, f"Terminal {which}")
        if chosen.isValid():
            setattr(self._config, which, chosen.name())
            self._refresh_derived()
            self._on_ui_changed()

    # -- Widget connection --------------------------------------------

    def _on_connected(self) -> None:
        self._set_status(
            "ok",
            "Connected -- the widget is in config mode. Drag it to move, drag "
            "its edges to resize.",
        )
        self.btn_launch.setVisible(False)
        self.client.send_config(self._config)

    def _on_disconnected(self) -> None:
        self._set_status(
            "info",
            "No widget running. Changes are saved to the config file and will "
            "apply next time it starts.",
        )
        self.btn_launch.setVisible(True)

    def _on_geometry_from_widget(self, x: int, y: int, w: int, h: int) -> None:
        """The user dragged or resized the widget; follow along."""
        self._updating = True
        self.spin_x.setValue(x)
        self.spin_y.setValue(y)
        self.spin_w.setValue(w)
        self.spin_h.setValue(h)
        self._updating = False
        self._config = self._config_from_ui()
        self._refresh_derived()

    def _launch_widget(self) -> None:
        from PySide6.QtCore import QProcess

        self._save()
        args = ["-m", "terminal_widget"]
        if self._path is not None:
            args += ["--config", str(self._path)]
        QProcess.startDetached(sys.executable, args)
        QTimer.singleShot(400, self.client.start)

    # -- Persistence ---------------------------------------------------

    def _save(self) -> None:
        try:
            self._config.clamped().save(self._path)
        except OSError as exc:
            self._set_status("error", f"Could not save: {exc}")

    def closeEvent(self, event) -> None:  # noqa: N802
        # Saving on close means arranging the widget by dragging it is enough;
        # you never have to remember to press Save.
        self._save()
        self.client.stop()
        super().closeEvent(event)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="terminal_widget.settings",
        description="Settings for the terminal widget.",
    )
    parser.add_argument(
        "--config", type=Path, default=None, metavar="PATH",
        help=f"config file to edit (default: {config_path()})",
    )
    args = parser.parse_args(argv)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("terminal_widget settings")
    window = SettingsWindow(Config.load(args.config), args.config)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
