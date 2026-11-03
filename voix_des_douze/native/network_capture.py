"""Source exécutée par Python système isolé, uniquement après autorisation macOS.

Aucun import du dépôt, aucun fichier de paquets : sortie vers un FIFO privé.
Le helper meurt avec la session ; il n’est jamais installé comme service.
"""
import argparse
import ipaddress
import os
from pathlib import Path
import re
import stat
import subprocess
import time


def supervise(child, directory, parent, client, lease_timeout=10):
    """Stop capture even if a live/reused PID no longer services the session."""
    heartbeat = directory / "heartbeat"
    stamp = None
    renewed = time.monotonic()
    diagnostic = b""
    try:
        while child.poll() is None and not (directory / "stop").exists():
            try:
                current = heartbeat.stat().st_mtime_ns
                os.kill(parent, 0)
                os.kill(client, 0)
            except (FileNotFoundError, ProcessLookupError):
                break
            now = time.monotonic()
            if current != stamp:
                stamp, renewed = current, now
            if now - renewed >= lease_timeout:
                break
            try:
                child.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                pass
    finally:
        if child.poll() is None:
            child.terminate()
        try:
            _, diagnostic = child.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill()
            _, diagnostic = child.communicate()
    return diagnostic


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    parser.add_argument("parent", type=int)
    parser.add_argument("client", type=int)
    parser.add_argument("uid", type=int)
    parser.add_argument("interface")
    parser.add_argument("local_ip")
    parser.add_argument("local_port", type=int)
    parser.add_argument("remote_ip")
    args = parser.parse_args()
    directory = Path(args.directory)
    info = directory.lstat()
    fifo = directory / "packets"
    pipe_info = fifo.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != args.uid
            or not stat.S_ISFIFO(pipe_info.st_mode) or stat.S_IMODE(pipe_info.st_mode) != 0o600
            or pipe_info.st_uid != args.uid or args.parent <= 1 or args.client <= 1):
        raise RuntimeError("Canal de capture non privé.")
    for address in (args.local_ip, args.remote_ip):
        if ipaddress.ip_address(address).version != 4:
            raise RuntimeError("Capture limitée à IPv4.")
    if not re.fullmatch(r"[A-Za-z0-9_.]+", args.interface) or not 1 <= args.local_port <= 65535:
        raise RuntimeError("Interface ou port invalide.")
    owner = subprocess.check_output(["/bin/ps", "-p", str(args.parent), "-o", "uid="], text=True).strip()
    client = subprocess.check_output(["/bin/ps", "-p", str(args.client), "-o", "comm="], text=True).strip()
    client_uid = subprocess.check_output(["/bin/ps", "-p", str(args.client), "-o", "uid="], text=True).strip()
    if owner != str(args.uid) or client_uid != str(args.uid) or Path(client).name != "Dofus":
        raise RuntimeError("Processus de jeu ou propriétaire incohérent.")
    endpoint = "%s:%d->%s:5555" % (args.local_ip, args.local_port, args.remote_ip)
    connections = subprocess.check_output(["/usr/sbin/lsof", "-a", "-p", str(args.client), "-iTCP:5555",
                                           "-sTCP:ESTABLISHED", "-nP", "-Fn"], text=True)
    if "n" + endpoint not in connections.splitlines():
        raise RuntimeError("La connexion choisie a changé.")
    stop = directory / "stop"
    if stop.exists():
        return
    fd = os.open(str(fifo), os.O_WRONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    os.set_blocking(fd, True)
    flow_filter = "tcp and host %s and host %s and port %d and port 5555" % (
        args.local_ip, args.remote_ip, args.local_port)
    with os.fdopen(fd, "wb", buffering=0) as stream:
        child = subprocess.Popen(["/usr/sbin/tcpdump", "-p", "-i", args.interface, "-nn", "-s", "0", "-U", "-w", "-", flow_filter],
                                 stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.PIPE)
        diagnostic = supervise(child, directory, args.parent, args.client)
        if not stop.exists() and child.returncode:
            raise RuntimeError("Capture interrompue : " + diagnostic.decode(errors="replace")[-500:])


if __name__ == "__main__":
    main()
