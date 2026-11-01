"""La capture, la stabilité et Vision s'exécutent hors du thread Qt."""
from __future__ import annotations
import threading
import time
from PIL import Image
from .dialogue import DialogueHistory, ImageGate
from .vision import Dialogue, recognize, detect_regions
from .activity import UserActivity


def region_sample(image, regions):
    parts = []
    for region in regions.values():
        x, y, w, h = region.x, region.y, region.width, region.height
        parts.append(image.crop((int(x * image.width), int(y * image.height),
                                 int((x + w) * image.width), int((y + h) * image.height))))
    if not parts:
        return Image.new("RGB", (1, 1))
    strip = Image.new("RGB", (max(p.width for p in parts), sum(p.height for p in parts)))
    top = 0
    for part in parts:
        strip.paste(part, (0, top))
        top += part.height
    return strip


class Monitor:
    def __init__(self, source, regions, on_dialogue, on_change, on_frame, on_error):
        self.source = source
        self.regions = regions
        self.on_dialogue, self.on_change = on_dialogue, on_change
        self.on_frame, self.on_error = on_frame, on_error
        self.stop_event = threading.Event()
        self.history = DialogueHistory()
        self.gate = ImageGate()
        self.revision = 0
        self.thread = threading.Thread(target=self.run, daemon=True, name="Capture-Vision")
        self.thread.start()

    def run(self):
        with UserActivity():
            self._watch()

    def _watch(self):
        try:
            while not self.stop_event.is_set():
                image = self.source()
                regions = self.regions if self.regions is not None else detect_regions(image)
                if self.stop_event.is_set():
                    return
                changed, ready = self.gate.observe(region_sample(image, regions), time.perf_counter())
                if changed:
                    self.revision += 1
                    self.on_change()
                    self.on_frame(image, regions)
                if ready:
                    if not regions:
                        self.history.closed()
                        self.on_dialogue(Dialogue(), False, 0.0, self.revision)
                    else:
                        start = time.perf_counter()
                        dialogue = recognize(image, regions)
                        elapsed = time.perf_counter() - start
                        if self.stop_event.is_set():
                            return
                        fresh = self.history.accept(dialogue.npc, dialogue.text, dialogue.responses)
                        self.on_dialogue(dialogue, fresh, elapsed, self.revision)
                self.stop_event.wait(0.2)
        except Exception as exc:
            if not self.stop_event.is_set():
                self.on_change()
                self.on_error(str(exc))

    def stop(self):
        self.stop_event.set()
        self.on_change()
