"""Windows Firewall allow-list mode.

Design:
- Every rule we create lives in the "svc.watch" group, so we can remove them all at once.
- For allow-list ports, a Block rule covers every address *except* the allowed ones. Block beats Allow in
  Windows Firewall, so other programs' "allow everyone" rules can't reopen those ports.
- "Local only" ports get a plain Block rule (loopback traffic is never filtered, so local use keeps working).
- Before applying, the whole policy is exported with netsh; if the user doesn't confirm in time, it is imported back.
"""
import ipaddress
import json
import os
import re
import subprocess
import threading
import time
from datetime import date

from .config import DATA_DIR, FW_GROUP, FW_ROLLBACK_SEC
from .store import store
from .system import CREATE_NO_WINDOW, is_admin, powershell

BACKUP_DIR = DATA_DIR / "fw-backups"
RULE_ID_RE = re.compile(r"^[\w{}()\-.@ \\:/+,#&]{1,400}$")  # app rule ids embed program paths
_QUOTES = "'‘’‚‛"


def ps_str(value):
    """Single-quoted PowerShell literal. PowerShell treats curly quotes as quotes too, so drop those."""
    s = "".join(ch for ch in str(value) if ch >= " " and ch not in _QUOTES[1:])
    return "'" + s.replace("'", "''") + "'"


# ---------------------------------------------------------------- validation

def normalize(cfg):
    out = {"mode": bool(cfg.get("mode")), "allow": [], "exposure": [], "disable_rules": [], "rule_names": {},
           "next_id": int(cfg.get("next_id") or 1)}
    seen = set()
    for a in cfg.get("allow") or []:
        try:
            net = ipaddress.ip_network(str(a.get("cidr", "")).strip(), strict=False)
        except ValueError:
            raise ValueError(f"잘못된 IP/대역: {a.get('cidr')}")
        if net.prefixlen == 0:
            raise ValueError("인터넷 전체(0.0.0.0/0)를 허용하면 허용 목록 모드가 의미가 없습니다")
        if str(net) in seen:
            raise ValueError(f"중복된 IP/대역: {net}")
        seen.add(str(net))
        ports = a.get("ports", "all")
        if ports != "all":
            ports = sorted({int(p) for p in ports})
            if not ports or any(p < 1 or p > 65535 for p in ports):
                raise ValueError(f"잘못된 포트: {a.get('ports')}")
        expires = a.get("expires") or None
        if expires:
            date.fromisoformat(expires)
        aid = int(a.get("id") or out["next_id"])
        out["next_id"] = max(out["next_id"], aid + 1)
        name = re.sub(r"[\x00-\x1f\x7f]", " ", str(a.get("name") or net)).strip()[:60] or str(net)
        out["allow"].append({"id": aid, "on": bool(a.get("on", True)), "name": name,
                             "cidr": str(net), "ports": ports, "expires": expires})
    ports_seen = set()
    for e in cfg.get("exposure") or []:
        port = int(e["port"])
        if not 1 <= port <= 65535 or port in ports_seen:
            raise ValueError(f"잘못되었거나 중복된 포트: {port}")
        ports_seen.add(port)
        mode = e.get("mode")
        if mode not in ("local", "allow"):
            raise ValueError("노출 범위는 local 또는 allow 여야 합니다")
        label = re.sub(r"[\x00-\x1f\x7f]", " ", str(e.get("label") or "")).strip()[:60]
        out["exposure"].append({"port": port, "label": label, "mode": mode})
    for rid in cfg.get("disable_rules") or []:
        if not RULE_ID_RE.match(str(rid)):
            raise ValueError(f"잘못된 규칙 ID: {rid}")
        out["disable_rules"].append(str(rid))
    names = cfg.get("rule_names") or {}
    out["rule_names"] = {rid: re.sub(r"[\x00-\x1f\x7f]", " ", str(names[rid]))[:120]
                         for rid in out["disable_rules"] if rid in names}
    return out


def active_allow(cfg):
    today = date.today().isoformat()
    return [a for a in cfg["allow"] if a["on"] and not (a["expires"] and a["expires"] < today)]


def _complement(nets, version):
    whole = ipaddress.ip_network("0.0.0.0/0" if version == 4 else "::/0")
    remaining = [whole]
    for n in (n for n in nets if n.version == version):
        nxt = []
        for r in remaining:
            if r.subnet_of(n):
                continue
            nxt.extend(r.address_exclude(n) if n.subnet_of(r) else [r])
        remaining = nxt
    return list(ipaddress.collapse_addresses(remaining))


