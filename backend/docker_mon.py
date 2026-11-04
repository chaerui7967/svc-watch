"""Docker monitoring through the docker CLI (works with Docker Desktop's named pipe without extra packages)."""
import json
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from datetime import datetime

from .config import DOCKER_SAMPLE_SEC, HISTORY_SAMPLES
from .system import CREATE_NO_WINDOW

DOCKER = shutil.which("docker")
_UNITS = {"b": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9, "tb": 1e12, "kib": 1024, "mib": 2**20, "gib": 2**30, "tib": 2**40}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def run(args, timeout=20):
    if not DOCKER:
        return 127, "", "docker CLI를 찾을 수 없습니다"
    try:
        r = subprocess.run([DOCKER, *args], capture_output=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=CREATE_NO_WINDOW)
        return r.returncode, r.stdout, r.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "docker 명령 시간 초과"


def _docker_desktop_running():
    import psutil
    for p in psutil.process_iter(["name"]):
        if (p.info["name"] or "").lower() in ("docker desktop.exe", "com.docker.backend.exe"):
            return True
    return False


def _size_mb(text):
    m = re.match(r"\s*([\d.]+)\s*([A-Za-z]+)", text or "")
    if not m:
        return None
    return float(m.group(1)) * _UNITS.get(m.group(2).lower(), 1) / 2**20


def _json_lines(text):
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def _ts(iso):
    if not iso or iso.startswith("0001"):
        return None
    try:
        return datetime.fromisoformat(iso[:26].rstrip("Z") + "+00:00").timestamp()
    except ValueError:
        return None


def _ports(inspect):
    out = []
    for cport, binds in ((inspect.get("NetworkSettings") or {}).get("Ports") or {}).items():
        for b in binds or []:
            item = f"{b.get('HostPort')}→{cport.split('/')[0]}"
            if item not in out:
                out.append(item)
    return out


