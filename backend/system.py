"""Windows services, processes and host information."""
import ctypes
import platform
import subprocess
import time

import psutil

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
NCPU = psutil.cpu_count() or 1


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def windows_edition():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
            name = winreg.QueryValueEx(k, "ProductName")[0]
            build = winreg.QueryValueEx(k, "CurrentBuild")[0]
            # Windows 11 still reports "Windows 10" in ProductName; the build number tells them apart.
            if name.startswith("Windows 10") and int(build) >= 22000:
                name = name.replace("Windows 10", "Windows 11", 1)
            return name
    except OSError:
        return f"{platform.system()} {platform.release()}"


def host_static():
    return {
        "name": platform.node(),
        "os": windows_edition(),
        "boot_time": psutil.boot_time(),
        "cpu_count": NCPU,
        "mem_total_mb": round(psutil.virtual_memory().total / 2**20),
    }


# ---------------------------------------------------------------- Windows services

def service_info(name):
    try:
        return psutil.win_service_get(name).as_dict()
    except (psutil.NoSuchProcess, OSError):
        return None


def list_services():
    out = []
    for s in psutil.win_service_iter():
        try:
            out.append({"name": s.name(), "display_name": s.display_name(), "status": s.status()})
        except (psutil.Error, OSError):
            continue
    return sorted(out, key=lambda x: x["display_name"].lower())


def _sc(*args, timeout=30):
    r = subprocess.run(["sc.exe", *args], capture_output=True, encoding="oem", errors="replace",
                       timeout=timeout, creationflags=CREATE_NO_WINDOW)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _sc_error(code, out):
    if code == 5:
        return "권한이 없습니다. 관리자 권한으로 대시보드를 실행하세요."
    if code == 1056:
        return None  # already running
    if code == 1062:
        return None  # not started
    return out.strip().splitlines()[-1] if out.strip() else f"sc.exe 오류 코드 {code}"


