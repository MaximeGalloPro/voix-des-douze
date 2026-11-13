"""Local JSONL diagnostics, flushed per event and bounded across application runs."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from .network_protocol import VERSION


class IdleJournal:
    def __init__(self, directory, error, max_bytes=2 * 1024 * 1024, backups=3):
        self.path = Path(directory) / 'idle.jsonl'
        self.error = error
        self.max_bytes = max_bytes
        self.backups = backups
        self.failed = False

    def write(self, run, stage, context, details):
        row = {'date': datetime.now(timezone.utc).isoformat(), 'monotonic': time.monotonic(),
               'version_jeu': VERSION, 'run': run, 'événement': stage,
               'contexte': context, 'détails': details}
        try:
            data = (json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size + len(data) > self.max_bytes:
                for index in range(self.backups, 0, -1):
                    source = self.path if index == 1 else self.path.with_name(f'idle.jsonl.{index - 1}')
                    if source.exists():
                        source.replace(self.path.with_name(f'idle.jsonl.{index}'))
            with os.fdopen(os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), 'ab') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(data)
            self.failed = False
        except (OSError, ValueError, TypeError) as exc:
            if not self.failed:
                self.failed = True
                self.error(f'Journal idle impossible : {exc}')