class DockerMonitor:
    def __init__(self, on_change=None):
        self.lock = threading.Lock()
        self.engine = {"ok": False, "error": "확인 중", "version": None}
        self.containers = {}   # name -> dict
        self.hist = {}         # name -> {"cpu": deque, "mem": deque}
        self.df = None
        self.ncpu = 1
        self._df_at = 0
        self.on_change = on_change  # callback(name, prev_state, container)

    # -------------------------------------------------------------- sampling
    def loop(self):
        while True:
            try:
                self.sample()
            except Exception as e:  # keep the thread alive no matter what
                with self.lock:
                    self.engine = {"ok": False, "error": f"샘플링 오류: {e}", "version": None}
            time.sleep(DOCKER_SAMPLE_SEC)

    def sample(self):
        code, out, err = run(["version", "--format", "{{json .}}"], timeout=10)
        info = _json_lines(out)[0] if out.strip() else {}
        server = info.get("Server") if isinstance(info, dict) else None
        if code != 0 or not server:
            desktop_up = _docker_desktop_running()
            if not DOCKER:
                msg = "docker CLI가 설치되어 있지 않습니다."
            elif code == 124 and desktop_up:
                # The app is up but the engine never answers (seen after sleep/WSL hiccups); only a restart helps.
                msg = ("Docker Desktop은 켜져 있지만 엔진이 응답하지 않아요(멈춤). "
                       "트레이 아이콘에서 Docker Desktop을 재시작하거나 'wsl --shutdown' 후 다시 켜 주세요.")
            elif desktop_up:
                msg = "Docker Desktop은 켜져 있지만 엔진이 아직 준비되지 않았어요. 잠시 후 다시 확인합니다."
            else:
                msg = "Docker 엔진에 연결할 수 없습니다. Docker Desktop이 실행 중인지 확인하세요."
            with self.lock:
                self.engine = {"ok": False, "version": None, "error": msg, "stuck": code == 124 and desktop_up}
            return
        if self.ncpu == 1:
            c, o, _ = run(["info", "--format", "{{.NCPU}}"], timeout=10)
            self.ncpu = int(o.strip()) if c == 0 and o.strip().isdigit() else 1

        _, out, _ = run(["ps", "-a", "--no-trunc", "--format", "{{json .}}"])
        rows = _json_lines(out)
        ids = [r["ID"] for r in rows]
        inspects = {}
        if ids:
            _, out, _ = run(["inspect", *ids])
            try:
                inspects = {i["Id"]: i for i in json.loads(out or "[]")}
            except ValueError:
                inspects = {}
        stats = {}
        if any(r.get("State") == "running" for r in rows):
            _, out, _ = run(["stats", "--no-stream", "--no-trunc", "--format", "{{json .}}"], timeout=30)
            for s in _json_lines(out):
                stats[s.get("Name")] = s

        new = {}
        for r in rows:
            name = r.get("Names", "").split(",")[0]
            ins = inspects.get(r["ID"], {})
            st = ins.get("State") or {}
            hc = ins.get("HostConfig") or {}
            labels = (ins.get("Config") or {}).get("Labels") or {}
            s = stats.get(name)
            cpu = mem = None
            if s and st.get("Running"):
                try:
                    cpu = round(float(s["CPUPerc"].rstrip("%")) / max(self.ncpu, 1), 1)
                except (KeyError, ValueError):
                    cpu = None
                mem = _size_mb((s.get("MemUsage") or "").split("/")[0])
            new[name] = {
                "id": r["ID"][:12], "name": name, "image": r.get("Image"),
                "project": labels.get("com.docker.compose.project"),
                "state": st.get("Status") or r.get("State"),
                "health": (st.get("Health") or {}).get("Status"),
                "exit_code": st.get("ExitCode"), "oom": bool(st.get("OOMKilled")),
                "started_at": _ts(st.get("StartedAt")), "finished_at": _ts(st.get("FinishedAt")),
                "restarts": ins.get("RestartCount", 0),
                "restart_policy": (hc.get("RestartPolicy") or {}).get("Name") or "no",
                "mem_limit_mb": round(hc["Memory"] / 2**20) if hc.get("Memory") else None,
                "ports": _ports(ins),
                "volumes": [m.get("Name") or m.get("Source") for m in ins.get("Mounts") or []],
                "cpu": cpu, "mem_mb": round(mem, 1) if mem is not None else None,
                "net": s.get("NetIO") if s else None,
                "status_text": r.get("Status"),
            }

        if time.time() - self._df_at > 60:
            _, out, _ = run(["system", "df", "--format", "{{json .}}"], timeout=30)
            self.df = [{"type": d.get("Type"), "count": d.get("TotalCount"), "active": d.get("Active"),
                        "size": d.get("Size"), "reclaimable": d.get("Reclaimable"),
                        "size_mb": _size_mb(d.get("Size"))} for d in _json_lines(out)]
            self._df_at = time.time()

        with self.lock:
            prev = self.containers
            self.engine = {"ok": True, "error": None, "version": (server or {}).get("Version"),
                           "os": (server or {}).get("Os"), "ncpu": self.ncpu}
            self.containers = new
            for name, c in new.items():
                h = self.hist.setdefault(name, {"cpu": deque(maxlen=HISTORY_SAMPLES // 2),
                                                "mem": deque(maxlen=HISTORY_SAMPLES // 2)})
                if c["cpu"] is not None:
                    h["cpu"].append(c["cpu"])
                    h["mem"].append(c["mem_mb"] or 0)
            for name in list(self.hist):
                if name not in new:
                    del self.hist[name]

        first, self._sampled = not getattr(self, "_sampled", False), True
        if self.on_change and not first:
            for name, c in new.items():
                p = prev.get(name)
                key = (c["state"], c["health"])
                if p is None or (p["state"], p["health"]) != key:
                    self.on_change(name, p, c)

    # -------------------------------------------------------------- queries
    def get(self, name):
        with self.lock:
            return self.containers.get(name)

    def history(self, name):
        with self.lock:
            h = self.hist.get(name)
            return (list(h["cpu"]), list(h["mem"])) if h else ([], [])


def control(name, action):
    if not NAME_RE.match(name or ""):
        return False, "잘못된 컨테이너 이름"
    if action not in ("start", "stop", "restart"):
        return False, "지원하지 않는 동작"
    code, _, err = run([action, name], timeout=60)
    return code == 0, (err or None) if code else None


def logs(name, tail=200):
    if not NAME_RE.match(name or ""):
        return [], "잘못된 컨테이너 이름"
    code, out, err = run(["logs", "--tail", str(int(tail)), "--timestamps", name], timeout=20)
    if code != 0:
        return [], err or "로그를 가져오지 못했습니다"
    # docker logs writes the container's stderr to our stderr; timestamps let us merge them in order.
    lines = sorted(out.splitlines() + err.splitlines())
    return lines[-tail:], None


def prune():
    code, out, err = run(["system", "prune", "-f"], timeout=300)
    return code == 0, (out.strip().splitlines()[-1] if out.strip() else None) if code == 0 else err
