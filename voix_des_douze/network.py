"""Écoute explicite de la seule connexion d’un client Dofus, sans service installé."""
from __future__ import annotations

from dataclasses import dataclass
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import select
import shlex
import subprocess
import tempfile
import threading
import time

from .network_protocol import Catalog, Conversation, PacketReader, ProtocolError, VERSION
from .native.network_capture import game_connections
from .harvest import HarvestTracker
from .gathering import MapTransitions


@dataclass(frozen=True)
class Client:
    pid: int
    label: str


@dataclass(frozen=True)
class Connection:
    local_ip: str
    local_port: int
    remote_ip: str
    remote_port: int = 5555


def _run(command, timeout=5):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise ProtocolError("Outil système indisponible ou client fermé : " + Path(command[0]).name)
    return result.stdout


def clients():
    result = subprocess.run(["/usr/bin/pgrep", "-x", "Dofus"], capture_output=True, text=True, timeout=5)
    if result.returncode == 1:
        return []
    if result.returncode:
        raise ProtocolError("Impossible de rechercher les clients Dofus.")
    found = []
    for item in result.stdout.split():
        pid = int(item)
        try:
            owner = _run(["/bin/ps", "-p", str(pid), "-o", "uid="]).strip()
            executable = _run(["/bin/ps", "-p", str(pid), "-o", "comm="]).strip()
        except ProtocolError:
            continue
        if owner == str(os.getuid()) and Path(executable).name == "Dofus":
            found.append(Client(pid, f"Dofus · PID {pid}"))
    return found


def connection(pid):
    text = _run(["/usr/sbin/lsof", "-a", "-p", str(pid), "-iTCP:5555", "-sTCP:ESTABLISHED", "-nP", "-Fn"])
    found = [Connection(*endpoint) for endpoint in game_connections(text)]
    if len(found) != 1:
        raise ProtocolError("Choisissez un client connecté : une seule connexion de jeu IPv4 ou IPv6 sur le port 5555 est nécessaire.")
    return found[0]


def client_version(pid):
    # Info.plist reports 3.0.0.0, not the actual game build. Use the startup
    # marker in the log demonstrably opened as stdout by this exact process.
    output = _run(["/usr/sbin/lsof", "-a", "-p", str(pid), "-d", "1", "-Fn"])
    paths = [Path(line[1:]) for line in output.splitlines() if line.startswith("n/")]
    if len(paths) != 1 or paths[0].suffix != ".log" or not paths[0].is_file():
        raise ProtocolError("Impossible de vérifier la version du client dans son journal de démarrage.")
    with paths[0].open("r", encoding="utf-8", errors="replace") as stream:
        text = stream.read(128_000)
    match = re.search(r"^INFO .*Dofus Version : ([0-9.]+)\s", text, re.MULTILINE)
    if not match:
        raise ProtocolError("Version du client absente de son journal ; écoute refusée.")
    return match.group(1)


def interface_for(remote_ip):
    family = "-inet6" if ipaddress.ip_address(remote_ip).version == 6 else "-inet"
    text = _run(["/sbin/route", "-n", "get", family, remote_ip])
    match = re.search(r"^\s*interface:\s*([A-Za-z0-9_.]+)\s*$", text, re.MULTILINE)
    if not match:
        raise ProtocolError("Impossible de trouver l’interface de la connexion choisie.")
    return match.group(1)


