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
import re
from html.parser import HTMLParser
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
# Les céréales historiques conservent la source Dofus 2, plus proche de Unity.
HISTORICAL_CEREALS = {
    "38": "Blé", "39": "Houblon", "42": "Lin", "43": "Orge",
    "44": "Seigle", "45": "Avoine", "46": "Chanvre", "47": "Malt", "111": "Riz",
}
JOL_URL = "https://dofus.jeuxonline.info/actualite/38078/devblog-maturite-ressources"
RETROVERSE_URL = "https://retroverse.xixou.io/guide/metiers.html"
RETROVERSE_FILE = "retroverse-metiers.html"
RETROVERSE_META = {
    "size": 117554,
    "sha256": "3cfe0b4bd1a2bcde68403eaf3110e195c6ddb3b209f1d18fac889afb45efa3ac",
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_verified(cache: Path, filename: str, expected: dict, url: str, offline: bool) -> bytes:
    path = cache / filename
    if path.exists():
        raw = path.read_bytes()
    elif offline:
        raise ValueError(f"Asset absent en mode hors ligne : {path}")
    else:
        with urllib.request.urlopen(url, timeout=120) as response:
            raw = response.read()
        if len(raw) != expected["size"] or digest(raw) != expected["sha256"]:
            raise ValueError(f"Taille/SHA256 incorrect : {filename}")
        cache.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    if len(raw) != expected["size"] or digest(raw) != expected["sha256"]:
        raise ValueError(f"Taille/SHA256 incorrect : {path} (laissé intact)")
    return raw


def load_asset(cache: Path, filename: str, offline: bool) -> dict:
    return json.loads(load_verified(cache, filename, FILES[filename], f"{BASE_URL}/{filename}", offline))


class RespawnTables(HTMLParser):
    """Lire seulement la colonne Repousse des tableaux de ressources."""

    def __init__(self):
        super().__init__()
        self.tables = []
        self.rows = []
        self.caption = ""
        self.cell = None
        self.in_caption = False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.rows, self.caption = [], ""
        elif tag == "caption":
            self.in_caption = True
        elif tag == "tr":
            self.row = []
        elif tag in ("th", "td"):
            self.cell = ""

    def handle_data(self, data):
        if self.in_caption:
            self.caption += data
        if self.cell is not None:
            self.cell += data

    def handle_endtag(self, tag):
        if tag == "caption":
            self.in_caption = False
        elif tag in ("th", "td"):
            self.row.append(" ".join(self.cell.split()))
            self.cell = None
        elif tag == "tr":
            self.rows.append(self.row)
        elif tag == "table" and self.caption.startswith("Ressources du métier "):
            self.tables.append((self.caption, self.rows))


def retroverse_respawns(raw: bytes, names: dict[str, str]) -> dict:
    parser = RespawnTables()
    parser.feed(raw.decode("utf-8"))
    result = {}
    for caption, rows in parser.tables:
        column = rows[0].index("Repousse")
        for row in rows[1:]:
            # Le guide explicite « Frêne (Bois de Frêne) » ; l'interactif est Frêne.
            name = row[0].split(" (", 1)[0]
            # Seule différence orthographique vérifiée entre les deux catalogues.
            name = {"Etain": "Étain"}.get(name, name)
            matches = [type_id for type_id, known in names.items() if known == name]
            if not matches:
                continue
            if len(matches) != 1:
                raise ValueError(f"Correspondance Retroverse ambiguë : {name}")
            match = re.fullmatch(r"(\d+(?:,\d+)?) (s|min)", row[column])
            if not match:
                raise ValueError(f"Délai Retroverse illisible : {name}/{row[column]}")
            seconds = float(match[1].replace(",", ".")) * (60 if match[2] == "min" else 1)
            # Plusieurs gisements portent le même nom dans le guide.
            previous = result.get(matches[0])
            if previous and previous["seconds"] != seconds:
                raise ValueError(f"Délais Retroverse contradictoires : {name}")
            result[matches[0]] = {
                "seconds": int(seconds) if seconds.is_integer() else seconds,
                "source": "retroverse_metiers",
                "label": f"{name} : estimation Retroverse / Rétro, non vérifiée Unity",
                "confidence": "cross_game_estimate",
                "source_name": row[0], "source_table": caption,
            }
    return result


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
    retroverse = load_verified(cache, RETROVERSE_FILE, RETROVERSE_META, RETROVERSE_URL, offline)
    respawns = retroverse_respawns(retroverse, harvest["interactive_names"])
    for type_id, name in sorted(HISTORICAL_CEREALS.items(), key=lambda item: int(item[0])):
        if harvest["interactive_names"].get(type_id) != name:
            raise ValueError(f"Identité de ressource incompatible : {type_id}/{name}")
        respawns[type_id] = {
            "seconds": 600, "source": "jol_maturity_2012",
            "label": f"{name} : estimation historique 2.9 (2012), non vérifiée Unity",
            "confidence": "historical",
        }
    respawns = dict(sorted(respawns.items(), key=lambda item: int(item[0])))
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
            "retroverse_metiers": {
                "url": RETROVERSE_URL, "snapshot_date": "2026-11-12",
                "game_version": "Retroverse / Dofus Rétro (jeu dérivé, pas Unity)",
                "kind": "cross_game_guide", "confidence": "cross_game_estimate",
                "file": RETROVERSE_FILE, **RETROVERSE_META,
                "scope": "Noms d'interactifs du guide présents dans harvest ; qualificatif d'objet entre parenthèses retiré, Etain = Étain vérifié ; aucun délai par défaut ni transposition des bancs vers les espèces de poissons.",
            },
        },
        "policy": {
            "maps": "Affichage de destinations observées uniquement ; aucune ressource ni arête prépeuplée.",
            "world_id": "Valeur worldMap de MapInformationData conservée, y compris -1 (non spécifié).",
            "travel": "Toujours false : flags intérieur/autopilot non décodés ; coordonnées seules insuffisantes.",
            "respawns": "Type absent = délai inconnu ; apprentissage runtime prioritaire, puis JOL Dofus 2 pour les neuf céréales historiques, puis estimation Retroverse / Rétro, non vérifiée Unity.",
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
    print(f"{len(catalog['maps'])} cartes ; {len(catalog['respawns'])} délais estimés ; travel certifié : 0")


if __name__ == "__main__":
    main()
