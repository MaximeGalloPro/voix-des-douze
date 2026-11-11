"""Opt-in, one-shot resource click; never controls travel or repeats a harvest."""
from __future__ import annotations

import ctypes as C
import json
import os
from pathlib import Path
import plistlib
import random
import sys
import tempfile
import time

from PySide6.QtCore import QObject, QPointF, QTimer

from .overlay import cell_point, fullscreen_transform, screen_key
from .settings import data_directory


class Point(C.Structure):
    _fields_ = [("x", C.c_double), ("y", C.c_double)]


def check_click_window(rows, pid, point, geometry):
    # WindowServer also lists auxiliary title bars outside the screen at layer 0.
    # Only a window covering the click can intercept it; preserve front-to-back order.
    front = next((row for row in rows
                  if row.get("kCGWindowLayer") == 0
                  and (bounds := row.get("kCGWindowBounds", {}))
                  and bounds.get("X", 0) <= point.x() < bounds.get("X", 0) + bounds.get("Width", 0)
                  and bounds.get("Y", 0) <= point.y() < bounds.get("Y", 0) + bounds.get("Height", 0)), {})
    if front.get("kCGWindowOwnerPID") != pid:
        owner = front.get("kCGWindowOwnerName", "inconnue")
        raise RuntimeError(f"Premier clic ignoré : fenêtre devant la cible : {owner}. Remettez le client Dofus sélectionné au premier plan.")
    bounds = front["kCGWindowBounds"]
    expected = {"X": geometry.x(), "Y": geometry.y(),
                "Width": geometry.width(), "Height": geometry.height()}
    if any(abs(bounds.get(key, float("inf")) - value) > 2 for key, value in expected.items()):
        actual = ", ".join(f"{key}={bounds.get(key, '?')}" for key in expected)
        target = ", ".join(f"{key}={value}" for key, value in expected.items())
        raise RuntimeError(f"Premier clic ignoré : fenêtre Dofus ({actual}) différente de l’écran réglé ({target}). Placez Dofus en plein écran sur cet écran.")


class MacClick:
    def __init__(self, trace=lambda *args, **fields: None):
        self.trace = trace
        self.lib = None
        if sys.platform == "darwin":
            self.lib = C.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
            self.lib.AXIsProcessTrusted.restype = C.c_bool
            self.lib.CGEventCreateMouseEvent.argtypes = [C.c_void_p, C.c_uint32, Point, C.c_uint32]
            self.lib.CGEventCreateMouseEvent.restype = C.c_void_p
            self.lib.CGEventPostToPid.argtypes = [C.c_int32, C.c_void_p]
            self.lib.CFRelease.argtypes = [C.c_void_p]
            self.lib.CGWindowListCopyWindowInfo.argtypes = [C.c_uint32, C.c_uint32]
            self.lib.CGWindowListCopyWindowInfo.restype = C.c_void_p
            self.lib.CFPropertyListCreateData.argtypes = [C.c_void_p, C.c_void_p, C.c_long, C.c_ulong, C.c_void_p]
            self.lib.CFPropertyListCreateData.restype = C.c_void_p
            self.lib.CFDataGetLength.argtypes = [C.c_void_p]
            self.lib.CFDataGetLength.restype = C.c_long
            self.lib.CFDataGetBytePtr.argtypes = [C.c_void_p]
            self.lib.CFDataGetBytePtr.restype = C.c_void_p

    def __call__(self, pid, point, geometry):
        trusted = self.lib is not None and self.lib.AXIsProcessTrusted()
        self.trace("accessibilité", autorisé=trusted)
        if not trusted:
            raise RuntimeError("Autorisez l’application dans Réglages Système → Confidentialité et sécurité → Accessibilité.")
        windows = self.lib.CGWindowListCopyWindowInfo(17, 0)
        data = None
        try:
            if windows:
                data = self.lib.CFPropertyListCreateData(None, windows, 100, 0, None)
            if not data:
                raise RuntimeError("Vérification de la fenêtre Dofus impossible.")
            rows = plistlib.loads(C.string_at(self.lib.CFDataGetBytePtr(data), self.lib.CFDataGetLength(data)))
            self.trace("fenêtres", fenêtres=[
                {key: row.get(key) for key in ("kCGWindowOwnerPID", "kCGWindowLayer", "kCGWindowBounds")}
                for row in rows])
            check_click_window(rows, pid, point, geometry)
        finally:
            if data:
                self.lib.CFRelease(data)
            if windows:
                self.lib.CFRelease(windows)
        down = self.lib.CGEventCreateMouseEvent(None, 1, Point(point.x(), point.y()), 0)
        up = self.lib.CGEventCreateMouseEvent(None, 2, Point(point.x(), point.y()), 0)
        try:
            if not down or not up:
                raise RuntimeError("Création du clic macOS impossible.")
            # Target the selected process, never the global event stream.
            self.lib.CGEventPostToPid(pid, down)
            self.lib.CGEventPostToPid(pid, up)
            self.trace("macOS", résultat="bouton gauche pressé puis relâché, événements postés au PID")
        finally:
            for event in (down, up):
                if event:
                    self.lib.CFRelease(event)


