"""python -m bot.setup: opens the setup wizard in your browser. Exits with 0 only when you finished and saved."""
from __future__ import annotations

import os
import sys
import threading
import webbrowser

import uvicorn

from bot.setup import server


def main() -> int:
    host = os.getenv("SETUP_HOST", "127.0.0.1")
    port = int(os.getenv("SETUP_PORT", "8765"))
    cfg = uvicorn.Config(server.app, host=host, port=port, log_level="warning", loop="asyncio")
    srv = uvicorn.Server(cfg)

    def stop() -> None:
        threading.Timer(1.0, lambda: setattr(srv, "should_exit", True)).start()   # let the last reply reach the browser
    server.state["finish"] = stop
    url = f"http://localhost:{port}"
    print(f"\nInternify setup is open at {url}\nIf your browser did not open, open that address yourself. Press Ctrl+C to cancel.\n", flush=True)
    if not os.getenv("SETUP_NO_BROWSER") and not os.path.exists("/.dockerenv"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.run()
    except KeyboardInterrupt:
        pass
    return 0 if server.state["done"] else 1


if __name__ == "__main__":
    sys.exit(main())
