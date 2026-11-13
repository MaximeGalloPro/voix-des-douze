"""Opt-in harvesting and ordered idle travel, using guarded macOS clicks."""
from __future__ import annotations

from collections import deque
import ctypes as C
import json
import math
import os
from pathlib import Path
import plistlib
import random
import sys
import tempfile
import time

from PySide6.QtCore import QObject, QPointF, Qt, QTimer, Signal

from .overlay import ForegroundProcess, cell_point, fullscreen_transform, screen_key
from .idle_hint import IdleHint
from .settings import data_directory
from .idle_route import IdleJourney
from .monster_safety import MonsterSafety, PlayerOverlap, cell_coordinates


def idle_nearest(cells, origin):
    """Choose by diamond-grid distance, not screen pixels or random deadlines."""
    coordinates = cell_coordinates

    points = {element_id: coordinates(cell) for element_id, cell in cells.items()}

    def distance(a, b):
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    if origin is not None:
        here = coordinates(origin)
        return min(points, key=lambda key: (distance(here, points[key]), key))

    # No player position: compare greedy open routes from the four extrema.
    # At most four O(n²) walks; never enumerate permutations.
    starts = {choose(points, key=lambda key: (points[key][axis], key))
              for axis in (0, 1) for choose in (min, max)}
    best = None
    for start in sorted(starts):
        remaining = set(points)
        remaining.remove(start)
        current, length = start, 0
        while remaining:
            following = min(remaining, key=lambda key: (
                distance(points[current], points[key]), key))
            length += distance(points[current], points[following])
            remaining.remove(following)
            current = following
        candidate = length, start
        if best is None or candidate < best:
            best = candidate
    return best[1]


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
        self.foreground = ForegroundProcess()
        self.lib = None
        if sys.platform == "darwin":
            self.lib = C.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
            self.lib.AXIsProcessTrusted.restype = C.c_bool
            self.lib.CGEventCreateMouseEvent.argtypes = [C.c_void_p, C.c_uint32, Point, C.c_uint32]
            self.lib.CGEventCreateMouseEvent.restype = C.c_void_p
            self.lib.CGEventPost.argtypes = [C.c_uint32, C.c_void_p]
            self.lib.CGEventPost.restype = None
            self.lib.CGEventSetIntegerValueField.argtypes = [C.c_void_p, C.c_uint32, C.c_int64]
            self.lib.CGEventSetIntegerValueField.restype = None
            self.lib.CGEventSetFlags.argtypes = [C.c_void_p, C.c_uint64]
            self.lib.CGEventSetFlags.restype = None
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
        events = []
        try:
            for kind in (5, 1, 2):  # Mouse moved, left down, left up.
                event = self.lib.CGEventCreateMouseEvent(None, kind, Point(point.x(), point.y()), 0)
                if not event:
                    raise RuntimeError("Création du clic macOS impossible.")
                events.append(event)
                # The ':' shortcut may still hold Shift; request a plain left click.
                self.lib.CGEventSetFlags(event, 0)
                if kind != 5:
                    self.lib.CGEventSetIntegerValueField(event, 1, 1)  # kCGMouseEventClickState
            # Recheck focus immediately before posting to the global HID stream.
            foreground = self.foreground()
            self.trace("focus avant envoi global", pid=pid, premier_plan=foreground)
            if foreground != pid:
                raise RuntimeError("Premier clic ignoré : Dofus sélectionné hors premier plan.")
            self.lib.CGEventPost(0, events[0])  # kCGHIDEventTap
            self.lib.CGEventPost(0, events[1])
            try:
                # Give frame-polled clients time to observe the pressed button.
                # Do not pump Qt events here: another shortcut must not reenter.
                time.sleep(0.08)
            finally:
                self.lib.CGEventPost(0, events[2])
            self.trace("macOS", résultat="mouvement, clic gauche sans modificateur maintenu 80 ms, événements globaux HID")
        finally:
            for event in events:
                self.lib.CFRelease(event)


