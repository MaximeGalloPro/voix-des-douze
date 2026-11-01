#!/usr/bin/env python3
"""Installe un lanceur utilisateur et l'épingle au Dock, sans privilèges."""
from pathlib import Path
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import uuid
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
APP = Path.home() / "Applications/Voix des Douze.app"
BUNDLE_ID = "fr.voixdesdouze.local"
MARKER = "VoixDesDouzeLauncherVersion"


def install():
    if sys.platform != "darwin":
        raise RuntimeError("Ce raccourci nécessite macOS.")
    launcher = ROOT / "Lancer.command"
    if not launcher.is_file() or not os.access(ROOT / ".venv/bin/python", os.X_OK):
        raise RuntimeError("Exécutez Installer.command dans ce projet avant de créer le raccourci.")
    runtime_source = Path(sys.base_prefix) / "Resources/Python.app/Contents/MacOS/Python"
    if not runtime_source.is_file():
        raise RuntimeError("Le Python 3.13 du venv doit fournir son exécutable graphique macOS.")
    info_path = APP / "Contents/Info.plist"
    if APP.exists():
        if not info_path.is_file() or plistlib.loads(info_path.read_bytes()).get(MARKER) != 1:
            raise RuntimeError(f"Application existante non gérée, laissée intacte : {APP}")
    executable = APP / "Contents/MacOS/VoixDesDouze"
    executable.parent.mkdir(parents=True, exist_ok=True)
    runtime = executable.parent / "PythonRuntime"
    # Use the GUI executable directly: the normal macOS Python shim would
    # register Python.app in the Dock instead of this application bundle.
    temporary = runtime.with_name(f"PythonRuntime-{uuid.uuid4().hex}")
    try:
        shutil.copy2(runtime_source, temporary)
        os.replace(temporary, runtime)
    finally:
        temporary.unlink(missing_ok=True)
    info = {
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleName": "Voix des Douze",
        "CFBundleDisplayName": "Voix des Douze",
        "CFBundleExecutable": executable.name,
        "CFBundlePackageType": "APPL",
        "CFBundleVersion": "1",
        "CFBundleShortVersionString": "0.1.0",
        "LSMinimumSystemVersion": "14.0",
        "NSHighResolutionCapable": True,
        MARKER: 1,
    }
    info_path.write_bytes(plistlib.dumps(info))
    executable.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        f"if [[ ! -x {shlex.quote(str(launcher))} ]]; then\n"
        "  /usr/bin/osascript -e 'display alert \"Voix des Douze\" message "
        "\"Le dossier du projet a été déplacé. Recréez le raccourci depuis son nouvel emplacement.\"'\n"
        "  exit 1\nfi\n"
        f"cd -- {shlex.quote(str(ROOT))}\n"
        f"export __PYVENV_LAUNCHER__={shlex.quote(str(ROOT / '.venv/bin/python'))}\n"
        f"exec {shlex.quote(str(runtime))} -m voix_des_douze\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    before = subprocess.run(
        ["/usr/bin/defaults", "export", "com.apple.dock", "-"],
        check=True, capture_output=True,
    ).stdout
    preferences = plistlib.loads(before)
    tiles = preferences.get("persistent-apps", [])
    uri = APP.as_uri() + "/"
    pinned = any(
        tile.get("tile-data", {}).get("bundle-identifier") == BUNDLE_ID
        or unquote(tile.get("tile-data", {}).get("file-data", {}).get("_CFURLString", "")).rstrip("/")
        == unquote(uri).rstrip("/")
        for tile in tiles
    )
    if not pinned:
        backup = Path.home() / "Library/Application Support/VoixDesDouze/dock-before-shortcut.plist"
        backup.parent.mkdir(parents=True, exist_ok=True)
        try:
            with backup.open("xb") as stream:
                stream.write(before)
            backup.chmod(0o600)
        except FileExistsError:
            pass
        tile = {
            "GUID": uuid.uuid4().int & 0xFFFFFFFF,
            "tile-type": "file-tile",
            "tile-data": {
                "bundle-identifier": BUNDLE_ID,
                "file-label": "Voix des Douze",
                "file-type": 41,
                "file-data": {"_CFURLString": uri, "_CFURLStringType": 15},
            },
        }
        xml = plistlib.dumps(tile).decode("utf-8")
        subprocess.run(
            ["/usr/bin/defaults", "write", "com.apple.dock", "persistent-apps", "-array-add", xml],
            check=True,
        )
        subprocess.run(["/usr/bin/killall", "Dock"], check=True)
    print(f"Lanceur installé : {APP}")
    print("Raccourci déjà présent dans le Dock." if pinned else "Raccourci ajouté au Dock ; autres éléments conservés.")


if __name__ == "__main__":
    try:
        install()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from exc
