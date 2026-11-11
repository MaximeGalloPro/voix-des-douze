#!/usr/bin/env python3
"""Build the bundled harvest metadata from pinned, verified game data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.prepare_models import download, verified
from voix_des_douze.network_protocol import VERSION, data_directory

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "ledouxm/dofus-sqlite@v6.0_3.7.13.17"
FILES = {
    "interactivesdata.json": {"size": 176076, "sha256": "3d2f2c5895998936114cfbe6d785f05f836dd53228cd7778a8d273fcb1f1a68f"},
    "skillsdata.json": {"size": 458144, "sha256": "2279f0162f2a8c7d5c717c66bfce104a8fbbbaf79821857e49f50ddec8ec2d61"},
    "i18n_fr.json": {"size": 28975738, "sha256": "ab0db2a7907858c954d18922b2916ad0cfa6d802bc1f7e255818b0573c33bdc3"},
}


def build(directory, output):
    if VERSION != "3.7.13.17":
        raise RuntimeError("Revérifier les sources et empreintes de récolte pour cette version.")
    directory.mkdir(parents=True, exist_ok=True)
    for name, spec in FILES.items():
        if not verified(directory / name, spec):
            download(f"https://github.com/ledouxm/dofus-sqlite/releases/download/v6.0_3.7.13.17/{name}", directory / name, spec)
    translations = json.loads((directory / "i18n_fr.json").read_text(encoding="utf-8"))
    interactives = json.loads((directory / "interactivesdata.json").read_text(encoding="utf-8"))
    skills = json.loads((directory / "skillsdata.json").read_text(encoding="utf-8"))
    names = {str(row["data"]["id"]): translations[str(row["data"]["nameId"])]
             for row in interactives["references"]["RefIds"]
             if row["type"]["class"] == "InteractiveData" and str(row["data"]["nameId"]) in translations}
    harvest_skills = sorted(row["data"]["id"] for row in skills["references"]["RefIds"]
                            if row["type"]["class"] == "SkillData" and row["data"]["gatheredRessourceItem"] > 0)
    result = {"version": VERSION, "source": SOURCE, "files": FILES,
              "harvest_skills": harvest_skills, "interactive_names": names}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Catalogue ressources : {len(harvest_skills)} compétences, {len(names)} noms.")
    for key in ("1", "33", "255"):
        print(f"Type {key} : {names.get(key)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=data_directory())
    parser.add_argument("--output", type=Path, default=ROOT / "models/harvest.json")
    args = parser.parse_args()
    build(args.data_dir, args.output)
