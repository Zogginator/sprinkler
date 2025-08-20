from dataclasses import dataclass, field
from typing import Dict, Optional
from time import time

@dataclass
class ZoneState:
    on: bool = False
    since_ts: Optional[float] = None
    remaining: Optional[int] = None  # másodpercben, ha időzített

@dataclass
class GlobalState:
    zones: Dict[int, ZoneState] = field(default_factory=dict)

    def ensure_zone(self, zid: int):
        if zid not in self.zones:
            self.zones[zid] = ZoneState()

    def set_on(self, zid: int, on: bool, remaining: Optional[int] = None):
        self.ensure_zone(zid)
        z = self.zones[zid]
        z.on = on
        z.remaining = remaining
        if on and z.since_ts is None:
            z.since_ts = time()
        if not on:
            z.since_ts = None
