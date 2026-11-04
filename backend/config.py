import os
from pathlib import Path

VERSION = "0.1.0"
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SVCWATCH_DATA") or BASE_DIR / "data")
FRONTEND_DIR = BASE_DIR / "frontend"
DATA_DIR.mkdir(exist_ok=True)

# The dashboard can control services and the firewall, so it only listens on loopback.
HOST = "127.0.0.1"
PORT = int(os.environ.get("SVCWATCH_PORT", "8765"))

SAMPLE_SEC = 5            # service/host sampling period
DOCKER_SAMPLE_SEC = 10    # docker ps/stats period (docker stats itself takes ~2s)
HISTORY_SAMPLES = 360     # 30 minutes at 5s
HOST_HISTORY_SAMPLES = 60 # 5 minutes at 5s
FW_GROUP = "svc.watch"
FW_ROLLBACK_SEC = 60
