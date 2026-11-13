"""Ordered idle stops, routed only through directed, observed walking passages."""
from collections import deque

from PySide6.QtCore import QObject, QPointF, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                              QHBoxLayout, QLabel, QListWidget, QPushButton, QVBoxLayout)

from .map_exits import DIRECTIONS, sample_exit
from .overlay import screen_key


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
        if len(stops) < 2 or len(set(stops)) != len(stops):
            raise ValueError('Choisissez au moins deux cartes distinctes, sans doublon.')
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

    @property
    def at_stop(self):
        return not self.path and self.expected is None

    def stop(self):
        self.active = False
        self.expected = None
        self.path.clear()
        self.timeout.stop()
        self.guard.stop()

    def fail(self, message):
        self.owner.stop_idle()
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
        configuration = w.store.settings.idle_routes.get(self.profile, {})
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
        if (not w.accepting or w.paused or w.overlay.calibrating
                or w.overlay.pid is None or w.overlay.foreground() != w.overlay.pid
                or w.store.settings.compass_profile != self.profile):
            self.fail('Arrêt : écoute, premier plan Dofus, profil ou calibration modifié. Réactivez idle pour reprendre.')
            return False
        return True

    def step(self):
        if not self.check() or self.expected is not None:
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
            self.owner.send(self.owner.window.overlay.pid, point, geometry)
        except (ValueError, RuntimeError) as exc:
            self.fail(str(exc))
            return
        self.timeout.start()
        self.owner.idle_changed.emit(
            f'Idle · étape {self.index + 1}/{len(self.stops)} · sortie {DIRECTIONS[direction]} · '
            f'attente carte {target}')

    def arrive(self, map_id):
        if not self.check():
            return False
        if self.expected is None or map_id != self.expected:
            self.fail(f'Arrivée inattendue sur la carte {map_id} (attendue : {self.expected}).')
            return False
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
        self.setWindowTitle('Circuit de cartes idle')
        self.resize(720, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f'Profil : {self.profile or "aucun — choisissez-le dans Boussole"}'))
        help_text = QLabel('Liste ordonnée des cartes où récolter. Les cartes intermédiaires sont traversées sans récolte. '
                           'Après la dernière étape, retour à la première. Les passages doivent avoir été appris, '
                           'dans chaque sens nécessaire, avec la boussole active. Aucun déplacement pendant l’édition.')
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        configuration = window.store.settings.idle_routes.get(self.profile, {})
        self.enabled = QCheckBox('Utiliser ce circuit avec idle')
        self.enabled.setChecked(configuration.get('enabled', False))
        layout.addWidget(self.enabled)
        self.stops = list(configuration.get('maps', []))
        self.list = QListWidget()
        self.list.setAccessibleName('Étapes du circuit idle, dans l’ordre')
        layout.addWidget(self.list)
        row = QHBoxLayout()
        self.choices = QComboBox()
        self.choices.setAccessibleName('Carte à ajouter au circuit')
        known = {r[0] for r in self.memory.db.execute('SELECT map_id FROM visited_maps WHERE profile=?', (self.profile,))}
        for a, b in self.memory.db.execute('SELECT origin,target FROM edges WHERE profile=?', (self.profile,)):
            known.update((a, b))
        snapshot = window.resource_click.snapshot
        if snapshot is not None:
            known.add(snapshot.map_id)
        for map_id in sorted(known):
            self.choices.addItem(map_label(self.memory, map_id), map_id)
        row.addWidget(self.choices, 1)
        self.button(row, 'Ajouter', self.add)
        layout.addLayout(row)
        actions = QHBoxLayout()
        self.button(actions, 'Monter', lambda: self.move(-1))
        self.button(actions, 'Descendre', lambda: self.move(1))
        self.button(actions, 'Retirer', self.remove)
        self.button(actions, 'Vérifier les passages', self.validate)
        layout.addLayout(actions)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText('Enregistrer')
        buttons.button(QDialogButtonBox.Cancel).setText('Annuler')
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def button(self, layout, text, callback):
        button = QPushButton(text)
        button.clicked.connect(callback)
        layout.addWidget(button)

    def refresh(self, selected=0):
        self.list.clear()
        self.list.addItems([f'{i + 1}. {map_label(self.memory, m)}' for i, m in enumerate(self.stops)])
        self.list.setCurrentRow(selected)
        self.notice.setText('Ajoutez des cartes explorées, puis vérifiez la boucle complète, retour inclus.')

    def add(self):
        map_id = self.choices.currentData()
        if map_id is None:
            self.notice.setText('Synchronisez des cartes avec la boussole active pour remplir la liste.')
        elif map_id in self.stops:
            self.notice.setText('Cette carte figure déjà dans le circuit.')
        else:
            self.stops.append(map_id)
            self.refresh(len(self.stops) - 1)

    def move(self, delta):
        index = self.list.currentRow()
        following = index + delta
        if 0 <= index < len(self.stops) and 0 <= following < len(self.stops):
            self.stops[index], self.stops[following] = self.stops[following], self.stops[index]
            self.refresh(following)

    def remove(self):
        index = self.list.currentRow()
        if 0 <= index < len(self.stops):
            self.stops.pop(index)
            self.refresh(min(index, len(self.stops) - 1))

    def validate(self):
        try:
            legs = WalkingGraph(self.memory).loop(tuple(self.stops))
        except ValueError as exc:
            self.notice.setText(str(exc))
            return False
        self.notice.setText('Boucle complète : ' + ' ; '.join(
            f'{a} → {b} : {len(path)} passage(s)' for (a, b), path in legs.items()))
        return True

    def save(self):
        if not self.profile:
            self.notice.setText('Choisissez d’abord un profil dans Boussole.')
            return
        if self.enabled.isChecked() and not self.validate():
            return
        settings = self.window.store.settings
        previous = settings.idle_routes.get(self.profile)
        settings.idle_routes[self.profile] = {'enabled': self.enabled.isChecked(), 'maps': list(self.stops)}
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
