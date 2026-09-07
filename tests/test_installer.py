"""Installer and uninstaller.

The installer is what a stranger runs first, so the parts that decide what
gets created and removed are worth pinning down. The network-bound step
(pip install) is exercised by --dry-run rather than actually run.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def installer_module():
    spec = importlib.util.spec_from_file_location("tw_install", ROOT / "install.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec: @dataclass resolves its own module by name.
    sys.modules["tw_install"] = module
    spec.loader.exec_module(module)
    yield module
    del sys.modules["tw_install"]


# -- Dry run ----------------------------------------------------------


def test_dry_run_creates_nothing(installer_module, tmp_path):
    target = tmp_path / "app"
    options = installer_module.Options(prefix=target, dry_run=True, shortcuts=False)
    installer_module.Installer(options).run(lambda *a: None)
    assert not target.exists()


def test_dry_run_still_reports_progress(installer_module, tmp_path):
    seen = []
    options = installer_module.Options(
        prefix=tmp_path / "app", dry_run=True, shortcuts=False
    )
    installer_module.Installer(options).run(lambda msg, frac: seen.append((msg, frac)))
    assert any(frac == 1.0 for _, frac in seen)


# -- Preflight --------------------------------------------------------


def test_refuses_without_a_project_to_install(installer_module, tmp_path):
    options = installer_module.Options(prefix=tmp_path / "app", dry_run=True)
    installer = installer_module.Installer(options, source=tmp_path / "empty")
    with pytest.raises(installer_module.InstallError):
        installer.preflight(lambda *a: None)


# -- Manifest ---------------------------------------------------------


def test_manifest_round_trips(installer_module, tmp_path):
    manifest = installer_module.Manifest(app_root=str(tmp_path))
    manifest.paths = [str(tmp_path / "a"), str(tmp_path / "b")]
    path = tmp_path / "install-manifest.json"
    path.write_text(manifest.to_json())
    assert installer_module.Manifest.load(path)["paths"] == manifest.paths


def test_manifest_rejects_an_unknown_schema(installer_module, tmp_path):
    path = tmp_path / "install-manifest.json"
    path.write_text(json.dumps({"schema": 999}))
    with pytest.raises(installer_module.InstallError):
        installer_module.Manifest.load(path)


def test_paths_are_recorded_once(installer_module, tmp_path):
    installer = installer_module.Installer(
        installer_module.Options(prefix=tmp_path / "app")
    )
    installer._record(tmp_path / "x")
    installer._record(tmp_path / "x")
    assert installer.manifest.paths.count(str(tmp_path / "x")) == 1


# -- Uninstaller ------------------------------------------------------


def _fake_install(module, tmp_path) -> Path:
    """Build an install-shaped tree with a manifest describing it."""
    root = tmp_path / "app"
    (root / "venv" / "bin").mkdir(parents=True)
    (root / "venv" / "bin" / "terminal-widget").write_text("#!/bin/sh\n")
    entry = tmp_path / "applications" / "terminal-widget.desktop"
    entry.parent.mkdir(parents=True)
    entry.write_text("[Desktop Entry]\n")
    config = tmp_path / "config" / "terminal_widget"
    config.mkdir(parents=True)
    (config / "config.json").write_text('{"x": 10}')

    manifest = module.Manifest(app_root=str(root))
    manifest.paths = [str(root), str(entry)]
    manifest.preserved = [str(config)]
    (root / "install-manifest.json").write_text(manifest.to_json())
    return root


def test_uninstall_removes_what_the_manifest_lists(installer_module, tmp_path):
    root = _fake_install(installer_module, tmp_path)
    installer_module.Uninstaller(root).run(lambda *a: None)
    assert not root.exists()
    assert not (tmp_path / "applications" / "terminal-widget.desktop").exists()


def test_uninstall_keeps_config_by_default(installer_module, tmp_path):
    root = _fake_install(installer_module, tmp_path)
    installer_module.Uninstaller(root).run(lambda *a: None)
    assert (tmp_path / "config" / "terminal_widget" / "config.json").exists()


def test_purge_removes_config_too(installer_module, tmp_path):
    root = _fake_install(installer_module, tmp_path)
    installer_module.Uninstaller(root, purge=True).run(lambda *a: None)
    assert not (tmp_path / "config" / "terminal_widget").exists()


def test_uninstall_dry_run_removes_nothing(installer_module, tmp_path):
    root = _fake_install(installer_module, tmp_path)
    installer_module.Uninstaller(root, dry_run=True).run(lambda *a: None)
    assert root.exists()
    assert (tmp_path / "applications" / "terminal-widget.desktop").exists()


def test_uninstall_refuses_without_a_manifest(installer_module, tmp_path):
    """It must never guess which files belong to it."""
    (tmp_path / "app").mkdir()
    with pytest.raises(installer_module.InstallError):
        installer_module.Uninstaller(tmp_path / "app")


def test_uninstall_tolerates_already_missing_paths(installer_module, tmp_path):
    root = _fake_install(installer_module, tmp_path)
    (tmp_path / "applications" / "terminal-widget.desktop").unlink()
    installer_module.Uninstaller(root).run(lambda *a: None)
    assert not root.exists()


def test_summary_separates_removed_from_kept(installer_module, tmp_path):
    root = _fake_install(installer_module, tmp_path)
    remove, keep = installer_module.Uninstaller(root).summary()
    assert any("applications" in item for item in remove)
    assert any("terminal_widget" in item for item in keep)


def test_purge_moves_config_into_the_removal_list(installer_module, tmp_path):
    root = _fake_install(installer_module, tmp_path)
    remove, keep = installer_module.Uninstaller(root, purge=True).summary()
    assert keep == []
    assert any("terminal_widget" in item for item in remove)


# -- Argument parsing -------------------------------------------------


def test_defaults(installer_module):
    args = installer_module.parse_args([])
    assert args.autostart is None and not args.uninstall and not args.dry_run


def test_autostart_flags_are_three_state(installer_module):
    """None means 'ask'; True and False mean the user already decided."""
    assert installer_module.parse_args(["--autostart"]).autostart is True
    assert installer_module.parse_args(["--no-autostart"]).autostart is False
