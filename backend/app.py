import os
import re
from contextlib import asynccontextmanager
from typing import Literal, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from . import docker_mon, system
from .config import FRONTEND_DIR
from .firewall import build_script, firewall, normalize
from .monitor import monitor
from .store import store

LOCAL_HOSTS = {"127.0.0.1", "localhost"}


@asynccontextmanager
async def lifespan(_app):
    monitor.start()
    yield


app = FastAPI(title="svc.watch", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def local_only(request: Request, call_next):
    # Host check stops DNS-rebinding; the custom header + Origin check stops other websites from
    # sending state-changing requests to this local server (CSRF).
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
    if host not in LOCAL_HOSTS:
        return JSONResponse({"detail": "허용되지 않은 Host"}, status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if request.headers.get("x-svcwatch") != "1" or (origin and urlparse(origin).hostname not in LOCAL_HOSTS):
            return JSONResponse({"detail": "허용되지 않은 요청"}, status_code=403)
    return await call_next(request)


def fail(msg, code=400):
    raise HTTPException(status_code=code, detail=msg)


# ====================================================================== services

class ServiceIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    type: Literal["svc", "proc", "docker", "http", "task"]
    target: str = Field(min_length=1, max_length=500)
    health: Literal["process", "tcp", "http"] = "process"
    health_target: Optional[str] = Field(None, max_length=500)
    interval: int = Field(10, ge=5, le=3600)
    port: Optional[int] = Field(None, ge=1, le=65535)
    tags: str = Field("", max_length=100)
    notify: bool = True
    auto_restart: bool = False
    cpu_alert: bool = True
    cpu_threshold: float = Field(80, ge=1, le=100)
    cpu_minutes: float = Field(5, ge=0.5, le=120)
    latency_ms: int = Field(1000, ge=10, le=60000)
    start_cmd: Optional[str] = Field(None, max_length=1000)
    cwd: Optional[str] = Field(None, max_length=500)
    log_file: Optional[str] = Field(None, max_length=500)

    @model_validator(mode="after")
    def check(self):
        self.name, self.target = self.name.strip(), self.target.strip()
        if self.type == "http":
            self.health, self.health_target = "http", self.health_target or self.target
        if self.type == "task":
            if not self.port:
                raise ValueError("작업 스케줄러 유형은 서버가 쓰는 포트 번호가 필요합니다")
            if self.health == "tcp":
                self.health = "process"  # port listening is already the liveness check
        if self.type == "docker" and not docker_mon.NAME_RE.match(self.target):
            raise ValueError("컨테이너 이름 형식이 올바르지 않습니다")
        if self.health == "tcp":
            if not (self.health_target or "").isdigit() or not 1 <= int(self.health_target) <= 65535:
                raise ValueError("TCP 헬스체크에는 1~65535 사이 포트 번호가 필요합니다")
        if self.health == "http" and not (self.health_target or "").lower().startswith(("http://", "https://")):
            raise ValueError("HTTP 헬스체크 주소는 http:// 또는 https:// 로 시작해야 합니다")
        if self.health == "process":
            self.health_target = None
        if self.type not in ("proc", "http"):
            self.start_cmd = None
        self.cwd = (self.cwd or "").strip() or None
        return self


def _validate_target(s: ServiceIn):
    if s.type == "svc" and system.service_info(s.target) is None:
        hint = ""
        if any(t["name"].lower() == s.target.lower() for t in _tasks()):
            hint = " '작업 스케줄러' 유형으로 등록된 작업이에요. 유형을 '작업 스케줄러'로 바꿔 주세요."
        fail(f"'{s.target}' 이름의 Windows 서비스를 찾을 수 없습니다.{hint or ' 작업 스케줄러로 자동 실행되게 해 둔 앱이라면 유형을 예약 작업으로 바꾸고 포트를 입력하세요.'}")
    if s.type == "task" and system.is_admin() and not monitor.task(s.target):
        monitor.refresh_tasks(force=True, wait=True)
        if not monitor.task(s.target):
            fail(f"작업 스케줄러에서 '{s.target}' 작업을 찾을 수 없습니다")


def _clean_desc(text):
    """Task descriptions saved through a CP949 console often come back garbled. Repair them when possible,
    otherwise drop them so the task name is used instead."""
    desc = (text or "").split("(")[0].strip()
    if not desc or desc.isascii():
        return desc[:60]
    try:
        fixed = desc.encode("cp949").decode("utf-8")
        if fixed != desc:
            desc = fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    garbled = ("�" in desc or "?" in desc
               or any("Ѐ" <= ch <= "ӿ" or "一" <= ch <= "鿿" or "À" <= ch <= "ɏ" for ch in desc))
    return "" if garbled else desc[:60]


def _tasks():
    monitor.refresh_tasks(wait=not monitor.tasks["at"])
    with monitor.lock:
        return list(monitor.tasks["items"].values())


def _find(sid):
    s = next((x for x in store.snapshot()["services"] if x["id"] == sid), None)
    if not s:
        fail("등록되지 않은 서비스", 404)
    return s


@app.get("/api/overview")
def overview():
    return monitor.overview()


@app.get("/api/services/{sid}")
def service_detail(sid: int):
    s = _find(sid)
    view = monitor.service_view(s, detail=True)
    view["folder"] = _project_folder(s)
    return view


@app.post("/api/services")
def create_service(body: ServiceIn):
    _validate_target(body)
    with store.lock:
        sid = store.data["next_id"]
        store.data["next_id"] += 1
        store.data["services"].append({"id": sid, **body.model_dump()})
        store.save()
    return {"id": sid}


@app.put("/api/services/{sid}")
def update_service(sid: int, body: ServiceIn):
    _find(sid)
    _validate_target(body)
    with store.lock:
        for i, s in enumerate(store.data["services"]):
            if s["id"] == sid:
                store.data["services"][i] = {"id": sid, **body.model_dump()}
        store.save()
    return {"ok": True}


@app.delete("/api/services/{sid}")
def delete_service(sid: int):
    _find(sid)
    with store.lock:
        store.data["services"] = [s for s in store.data["services"] if s["id"] != sid]
        store.save()
    return {"ok": True}


def _project_folder(s):
    """Folder set by the user, else the task's project folder, else where the running process lives."""
    t = monitor.task(s["target"]) if s["type"] == "task" else None
    with monitor.lock:
        proc_cwd = (monitor.rt.get(s["id"]) or {}).get("proc_cwd")
    for path in (s.get("cwd"), (t or {}).get("root"), proc_cwd,
                 os.path.dirname(s["log_file"]) if s.get("log_file") else None):
        if path and os.path.isdir(path):
            return path
    return None


# Declared before the start/stop route so "open-folder" isn't taken as an action name.
@app.post("/api/services/{sid}/open-folder")
def open_folder(sid: int):
    path = _project_folder(_find(sid))
    if not path:
        fail("프로젝트 폴더를 알 수 없어요. 설정 탭에서 '프로젝트 폴더'를 입력해 주세요.")
    os.startfile(path)  # only folders derived from registered services, never a path sent by the browser
    return {"ok": True, "path": path}


@app.post("/api/services/{sid}/{action}")
def control_service(sid: int, action: Literal["start", "stop", "restart"]):
    _find(sid)
    ok, err = monitor.control(sid, action)
    if not ok:
        fail(err or "실패")
    return {"ok": True}


@app.get("/api/services/{sid}/logs")
def service_logs(sid: int, tail: int = 200):
    s = _find(sid)
    tail = max(10, min(tail, 2000))
    if s.get("log_file"):
        lines, err = system.tail_file(s["log_file"], tail)
        return {"source": s["log_file"], "lines": lines, "error": err}
    if s["type"] == "docker":
        lines, err = docker_mon.logs(s["target"], tail)
        return {"source": f"docker logs {s['target']}", "lines": lines, "error": err}
    # Newest of: the project's own logs\*.log and the output of starts done from the dashboard.
    candidates = [(p, label) for p, label in ((system.newest_log(_project_folder(s)), "프로젝트 폴더에서 자동으로 찾은 최신 로그"),
                                              (system.start_log_path(s["id"]), "대시보드에서 시작한 실행 출력"))
                  if p and os.path.isfile(p)]
    if candidates:
        path, label = max(candidates, key=lambda c: os.path.getmtime(c[0]))
        lines, err = system.tail_file(path, tail)
        return {"source": f"{path} ({label})", "lines": lines, "error": err}
    if s["type"] == "svc":
        info = system.service_info(s["target"])
        lines, err = system.service_events(info["display_name"] if info else s["target"])
        return {"source": "Windows 이벤트 로그 (System · Service Control Manager)", "lines": lines, "error": err}
    return {"source": None, "lines": [], "error": "로그 파일을 찾지 못했어요. 설정 탭에서 로그 파일 경로나 프로젝트 폴더를 지정하세요."}


@app.post("/api/test")
def test_service(body: ServiceIn):
    from . import checks
    if body.type == "svc":
        info = system.service_info(body.target)
        if not info:
            return {"ok": False, "message": "서비스를 찾을 수 없습니다"}
        msg = f"서비스 상태: {info['status']} (PID {info['pid'] or '-'})"
        if body.health == "process":
            return {"ok": info["status"] == "running", "message": msg}
    elif body.type == "proc":
        pids = system.match_processes(system.snapshot_processes(), body.target)
        msg = f"일치하는 프로세스 {len(pids)}개" + (f" (PID {', '.join(map(str, pids[:5]))})" if pids else "")
        if body.health == "process":
            return {"ok": bool(pids), "message": msg}
    elif body.type == "docker":
        c = monitor.docker.get(body.target)
        if not monitor.docker.engine.get("ok"):
            return {"ok": False, "message": monitor.docker.engine.get("error")}
        if not c:
            return {"ok": False, "message": "컨테이너를 찾을 수 없습니다"}
        msg = f"컨테이너 상태: {c['state']}"
        if body.health == "process":
            return {"ok": c["state"] == "running", "message": msg}
    elif body.type == "task":
        t = monitor.task(body.target)
        pids = system.pids_on_port(body.port)
        msg = (f"작업 상태: {t['state']}" if t else "작업 정보 없음(권한 또는 이름 확인)") +               (f" · 포트 {body.port}에서 서버 대기 중 (PID {', '.join(map(str, pids))})" if pids else f" · 포트 {body.port}에서 대기 중인 서버 없음")
        if body.health == "process":
            return {"ok": bool(pids), "message": msg}
    else:
        port = checks.local_port(body.target)
        pids = system.pids_on_port(port) if port else []
        msg = f"이 PC의 포트 {port}: 서버 프로세스 PID {', '.join(map(str, pids))}" if pids else (f"이 PC의 포트 {port}에서 대기 중인 프로세스 없음" if port else "")
    ok, ms, err = checks.tcp(body.health_target) if body.health == "tcp" else checks.http(body.health_target)
    detail = f"{'성공' if ok else '실패'}" + (f" · {ms}ms" if ms is not None else "") + (f" · {err}" if err else "")
    return {"ok": ok, "message": (msg + " · " if msg else "") + f"헬스체크 {detail}"}


@app.get("/api/discover/{kind}")
def discover(kind: Literal["svc", "proc", "docker", "task"]):
    if kind == "task":
        monitor.refresh_tasks(force=True, wait=True)
        listeners = system.listener_details()
        registered = {system.task_full_name(s["target"]).lower() for s in store.snapshot()["services"] if s["type"] == "task"}
        out = []
        for t in _tasks():
            port = system.guess_task_port(t, listeners)
            desc = _clean_desc(t.get("description"))
            out.append({"name": t["name"], "full_name": t["full_name"], "state": t["state"], "description": desc,
                        "root": t["root"], "port": port, "listening": bool(port and system.pids_on_port(port)),
                        "registered": t["full_name"].lower() in registered, "user": t.get("user")})
        return {"admin": system.is_admin(), "error": monitor.tasks["error"],
                "items": sorted(out, key=lambda x: (x["port"] is None, x["name"].lower()))}
    if kind == "svc":
        return system.list_services()
    if kind == "proc":
        return system.list_processes(system.snapshot_processes())
    with monitor.docker.lock:
        return [{"name": c["name"], "image": c["image"], "state": c["state"]} for c in monitor.docker.containers.values()]


# ====================================================================== docker

@app.get("/api/docker")
def docker_overview():
    return monitor.docker_view()


@app.post("/api/docker/prune")
def docker_prune():
    ok, msg = docker_mon.prune()
    if not ok:
        fail(msg or "정리 실패")
    return {"ok": True, "message": msg}


@app.post("/api/docker/{name}/{action}")
def docker_control(name: str, action: Literal["start", "stop", "restart"]):
    ok, err = docker_mon.control(name, action)
    if not ok:
        fail(err or "실패")
    return {"ok": True}


@app.get("/api/docker/{name}/logs")
def docker_logs(name: str, tail: int = 200):
    lines, err = docker_mon.logs(name, max(10, min(tail, 2000)))
    return {"source": f"docker logs {name}", "lines": lines, "error": err}


# ====================================================================== firewall

def _suggest_exposure(cfg):
    """Externally reachable ports of registered services that aren't in the exposure list yet, plus RDP."""
    known = {e["port"] for e in cfg["exposure"]}
    out = []
    for s in monitor.overview()["services"]:
        for port in s.get("ext_ports") or []:
            if port not in known and len(out) < 20:
                known.add(port)
                out.append({"port": port, "label": s["name"], "mode": "local"})
    if 3389 not in known:
        out.append({"port": 3389, "label": "원격 데스크톱 (RDP)", "mode": "local"})
    return out


@app.get("/api/firewall")
def firewall_overview():
    data = store.snapshot()
    st = firewall.status()
    log_file = next((p.get("log_file") for p in st.get("profiles", []) if p.get("log_file")), None)
    blocked, blocked_note = firewall.blocked(log_file)
    cfg = data["firewall"]
    return {
        "admin": system.is_admin(),
        "status": st,
        "config": cfg,
        "applied": data.get("firewall_applied"),
        "applied_at": data.get("firewall_applied_at"),
        "disabled_by_us": data.get("firewall_disabled_by_us") or [],
        "pending": firewall.pending_view(),
        "last_rollback": getattr(firewall, "last_rollback", None),
        "suggested_exposure": _suggest_exposure(cfg),
        "conflicts": firewall.conflicts,
        "blocked": blocked,
        "blocked_note": blocked_note,
    }


class FirewallIn(BaseModel):
    mode: bool
    allow: list[dict] = Field(default_factory=list, max_length=200)
    exposure: list[dict] = Field(default_factory=list, max_length=200)
    disable_rules: list[str] = Field(default_factory=list, max_length=500)
    rule_names: dict[str, str] = Field(default_factory=dict)
    next_id: int = 1


@app.put("/api/firewall/config")
def firewall_save(body: FirewallIn):
    try:
        cfg = normalize(body.model_dump())
    except (ValueError, KeyError, TypeError) as e:
        fail(str(e))
    with store.lock:
        store.data["firewall"] = cfg
        store.save()
    return cfg


@app.get("/api/firewall/preview")
def firewall_preview():
    data = store.snapshot()
    try:
        cfg = normalize(data["firewall"])
    except ValueError as e:
        fail(str(e))
    return {"script": build_script(cfg, data.get("firewall_disabled_by_us") or [], for_display=True)}


@app.post("/api/firewall/conflicts/scan")
def firewall_scan():
    return firewall.scan_conflicts()


class ApplyIn(BaseModel):
    auto_rollback: bool = True


@app.post("/api/firewall/apply")
def firewall_apply(body: ApplyIn):
    try:
        return firewall.apply(body.auto_rollback)
    except PermissionError as e:
        fail(str(e), 403)
    except (RuntimeError, ValueError) as e:
        fail(str(e), 409)


@app.post("/api/firewall/confirm")
def firewall_confirm():
    if not firewall.confirm():
        fail("확인할 적용 내역이 없습니다", 409)
    return {"ok": True}


@app.post("/api/firewall/rollback")
def firewall_rollback():
    ok, err = firewall.rollback("user")
    if not ok:
        fail(err or "되돌리기 실패", 409)
    return {"ok": True}


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