def _wait_service(name, want, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        info = service_info(name)
        if info and info["status"] == want:
            return True
        time.sleep(0.5)
    return False


def control_service(name, action):
    if action in ("stop", "restart"):
        code, out = _sc("stop", name)
        err = _sc_error(code, out) if code else None
        if err:
            return False, err
        if not _wait_service(name, "stopped"):
            return False, "서비스가 제시간에 멈추지 않았습니다."
    if action in ("start", "restart"):
        code, out = _sc("start", name)
        err = _sc_error(code, out) if code else None
        if err:
            return False, err
    return True, None


# ---------------------------------------------------------------- processes

def snapshot_processes():
    procs = []
    for p in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            cmd = " ".join(p.info["cmdline"] or []) or (p.info["name"] or "")
            procs.append((p.info["pid"], p.info["name"] or "", cmd, p.info["create_time"] or 0))
        except (psutil.Error, OSError):
            continue
    return procs


def match_processes(procs, needle):
    """Root processes whose name or command line contains `needle` (children are excluded)."""
    n = needle.lower().strip()
    if not n:
        return []
    hits = {pid: ct for pid, name, cmd, ct in procs if n in name.lower() or n in cmd.lower()}
    roots = []
    for pid in hits:
        try:
            parent = psutil.Process(pid).ppid()
        except psutil.Error:
            continue
        if parent not in hits:
            roots.append(pid)
    return sorted(roots, key=lambda pid: hits[pid])


def list_processes(procs):
    seen, out = set(), []
    for pid, name, cmd, _ in procs:
        if pid in (0, 4) or cmd in seen:
            continue
        seen.add(cmd)
        out.append({"pid": pid, "name": name, "cmdline": cmd[:300]})
    return sorted(out, key=lambda x: x["name"].lower())


LOOPBACK = {"127.0.0.1", "::1"}


def listening_ports():
    """pid -> {port: reachable_from_outside}. Ports bound only to loopback can't be reached from the network."""
    ports = {}
    try:
        for c in psutil.net_connections(kind="inet"):
            if c.status == psutil.CONN_LISTEN and c.pid:
                d = ports.setdefault(c.pid, {})
                d[c.laddr.port] = d.get(c.laddr.port, False) or c.laddr.ip not in LOOPBACK
    except (psutil.Error, OSError):
        pass
    return ports


class ProcMeter:
    """Keeps psutil.Process objects alive so cpu_percent() has a baseline between samples."""

    def __init__(self):
        self.cache = {}

    def _proc(self, pid):
        p = self.cache.get(pid)
        if p is None or not p.is_running():
            p = psutil.Process(pid)
            p.cpu_percent(None)
            self.cache[pid] = p
        return p

    def measure(self, root_pids):
        """CPU (% of whole machine) and RSS (MB) of the given processes and their children."""
        cpu = mem = 0.0
        tree = set()
        for pid in root_pids:
            try:
                root = psutil.Process(pid)
                tree.add(pid)
                tree.update(c.pid for c in root.children(recursive=True))
            except psutil.Error:
                continue
        measured = 0
        for pid in tree:
            try:
                p = self._proc(pid)
                cpu += p.cpu_percent(None)
                mem += p.memory_info().rss
                measured += 1
            except psutil.Error:
                continue
        if not measured:  # e.g. a SYSTEM process seen from a non-admin dashboard
            return None, None, tree
        return round(cpu / NCPU, 1), round(mem / 2**20, 1), tree

    def prune(self, alive):
        for pid in list(self.cache):
            if pid not in alive:
                del self.cache[pid]


def stop_processes(root_pids, timeout=5):
    procs, denied = [], False
    for pid in root_pids:
        try:
            root = psutil.Process(pid)
            procs.extend(root.children(recursive=True))
            procs.append(root)
        except psutil.AccessDenied:
            denied = True
        except psutil.Error:
            continue
    for p in procs:
        try:
            p.terminate()
        except psutil.AccessDenied:
            denied = True
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    for p in alive:
        try:
            p.kill()
        except psutil.AccessDenied:
            denied = True
        except psutil.Error:
            pass
    if denied:
        return False, "프로세스를 종료할 권한이 없습니다. SYSTEM 계정으로 실행 중인 서버는 관리자 권한으로 대시보드를 실행해야 제어할 수 있어요."
    return True, None


_SCRIPT_HOSTS = {"wscript.exe", "cscript.exe"}
_WRAPPERS = {"python.exe", "pythonw.exe", "uv.exe", "uvx.exe", "py.exe"}  # venv/uv launchers that just spawn the real server


def _is_launcher(proc):
    name = proc.name().lower()
    if name in _SCRIPT_HOSTS:
        return True
    if name == "cmd.exe":
        # Only a cmd running a script (cmd /c run_server.bat), never an interactive terminal window.
        cmd = " ".join(proc.cmdline()).lower()
        return " /c" in cmd or ".bat" in cmd or ".cmd" in cmd
    if name in _WRAPPERS:
        return len(proc.children()) == 1
    return False


def launcher_root(pid):
    """Walk up from a server process through its launchers (venv/uv wrappers, a run_server.bat restart loop)
    and return the topmost one, so stopping it doesn't just make the .bat relaunch the server.
    Stops before terminals the user opened (PowerShell, interactive cmd, editors)."""
    top = pid
    try:
        p = psutil.Process(pid)
        for _ in range(6):
            parent = p.parent()
            if not parent or not _is_launcher(parent):
                break
            top, p = parent.pid, parent
    except psutil.Error:
        pass
    return top


def pids_on_port(port, ports_by_pid=None):
    ports_by_pid = listening_ports() if ports_by_pid is None else ports_by_pid
    return sorted(pid for pid, ports in ports_by_pid.items() if int(port) in ports)


# ---------------------------------------------------------------- Task Scheduler

TASKS_PS = r"""
$out = foreach ($t in Get-ScheduledTask -ErrorAction SilentlyContinue) {
  if ($t.TaskPath -like '\Microsoft\*') { continue }
  $i = $t | Get-ScheduledTaskInfo -ErrorAction SilentlyContinue
  $a = @($t.Actions)[0]
  [pscustomobject]@{ name=$t.TaskName; path=$t.TaskPath; state=[string]$t.State; description=$t.Description;
    user=$t.Principal.UserId; execute=$a.Execute; args=$a.Arguments; cwd=$a.WorkingDirectory;
    last_run=$(if ($i.LastRunTime -and $i.LastRunTime.Year -gt 2000) { $i.LastRunTime.ToString('o') } else { $null });
    last_result=$i.LastTaskResult }
}
ConvertTo-Json -InputObject @($out) -Compress -Depth 3
"""
_SKIP_TASKS = ("onedrive", "adobe", "googleupdate", "createexplorershell", "microsoftedge", "npcap", "user_feed",
               "platformexperiencehelper", "autorunforuser")
_HELPER_DIRS = {"win", "windows", "service", "services", "scripts", "script", "bin", "deploy", "tools"}


def task_full_name(name):
    name = (name or "").strip()
    return name if name.startswith("\\") else "\\" + name


def list_tasks():
    """Non-Microsoft scheduled tasks. Tasks registered for SYSTEM by an admin are only visible when running as admin."""
    import json
    code, out, err = powershell(TASKS_PS, timeout=90)
    try:
        items = json.loads(out or "[]")
    except ValueError:
        return [], err or "작업 목록을 읽지 못했습니다"
    items = items if isinstance(items, list) else [items]
    for t in items:
        t["full_name"] = (t.get("path") or "\\") + t["name"]
        t["root"] = task_root(t)
    return [t for t in items if not any(k in t["name"].lower().replace(" ", "") for k in _SKIP_TASKS)], None


def task_root(task):
    """Project folder a task starts, e.g. ...\\InSide\\service\\win\\run.vbs -> ...\\InSide."""
    import ntpath
    import re
    text = f"{task.get('args') or ''} {task.get('execute') or ''}"
    m = re.search(r'"?([A-Za-z]:\\[^"]+?\.(?:vbs|bat|cmd|ps1|py|exe|js))"?', text, re.I)
    path = ntpath.dirname(m.group(1)) if m else (task.get("cwd") or "")
    if not path:
        return None
    while ntpath.basename(path).lower() in _HELPER_DIRS and ntpath.dirname(path) != path:
        path = ntpath.dirname(path)
    return path


_EDITORS = {"code.exe", "cursor.exe", "windsurf.exe", "idea64.exe", "pycharm64.exe", "devenv.exe"}


def _task_script(task):
    import re
    m = re.search(r'"?([A-Za-z]:\\[^"]+?\.(?:vbs|bat|cmd|ps1|py|exe|js))"?', f"{task.get('args') or ''} {task.get('execute') or ''}", re.I)
    return m.group(1) if m else None


def _read_text(path):
    try:
        raw = open(path, "rb").read(256 * 1024)
    except OSError:
        return ""
    for enc in ("utf-8", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _configured_port(task):
    """Port written in the project: .env PORT=, or `set PORT=` in the launcher scripts next to the task's script."""
    import glob
    import ntpath
    import re
    root, script = task.get("root"), _task_script(task)
    if root:
        m = re.search(r"^\s*PORT\s*=\s*(\d{2,5})\s*$", _read_text(ntpath.join(root, ".env")), re.M)
        if m:
            return int(m.group(1))
    if script:
        for path in [script] + glob.glob(ntpath.join(ntpath.dirname(script), "*.bat")):
            m = re.search(r"^\s*set\s+\"?PORT=(\d{2,5})", _read_text(path), re.M | re.I)
            if m:
                return int(m.group(1))
    return None


def _ancestors(pid, depth=6):
    out = []
    try:
        p = psutil.Process(pid)
        for _ in range(depth):
            p = p.parent()
            if not p:
                break
            try:
                out.append((p.name().lower(), " ".join(p.cmdline()).lower()))
            except psutil.Error:
                out.append((p.name().lower(), ""))
    except psutil.Error:
        pass
    return out


def guess_task_port(task, listeners):
    """Find the port of the server a task started, most reliable signal first:
    1. the port configured in the project (.env PORT= / `set PORT=` in its launcher .bat) that is listening
    2. a listener launched through the task's own script (its cmd/wscript ancestor mentions the script folder)
    3. a listener running a program inside the project folder (e.g. .venv\\Scripts\\python.exe)
    4. a listener whose working folder is the project, unless an editor (VS Code etc.) started it
    5. a port number in the task's arguments or description"""
    import ntpath
    import re
    root = (task.get("root") or "").lower().rstrip("\\")
    script = _task_script(task)
    script_dir = ntpath.dirname(script).lower() if script else None
    listening = {l["port"] for l in listeners}
    in_root = lambda path: bool(root and path and (path.lower() == root or path.lower().startswith(root + "\\")))

    configured = _configured_port(task)
    if configured and configured in listening:
        return configured
    if script_dir:
        for l in listeners:
            if any(script_dir in cmd for _, cmd in _ancestors(l["pid"])):
                return l["port"]
    for l in listeners:
        if in_root(l["exe"]):
            return l["port"]
    for l in listeners:
        if in_root(l["cwd"]) and not any(name in _EDITORS for name, _ in _ancestors(l["pid"])):
            return l["port"]
    for n in re.findall(r"(?<![\d.])(\d{4,5})(?![\d.])", f"{task.get('args') or ''} {task.get('description') or ''}"):
        if 1024 <= int(n) <= 65535 and (int(n) in listening or not listening):
            return int(n)
    return configured  # configured but not running right now


def listener_details():
    out = []
    for pid, ports in listening_ports().items():
        try:
            p = psutil.Process(pid)
        except psutil.Error:
            continue
        info = {}
        for key, fn in (("exe", p.exe), ("cwd", p.cwd), ("cmdline", lambda: " ".join(p.cmdline()))):
            try:
                info[key] = fn()
            except (psutil.Error, OSError):
                info[key] = None
        for port in ports:
            out.append({"port": port, "pid": pid, **info})
    return out


def run_task(full_name, action):
    verb = {"start": "Start", "stop": "Stop"}[action]
    script = f"""
$f = $env:SVCWATCH_TASK; $i = $f.LastIndexOf('\\')
{verb}-ScheduledTask -TaskPath $f.Substring(0, $i + 1) -TaskName $f.Substring($i + 1) -ErrorAction Stop
"""
    code, out, err = powershell(script, timeout=60, env={"SVCWATCH_TASK": task_full_name(full_name)})
    if code == 0:
        return True, None
    if "Access is denied" in err or "0x80070005" in err or "액세스" in err:
        return False, "작업을 제어할 권한이 없습니다. SYSTEM 계정 작업은 관리자 권한으로 대시보드를 실행하세요."
    if "No MSFT_ScheduledTask" in err or "0x80070002" in err:
        return False, "작업 스케줄러에서 이 작업을 찾을 수 없습니다 (관리자 권한이 아니면 SYSTEM 작업은 보이지 않아요)."
    return False, err.strip().splitlines()[0] if err.strip() else f"오류 코드 {code}"


def newest_log(root):
    """Newest *.log under root\\logs, root\\log or root itself."""
    import glob
    import os
    if not root:
        return None
    files = []
    for pattern in ("logs\\**\\*.log", "log\\**\\*.log", "*.log"):
        files.extend(glob.glob(os.path.join(root, pattern), recursive=True))
    files = [f for f in files if os.path.isfile(f)]
    return max(files, key=os.path.getmtime) if files else None


CREATE_BREAKAWAY_FROM_JOB = 0x01000000
# Settings meant for the dashboard itself (set by service/win/run_server.bat) must not leak into apps it starts.
_DASHBOARD_ONLY_ENV = ("PYTHONIOENCODING", "SVCWATCH_NO_BROWSER", "SVCWATCH_PORT", "SVCWATCH_DATA")


def start_log_path(sid):
    from .config import DATA_DIR
    return str(DATA_DIR / "logs" / f"service-{sid}.log")


def start_process(cmd, cwd=None, log_file=None, start_log=None):
    """Start a server command hidden and detached from the dashboard. Output goes to the service's log file
    (or the dashboard's per-service start log) so a failing start can be diagnosed from the log tab."""
    import os
    import time
    from datetime import datetime
    if not cmd:
        return False, "시작 명령이 설정되지 않았습니다. 설정 탭에서 '시작 명령'을 입력하세요."
    if not cwd or not os.path.isdir(cwd):
        return False, "어느 폴더에서 실행할지 모릅니다. 설정 탭에서 '프로젝트 폴더'를 입력하세요."
    path = log_file or start_log
    out = subprocess.DEVNULL
    if path:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        out = open(path, "ab")
        out.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] svc.watch 시작: {cmd} (폴더 {cwd})\n".encode("utf-8"))
        out.flush()
    from .config import PORT as DASHBOARD_PORT
    env = {k: v for k, v in os.environ.items() if k not in _DASHBOARD_ONLY_ENV}
    # run_server.bat keeps the dashboard's port in PORT (and its loop state in STOP_FLAG/RESTARTS). Apps that
    # read PORT (Flask configs often do) would otherwise start on the dashboard's port instead of their own.
    if env.get("PORT") == str(DASHBOARD_PORT):
        env.pop("PORT")
    for k in ("STOP_FLAG", "RESTARTS"):
        env.pop(k, None)
    env["PYTHONUNBUFFERED"] = "1"
    # CREATE_NO_WINDOW gives the shell a hidden console that child programs (uv, python) share, so no window pops up.
    # Breaking away from the job keeps the server alive if the dashboard's scheduled task is stopped.
    flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        try:
            proc = subprocess.Popen(cmd, cwd=cwd, shell=True, stdout=out, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, env=env, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB)
        except PermissionError:  # the job doesn't allow breakaway
            proc = subprocess.Popen(cmd, cwd=cwd, shell=True, stdout=out, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, env=env, creationflags=flags)
    except OSError as e:
        return False, f"실행 실패: {e}"
    finally:
        if path:
            out.close()
    # A command that dies right away (typo, wrong folder, missing package) should fail loudly, not look "started".
    time.sleep(3)
    code = proc.poll()
    if code is not None and code != 0:
        tail = tail_file(path, 5)[0] if path else []
        return False, f"시작 직후 종료됨 (종료 코드 {code})" + (": " + " / ".join(l for l in tail if l.strip())[-300:] if tail else "")
    return True, None


def powershell(script, timeout=120, env=None):
    """Run a PowerShell script from a temp file. Pass untrusted values through `env`, never by formatting them in."""
    import os
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".ps1", prefix="svcwatch_")
    os.close(fd)
    header = "[Console]::OutputEncoding=[Text.Encoding]::UTF8\n$ProgressPreference='SilentlyContinue'\n"
    # utf-8-sig: Windows PowerShell 5.1 reads BOM-less files as ANSI, which breaks Korean text.
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(header + script)
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", path],
                           capture_output=True, encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=CREATE_NO_WINDOW, env={**os.environ, **(env or {})})
        return r.returncode, r.stdout, r.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "PowerShell 실행 시간 초과"
    finally:
        os.unlink(path)


def service_events(display_name, limit=100):
    script = """
$n = $env:SVCWATCH_DISPLAY
Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Service Control Manager'} -MaxEvents 2000 -ErrorAction SilentlyContinue |
  Where-Object { $_.Message -like "*$n*" } | Select-Object -First %d |
  ForEach-Object { '{0:yyyy-MM-dd HH:mm:ss} {1,-5} {2}' -f $_.TimeCreated, $_.LevelDisplayName, ($_.Message -replace '\\s+', ' ') }
""" % int(limit)
    code, out, err = powershell(script, timeout=60, env={"SVCWATCH_DISPLAY": display_name})
    lines = out.splitlines()[::-1]
    return lines, None if lines or code == 0 else (err or "이벤트 로그를 읽지 못했습니다")


def tail_file(path, lines=200):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 256 * 1024))
            raw = f.read().splitlines()[-lines:]
        # Logs written by .bat files are CP949 while Python apps often write UTF-8, sometimes into the same file.
        out = []
        for line in raw:
            try:
                out.append(line.decode("utf-8"))
            except UnicodeDecodeError:
                out.append(line.decode("cp949", errors="replace"))
        return out, None
    except OSError as e:
        return [], f"로그 파일을 읽을 수 없습니다: {e}"
