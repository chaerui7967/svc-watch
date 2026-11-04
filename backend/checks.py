"""Health checks: TCP port and HTTP endpoint."""
import socket
import ssl
import time
import urllib.error
import urllib.request


def tcp(port, host="127.0.0.1", timeout=1.0):
    start = time.perf_counter()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True, round((time.perf_counter() - start) * 1000), None
    except OSError as e:
        return False, None, f"TCP {port} 연결 실패 ({e.__class__.__name__})"


def detect_web(port, timeout=1.5):
    """Return the local URL if something on this port answers HTTP or HTTPS, else None."""
    for scheme in ("http", "https"):
        url = f"{scheme}://127.0.0.1:{int(port)}/"
        ctx = ssl._create_unverified_context() if scheme == "https" else None  # local self-signed certs are common
        # Probe a cheap path: any HTTP answer (even 404) proves a web server, and home pages can be slow.
        try:
            with urllib.request.urlopen(urllib.request.Request(url + "favicon.ico", headers={"User-Agent": "svc.watch"}),
                                        timeout=timeout, context=ctx):
                return url
        except urllib.error.HTTPError:
            return url  # an HTTP error status still means a web server is there
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return None


def http(url, timeout=3.0):
    if not url.lower().startswith(("http://", "https://")):
        return False, None, "http:// 또는 https:// 주소만 지원합니다"
    req = urllib.request.Request(url, headers={"User-Agent": "svc.watch-healthcheck"})
    ctx = ssl.create_default_context()
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            ms = round((time.perf_counter() - start) * 1000)
            return r.status < 400, ms, None if r.status < 400 else f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return False, round((time.perf_counter() - start) * 1000), f"HTTP {e.code}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        reason = getattr(e, "reason", e)
        return False, None, f"요청 실패: {reason}"
