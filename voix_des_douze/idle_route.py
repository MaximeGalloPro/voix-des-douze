"""Ordered idle stops, routed only through directed, observed walking passages."""
from collections import deque

from PySide6.QtCore import QObject, QPointF, QTimer
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox,
                              QLabel, QLineEdit, QListWidget, QPushButton, QVBoxLayout)

from .map_exits import DIRECTIONS, sample_exit
from .overlay import screen_key
from .monster_safety import MonsterProximity, PlayerOverlap


def direction_between(maps, origin, target):
    a, b = maps.get(str(origin)), maps.get(str(target))
    if not a or not b or a['world_id'] < 0 or a['world_id'] != b['world_id']:
        return None
    return {(1, 0): 'right', (-1, 0): 'left', (0, 1): 'down', (0, -1): 'up'}.get(
        (b['x'] - a['x'], b['y'] - a['y']))


class WalkingGraph:
    def __init__(self, memory):
        self.maps = memory.catalog['maps']
        self.adjacency = {}
        for origin, target in memory.db.execute(
                'SELECT origin,target FROM edges WHERE profile=? ORDER BY origin,target', (memory.profile,)):
            if direction_between(self.maps, origin, target):
                self.adjacency.setdefault(origin, []).append(target)

    def path(self, origin, target):
        parents = {origin: None}
        queue = deque([origin])
        while queue and target not in parents:
            here = queue.popleft()
            for following in self.adjacency.get(here, ()):
                if following not in parents:
                    parents[following] = here
                    queue.append(following)
        if target not in parents:
            raise ValueError(f'Passage appris manquant : carte {origin} → {target}. '
                             'Activez la boussole et parcourez manuellement ce trajet dans ce sens.')
        path = []
        here = target
        while here != origin:
            path.append(here)
            here = parents[here]
        return tuple(reversed(path))

    def loop(self, stops):
        if len(stops) < 2 or len(set(stops)) < 2:
            raise ValueError('Enregistrez au moins deux cartes distinctes.')
        return {(a, b): self.path(a, b) for a, b in zip(stops, stops[1:] + stops[:1])}


def map_label(memory, map_id):
    position = memory.catalog['maps'].get(str(map_id))
    return (f"[{position['x']}, {position['y']}] · monde {position['world_id']} · carte {map_id}"
            if position else f'Carte {map_id} · coordonnées inconnues')