# ---------------------------------------------------------------- script

def build_script(cfg, disabled_by_us, for_display=False):
    L = []
    c = L.append
    c(f"# svc.watch 방화벽 규칙 · 관리자 권한 PowerShell")
    c(f"Get-NetFirewallRule -Group {ps_str(FW_GROUP)} -ErrorAction SilentlyContinue | Remove-NetFirewallRule")

    if not cfg["mode"]:
        c("")
        c("# 허용 목록 모드 끔 → svc.watch 규칙 제거, 이전에 끈 규칙 다시 켜기")
        for rid in disabled_by_us:
            c(f"Enable-NetFirewallRule -Name {ps_str(rid)} -ErrorAction SilentlyContinue")
        return "\n".join(L)

    c("")
    c("# 1) 모든 프로필 켜기 · 들어오는 연결 기본 차단 · 나가는 연결 허용 · 차단 로그 기록")
    c("Set-NetFirewallProfile -All -Enabled True -DefaultInboundAction Block -DefaultOutboundAction Allow "
      "-LogBlocked True -LogMaxSizeKilobytes 16384")

    to_enable = [r for r in disabled_by_us if r not in cfg["disable_rules"]]
    if cfg["disable_rules"] or to_enable:
        c("")
        c("# 2) 모든 IP를 허용하던 기존 규칙 끄기")
        for rid in cfg["disable_rules"]:
            c(f"Disable-NetFirewallRule -Name {ps_str(rid)} -ErrorAction SilentlyContinue")
        for rid in to_enable:
            c(f"Enable-NetFirewallRule -Name {ps_str(rid)} -ErrorAction SilentlyContinue")

    allow_ports = sorted(e["port"] for e in cfg["exposure"] if e["mode"] == "allow")
    local_ports = sorted(e["port"] for e in cfg["exposure"] if e["mode"] == "local")
    entries = active_allow(cfg)

    c("")
    c("# 3) 허용 목록 IP → 지정한 포트 허용")
    sources_by_port = {p: set() for p in allow_ports}
    if not entries:
        c("# (허용된 IP 없음 → 외부에서 들어오는 접속은 모두 차단)")
    for a in entries:
        ports = allow_ports if a["ports"] == "all" else a["ports"]
        if not ports:
            c(f"# '{a['name']}': 허용 목록 포트가 없어 규칙을 만들지 않음")
            continue
        for p in ports:
            sources_by_port.setdefault(p, set()).add(a["cidr"])
        c(f"New-NetFirewallRule -Group {ps_str(FW_GROUP)} -DisplayName {ps_str('svc.watch 허용 · ' + a['name'])} "
          f"-Direction Inbound -Action Allow -Protocol TCP -RemoteAddress {ps_str(a['cidr'])} "
          f"-LocalPort {','.join(map(str, ports))} | Out-Null")

    # Group ports that share the same allowed sources into one block rule.
    groups = {}
    for p, srcs in sources_by_port.items():
        if p not in local_ports:
            groups.setdefault(frozenset(srcs), []).append(p)
    if groups:
        c("")
        c("# 4) 같은 포트로 들어오는 '허용 목록 외' 주소 차단 (차단 규칙은 다른 프로그램의 허용 규칙보다 우선)")
    for srcs, ports in groups.items():
        nets = [ipaddress.ip_network(s) for s in srcs]
        comp = _complement(nets, 4) + _complement(nets, 6)
        if not comp:
            continue
        addr = ",".join(ps_str(str(n)) for n in comp)
        shown = addr if not for_display or len(comp) <= 6 else ",".join(ps_str(str(n)) for n in comp[:4]) + f", … (총 {len(comp)}개 대역)"
        c(f"New-NetFirewallRule -Group {ps_str(FW_GROUP)} -DisplayName {ps_str('svc.watch 차단 · 허용 목록 외 (' + ','.join(map(str, sorted(ports))) + ')')} "
          f"-Direction Inbound -Action Block -Protocol TCP -LocalPort {','.join(map(str, sorted(ports)))} "
          f"-RemoteAddress @({shown}) | Out-Null")

    if local_ports:
        c("")
        c("# 5) 로컬 전용 포트 → 외부 접속 차단 (이 PC 안에서의 접속은 영향 없음)")
        c(f"New-NetFirewallRule -Group {ps_str(FW_GROUP)} -DisplayName {ps_str('svc.watch 차단 · 로컬 전용')} "
          f"-Direction Inbound -Action Block -Protocol TCP -LocalPort {','.join(map(str, local_ports))} | Out-Null")
    return "\n".join(L)


