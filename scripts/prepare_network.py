#!/usr/bin/env python3
"""Préparation explicite du catalogue réseau ; aucun téléchargement au lancement."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from voix_des_douze.network_protocol import Catalog, VERSION, data_directory
from prepare_models import download, verified

MANIFEST = Path(__file__).resolve().parents[1] / "models/network.json"


def prepare(directory: Path, verify_only=False):
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest["version"] != VERSION:
        raise RuntimeError("Manifeste réseau incompatible avec le décodeur.")
    if verify_only:
        Catalog.load(directory)
        for item in manifest["files"]:
            if not verified(directory / item["source"], item):
                raise RuntimeError("Fichier absent ou modifié : " + item["source"])
        print(f"Catalogue et sources vérifiés : Dofus {VERSION}")
        return
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    base = f"https://github.com/{manifest['repository']}/releases/download/{manifest['release']}"
    for item in manifest["files"]:
        download(f"{base}/{item['source']}", directory / item["source"], item)
    translations = json.loads((directory / "i18n_fr.json").read_text(encoding="utf-8"))
    npcs = json.loads((directory / "npcsdata.json").read_text(encoding="utf-8"))
    names = {str(row["data"]["id"]): translations[str(row["data"]["nameId"])]
             for row in npcs["references"]["RefIds"] if row["type"]["class"] == "NpcData"
             and str(row["data"]["nameId"]) in translations}
    del npcs
    messages = json.loads((directory / "npcmessagesdata.json").read_text(encoding="utf-8"))
    texts = {str(row["data"]["id"]): {"text": translations[str(row["data"]["messageId"])],
                                    "params": row["data"]["messageParams"]["Array"]}
             for row in messages["references"]["RefIds"] if row["type"]["class"] == "NpcMessageData"
             and str(row["data"]["messageId"]) in translations}
    target = directory / "catalog.json"
    raw = {"version": VERSION, "source": f"{manifest['repository']}@{manifest['release']}", "names": names, "texts": texts}
    if target.exists():
        if json.loads(target.read_text(encoding="utf-8")) != raw:
            raise RuntimeError(f"Catalogue existant différent, laissé intact : {target}. Déplacez-le avant de relancer.")
    else:
        fd, temporary = tempfile.mkstemp(prefix="catalog-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(raw, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
    print(f"Dofus {VERSION} : {len(names)} noms, {len(texts)} répliques françaises. Données Ankama ; source communautaire, sans approbation supposée.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=data_directory())
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    prepare(args.data_dir, args.verify_only)
