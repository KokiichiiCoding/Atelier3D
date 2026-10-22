"""python -m atelier [--demo] [--port 7870] [--open]"""
from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn

from .config import CFG


def main():
    ap = argparse.ArgumentParser(prog="atelier", description="Atelier3D studio")
    ap.add_argument("--demo", action="store_true", help="no GPU: fake engine, procedural models")
    ap.add_argument("--host", default=CFG.host, help="interface to bind (default: loopback only)")
    ap.add_argument("--port", type=int, default=CFG.port)
    ap.add_argument("--open", action="store_true", help="open the studio in the browser")
    a = ap.parse_args()
    CFG.demo = CFG.demo or a.demo
    CFG.host, CFG.port = a.host, a.port

    from .api import create_app  # after CFG is final

    url = f"http://127.0.0.1:{CFG.port}/"
    print(f"\n  Atelier3D{' (demo mode)' if CFG.demo else ''}  ->  {url}\n")
    if a.open:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(), host=CFG.host, port=CFG.port, log_level="warning")


if __name__ == "__main__":
    main()
