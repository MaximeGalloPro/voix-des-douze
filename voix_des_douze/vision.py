"""Localisation des panneaux Dofus et OCR Apple Vision français hors ligne.

Rect utilise des coordonnées normalisées, origine haut-gauche. Les clés sont npc,
text et responses. Aucun panneau détecté signifie Dialogue vide (conversation
fermée), pas une reconnaissance de toute l’interface. La détection automatique
cible le thème sombre bleu/violet et les cartouches beige des captures fournies ;
les autres thèmes se configurent par régions manuelles. Vision reste chargé dans
le helper persistant. Les fonctions bloquent et doivent être appelées hors UI.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
import tempfile

from PIL import Image

from .macos import CaptureError, _native


class OCRError(RuntimeError):
    """Échec de reconnaissance Apple Vision ou régions invalides."""


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self):
        values = (self.x, self.y, self.width, self.height)
        if not all(math.isfinite(v) for v in values) or min(self.x, self.y) < 0 or min(self.width, self.height) <= 0 or self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("Zone hors image : coordonnées normalisées 0..1 attendues.")

    def pixels(self, image: Image.Image) -> tuple[int, int, int, int]:
        return (round(self.x * image.width), round(self.y * image.height), round((self.x + self.width) * image.width), round((self.y + self.height) * image.height))


@dataclass(frozen=True)
class Dialogue:
    npc: str = ""
    text: str = ""
    responses: tuple[str, ...] = ()
    regions: dict[str, Rect] = field(default_factory=dict)


def detect_regions(image: Image.Image) -> dict[str, Rect]:
    """Repère les grands aplats bleu nuit, puis valide leur cartouche beige.

    Analyse réduite à 640 px de large ; aucune invocation de Vision. La géométrie
    est relative au panneau, pas à la position de la fenêtre ou au facteur Retina.
    """
    if image.width < 40 or image.height < 40:
        return {}
    small = image.convert("RGB")
    small.thumbnail((640, 640))
    w, h = small.size
    pixels = small.load()
    mask = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            r, g, b = pixels[x, y]
            if 12 <= r <= 65 and 12 <= g <= 65 and 24 <= b <= 105 and b > r * 1.15 and b > g * 1.1:
                mask[y * w + x] = 1
    components = []
    for start in range(len(mask)):
        if not mask[start]:
            continue
        mask[start] = 0
        stack = [start]
        left = right = start % w
        top = bottom = start // w
        area = 0
        while stack:
            current = stack.pop()
            x, y = current % w, current // w
            left, right, top, bottom = min(left, x), max(right, x), min(top, y), max(bottom, y)
            area += 1
            for neighbour in ((current - 1 if x else -1), (current + 1 if x + 1 < w else -1), current - w, current + w):
                if 0 <= neighbour < len(mask) and mask[neighbour]:
                    mask[neighbour] = 0
                    stack.append(neighbour)
        cw, ch = right - left + 1, bottom - top + 1
        if cw >= w * .16 and 12 <= ch <= h * .3 and area / (cw * ch) > .55:
            components.append((left, top, right + 1, bottom + 1))
    candidates = []
    for left, top, right, bottom in components:
        cw = right - left
        # A dialogue has a beige name cartouche straddling its upper edge.
        gold = []
        for y in range(max(0, top - int(cw * .065)), min(h, top + int(cw * .05))):
            for x in range(left + int(cw * .2), right - int(cw * .2)):
                r, g, b = pixels[x, y]
                if 140 < r < 205 and 110 < g < 180 and 18 < r - g < 40 and 32 < g - b < 65:
                    gold.append((x, y))
        if len(gold) < cw * 1.2:
            continue
        # Isolate the cartouche from similarly coloured scenery in the band.
        remaining = set(gold)
        gold_parts = []
        while remaining:
            seed = remaining.pop()
            part, pending = [seed], [seed]
            while pending:
                x, y = pending.pop()
                for point in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if point in remaining:
                        remaining.remove(point)
                        pending.append(point)
                        part.append(point)
            if len(part) >= cw * 1.2:
                gold_parts.append(part)
        if not gold_parts:
            continue
        gold = min(gold_parts, key=lambda part: abs(sum(x for x, _ in part) / len(part) - (left + right) / 2))
        gx0, gx1 = min(x for x, _ in gold), max(x for x, _ in gold)
        if not .15 * cw < gx1 - gx0 < .55 * cw or abs((gx0 + gx1) / 2 - (left + right) / 2) > cw * .15:
            continue
        candidates.append((left, top, right, bottom, gold))
    if not candidates:
        return {}
    left, top, right, bottom, gold = max(candidates, key=lambda c: (c[2] - c[0]) * (c[3] - c[1]))
    cw = right - left

    def rect(x0, y0, x1, y1):
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
        return Rect(x0 / w, y0 / h, (x1 - x0) / w, (y1 - y0) / h)

    regions = {
        "npc": rect(min(x for x, _ in gold) - 1, min(y for _, y in gold) - 1, max(x for x, _ in gold) + 2, max(y for _, y in gold) + 2),
        "text": rect(left + cw * .03, top + cw * .085, right - cw * .035, bottom - cw * .025),
    }
    replies = [c for c in components if c[1] >= bottom and c[1] - bottom < cw * .12 and abs(c[0] - left) < cw * .08 and abs(c[2] - right) < cw * .08]
    if replies:
        a, b, c, d = min(replies, key=lambda c: c[1])
        regions["responses"] = rect(a + cw * .035, b + cw * .025, c - cw * .035, d - cw * .025)
    return regions


def _lines(image: Image.Image) -> list[dict]:
    # Preserve native glyphs: interpolation confused the bank's “0” with “O”.
    with tempfile.TemporaryDirectory(prefix="voix-ocr-") as directory:
        path = Path(directory) / "region.png"
        image.convert("RGB").save(path)
        try:
            lines = _native.call("ocr", path=str(path))
        except CaptureError as exc:
            raise OCRError(str(exc)) from exc
    return sorted(lines, key=lambda item: (round(item["y"] / .015), item["x"]))


def recognize(image: Image.Image, regions: dict[str, Rect] | None = None) -> Dialogue:
    """OCR des seules régions ; réponses groupées par bouton, dans l’ordre visuel."""
    selected = detect_regions(image) if regions is None else dict(regions)
    if not selected:
        return Dialogue()
    unknown = set(selected) - {"npc", "text", "responses"}
    if unknown:
        raise OCRError(f"Zones inconnues : {', '.join(sorted(unknown))}")
    result = {}
    for key, region in selected.items():
        if not isinstance(region, Rect):
            raise OCRError("Chaque zone doit être un Rect normalisé.")
        crop = image.crop(region.pixels(image))
        if crop.width < 1 or crop.height < 1:
            raise OCRError("Zone OCR trop petite.")
        result[key] = _lines(crop)
    responses = []
    previous = None
    for line in result.get("responses", []):
        if previous is not None and line["y"] - (previous["y"] + previous["height"]) < max(line["height"], previous["height"]) * .65:
            responses[-1] += " " + line["text"]
        else:
            responses.append(line["text"])
        previous = line
    return Dialogue(" ".join(line["text"] for line in result.get("npc", [])), " ".join(line["text"] for line in result.get("text", [])), tuple(responses), selected)
