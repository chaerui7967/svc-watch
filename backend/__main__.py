import webbrowser

import uvicorn

from .config import HOST, PORT
from .system import is_admin


def main():
    url = f"http://{HOST}:{PORT}"
    print(f"svc.watch → {url}  (관리자 권한: {'예' if is_admin() else '아니오'})")
    webbrowser.open(url)
    uvicorn.run("backend.app:app", host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