class NetworkSession:
    """Un worker par session ; stop() ne bloque jamais la boucle Qt.

    emit(kind, value) doit être un pont thread-safe (signal Qt). Aucun texte ni
    paquet n’est journalisé ; les données brutes ne quittent pas le FIFO privé.
    """
    def __init__(self, client: Client, emit, invalidate, catalog_directory=None):
        self.client, self.emit, self.invalidate_audio = client, emit, invalidate
        self.catalog_directory = catalog_directory
        self.stop_event = threading.Event()
        self.state_lock = threading.RLock()
        self.revision = 0
        self.directory = None
        self.thread = threading.Thread(target=self._run, daemon=True, name="Dofus network")
        self.thread.start()

    def stop(self):
        with self.state_lock:
            self.stop_event.set()
            self.revision += 1
            if self.directory:
                try:
                    (self.directory / "stop").touch(mode=0o600)
                except FileNotFoundError:
                    pass
        self.invalidate_audio()

    def deliver(self, revision, callback):
        """Keep queued Qt events from starting audio after a later reply/stop."""
        with self.state_lock:
            if not self.stop_event.is_set() and revision == self.revision:
                callback()

    def _conversation(self, kind, value):
        with self.state_lock:
            if self.stop_event.is_set():
                return
            if kind == "invalidate":
                self.revision += 1
                self.invalidate_audio()
                return
            self.emit(kind, (self.revision, value))

    def _resources(self, snapshot):
        # Resource snapshots are independent of dialogue revision/cancellation.
        with self.state_lock:
            if not self.stop_event.is_set():
                self.emit("resources", snapshot)

    def _run(self):
        auth = None
        fd = None
        error = None
        try:
            if self.client not in clients():
                raise ProtocolError("Le client choisi n’est plus disponible.")
            version = client_version(self.client.pid)
            if version != VERSION:
                raise ProtocolError(f"Client {version} incompatible : mode réseau vérifié uniquement pour {VERSION}.")
            catalog = Catalog.load(self.catalog_directory)
            selected = connection(self.client.pid)
            interface = interface_for(selected.remote_ip)
            state = Conversation(catalog, self._conversation)
            harvest = HarvestTracker(self._resources)
            transitions = MapTransitions(lambda pair: self.emit("map_transition", pair),
                                         lambda event: self.emit("map_timing", event))

            def handle(direction, key, raw):
                transitions.handle(direction, key, raw)
                state.handle(direction, key, raw)
                harvest.handle(direction, key, raw)

            reader = PacketReader(handle, local_ip=selected.local_ip, local_port=selected.local_port,
                                  remote_ip=selected.remote_ip)
            with tempfile.TemporaryDirectory(prefix="voix-network-") as directory:
                with self.state_lock:
                    self.directory = Path(directory)
                    if self.stop_event.is_set():
                        return
                    fifo = self.directory / "packets"
                    os.mkfifo(fifo, 0o600)
                    heartbeat = self.directory / "heartbeat"
                    heartbeat.touch(mode=0o600)
                    fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
                source = (Path(__file__).parent / "native/network_capture.py").read_text(encoding="utf-8")
                arguments = [str(self.directory), str(os.getpid()), str(self.client.pid), str(os.getuid()), interface,
                             selected.local_ip, str(selected.local_port), selected.remote_ip]
                # Freeze the reviewed helper source in the authorization request;
                # privileged Python imports only its system stdlib, never the repo.
                encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
                program = f"import base64;exec(base64.b64decode({encoded!r}))"
                command = shlex.join(["/usr/bin/python3", "-I", "-c", program, *arguments])
                script = "do shell script " + json.dumps(command, ensure_ascii=False) + " with administrator privileges"
                auth = subprocess.Popen(["/usr/bin/osascript", "-e", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self.emit("authorizing", self.client.label)
                authorized_deadline = time.monotonic() + 120
                check_at = time.monotonic() + 3
                ready = False
                heartbeat_at = time.monotonic()
                try:
                    while not self.stop_event.is_set():
                        readable, _, _ = select.select([fd], [], [], 0.2)
                        if readable:
                            data = os.read(fd, 65536)
                            if data:
                                reader.feed(data)
                                if reader.endian is not None and not ready:
                                    ready = True
                                    self.emit("ready", f"{self.client.label} · version {version} · {interface}")
                            elif auth.poll() is not None:
                                break
                            else:
                                self.stop_event.wait(0.05)
                        elif auth.poll() is not None:
                            break
                        now = time.monotonic()
                        if now >= heartbeat_at:
                            heartbeat.touch(mode=0o600)
                            heartbeat_at = now + 1
                        if not ready and now > authorized_deadline:
                            raise ProtocolError("Autorisation ou démarrage de capture trop long ; relancez l’écoute.")
                        if now >= check_at:
                            if connection(self.client.pid) != selected:
                                raise ProtocolError("La connexion a changé ; arrêtez puis relancez l’écoute pour ce client.")
                            check_at = now + 3
                    if not self.stop_event.is_set():
                        stdout, stderr = auth.communicate(timeout=3)
                        if auth.returncode:
                            detail = stderr.decode(errors="replace")
                            raise ProtocolError("Autorisation annulée ou capture refusée." if "-128" in detail else
                                                "Capture impossible : " + detail[-800:])
                        raise ProtocolError("L’écoute s’est arrêtée ; relancez-la après reconnexion.")
                finally:
                    (self.directory / "stop").touch(mode=0o600)
                    if fd is not None:
                        os.close(fd)
                        fd = None
                    if auth is not None:
                        try:
                            auth.communicate(timeout=5)
                        except subprocess.TimeoutExpired:
                            auth.terminate()
                            try:
                                auth.communicate(timeout=2)
                            except subprocess.TimeoutExpired:
                                auth.kill()
                                auth.communicate()
                    with self.state_lock:
                        self.directory = None
        except Exception as exc:
            if not self.stop_event.is_set():
                error = str(exc)
        finally:
            if fd is not None:
                os.close(fd)
            self.stop()
            self.emit("stopped", error)
