"""Explicit uninterrupted travel samples; ordinary dwell times never train the model."""
import statistics


class TravelCalibration:
    def __init__(self):
        self.active = False
        self.paused = False
        self.profile = None
        self.samples = []
        self.anchor = None

    def start(self, profile):
        self.active = True
        self.paused = False
        self.profile = profile
        self.samples = []
        self.anchor = None

    def cancel(self):
        self.active = False
        self.paused = False
        self.anchor = None

    def pause(self):
        self.paused = not self.paused
        self.anchor = None

    def observe(self, event, maps):
        if not self.active or self.paused:
            return None
        origin, target, arrived = event['origin'], event['target'], event['arrived_at']
        if target == origin:
            return None
        a, b = maps.get(str(origin)), maps.get(str(target))
        adjacent = (a and b and a['world_id'] >= 0 and a['world_id'] == b['world_id']
                    and abs(a['x']-b['x']) + abs(a['y']-b['y']) == 1)
        sample = None
        requested = event['requested_at']
        if (adjacent and event['requested'] == target and self.anchor is not None
                and self.anchor[0] == origin and requested is not None
                and self.anchor[1] <= requested <= arrived):
            seconds = arrived - self.anchor[1]
            if seconds > 0:
                self.samples.append(seconds)
                sample = seconds
        self.anchor = (target, arrived)
        return sample

    def finish(self):
        if len(self.samples) < 20:
            raise ValueError('Il faut au moins 20 passages mesurés, après le passage d’amorçage.')
        seconds = statistics.median(self.samples)
        self.cancel()
        return seconds