class FirstResourceClick(QObject):
    def __init__(self, window, send=None):
        super().__init__(window)
        self.window = window
        self.file = None
        self.debug_path = None
        self.send = send if send is not None else MacClick(self.record)
        self.snapshot = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(700)
        self.timer.timeout.connect(self.click)

    def record(self, stage, **fields):
        if self.file is None:
            return
        try:
            if self.file.tell() >= 512 * 1024:
                self.stop_debug()
                self.window.show_error("Journal du clic limité à 512 Kio ; désactivez puis réactivez le debug.")
                return
            self.file.write(json.dumps({"heure": time.time(), "étape": stage, **fields},
                                       ensure_ascii=False) + "\n")
        except OSError as exc:
            self.stop_debug()
            self.window.show_error(f"Journal du clic impossible : {exc}")

    def start_debug(self, directory=None):
        self.stop_debug()
        directory = Path(directory) if directory is not None else data_directory() / "debug"
        directory.mkdir(parents=True, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix="premier-clic-", suffix=".jsonl", dir=directory)
        self.debug_path = Path(path)
        self.file = os.fdopen(fd, "w", encoding="utf-8", buffering=1)
        self.record("début")

    def stop_debug(self):
        if self.file is not None:
            self.file.close()
            self.file = None

    def manual_click(self):
        if self.file is None:
            return
        self.record("raccourci : reçu")
        self.timer.stop()
        self.click(manual=True)

    def clear(self):
        self.timer.stop()
        self.record("annulation", raison="ressources effacées")
        self.snapshot = None

    def update(self, snapshot):
        previous = self.snapshot
        self.snapshot = snapshot
        self.record("carte", carte=snapshot.map_id, ressources=[
            {"élément": r.element_id, "case": r.cell, "état": r.state, "récoltable": r.harvestable}
            for r in snapshot.resources])
        if previous is None or previous.map_id != snapshot.map_id:
            self.timer.stop()
            # First synchronization is not an arrival: it can occur mid-action.
            if previous is not None and self.window.first_resource.isChecked():
                self.timer.start()
                self.record("arrivée programmée", délai_ms=self.timer.interval())

    def click(self, manual=False):
        w = self.window
        snapshot = self.snapshot
        foreground = w.overlay.foreground()
        self.record("tentative", manuel=manual, pid=w.overlay.pid, premier_plan=foreground)
        reason = next((message for blocked, message in [
            (snapshot is None, "aucune carte synchronisée"),
            (not manual and not w.first_resource.isChecked(), "option automatique désactivée"),
            (not w.accepting, "écoute arrêtée"), (w.paused, "écoute suspendue"),
            (w.overlay.calibrating, "réglage des repères en cours"),
            (w.overlay.pid is None, "aucun PID Dofus"),
            (foreground != w.overlay.pid, "Dofus sélectionné hors premier plan"),
        ] if blocked), None)
        if reason:
            self.record("bloqué", raison=reason)
            if manual:
                w.show_error(f"Clic debug ignoré : {reason}.")
            return
        settings = w.store.settings
        screen = next((s for s in w.screen().virtualSiblings()
                       if screen_key(s) == settings.overlay_screen), None)
        adjustment = settings.overlay_adjustments.get(settings.overlay_screen)
        if screen is None or adjustment is None:
            w.show_error("Premier clic ignoré : réglez d’abord les repères sur l’écran Dofus plein écran.")
            self.record("bloqué", raison="écran ou réglage absent", écran=settings.overlay_screen)
            return
        geometry = screen.geometry()
        self.record("projection", écran=settings.overlay_screen, géométrie=geometry.getRect(), réglage=adjustment)
        transform = fullscreen_transform(geometry, adjustment)
        candidates = []
        for resource in snapshot.resources:
            if resource.harvestable and 0 <= resource.cell < 560:
                local = transform.map(cell_point(resource.cell))
                if 0 <= local.x() < geometry.width() and 0 <= local.y() < geometry.height():
                    candidates.append((resource, local + QPointF(geometry.topLeft())))
        if not candidates:
            self.record("bloqué", raison="aucune ressource récoltable projetée dans l’écran")
            if manual:
                w.show_error("Clic debug ignoré : aucune ressource récoltable projetée dans l’écran.")
            return
        resource, point = candidates[0] if manual else random.choice(candidates)
        self.record("cible", élément=resource.element_id, nom=resource.name,
                    case=resource.cell, x=point.x(), y=point.y())
        try:
            self.send(w.overlay.pid, point, geometry)
        except RuntimeError as exc:
            self.record("bloqué", raison=str(exc))
            w.show_error(str(exc))
            return
        self.record("clic envoyé", récolte_confirmée=False)
        w.status.setText(f"Premier clic envoyé : {resource.name}, case {resource.cell} · récolte non confirmée")
