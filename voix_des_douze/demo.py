"""Images synthétiques signalées comme telles ; même chemin OCR que les captures."""
import textwrap
import threading
import time
from PIL import Image, ImageDraw, ImageFont
from .vision import Rect

SLIDES = [
    ("Gardienne des Douze", "Bienvenue dans cette démonstration. Les dialogues sont reconnus localement et chaque personnage conserve sa voix.", ("Continuer la visite", "Quitter la conversation")),
    ("Voyageur d'Astrub", "Le chemin est encore long. Prenez votre temps, je vous attendrai près de la fontaine.", ("Demander le chemin", "Remercier le voyageur")),
    ("Gardienne des Douze", "Vous voici de retour ! Ma voix est toujours la même. Vous pouvez suspendre la lecture ou changer de personnage à tout moment.", ("Écouter encore",)),
]
REGIONS = {"npc": Rect(0.08, 0.08, 0.8, 0.1), "text": Rect(0.08, 0.22, 0.8, 0.4), "responses": Rect(0.08, 0.68, 0.8, 0.25)}


def render_slide(index):
    image = Image.new("RGB", (1000, 600), "#eee8d9")
    draw = ImageDraw.Draw(image)
    name, text, answers = SLIDES[index % len(SLIDES)]
    draw.rectangle((50, 35, 950, 120), fill="#51402d")
    draw.text((80, 52), name, font=ImageFont.load_default(size=36), fill="white")
    draw.multiline_text((80, 140), "\n".join(textwrap.wrap(text, width=49)),
                        font=ImageFont.load_default(size=29), fill="#241f18", spacing=10)
    for row, answer in enumerate(answers):
        draw.text((80, 415 + row * 48), answer, font=ImageFont.load_default(size=25), fill="#241f18")
    return image


class DemoSource:
    def __init__(self):
        self.started = time.monotonic()
        self.index = -1
        self.image = None
        self.lock = threading.Lock()

    def next(self):
        with self.lock:
            self.started -= 12

    def __call__(self):
        with self.lock:
            index = int((time.monotonic() - self.started) // 12) % len(SLIDES)
            if index != self.index:
                self.image, self.index = render_slide(index), index
            return self.image.copy()
