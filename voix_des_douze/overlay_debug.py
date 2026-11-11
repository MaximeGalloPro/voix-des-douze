"""Opt-in, window-scoped adjustment diagnostics; never reads typed text."""
from __future__ import annotations

import ctypes as C
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from PySide6.QtCore import QAbstractNativeEventFilter, QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from .settings import data_directory


QT_KEYS = {
    Qt.Key_Left: "gauche", Qt.Key_Right: "droite", Qt.Key_Up: "haut",
    Qt.Key_Down: "bas", Qt.Key_Plus: "plus", Qt.Key_Equal: "égal",
    Qt.Key_Minus: "moins", Qt.Key_Return: "entrée", Qt.Key_Enter: "entrée-pavé",
    Qt.Key_Escape: "échap",
}
# Physical macOS key codes only. No characters/charactersIgnoringModifiers access.
MAC_KEYS = {123: "gauche", 124: "droite", 125: "bas", 126: "haut",
            24: "égal/plus", 27: "moins", 69: "plus-pavé", 78: "moins-pavé",
            36: "entrée", 76: "entrée-pavé", 53: "échap"}


class AdjustmentDebug(QAbstractNativeEventFilter):
    MAX_BYTES = 512 * 1024

    def __init__(self, widget, directory=None):
        super().__init__()
        self.widget = widget
        self.file = None
        self.path = None
        self.error = ""
        self.active = False
        self.last_native = "aucune"
        self.last_qt = "aucune"
        self.state = None
        self.started = time.monotonic()
        self.window = None
        self.get = None
        self.selector = None
        self.directory = Path(directory) if directory is not None else data_directory() / "debug"
        self.timer = QTimer(widget)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.sample)

    def start(self):
        if self.active:
            return
        self.active = True
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            fd, path = tempfile.mkstemp(prefix="reglage-clavier-", suffix=".jsonl", dir=self.directory)
            self.path = Path(path)
            self.file = os.fdopen(fd, "w", encoding="utf-8", buffering=1)
        except OSError as exc:
            self.error = f"Journal indisponible : {exc}"
        if sys.platform == "darwin" and QGuiApplication.platformName() == "cocoa":
            objc = C.CDLL("/usr/lib/libobjc.A.dylib")
            self.selector = objc.sel_registerName
            self.selector.argtypes = [C.c_char_p]
            self.selector.restype = C.c_void_p
            self.get = C.CFUNCTYPE(C.c_ulong, C.c_void_p, C.c_void_p)(("objc_msgSend", objc))
            self.window = self.get(int(self.widget.winId()), self.selector(b"window"))
            QApplication.instance().installNativeEventFilter(self)
        self.record("début")
        self.sample()
        self.timer.start()

    def record(self, stage, **fields):
        if not self.active or self.file is None:
            return
        try:
            if self.file.tell() >= self.MAX_BYTES:
                self.error = "Journal limité à 512 Kio ; écriture arrêtée"
                self.file.close()
                self.file = None
                return
            self.file.write(json.dumps({"secondes": round(time.monotonic() - self.started, 3),
                                        "étape": stage, **fields}, ensure_ascii=False) + "\n")
        except OSError as exc:
            self.error = f"Écriture interrompue : {exc}"
            self.file.close()
            self.file = None

    def sample(self):
        if not self.active:
            return
        state = {"focus_qt": self.widget.hasFocus(), "active_qt": self.widget.isActiveWindow(),
                 "visible_qt": self.widget.isVisible()}
        if self.window:
            state["key_cocoa"] = bool(self.get(self.window, self.selector(b"isKeyWindow")))
            state["space_actif"] = bool(self.get(self.window, self.selector(b"isOnActiveSpace")))
        if state != self.state:
            self.state = state
            self.record("focus", **state)
        self.widget.update()

    def nativeEventFilter(self, event_type, message):
        if self.active and self.window and bytes(event_type) == b"mac_generic_NSEvent":
            event = int(message)
            if (self.get(event, self.selector(b"type")) == 10
                    and self.get(event, self.selector(b"window")) == self.window):
                key = MAC_KEYS.get(self.get(event, self.selector(b"keyCode")))
                if key is not None:
                    self.last_native = key
                    self.record("cocoa", touche=key)
                    self.widget.update()
        return False, 0

    def qt_key(self, event):
        key = QT_KEYS.get(event.key())
        if self.active and key is not None:
            self.last_qt = key
            self.record("qt", touche=key, maj=bool(event.modifiers() & Qt.ShiftModifier))

    def stop(self):
        if not self.active:
            return
        self.timer.stop()
        if self.window:
            QApplication.instance().removeNativeEventFilter(self)
        self.record("fin", réglage=list(self.widget.adjustment))
        self.active = False
        if self.file is not None:
            self.file.close()
            self.file = None

    def lines(self):
        state = self.state or {}
        cocoa = state.get("key_cocoa", "n/a")
        return [f"DEBUG · Cocoa key={cocoa} · focus Qt={state.get('focus_qt', False)} · Space={state.get('space_actif', 'n/a')}",
                f"Dernière touche de réglage · Cocoa : {self.last_native} · Qt : {self.last_qt}",
                self.error or f"Journal : {self.path}"]