class IdleJourney(QObject):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.active = False
        self.expected = None
        self.path = deque()
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.setInterval(20000)
        self.timeout.timeout.connect(lambda: self.fail('Changement de carte non confirmé après 20 s ; aucun nouveau clic envoyé.'))
        self.guard = QTimer(self)
        self.guard.setInterval(250)
        self.guard.timeout.connect(self.check)
        self.retry = QTimer(self)
        self.retry.setSingleShot(True)
        self.retry.setInterval(500)
        self.retry.timeout.connect(self.step)

    @property
    def at_stop(self):
        return not self.path and self.expected is None

    def stop(self):
        self.active = False
        self.expected = None
        self.path.clear()
        self.timeout.stop()
        self.guard.stop()
        self.retry.stop()

    def fail(self, message):
        self.owner.stop_idle(message)
        self.owner.window.show_error('Circuit idle : ' + message)

    def exit_screen(self):
        w = self.owner.window
        settings = w.store.settings
        screen = next((s for s in w.screen().virtualSiblings()
                       if screen_key(s) == settings.map_exit_screen), None)
        profile = settings.map_exits.get(settings.map_exit_screen)
        if screen is None or profile is None or profile['size'] != [screen.geometry().width(), screen.geometry().height()]:
            raise ValueError('Réglez les quatre sorties sur l’écran Dofus à sa définition actuelle.')
        if settings.map_exit_screen != settings.overlay_screen:
            raise ValueError('Les sorties et les repères de récolte doivent utiliser le même écran Dofus.')
        return screen, profile

    def start(self, snapshot):
        w = self.owner.window
        self.profile = w.store.settings.compass_profile
        collection = w.store.settings.idle_routes.get(self.profile, {})
        configuration = collection.get('circuits', {}).get(collection.get('selected'), {})
        if not configuration.get('enabled'):
            return
        memory = w.compass.memory
        if not self.profile or memory.profile != self.profile:
            raise ValueError('Choisissez un profil serveur / personnage dans Boussole.')
        self.stops = tuple(configuration['maps'])
        self.graph = WalkingGraph(memory)
        self.legs = self.graph.loop(self.stops)
        if snapshot.map_id not in self.stops and not any(
                snapshot.map_id in leg for leg in self.legs.values()):
            return
        self.exit_screen()
        self.index = self.stops.index(snapshot.map_id) if snapshot.map_id in self.stops else 0
        self.path = deque(self.graph.path(snapshot.map_id, self.stops[self.index]))
        self.active = True
        self.guard.start()

    def check(self):
        if not self.active:
            return False
        w = self.owner.window
        foreground = w.overlay.foreground()
        reasons = [message for blocked, message in (
            (not w.accepting, "écoute inactive"), (w.paused, "écoute suspendue"),
            (w.overlay.calibrating, "calibration ouverte"),
            (w.overlay.pid is None, "PID Dofus absent"),
            (foreground != w.overlay.pid, f"premier plan perdu (PID observé {foreground}, Dofus {w.overlay.pid})"),
            (w.store.settings.compass_profile != self.profile, "profil modifié")) if blocked]
        if reasons:
            self.fail("Arrêt : " + " ; ".join(reasons) + ". Réactivez idle pour reprendre.")
            return False
        return True

    def step(self):
        if not self.check() or self.expected is not None or self.retry.isActive():
            return
        current = self.owner.snapshot.map_id
        if not self.path:
            previous = self.stops[self.index]
            self.index = (self.index + 1) % len(self.stops)
            self.path.extend(self.legs[previous, self.stops[self.index]])
        target = self.path[0]
        direction = direction_between(self.graph.maps, current, target)
        if direction is None:
            self.fail('Passage non adjacent ; déplacement annulé.')
            return
        try:
            screen, profile = self.exit_screen()
            geometry = screen.geometry()
            point = sample_exit(profile, direction) + QPointF(geometry.topLeft())
            # Mark in flight before posting; no further action until full map confirmation.
            self.expected = target
            self.owner.record("sortie tentée", origine=current, destination=target,
                              direction=direction, x=point.x(), y=point.y())
            self.owner.guarded_send(current, point, geometry)
        except (MonsterProximity, PlayerOverlap) as exc:
            self.expected = None
            self.owner.record("sortie en attente", origine=current, destination=target,
                              raison=str(exc), réévaluation_ms=self.retry.interval())
            self.owner.idle_changed.emit(f'Idle · sortie {DIRECTIONS[direction]} occupée · attente obstacle')
            self.retry.start()
            return
        except (ValueError, RuntimeError) as exc:
            self.fail(str(exc))
            return
        self.timeout.start()
        self.owner.record("sortie envoyée", origine=current, destination=target,
                          délai_confirmation_ms=self.timeout.interval())
        self.owner.idle_changed.emit(
            f'Idle · étape {self.index + 1}/{len(self.stops)} · sortie {DIRECTIONS[direction]} · '
            f'attente carte {target}')

    def arrive(self, map_id):
        if not self.check():
            return False
        if self.expected is None or map_id != self.expected:
            self.fail(f'Arrivée inattendue sur la carte {map_id} (attendue : {self.expected}).')
            return False
        self.owner.record("arrivée confirmée", carte=map_id)
        self.path.popleft()
        self.expected = None
        self.timeout.stop()
        self.owner.idle_changed.emit(
            f'Idle · étape {self.index + 1}/{len(self.stops)} · '
            + ('récolte' if self.at_stop else 'traversée'))
        return True


