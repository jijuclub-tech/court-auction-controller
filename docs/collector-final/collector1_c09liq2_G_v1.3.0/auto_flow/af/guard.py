# -*- coding: utf-8 -*-
"""Stop requests, checked BETWEEN ACTIONS (before every Pico2 move/click/wheel/key/type, every wait poll and
every pacing-wait second), not only while waiting for an AI answer.

Hard stop (stop now, park the cursor, keep files):   <run>/handoff/ABORT, <auto_flow>/STOP.txt, <base>/local_data/STOP.json
Soft stop (finish the current item, then stop):     <auto_flow>/STOP_AFTER_ITEM.txt
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable, Optional


class StopRequested(RuntimeError):
    pass


class StopGuard:
    def __init__(self, hard: Iterable[Path] = (), soft: Iterable[Path] = (), log=None, min_interval=0.0,
                 clock=time.time):
        self.hard = [Path(p) for p in hard]
        self.soft = [Path(p) for p in soft]
        self.log = log or (lambda rec: None)
        self.min_interval = float(min_interval)
        self.clock = clock
        self._last = -1e18
        self.tripped: Optional[str] = None

    @classmethod
    def for_run(cls, run_dir: Path, af_dir: Path, base_dir: Path, log=None):
        return cls(hard=[Path(run_dir) / 'handoff' / 'ABORT', Path(af_dir) / 'STOP.txt',
                         Path(base_dir) / 'local_data' / 'STOP.json'],
                   soft=[Path(af_dir) / 'STOP_AFTER_ITEM.txt'], log=log)

    def check(self, where=''):
        if self.tripped:
            raise StopRequested(self.tripped)
        now = self.clock()
        if now - self._last < self.min_interval:
            return
        self._last = now
        for p in self.hard:
            if p.exists():
                self.tripped = 'stop file %s (%s)' % (p.name, where or 'between actions')
                self.log({'action': 'stop_requested', 'file': str(p), 'where': where})
                raise StopRequested(self.tripped)

    def soft_requested(self) -> Optional[str]:
        for p in self.soft:
            if p.exists():
                return str(p)
        return None
