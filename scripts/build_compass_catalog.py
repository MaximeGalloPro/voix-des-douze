#!/usr/bin/env python3
"""Construire le catalogue d'affichage, sans ressources ni arêtes de navigation.

Téléchargement explicite uniquement ; cache vérifié en taille/SHA256 avant parsing.
--offline interdit tout téléchargement. --check compare sans réécrire la sortie.
Les RefIds Unity sont résolus depuis objectsById, pas parcourus aveuglément.
Les flags Unity ne sont pas décodés : aucune destination /travel n'est certifiée.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "3.7.13.17"
RELEASE = "v6.0_" + VERSION
BASE_URL = f"https://github.com/ledouxm/dofus-sqlite/releases/download/{RELEASE}"
FILES = {
    "mapscoordinatesdata.json": {
        "size": 3416171,
        "sha256": "1bde4785b6a8fe2506aef0a569de351f162e40a60e2c18fe7eda4a4be3878fa7",
    },
    "mapsinformationdata.json": {
        "size": 8705353,
        "sha256": "e3ee42a77d99ab3698fe64a7e22289e2bc388caa23127963ca08f6664500bcf4",
    },
}
# Identités reprises du catalogue harvest, vérifiées à chaque génération.
# Pas d'extension aux céréales ajoutées après 2012 ni aux autres métiers.
HISTORICAL_CEREALS = {
    "38": "Blé", "39": "Houblon", "42": "Lin", "43": "Orge",
    "44": "Seigle", "45": "Avoine", "46": "Chanvre", "47": "Malt", "111": "Riz",
}
JOL_URL = "https://dofus.jeuxonline.info/actualite/38078/devblog-maturite-ressources"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_asset(cache: Path, filename: str, offline: bool) -> dict:
    path = cache / filename
    expected = FILES[filename]
    if path.exists():
        raw = path.read_bytes()
    elif offline:
        raise ValueError(f"Asset absent en mode hors ligne : {path}")
    else:
        with urllib.request.urlopen(f"{BASE_URL}/{filename}", timeout=120) as response:
            raw = response.read()
        if len(raw) != expected["size"] or digest(raw) != expected["sha256"]:
            raise ValueError(f"Taille/SHA256 incorrect : {filename}")
        cache.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    if len(raw) != expected["size"] or digest(raw) != expected["sha256"]:
        raise ValueError(f"Taille/SHA256 incorrect : {path} (laissé intact)")
    return json.loads(raw)


def indexed_records(asset: dict, class_name: str, key_field: str) -> dict[int, dict]:
    refs = {}
    for ref in asset["references"]["RefIds"]:
        rid = ref["rid"]
        if rid in refs:
            raise ValueError(f"RefId dupliqué : {rid}")
        refs[rid] = ref
    keys = asset["objectsById"]["m_keys"]["Array"]
    values = asset["objectsById"]["m_values"]["Array"]
    if len(keys) != len(values):
        raise ValueError("Index Unity incohérent")
    result = {}
    for key, value in zip(keys, values):
        ref = refs[value["rid"]]
        data = ref["data"]
        if ref["type"]["class"] != class_name or data[key_field] != key or key in result:
            raise ValueError(f"Index/type Unity incohérent : {key}")
        result[key] = data
    return result


def build(cache: Path, harvest_path: Path, offline: bool) -> dict:
    coordinates = indexed_records(load_asset(cache, "mapscoordinatesdata.json", offline),
                                  "MapsCoordinateData", "compressedCoords")
    information = indexed_records(load_asset(cache, "mapsinformationdata.json", offline),
                                  "MapInformationData", "id")
    coordinate_index = {}
    for compressed, record in coordinates.items():
        for map_id in record["mapIds"]["Array"]:
            if map_id in coordinate_index:
                raise ValueError(f"MapId dupliqué dans les coordonnées : {map_id}")
            coordinate_index[map_id] = compressed
    if coordinate_index.keys() != information.keys():
        raise ValueError("Les deux assets ne décrivent pas les mêmes cartes")
    maps = {}
    for map_id, record in sorted(information.items()):
        x, y, world = record["posX"], record["posY"], record["worldMap"]
        if not all(type(value) is int for value in (map_id, x, y, world)):
            raise ValueError(f"Coordonnées non entières : {map_id}")
        if coordinate_index[map_id] != ((x << 16) | (y & 0xFFFF)):
            raise ValueError(f"Coordonnées contradictoires : {map_id}")
        maps[str(map_id)] = {"x": x, "y": y, "world_id": world, "travel": False}
    harvest_raw = harvest_path.read_bytes()
    harvest = json.loads(harvest_raw)
    if harvest["version"] != VERSION or harvest["source"] != f"ledouxm/dofus-sqlite@{RELEASE}":
        raise ValueError("Version du catalogue de récolte incompatible")
    respawns = {}
    for type_id, name in sorted(HISTORICAL_CEREALS.items(), key=lambda item: int(item[0])):
        if harvest["interactive_names"].get(type_id) != name:
            raise ValueError(f"Identité de ressource incompatible : {type_id}/{name}")
        respawns[type_id] = {
            "seconds": 600, "source": "jol_maturity_2012",
            "label": f"{name} : estimation historique 2.9 (2012), non vérifiée Unity",
            "confidence": "historical",
        }
    return {
        "version": 1,
        "game_version": VERSION,
        "release": RELEASE,
        "sources": {
            "maps": {"repository": "ledouxm/dofus-sqlite", "kind": "community_extraction",
                     "confidence": "version_pinned_cross_checked",
                     "files": {name: {"url": f"{BASE_URL}/{name}", **meta}
                               for name, meta in FILES.items()}},
            "harvest_names": {"path": "models/harvest.json", "sha256": digest(harvest_raw),
                              "source": harvest["source"]},
            "jol_maturity_2012": {
                "url": JOL_URL, "date": "2012-11-26", "game_version": "2.9.0",
                "kind": "community_reprint_of_ankama_devblog", "confidence": "historical",
                "quote": "Nous avons donc décidé de l'augmenter à 600 secondes.",
                "scope": "Céréales de l'époque seulement ; aucune confirmation Unity.",
            },
        },
        "policy": {
            "maps": "Affichage de destinations observées uniquement ; aucune ressource ni arête prépeuplée.",
            "world_id": "Valeur worldMap de MapInformationData conservée, y compris -1 (non spécifié).",
            "travel": "Toujours false : flags intérieur/autopilot non décodés ; coordonnées seules insuffisantes.",
            "respawns": "Type absent = délai inconnu ; apprentissage runtime prioritaire sur estimation historique.",
        },
        "maps": maps,
        "respawns": respawns,
    }


def encode(catalog: dict) -> str:
    # Un enregistrement par ligne : compact, déterministe, diffable.
    header = {key: value for key, value in catalog.items() if key not in ("maps", "respawns")}
    prefix = json.dumps(header, ensure_ascii=False, indent=2)[:-2]
    sections = []
    for key in ("maps", "respawns"):
        rows = ["    " + json.dumps(map_id) + ":" + json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                for map_id, record in catalog[key].items()]
        sections.append(f'  "{key}": {{\n' + ",\n".join(rows) + "\n  }")
    return prefix + ",\n" + ",\n".join(sections) + "\n}\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / ".context/compass-assets")
    parser.add_argument("--harvest", type=Path, default=ROOT / "models/harvest.json")
    parser.add_argument("--output", type=Path, default=ROOT / "models/compass_catalog.json")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    catalog = build(args.cache_dir, args.harvest, args.offline)
    output = encode(catalog)
    if args.check:
        if args.output.read_text(encoding="utf-8") != output:
            raise ValueError("Catalogue différent de la génération reproductible")
    else:
        args.output.write_text(output, encoding="utf-8")
    print(f"{len(catalog['maps'])} cartes ; {len(catalog['respawns'])} délais historiques ; travel certifié : 0")


if __name__ == "__main__":
    main()