class FirstResourceClick(QObject):
    idle_changed = Signal(str)

    def __init__(self, window, send=None):
        super().__init__(window)
        self.window = window
        self.file = None
        self.debug_path = None
        self.send = send if send is not None else MacClick(self.record)
        self.snapshot = None
        self.monsters = MonsterSafety()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.debug_targets = deque()
        self.debug_timer = QTimer(self)
        self.debug_timer.setSingleShot(True)
        self.debug_timer.setTimerType(Qt.PreciseTimer)
        self.debug_timer.timeout.connect(self.debug_next)
        self.timer.timeout.connect(self.click)
        self.idle = False
        self.idle_pending = {}
        self.idle_skipped = set()
        self.idle_target = None
        self.idle_origin = None
        self.idle_timer = QTimer(self)
        self.idle_timer.setSingleShot(True)
        self.idle_timer.setTimerType(Qt.PreciseTimer)
        self.idle_timer.timeout.connect(self.idle_next)
        self.idle_timeout = QTimer(self)
        self.idle_timeout.setSingleShot(True)
        self.idle_timeout.setTimerType(Qt.PreciseTimer)
        self.idle_timeout.setInterval(10000)
        self.idle_timeout.timeout.connect(self.idle_expired)
        self.idle_hint = IdleHint(window)
        self.journey = IdleJourney(self)

    def stop_idle(self):
        self.journey.stop()
        self.idle = False
        self.idle_hint.set_active(False)
        self.idle_timer.stop()
        self.idle_timeout.stop()
        self.idle_pending.clear()
        self.idle_skipped.clear()
        self.idle_target = None
        self.idle_origin = None
        self.idle_changed.emit("Idle désactivé")

    def toggle_idle(self):
        if self.idle:
            self.stop_idle()
            return
        w = self.window
        if getattr(w, "route_editor", None) is not None:
            w.show_error("Idle : terminez ou annulez l’enregistrement du circuit.")
            return
        if (self.snapshot is None or not w.accepting or w.paused
                or w.overlay.pid is None or w.overlay.foreground() != w.overlay.pid):
            w.show_error("Idle : synchronisez une carte et gardez Dofus au premier plan pendant l’écoute.")
            return
        if w.overlay.calibrating:
            w.show_error("Idle : terminez le réglage des repères ou des sorties.")
            return
        try:
            self.journey.start(self.snapshot)
        except ValueError as exc:
            self.journey.stop()
            w.show_error(f"Circuit idle : {exc}")
            return
        self.timer.stop()
        self.cancel_debug_sequence()
        self.idle = True
        self.idle_hint.set_active(True)
        now = time.monotonic()
        self.idle_pending = {
            r.element_id: now + random.randint(800, 1800) / 1000
            for r in self.snapshot.resources if r.harvestable
            and (not self.journey.active or self.journey.at_stop)}
        self.idle_changed.emit("Idle actif · circuit de cartes" if self.journey.active
                               else "Idle actif · une cible à la fois · 800–1 800 ms")
        if self.idle_pending or self.journey.active:
            self.idle_timer.start(random.randint(800, 1800) if self.journey.active else 0)

    def idle_next(self):
        if not self.idle or self.idle_target is not None:
            return
        if self.journey.active:
            if not self.journey.check() or self.journey.expected is not None:
                return
            if not self.journey.at_stop:
                self.journey.step()
                return
        if not self.idle_pending:
            if self.journey.active and not any(
                    r.element_id not in self.idle_skipped and (r.harvestable or r.state == 2)
                    for r in self.snapshot.resources):
                self.journey.step()
            return
        cells = {r.element_id: r.cell for r in self.snapshot.resources
                 if r.element_id in self.idle_pending and r.harvestable
                 and 0 <= r.cell < 560}
        if not cells:
            self.idle_pending.clear()
            if self.journey.active:
                self.journey.fail("Ressources disponibles mais cases non projetables ; vérifiez les repères.")
            return
        element_id = idle_nearest(cells, self.idle_origin)
        remaining = self.idle_pending[element_id] - time.monotonic()
        if remaining > 0:
            self.idle_timer.start(max(1, math.ceil(remaining * 1000)))
            return
        del self.idle_pending[element_id]
        self.idle_target = element_id
        if not self.click(manual=True, element_id=element_id):
            self.stop_idle()
            return
        if self.idle_target is not None:
            self.idle_timeout.start()
        else:
            self.idle_timer.start(0)

    def idle_expired(self):
        if not self.idle or self.idle_target is None:
            return
        self.idle_timeout.stop()
        element_id = self.idle_target
        self.idle_skipped.add(element_id)
        self.idle_pending.pop(element_id, None)
        self.idle_target = None
        self.idle_changed.emit(f"Idle · récolte non confirmée · élément {element_id} ignoré pour cette visite")
        self.idle_next()

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
        self.cancel_debug_sequence()
        self.window.overlay.clear_click_target()
        if self.file is not None:
            self.file.close()
            self.file = None

    def manual_click(self):
        self.stop_idle()
        if self.file is None:
            return
        if self.debug_timer.isActive() or self.debug_targets:
            self.cancel_debug_sequence()
            self.record("séquence annulée", raison="nouvel appui sur :")
            return
        self.record("raccourci : reçu")
        self.timer.stop()
        if self.snapshot is not None:
            self.debug_targets.extend(dict.fromkeys(
                r.element_id for r in self.snapshot.resources
                if r.harvestable and 0 <= r.cell < 560))
        self.debug_next()

    def cancel_debug_sequence(self):
        self.debug_timer.stop()
        self.debug_targets.clear()

    def debug_next(self):
        if self.file is None or self.snapshot is None:
            self.cancel_debug_sequence()
            return
        available = {r.element_id for r in self.snapshot.resources if r.harvestable}
        while self.debug_targets:
            element_id = self.debug_targets.popleft()
            if element_id not in available:
                continue
            if not self.click(manual=True, element_id=element_id):
                self.cancel_debug_sequence()
                return
            if self.debug_targets and self.file is not None:
                delay = random.randint(700, 1000)
                self.debug_timer.start(delay)
                self.record("suite debug programmée", délai_ms=delay)
            return

    def clear(self):
        self.stop_idle()
        self.cancel_debug_sequence()
        self.timer.stop()
        self.window.overlay.clear_click_target()
        self.record("annulation", raison="ressources effacées")
        self.snapshot = None
        self.monsters.clear()

    def update(self, snapshot):
        previous = self.snapshot
        self.snapshot = snapshot
        self.record("carte", carte=snapshot.map_id, ressources=[
            {"élément": r.element_id, "case": r.cell, "état": r.state, "récoltable": r.harvestable}
            for r in snapshot.resources])
        if previous is None or previous.map_id != snapshot.map_id:
            self.idle_skipped.clear()
            was_journey = self.idle and self.journey.active
            travelling = was_journey and self.journey.arrive(snapshot.map_id)
            if not travelling:
                self.stop_idle()
            self.cancel_debug_sequence()
            self.window.overlay.clear_click_target()
            self.timer.stop()
            if travelling:
                self.idle_timeout.stop()
                self.idle_target = None
                self.idle_origin = None
                now = time.monotonic()
                self.idle_pending = {
                    r.element_id: now + random.randint(800, 1800) / 1000
                    for r in snapshot.resources if r.harvestable and self.journey.at_stop}
                self.idle_timer.start(random.randint(800, 1800))
            # First sync is silent; circuit owns arrivals, including failed ones.
            elif not was_journey and previous is not None and self.window.first_resource.isChecked():
                self.timer.start(random.randint(700, 850))
                self.record("arrivée programmée", délai_ms=self.timer.interval())
        elif self.idle:
            if self.journey.active and (self.journey.expected is not None or not self.journey.at_stop):
                return
            before = {r.element_id for r in previous.resources if r.harvestable}
            available = {r.element_id for r in snapshot.resources if r.harvestable}
            if self.idle_target is not None:
                target = next((r for r in snapshot.resources
                               if r.element_id == self.idle_target), None)
                if target is None or target.state == 1 or (
                        target.state == 0 and not target.harvestable):
                    self.idle_target = None
                    self.idle_timeout.stop()
            for element_id in self.idle_pending.keys() - available:
                del self.idle_pending[element_id]
            now = time.monotonic()
            for element_id in (available - before) - {self.idle_target} - self.idle_skipped:
                self.idle_pending[element_id] = now + random.randint(800, 1800) / 1000
            if self.idle_target is None and (self.idle_pending or self.journey.active):
                self.idle_timer.start(0 if self.idle_pending else random.randint(800, 1800))

    def guarded_send(self, map_id, point, geometry):
        settings = self.window.store.settings
        adjustment = settings.overlay_adjustments.get(settings.overlay_screen)
        if adjustment is None:
            raise RuntimeError("Protection anti-combat : réglez les repères avant tout clic.")
        inverse, valid = fullscreen_transform(geometry, adjustment).inverted()
        if not valid:
            raise RuntimeError("Protection anti-combat : projection des cases invalide.")
        local = inverse.map(point - QPointF(geometry.topLeft()))
        coordinates = local.x() + local.y() / 2, local.x() - local.y() / 2
        self.monsters.send(map_id, coordinates,
                           lambda: self.send(self.window.overlay.pid, point, geometry))

    def click(self, manual=False, element_id=None):
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
            if (resource.harvestable and 0 <= resource.cell < 560
                    and (element_id is None or resource.element_id == element_id)):
                local = transform.map(cell_point(resource.cell))
                if 0 <= local.x() < geometry.width() and 0 <= local.y() < geometry.height():
                    candidates.append((resource, local + QPointF(geometry.topLeft())))
        if not candidates and element_id is not None:
            self.record("cible ignorée", élément=element_id, raison="hors écran ou indisponible")
            return True
        if not candidates:
            self.record("bloqué", raison="aucune ressource récoltable projetée dans l’écran")
            if manual:
                w.show_error("Clic debug ignoré : aucune ressource récoltable projetée dans l’écran.")
            return
        resource, point = candidates[0] if manual else random.choice(candidates)
        self.record("cible", élément=resource.element_id, nom=resource.name,
                    case=resource.cell, x=point.x(), y=point.y())
        if self.file is not None:
            w.overlay.show_click_target(point, geometry)
        try:
            self.guarded_send(snapshot.map_id, point, geometry)
        except PlayerOverlap as exc:
            self.record("cible ignorée", élément=resource.element_id, raison=str(exc))
            if self.idle and element_id == self.idle_target:
                self.idle_skipped.add(element_id)
                self.idle_target = None
                self.idle_changed.emit("Idle · cible masquée par un joueur · ignorée pour cette visite")
                return True
            w.show_error(str(exc))
            return
        except RuntimeError as exc:
            self.record("bloqué", raison=str(exc))
            w.show_error(str(exc))
            return
        if self.idle and element_id == self.idle_target:
            self.idle_origin = resource.cell
        self.record("clic envoyé", récolte_confirmée=False)
        w.status.setText(f"Premier clic envoyé : {resource.name}, case {resource.cell} · récolte non confirmée")
        return True
