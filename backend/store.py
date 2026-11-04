"""Small JSON file store. All config lives in data/config.json."""
import copy
import json
import os
import threading

from .config import DATA_DIR

DEFAULT = {
    "next_id": 1,
    "services": [],
    "firewall": {
        "mode": False,
        "allow": [],          # {id, on, name, cidr, ports: "all" | [int], expires: "YYYY-MM-DD" | None}
        "exposure": [],       # {port: int, label, mode: "local" | "allow"}
        "disable_rules": [],  # firewall rule Names (ids) to disable when applying
        "next_id": 1,
    },
    "firewall_applied": None,  # snapshot of the firewall section at last successful apply
    "firewall_disabled_by_us": [],
    "firewall_applied_at": None,
}


class Store:
    def __init__(self, name="config.json"):
        self.path = DATA_DIR / name
        self.lock = threading.RLock()
        self.data = copy.deepcopy(DEFAULT)
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                self.data.update(loaded)
                for k, v in DEFAULT["firewall"].items():
                    self.data["firewall"].setdefault(k, copy.deepcopy(v))
            except (OSError, ValueError):
                # Keep the broken file for inspection instead of overwriting it silently.
                self.path.replace(self.path.with_suffix(".broken.json"))

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def save(self):
        with self.lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)


store = Store()