class RouteEditor(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.memory = window.compass.memory
        self.profile = window.store.settings.compass_profile
        self.recording = False
        self.last_map = None
        self.setWindowTitle('Enregistrer un circuit idle')
        self.resize(620, 440)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f'Profil : {self.profile or "à choisir dans Boussole"}'))
        collection = window.store.settings.idle_routes.get(self.profile, {})
        selected = collection.get('selected', '')
        configuration = collection.get('circuits', {}).get(selected, {})
        self.name = QLineEdit(selected)
        self.name.setPlaceholderText('Nom du circuit')
        self.name.setAccessibleName('Nom du circuit idle')
        layout.addWidget(self.name)
        help_text = QLabel('Saisissez un nom, puis lancez l’enregistrement. La prochaine carte rejointe '
                           'sera la première étape. Parcourez le circuit dans Dofus, puis cliquez sur '
                           'Terminé. La carte actuelle n’est pas ajoutée au départ. Aucun clic automatique '
                           'n’est envoyé pendant l’enregistrement.')
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self.stops = list(configuration.get('maps', []))
        self.list = QListWidget()
        self.list.setAccessibleName('Cartes enregistrées dans l’ordre de visite')
        layout.addWidget(self.list)
        self.enabled = QCheckBox('Utiliser ce circuit avec idle')
        self.enabled.setChecked(configuration.get('enabled', False))
        layout.addWidget(self.enabled)
        self.start_button = QPushButton('Lancer l’enregistrement')
        self.start_button.clicked.connect(self.start_recording)
        layout.addWidget(self.start_button)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.finish_button = buttons.button(QDialogButtonBox.Save)
        self.finish_button.setText('Terminé')
        self.finish_button.setEnabled(bool(self.stops))
        buttons.button(QDialogButtonBox.Cancel).setText('Annuler')
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self):
        self.list.clear()
        self.list.addItems([f'{i + 1}. {map_label(self.memory, m)}' for i, m in enumerate(self.stops)])

    def start_recording(self):
        if not self.profile or self.memory.profile != self.profile:
            self.notice.setText('Choisissez d’abord un profil dans Boussole.')
            return
        if not self.name.text().strip():
            self.notice.setText('Saisissez le nom du circuit.')
            return
        if not self.window.accepting or self.window.paused:
            self.notice.setText('Démarrez l’écoute avant l’enregistrement.')
            return
        self.window.resource_click.stop_idle("enregistrement d’un circuit")
        self.window.resource_click.timer.stop()
        self.window.resource_click.cancel_debug_sequence()
        snapshot = self.window.resource_click.snapshot
        self.last_map = snapshot.map_id if snapshot is not None else None
        self.stops = []
        self.recording = True
        self.name.setEnabled(False)
        self.start_button.setEnabled(False)
        self.finish_button.setEnabled(True)
        self.refresh()
        self.notice.setText('Enregistrement armé · en attente de la prochaine carte.')

    def observe(self, snapshot):
        if not self.recording or snapshot.map_id == self.last_map:
            return
        if self.window.store.settings.compass_profile != self.profile:
            self.interrupt()
            return
        if self.stops:
            self.memory.observe_transition(self.stops[-1], snapshot.map_id)
        self.last_map = snapshot.map_id
        self.stops.append(snapshot.map_id)
        self.refresh()
        self.notice.setText(f'Enregistrement en cours · {len(self.stops)} carte(s). Cliquez sur Terminé pour arrêter.')

    def interrupt(self):
        if self.recording:
            self.recording = False
            self.start_button.setEnabled(True)
            self.name.setEnabled(True)
            self.finish_button.setEnabled(False)
            self.notice.setText('Enregistrement interrompu par l’arrêt de l’écoute ou un changement de profil. Relancez-le.')

    def done(self, result):
        self.recording = False
        super().done(result)

    def save(self):
        # Stop first: later network arrivals must not extend a finished recording.
        self.recording = False
        self.start_button.setEnabled(True)
        self.name.setEnabled(True)
        if not self.name.text().strip():
            self.notice.setText('Saisissez le nom du circuit.')
            return
        if len(self.stops) > 1 and self.stops[-1] == self.stops[0]:
            self.stops.pop()
            self.refresh()
        if len(set(self.stops)) < 2:
            self.notice.setText('Enregistrez au moins deux cartes distinctes avant de terminer.')
            return
        if self.enabled.isChecked():
            try:
                WalkingGraph(self.memory).loop(tuple(self.stops))
            except ValueError as exc:
                self.notice.setText(str(exc) + ' Circuit arrêté ; décochez son utilisation pour le conserver.')
                return
        settings = self.window.store.settings
        previous = settings.idle_routes.get(self.profile)
        name = self.name.text().strip()
        circuits = dict(previous.get('circuits', {})) if previous else {}
        circuits[name] = {'enabled': self.enabled.isChecked(), 'maps': list(self.stops)}
        settings.idle_routes[self.profile] = {'selected': name, 'circuits': circuits}
        try:
            self.window.store.save()
        except OSError as exc:
            if previous is None:
                settings.idle_routes.pop(self.profile)
            else:
                settings.idle_routes[self.profile] = previous
            self.notice.setText(f'Circuit non enregistré : {exc}')
            return
        self.accept()
