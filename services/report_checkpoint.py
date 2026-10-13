"""Coalesce report uploads; operational decisions remain immediately durable."""

import json
import os
from time import monotonic


class ReportCheckpoint:
    def __init__(self, persistence, path, report, interval_seconds=None, batch_size=None, clock=monotonic):
        self.persistence, self.path, self.report = persistence, path, report
        self.interval = float(interval_seconds if interval_seconds is not None else
                              os.environ.get('REPORT_CHECKPOINT_SECONDS', '60'))
        self.batch_size = int(batch_size if batch_size is not None else
                              os.environ.get('REPORT_CHECKPOINT_POSTINGS', '10'))
        if self.interval <= 0 or self.batch_size < 1:
            raise ValueError('report checkpoint interval and batch size must be positive')
        self.clock = clock
        self.last_saved = None
        self.pending = 0
        self.uploads = 0

    def save(self, force=False):
        self.pending += 1
        now = self.clock()
        if (not force and self.last_saved is not None and
                self.pending < self.batch_size and now - self.last_saved < self.interval):
            return False
        self.persistence.store_file(self.path, json.dumps(self.report, default=str).encode(), 'application/json')
        self.last_saved = self.clock()
        self.pending = 0
        self.uploads += 1
        return True
