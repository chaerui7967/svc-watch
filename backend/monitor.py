"""Samples every registered service, keeps short history, and records state changes as events."""
import json
import threading
import time
import traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import psutil

from . import checks, docker_mon, system
from .config import DATA_DIR, HISTORY_SAMPLES, HOST_HISTORY_SAMPLES, SAMPLE_SEC, VERSION
from .store import store

SEVERITY = {"running": 0, "stopped": 1, "warning": 2, "down": 3}
SLOT_SEC = 1800  # availability bar: 48 half-hour slots
EVENTS_FILE = DATA_DIR / "events.json"


def minute_series(samples, per_minute, points=30):
    """Average raw samples into per-minute points (newest last)."""
    out = []
    data = list(samples)
    while data and len(out) < points:
        chunk, data = data[-per_minute:], data[:-per_minute]
        out.append(round(sum(chunk) / len(chunk), 1))
    return out[::-1]


class Monitor:
    def __init__(self):
        self.lock = threading.RLock()
        self.rt = {}
        self.host = system.host_static()
        self.host_cpu = deque(maxlen=HOST_HISTORY_SAMPLES)
        self.host_mem = deque(maxlen=HOST_HISTORY_SAMPLES)
        self.meter = system.ProcMeter()
        self.checks_pool = ThreadPoolExecutor(8, thread_name_prefix="check")
        self.action_pool = ThreadPoolExecutor(4, thread_name_prefix="action")
        self.events = deque(maxlen=500)
        self.docker_events = deque(maxlen=100)
        self.docker = docker_mon.DockerMonitor(on_change=self._docker_changed)
        self.last_sample = None
        self.tasks = {"at": 0, "items": {}, "error": None, "loading": False}
        try:
            self.events.extend(json.loads(EVENTS_FILE.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="monitor").start()
        threading.Thread(target=self.docker.loop, daemon=True, name="docker").start()

    # ------------------------------------------------------------------ events
    def _event(self, level, title, detail="", sid=None, name=None, docker=False):
        ev = {"ts": time.time(), "level": level, "title": title, "detail": detail or "", "sid": sid, "name": name}
        with self.lock:
            (self.docker_events if docker else self.events).appendleft(ev)
            if not docker:
                try:
                    EVENTS_FILE.write_text(json.dumps(list(self.events), ensure_ascii=False), encoding="utf-8")
                except OSError:
                    pass

    def _docker_changed(self, name, prev, c):
        st, health, code = c["state"], c["health"], c["exit_code"]
        if st == "running" and health == "unhealthy":
            self._event("warning", f"{name} unhealthy", "헬스체크 실패", docker=True)
        elif st == "running":
            self._event("running", f"{name} 실행", c["image"] or "", docker=True)
        elif st == "restarting":
            self._event("warning", f"{name} 재시작 중", f"{c['restarts']}회째", docker=True)
        elif st in ("exited", "dead") and code:
            self._event("down", f"{name} 종료", f"exit {code}" + (" · OOM" if c["oom"] or code == 137 else ""), docker=True)
        else:
            self._event("stopped", f"{name} {st}", "", docker=True)

    # ------------------------------------------------------------------ sampling
    def _loop(self):
        psutil.cpu_percent(None)
        while True:
            t0 = time.time()
            try:
                self._sample()
            except Exception:
                traceback.print_exc()
            time.sleep(max(0.5, SAMPLE_SEC - (time.time() - t0)))

    def _rt(self, sid):
        return self.rt.setdefault(sid, {
            "status": None, "reason": None, "pid": None, "ports": [], "cpu": None, "mem_mb": None,
            "started_at": None, "latency": None, "health_ok": None, "health_err": None, "last_check": 0,
            "expected": None, "ext_ports": [], "web_url": None, "web_checked": 0, "proc_cwd": None, "cpu_hist": deque(maxlen=HISTORY_SAMPLES), "mem_hist": deque(maxlen=HISTORY_SAMPLES),
            "slots": {}, "auto_restarts": deque(maxlen=10),
        })

    def _health(self, s):
        if s["health"] == "tcp":
            return checks.tcp(s.get("health_target") or s.get("port"))
        return checks.http(s.get("health_target") or "")

    def _sample(self):
        vm = psutil.virtual_memory()
        cpu = psutil.cpu_percent(None)
        services = store.snapshot()["services"]
        procs = system.snapshot_processes() if any(s["type"] == "proc" for s in services) else []
        ports_by_pid = system.listening_ports()
        now = time.time()
        if any(s["type"] == "task" for s in services):
            self.refresh_tasks()
        self._probe_web(services, now)

        futures = {}
        with self.lock:
            self.host_cpu.append(cpu)
            self.host_mem.append(vm.percent)
            for s in services:
                rt = self._rt(s["id"])
                if s["health"] in ("tcp", "http") and now - rt["last_check"] >= int(s.get("interval") or 10):
                    futures[s["id"]] = self.checks_pool.submit(self._health, s)
        results = {}
        for sid, f in futures.items():
            try:
                results[sid] = f.result(timeout=8)
            except Exception as e:
                results[sid] = (False, None, f"헬스체크 오류: {e}")

        alive_pids = set()
        for s in services:
            self._evaluate(s, procs, ports_by_pid, results.get(s["id"]), now, alive_pids)
        self.meter.prune(alive_pids)
        with self.lock:
            ids = {s["id"] for s in services}
            for sid in list(self.rt):
                if sid not in ids:
                    del self.rt[sid]
            self.last_sample = now

    def _probe_web(self, services, now):
        """Every 60s, check whether a running service's port serves a web page (for the clickable link).
        HTTP services already have their URL, so they are skipped."""
        for s in services:
            if s["type"] == "http":
                continue
            with self.lock:
                rt = self._rt(s["id"])
                if rt["status"] not in ("running", "warning") or now - rt["web_checked"] < 60:
                    continue
                ports = [int(s["port"])] if s["type"] == "task" and s.get("port") else list(rt["ext_ports"] or rt["ports"])
                if not ports:
                    continue
                rt["web_checked"] = now

            def work(sid=s["id"], ports=ports[:3]):
                url = next((u for u in (checks.detect_web(p) for p in ports) if u), None)
                with self.lock:
                    if sid in self.rt:
                        self.rt[sid]["web_url"] = url
            self.checks_pool.submit(work)

    def _evaluate(self, s, procs, ports_by_pid, result, now, alive_pids):
        typ, target = s["type"], s["target"]
        alive, pids, started, reason, forced = False, [], None, None, None
        cpu = mem = None
        ports, ext_ports = [], []
        container = None

        if typ == "svc":
            info = system.service_info(target)
            if info is None:
                forced, reason = "down", "Windows 서비스를 찾을 수 없음"
            elif info["status"] == "running":
                alive, pids = True, [info["pid"]] if info.get("pid") else []
            elif info["start_type"] == "automatic" and info["status"] == "stopped":
                forced, reason = "down", "자동 시작 서비스가 중지됨"
            else:
                reason = f"서비스 상태: {info['status']}"
        elif typ == "proc":
            pids = system.match_processes(procs, target)
            alive = bool(pids)
            reason = None if alive else "일치하는 프로세스 없음"
        elif typ == "docker":
            if not self.docker.engine.get("ok"):
                forced, reason = "down", "Docker 엔진에 연결할 수 없음"
            else:
                container = self.docker.get(target)
                if not container:
                    forced, reason = "down", "컨테이너를 찾을 수 없음"
                else:
                    st, code = container["state"], container["exit_code"]
                    if st == "running":
                        alive, started = True, container["started_at"]
                        cpu, mem, ports = container["cpu"], container["mem_mb"], container["ports"]
                        ext_ports = [int(p.split("→")[0]) for p in ports if p.split("→")[0].isdigit()]
                        if container["health"] == "unhealthy":
                            forced, reason = "warning", "컨테이너 헬스체크 실패 (unhealthy)"
                    elif st == "restarting":
                        forced, reason = "warning", f"재시작 반복 중 · {container['restarts']}회"
                    elif st in ("exited", "dead") and code:
                        forced = "down"
                        reason = f"종료됨 · exit {code}" + (" (메모리 부족 OOM)" if container["oom"] or code == 137 else "")
                    else:
                        reason = f"컨테이너 상태: {st}"
        elif typ == "http":
            alive = True  # decided by the HTTP health check
            port = checks.local_port(target)
            if port:  # a web server on this PC: measure the process behind the port
                pids = system.pids_on_port(port, ports_by_pid)
        elif typ == "task":
            port = int(s.get("port") or 0)
            pids = system.pids_on_port(port, ports_by_pid) if port else []
            alive = bool(pids)
            task = self.task(target)
            if not alive:
                if task and task.get("state") == "Disabled":
                    reason = "작업 스케줄러에서 '사용 안 함' 상태"
                else:
                    forced, reason = "down", f"포트 {port}에서 대기 중인 서버가 없음"
                    if task and task.get("last_result") not in (None, 0, 267009, 267011):
                        reason += f" · 작업 마지막 결과 0x{int(task['last_result']) & 0xFFFFFFFF:X}"

        if pids:
            cpu, mem, tree = self.meter.measure(pids)
            alive_pids |= tree
            ports = sorted({p for pid in tree for p in ports_by_pid.get(pid, {})})
            ext_ports = sorted({p for pid in tree for p, ext in ports_by_pid.get(pid, {}).items() if ext})
            try:
                started = min(psutil.Process(p).create_time() for p in pids)
            except psutil.Error:
                pass
            try:
                proc_cwd = psutil.Process(pids[0]).cwd()
            except (psutil.Error, OSError):
                proc_cwd = None
        else:
            proc_cwd = None

        with self.lock:
            rt = self._rt(s["id"])
            if result:
                rt["last_check"] = now
                rt["health_ok"], rt["latency"], rt["health_err"] = result
            if s["health"] in ("tcp", "http") and alive and forced != "down" and rt["health_ok"] is False:
                forced, reason = "down", rt["health_err"] or "헬스체크 실패"

            if forced == "down":
                status = "down"
            elif forced == "warning":
                status = "warning"
            elif not alive:
                # A tracked process that vanished on its own is a failure; a service/container stopped normally is not.
                status = "down" if typ == "proc" and rt["expected"] else "stopped"
                if status == "down":
                    reason = "프로세스가 예기치 않게 종료됨"
            else:
                status = "running"
                rt["expected"] = True

            if cpu is not None:
                rt["cpu_hist"].append(cpu)
                rt["mem_hist"].append(mem or 0)
            if status == "running":
                thr = float(s.get("cpu_threshold") or 80)
                mins = float(s.get("cpu_minutes") or 5)
                if typ == "docker":
                    need = max(1, int(mins * 60 / 10))
                    recent = self.docker.history(target)[0][-need:]
                else:
                    need = max(1, int(mins * 60 / SAMPLE_SEC))
                    recent = list(rt["cpu_hist"])[-need:]
                if s.get("cpu_alert", True) and len(recent) >= need and min(recent) >= thr:
                    status, reason = "warning", f"CPU {thr:g}% 이상 {mins:g}분 지속 (현재 {recent[-1]:.0f}%)"
                lat_limit = int(s.get("latency_ms") or 1000)
                if rt["latency"] and s["health"] in ("tcp", "http") and rt["latency"] > lat_limit:
                    status, reason = "warning", f"응답시간 {rt['latency']}ms (기준 {lat_limit}ms)"

            prev = rt["status"]
            if proc_cwd:
                rt["proc_cwd"] = proc_cwd
            rt.update(status=status, reason=reason, pid=pids[0] if pids else None, ports=ports, ext_ports=ext_ports,
                      cpu=cpu, mem_mb=mem, started_at=started if alive else None)
            if rt["expected"] is None:
                rt["expected"] = alive
            slot = int(now // SLOT_SEC)
            if SEVERITY[status] >= SEVERITY.get(rt["slots"].get(slot), -1):
                rt["slots"][slot] = status
            for k in [k for k in rt["slots"] if k <= slot - 48]:
                del rt["slots"][k]

        if prev is None and status in ("warning", "down"):
            # After a dashboard restart, don't repeat a problem that was already the latest recorded event.
            with self.lock:
                latest = next((e for e in self.events if e["sid"] == s["id"]), None)
            if latest and latest["level"] == status:
                prev = status
        if prev != status and (prev is not None or status in ("warning", "down")):
            titles = {"running": "정상으로 복구" if prev in ("down", "warning") else "실행됨",
                      "warning": "경고", "down": "응답 없음", "stopped": "중지됨"}
            self._event(status, f"{s['name']} {titles[status]}", reason or "", sid=s["id"], name=s["name"])
            if status == "down" and s.get("auto_restart") and typ in ("svc", "proc", "docker"):
                self._auto_restart(s, rt)

    def _auto_restart(self, s, rt):
        recent = [t for t in rt["auto_restarts"] if time.time() - t < 3600]
        if len(recent) >= 3:
            self._event("down", f"{s['name']} 자동 재시작 중단", "1시간 안에 3회 시도했지만 복구되지 않았습니다", s["id"], s["name"])
            return
        rt["auto_restarts"].append(time.time())
        self._event("warning", f"{s['name']} 자동 재시작 시도 {len(recent) + 1}/3", "", s["id"], s["name"])
        self.action_pool.submit(self.control, s["id"], "restart", True)

    # ------------------------------------------------------------------ control
    def control(self, sid, action, auto=False):
        s = next((x for x in store.snapshot()["services"] if x["id"] == sid), None)
        if not s:
            return False, "등록되지 않은 서비스"
        typ, target = s["type"], s["target"]
        if typ == "svc":
            ok, err = system.control_service(target, action)
        elif typ == "proc":
            ok, err = True, None
            if action in ("stop", "restart"):
                ok, err = system.stop_processes(system.match_processes(system.snapshot_processes(), target))
            if ok and action in ("start", "restart"):
                ok, err = self._start(s)
        elif typ == "docker":
            ok, err = docker_mon.control(target, action)
        elif typ == "task":
            ok, err = self._control_task(s, action)
        else:
            ok, err = self._control_http(s, action)
        with self.lock:
            rt = self._rt(sid)
            rt["expected"] = action != "stop"
        label = {"start": "시작", "stop": "중지", "restart": "재시작"}[action]
        who = "자동" if auto else "대시보드에서"
        if ok:
            self._event("stopped" if action == "stop" else "running", f"{s['name']} {who} {label}", "", sid, s["name"])
        else:
            self._event("warning", f"{s['name']} {label} 실패", err or "", sid, s["name"])
        return ok, err

    def _start(self, s):
        """Run the service's start command in its project folder (set by the user, else where it last ran)."""
        with self.lock:
            proc_cwd = self._rt(s["id"]).get("proc_cwd")
        return system.start_process(s.get("start_cmd"), s.get("cwd") or proc_cwd, s.get("log_file"),
                                    system.start_log_path(s["id"]))

    def _control_http(self, s, action):
        port = checks.local_port(s["target"])
        if not port:
            return False, "이 PC가 아닌 주소는 시작/중지할 수 없어요"
        if action in ("start", "restart") and not s.get("start_cmd"):
            return False, "시작하려면 설정 탭에서 '시작 명령'을 입력하세요"
        if action in ("stop", "restart"):
            pids = system.pids_on_port(port)
            if pids:
                ok, err = system.stop_processes(pids)
                if not ok:
                    return ok, err
        if action in ("start", "restart"):
            return self._start(s)
        return True, None

    def _control_task(self, s, action):
        port = int(s.get("port") or 0)
        if action in ("stop", "restart"):
            system.run_task(s["target"], "stop")  # ends the launcher if it is still running; the server is usually detached
            pids = system.pids_on_port(port) if port else []
            if pids:
                # Launcher .bat files often loop and relaunch the server, so stop from the launcher down.
                ok, err = system.stop_processes(sorted({system.launcher_root(p) for p in pids}))
                if not ok:
                    return ok, err
            end = time.time() + 10
            while port and system.pids_on_port(port) and time.time() < end:
                time.sleep(0.5)
            if port and system.pids_on_port(port):
                return False, f"포트 {port}의 서버가 종료되지 않았습니다"
        if action in ("start", "restart"):
            ok, err = system.run_task(s["target"], "start")
            if not ok:
                return ok, err
        return True, None

    # ------------------------------------------------------------------ task scheduler cache
    def refresh_tasks(self, force=False, wait=False):
        """Task list via PowerShell is slow (~1-2s), so refresh it in the background every 30s."""
        with self.lock:
            if self.tasks["loading"] or (not force and time.time() - self.tasks["at"] < 30):
                return
            self.tasks["loading"] = True

        def work():
            items, err = system.list_tasks()
            with self.lock:
                self.tasks = {"at": time.time(), "error": err, "loading": False,
                              "items": {t["full_name"].lower(): t for t in items}}
        if wait:
            work()
        else:
            self.checks_pool.submit(work)

    def task(self, name):
        with self.lock:
            return self.tasks["items"].get(system.task_full_name(name).lower())

    # ------------------------------------------------------------------ views
    def service_view(self, s, detail=False):
        with self.lock:
            rt = self._rt(s["id"])
            if s["type"] == "docker":
                cpu_h, mem_h = self.docker.history(s["target"])
                per_min = 6
                c = self.docker.get(s["target"]) or {}
                mem_limit = c.get("mem_limit_mb")
            else:
                cpu_h, mem_h, per_min, mem_limit = rt["cpu_hist"], rt["mem_hist"], 60 // SAMPLE_SEC, None
            view = {k: v for k, v in s.items()}
            view.update(
                status=rt["status"] or "unknown", reason=rt["reason"], pid=rt["pid"], ports=rt["ports"], ext_ports=rt["ext_ports"],
                web_url=(s["target"] if s["type"] == "http" else rt["web_url"]) if rt["status"] in ("running", "warning") else None,
                proc_cwd=rt["proc_cwd"],
                cpu=rt["cpu"], mem_mb=rt["mem_mb"], mem_limit_mb=mem_limit, latency=rt["latency"],
                started_at=rt["started_at"], cpu_series=minute_series(cpu_h, per_min),
            )
            if s["type"] == "task":
                t = self.tasks["items"].get(system.task_full_name(s["target"]).lower())
                view["task"] = {k: t.get(k) for k in ("state", "last_run", "last_result", "user", "root", "description")} if t else None
            if detail:
                now_slot = int(time.time() // SLOT_SEC)
                slots = [rt["slots"].get(now_slot - 47 + i) for i in range(48)]
                known = [x for x in slots if x]
                view["avail_slots"] = slots
                view["uptime_pct"] = round(100 * sum(x in ("running", "warning") for x in known) / len(known), 1) if known else None
                view["mem_series"] = minute_series(mem_h, per_min)
                view["events"] = [e for e in self.events if e["sid"] == s["id"]][:30]
            return view

    def overview(self):
        services = store.snapshot()["services"]
        with self.lock:
            vm = psutil.virtual_memory()
            return {
                "now": time.time(),
                "last_sample": self.last_sample,
                "agent": {"version": VERSION, "admin": system.is_admin()},
                "host": {**self.host, "cpu": self.host_cpu[-1] if self.host_cpu else None, "mem_pct": vm.percent,
                         "mem_used_mb": round(vm.used / 2**20), "cpu_hist": list(self.host_cpu),
                         "mem_hist": list(self.host_mem)},
                "services": [self.service_view(s) for s in services],
                "events": list(self.events)[:30],
            }

    def docker_view(self):
        d = self.docker
        with d.lock:
            containers = [dict(c) for c in d.containers.values()]
            engine, df = dict(d.engine), d.df
        for c in containers:
            cpu_h, mem_h = d.history(c["name"])
            c["cpu_series"] = minute_series(cpu_h, 6)
            c["mem_series"] = minute_series(mem_h, 6)
        with self.lock:
            events = list(self.docker_events)[:20]
        return {"engine": engine, "containers": containers, "df": df, "events": events}


monitor = Monitor()
