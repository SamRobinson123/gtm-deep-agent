"""python -m gtm_ui — serve the chat UI on localhost."""
from __future__ import annotations

import argparse
import socket
import sys
import threading
import webbrowser


def port_free(host, port):
    with socket.socket() as s:
        return s.connect_ex((host, port)) != 0


def main():
    ap = argparse.ArgumentParser(description="GTM chat UI")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    # 127.0.0.1 only, never 0.0.0.0: no auth, and this process can reach production.
    host = "127.0.0.1"
    if not port_free(host, args.port):
        print(f"Port {args.port} is already in use. Try --port {args.port + 1}.", file=sys.stderr)
        sys.exit(1)

    url = f"http://{host}:{args.port}"
    print(f"GTM Pipe Analytics UI -> {url}")
    if not args.no_open:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    import uvicorn
    # "info" keeps the access log on. At "warning" every request is invisible,
    # so a UI that does nothing gives no way to tell whether the click even
    # reached the server.
    uvicorn.run("gtm_ui.server:app", host=host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
