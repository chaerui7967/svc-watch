import os
import webbrowser

import uvicorn

from .config import HOST, PORT
from .system import is_admin


def main():
    url = f"http://{HOST}:{PORT}"
    print(f"svc.watch -> {url}  (관리자 권한: {'예' if is_admin() else '아니오'})", flush=True)
    # The scheduled task (service/win/run_server.bat) sets this so logons and auto-restarts don't pop a browser tab.
    if not os.environ.get("SVCWATCH_NO_BROWSER"):
        webbrowser.open(url)
    uvicorn.run("backend.app:app", host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