# ---------------------------------------------------------------- status / scanning

STATUS_PS = r"""
$p = Get-NetFirewallProfile | ForEach-Object {
  [pscustomobject]@{ name=$_.Name; enabled=[string]$_.Enabled; inbound=[string]$_.DefaultInboundAction;
                     outbound=[string]$_.DefaultOutboundAction; log_blocked=[string]$_.LogBlocked; log_file=$_.LogFileName } }
$a = @(Get-NetConnectionProfile -ErrorAction SilentlyContinue | ForEach-Object { [string]$_.NetworkCategory })
$m = @(Get-NetFirewallRule -Group 'svc.watch' -ErrorAction SilentlyContinue).Count
[pscustomobject]@{ profiles=@($p); active=$a; managed=$m } | ConvertTo-Json -Compress -Depth 4
"""

CONFLICT_PS = r"""
$addr=@{}; Get-NetFirewallAddressFilter | ForEach-Object { $addr[$_.InstanceID] = $_ }
$port=@{}; Get-NetFirewallPortFilter | ForEach-Object { $port[$_.InstanceID] = $_ }
$app=@{};  Get-NetFirewallApplicationFilter | ForEach-Object { $app[$_.InstanceID] = $_ }
$out = foreach ($r in Get-NetFirewallRule -Direction Inbound -Action Allow -Enabled True) {
  if ($r.Group -eq 'svc.watch') { continue }
  $a = $addr[$r.InstanceID]; $pf = $port[$r.InstanceID]
  if (-not $a -or (@($a.RemoteAddress) -notcontains 'Any')) { continue }
  if ($pf -and @('TCP','UDP','Any') -notcontains [string]$pf.Protocol) { continue }
  [pscustomobject]@{ id=$r.Name; name=$r.DisplayName; group=$r.DisplayGroup; profile=[string]$r.Profile;
    protocol=[string]$pf.Protocol; ports=(@($pf.LocalPort) -join ','); program=[string]$app[$r.InstanceID].Program }
}
ConvertTo-Json -InputObject @($out) -Compress
"""


class FirewallManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.pending = None
        self._status = (0, None)
        self.conflicts = {"scanned_at": None, "items": [], "error": None}

    def status(self, force=False):
        at, cached = self._status
        if cached and not force and time.time() - at < 10:
            return cached
        code, out, err = powershell(STATUS_PS, timeout=60)
        try:
            data = json.loads(out)
        except ValueError:
            data = {"profiles": [], "active": [], "managed": 0, "error": err or "방화벽 상태를 읽지 못했습니다"}
        self._status = (time.time(), data)
        return data

    def scan_conflicts(self):
        if not is_admin():
            self.conflicts = {"scanned_at": None, "items": [], "error": "방화벽 규칙 목록은 관리자 권한으로 실행할 때만 읽을 수 있어요"}
            return self.conflicts
        code, out, err = powershell(CONFLICT_PS, timeout=180)
        if "PermissionDenied" in err or "Access is denied" in err:
            self.conflicts = {"scanned_at": None, "items": [], "error": "방화벽 규칙을 읽을 권한이 없습니다"}
            return self.conflicts
        try:
            items = json.loads(out or "[]")
            items = items if isinstance(items, list) else [items]
            for it in items:
                prog = (it.get("program") or "").lower()
                it["suggested"] = (it.get("ports") in ("Any", "")) and prog not in ("", "any", "system") \
                    and "\\windows\\" not in prog and "%systemroot%" not in prog
            self.conflicts = {"scanned_at": time.time(), "items": items, "error": None}
        except ValueError:
            self.conflicts = {"scanned_at": time.time(), "items": [], "error": err or "규칙을 읽지 못했습니다"}
        return self.conflicts

    def blocked(self, log_file, limit=20):
        if not is_admin():
            return None, "차단 로그는 관리자 권한으로 실행할 때만 읽을 수 있어요"
        path = os.path.expandvars((log_file or "").replace("%systemroot%", "%SystemRoot%"))
        try:
            with open(path, "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 2 * 2**20))
                lines = f.read().decode("utf-8", errors="replace").splitlines()
        except OSError:
            return [], "아직 차단 로그가 없어요 (허용 목록 모드를 적용하면 기록이 시작됩니다)"
        agg = {}
        for line in lines:
            parts = line.split()
            if len(parts) < 8 or parts[0].startswith("#") or parts[2] != "DROP" or (len(parts) > 16 and parts[16] != "RECEIVE"):
                continue
            key = (parts[4], parts[7], parts[3])
            item = agg.setdefault(key, {"ip": parts[4], "port": parts[7], "protocol": parts[3], "count": 0})
            item["count"] += 1
            item["last"] = f"{parts[0]} {parts[1]}"
        items = sorted(agg.values(), key=lambda x: x["last"], reverse=True)[:limit]
        for it in items:
            try:
                it["private"] = ipaddress.ip_address(it["ip"]).is_private
            except ValueError:
                it["private"] = False
        return items, None

    # ------------------------------------------------------------ apply / rollback
    def _netsh(self, *args):
        r = subprocess.run(["netsh", "advfirewall", *args], capture_output=True, encoding="oem", errors="replace",
                           timeout=120, creationflags=CREATE_NO_WINDOW)
        return r.returncode == 0, (r.stdout + r.stderr).strip()

    def apply(self, auto_rollback=True):
        if not is_admin():
            raise PermissionError("방화벽을 바꾸려면 관리자 권한이 필요합니다. run-admin.bat으로 대시보드를 다시 실행하세요.")
        with self.lock:
            if self.pending:
                raise RuntimeError("이전 적용을 먼저 '유지' 또는 '되돌리기' 하세요.")
            data = store.snapshot()
            cfg = normalize(data["firewall"])
            disabled_before = data.get("firewall_disabled_by_us") or []
            BACKUP_DIR.mkdir(exist_ok=True)
            backup = BACKUP_DIR / time.strftime("%Y%m%d-%H%M%S.wfw")
            ok, msg = self._netsh("export", str(backup))
            if not ok:
                raise RuntimeError(f"현재 방화벽 설정 백업 실패: {msg}")
            for old in sorted(BACKUP_DIR.glob("*.wfw"))[:-10]:
                old.unlink(missing_ok=True)

            code, out, err = powershell("$ErrorActionPreference='Stop'\n" + build_script(cfg, disabled_before), timeout=180)
            if code != 0:
                self._netsh("import", str(backup))
                raise RuntimeError(f"적용 실패, 이전 설정으로 복구했습니다: {err[-500:]}")

            if cfg["mode"]:
                disabled_now = sorted(set(cfg["disable_rules"]))
            else:
                disabled_now = []
            prev = {k: data.get(k) for k in ("firewall_applied", "firewall_disabled_by_us", "firewall_applied_at")}
            with store.lock:
                store.data["firewall_applied"] = cfg
                store.data["firewall_disabled_by_us"] = disabled_now
                store.data["firewall_applied_at"] = time.time()
                store.save()
            self._status = (0, None)
            if auto_rollback:
                timer = threading.Timer(FW_ROLLBACK_SEC, self.rollback, kwargs={"reason": "timeout"})
                timer.daemon = True
                self.pending = {"deadline": time.time() + FW_ROLLBACK_SEC, "backup": str(backup), "prev": prev, "timer": timer}
                timer.start()
            return {"pending": bool(auto_rollback)}

    def confirm(self):
        with self.lock:
            if not self.pending:
                return False
            self.pending["timer"].cancel()
            self.pending = None
            return True

    def rollback(self, reason="user"):
        with self.lock:
            p = self.pending
            if not p:
                return False, "되돌릴 적용 내역이 없습니다"
            p["timer"].cancel()
            ok, msg = self._netsh("import", p["backup"])
            with store.lock:
                store.data.update(p["prev"])
                store.save()
            self.pending = None
            self._status = (0, None)
            self.last_rollback = {"at": time.time(), "reason": reason, "ok": ok, "msg": None if ok else msg}
            return ok, None if ok else msg

    def pending_view(self):
        p = self.pending
        return {"deadline": p["deadline"], "left": max(0, round(p["deadline"] - time.time()))} if p else None


firewall = FirewallManager()
