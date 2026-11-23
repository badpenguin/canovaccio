"""Integrazione col desktop Linux (editor/desktop.py, 2026-11-23): file installati, link, aggiornamento e rimozione.
Tutto in una cartella temporanea (HOME e XDG_DATA_HOME finti), senza lanciare gli strumenti del sistema."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from editor import APP_COMMAND, APP_ID, desktop

# 2026-11-23: l'integrazione esiste solo su Linux (i test usano link simbolici e le cartelle XDG)
pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="desktop integration is Linux only")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Cartella dell'utente finta, con ~/.local/bin nel PATH."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("PATH", f"{tmp_path / '.local' / 'bin'}:/usr/bin")
    return tmp_path


def _program(home: Path, name: str) -> Path:
    """Un finto programma da collegare (AppImage o canovaccio.sh)."""
    path = home / "apps" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    return path


def test_install_writes_link_menu_entry_types_and_icon(home):
    target = _program(home, "Canovaccio-0.6.3-x86_64.AppImage")
    assert not desktop.is_installed()
    warnings = desktop.install(target, tools=False)
    assert warnings == []
    link = home / ".local" / "bin" / APP_COMMAND
    assert link.is_symlink() and Path(link.readlink()) == target
    assert desktop.is_installed() and desktop.link_is_current(target)
    # il menu lancia il link, non l'AppImage: una nuova versione cambia solo il link
    entry = desktop.desktop_path().read_text()
    assert f"Exec={link} %f" in entry
    assert f"Icon={APP_ID}" in entry
    assert "application/x-canovaccio-project;" in entry and "audio/midi;" in entry
    assert desktop.icon_path().read_bytes() == desktop.ICON.read_bytes()
    assert desktop.mime_path() == home / ".local" / "share" / "mime" / "packages" / "canovaccio.xml"


def test_mime_types_have_generic_icon_and_sty_magic():
    root = ET.fromstring(desktop.mime_xml())
    ns = {"m": "http://www.freedesktop.org/standards/shared-mime-info"}
    types = root.findall("m:mime-type", ns)
    assert [t.get("type") for t in types] == [t[0] for t in desktop.NEW_TYPES]
    for t in types:
        assert t.find("m:generic-icon", ns).get("name") == APP_ID
    sty = types[2]
    assert sty.find("m:glob", ns).get("pattern") == "*.sty"
    assert sty.find("m:magic/m:match", ns).get("value") == "MThd"


def test_real_file_in_place_of_the_link_is_left_alone(home):
    target = _program(home, "canovaccio.sh")
    own = home / ".local" / "bin" / APP_COMMAND
    own.parent.mkdir(parents=True)
    own.write_text("mine")
    warnings = desktop.install(target, tools=False)
    assert own.read_text() == "mine" and not own.is_symlink()
    assert len(warnings) == 1 and "not a link" in warnings[0]
    # senza link il menu lancia direttamente il programma
    assert f"Exec={target} %f" in desktop.desktop_path().read_text()
    desktop.uninstall(tools=False)
    assert own.read_text() == "mine"


def test_warning_when_local_bin_is_not_in_path(home, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    warnings = desktop.install(_program(home, "canovaccio.sh"), tools=False)
    assert len(warnings) == 1 and "PATH" in warnings[0]


def test_should_refresh(home):
    old = _program(home, "Canovaccio-0.6.2-x86_64.AppImage")
    new = _program(home, "Canovaccio-0.6.3-x86_64.AppImage")
    source = _program(home, "canovaccio.sh")
    assert desktop.should_refresh(new)  # niente installato
    desktop.install(old, tools=False)
    assert not desktop.should_refresh(old)  # già a posto
    assert desktop.should_refresh(new)  # nuova versione: si sposta il link
    assert not desktop.should_refresh(source)  # sorgenti accanto all'AppImage: solo dal menu Help
    desktop.install(source, tools=False)
    assert not desktop.should_refresh(new)  # e viceversa
    source.unlink()
    assert desktop.should_refresh(new)  # il programma collegato non c'è più


def test_uninstall_removes_everything(home):
    target = _program(home, "Canovaccio-0.6.3-x86_64.AppImage")
    desktop.install(target, tools=False)
    desktop.uninstall(tools=False)
    assert not desktop.is_installed()
    assert not desktop.link_path().is_symlink()
    assert not desktop.icon_path().exists()
    assert target.exists()  # l'AppImage resta dov'è


def test_exec_path_with_spaces_is_quoted():
    assert desktop._exec_quote(Path("/home/a/My Apps/canovaccio")) == '"/home/a/My Apps/canovaccio"'
    assert desktop._exec_quote(Path("/opt/canovaccio")) == "/opt/canovaccio"


def test_desktop_integration_preference(tmp_path, monkeypatch):
    from editor.ui import preferences

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert preferences.desktop_integration() is None
    assert not preferences.desktop_open_others()
    preferences.set_desktop_integration("yes", True)
    assert preferences.desktop_integration() == "yes" and preferences.desktop_open_others()
    preferences.set_desktop_integration("no")
    assert preferences.desktop_integration() == "no" and preferences.desktop_open_others()
