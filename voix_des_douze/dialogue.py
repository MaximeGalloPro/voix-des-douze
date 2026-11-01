"""Stabilité visuelle et transitions de conversation, indépendantes de l'interface."""
from difflib import SequenceMatcher
import re
import unicodedata
from PIL import Image, ImageChops, ImageStat


def canonical(text: str) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFC", text).casefold()))


def same_dialogue(a: str, b: str) -> bool:
    a, b = canonical(a), canonical(b)
    if a == b:
        return True
    # Short answers and changed numbers/negation must never disappear as OCR noise.
    if min(len(a), len(b)) < 80:
        return False
    if re.findall(r"\d+", a) != re.findall(r"\d+", b):
        return False
    aw, bw = a.split(), b.split()
    if len(aw) != len(bw):
        return False
    changes = [(x, y) for x, y in zip(aw, bw) if x != y]
    return len(changes) == 1 and SequenceMatcher(None, *changes[0]).ratio() >= 0.82


class DialogueHistory:
    def __init__(self):
        self.last = None

    def closed(self):
        self.last = None

    def accept(self, npc: str, text: str, responses=()) -> bool:
        if not text.strip():
            self.closed()
            return False
        entry = (canonical(npc), text, tuple(responses))
        if self.last and entry[0] == self.last[0] and same_dialogue(entry[1], self.last[1]) and entry[2] == self.last[2]:
            return False
        self.last = entry
        return True


class ImageGate:
    """Return changed immediately, ready once per visually stable crop."""
    def __init__(self, delay=0.35):
        self.delay = delay
        self.previous = None
        self.since = 0.0
        self.processed = False

    def observe(self, image: Image.Image, now: float) -> tuple[bool, bool]:
        sample = image.convert("L").resize((256, 128))
        changed = self.previous is None or ImageStat.Stat(ImageChops.difference(self.previous, sample)).mean[0] > 0.7
        if changed:
            self.previous = sample
            self.since = now
            self.processed = False
        ready = not self.processed and now - self.since >= self.delay
        if ready:
            self.processed = True
        return changed, ready
