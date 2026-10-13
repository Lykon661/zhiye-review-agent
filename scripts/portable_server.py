"""Browser UI and API in one local server; invoked only by the portable launcher."""
import ctypes
import os
from pathlib import Path
import socket
import sys
from threading import Thread
import time
import traceback
from urllib.request import urlopen
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8000/"


def open_when_ready():
    for _ in range(120):
        try:
            with urlopen(URL + "health", timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(URL)
                    return
        except OSError:
            time.sleep(0.5)
    print("Startup is taking longer than expected. Read the log shown above.", flush=True)


def main():
    # One instance per Windows login protects the shared user database across upgrades.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    mutex = kernel.CreateMutexW(None, False, "Local\\ZhiyeReviewAgentPortable")
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        kernel.CloseHandle(mutex)
        webbrowser.open(URL)
        return
    try:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 8000))
        sys.path.insert(0, str(ROOT / "backend"))
        from fastapi.staticfiles import StaticFiles
        import uvicorn
        from uvicorn.config import LOGGING_CONFIG
        from app.main import app

        app.mount("/", StaticFiles(directory=ROOT / "frontend" / "dist", html=True), name="web")
        log = Path(os.environ["ZHIYE_LOG_DIR"]) / "server.log"
        LOGGING_CONFIG["handlers"]["file"] = {
            "class": "logging.FileHandler", "filename": str(log),
            "encoding": "utf-8", "formatter": "default",
        }
        for logger in LOGGING_CONFIG["loggers"].values():
            logger.setdefault("handlers", []).append("file")
        LOGGING_CONFIG["root"] = {"level": "INFO", "handlers": ["default", "file"]}
        print(f"Zhiye: {URL}\nData: {os.environ['DATA_DIR']}\nLog: {log}", flush=True)
        print("Keep this window open. Press Ctrl+C to stop before upgrading.", flush=True)
        Thread(target=open_when_ready, daemon=True).start()
        uvicorn.run(app, host="127.0.0.1", port=8000, log_config=LOGGING_CONFIG)
    finally:
        kernel.CloseHandle(mutex)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        detail = traceback.format_exc()
        print(detail, file=sys.stderr)
        log_dir = Path(os.environ["ZHIYE_LOG_DIR"])
        with (log_dir / "startup-error.log").open("a", encoding="utf-8") as stream:
            stream.write(detail)
        sys.exit(1)
