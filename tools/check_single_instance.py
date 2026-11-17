"""Drive two real source-app launches in an isolated singleton namespace and temporary profile."""

import ctypes as C
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory, mkdtemp
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dynamic_wallpaper import win32 as win
from dynamic_wallpaper.__main__ import configure_dpi


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise RuntimeError("Source app did not reach the requested window state")


def main():
    configure_dpi()
    name = "LocalDynamicWallpaper.Smoke." + uuid.uuid4().hex
    code = ("from dynamic_wallpaper.win32 import SingleInstance as S; "
            f"S.MESSAGE_NAME={name!r}; S.MUTEX_NAME={'Local' + chr(92) + name!r}; "
            f"S.WINDOW_PROPERTY={name!r}; S.WINDOW_TITLE={name!r}; "
            "from dynamic_wallpaper.__main__ import main; raise SystemExit(main())")
    command = [sys.executable, "-c", code]
    process = None
    hwnd = None
    with TemporaryDirectory(prefix="wallpaper-instance-profile-") as profile:
        env = os.environ.copy()
        env["LOCALAPPDATA"] = profile
        env["QT_QPA_PLATFORM"] = "windows"
        env["PYTHONIOENCODING"] = "utf-8"
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", errors="replace", creationflags=0x08000000)
        try:
            def find_window():
                return next((h for h in win.window_list()
                             if win.GetProp(h, name) == process.pid), None)
            hwnd = wait_for(find_window)
            wait_for(lambda: win.IsWindowVisible(hwnd))
            owner = process.pid

            def launch_again(label):
                result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                                        text=True, encoding="utf-8", errors="replace", timeout=15)
                if result.returncode:
                    raise RuntimeError(result.stderr or result.stdout or "Duplicate launch failed")
                wait_for(lambda: win.IsWindowVisible(hwnd) and not win.IsIconic(hwnd))
                if win.window_pid(hwnd) != owner or process.poll() is not None:
                    raise RuntimeError("Duplicate launch did not reuse the first process")
                print(json.dumps({"case": label, "owner_pid": owner, "visible": True,
                                  "foreground": win.GetForegroundWindow() == hwnd,
                                  "maximized": bool(win.IsZoomed(hwnd))}))

            win.check(win.PostMessage(hwnd, 0x0010, 0, 0))  # User close button hides to tray.
            wait_for(lambda: not win.IsWindowVisible(hwnd))
            launch_again("hidden")
            if win.GetForegroundWindow() == hwnd:
                from PIL import ImageGrab
                from ctypes import wintypes as W
                bounds = W.RECT()
                win.check(win.GetWindowRect(hwnd, C.byref(bounds)))
                screenshot = Path(mkdtemp(prefix="wallpaper-instance-preview-")) / "source-window.png"
                ImageGrab.grab((bounds.left, bounds.top, bounds.right, bounds.bottom),
                               all_screens=True).save(screenshot)
                print(f"Source window screenshot: {screenshot}")
            win.check(win.PostMessage(hwnd, 0x0112, 0xF020, 0))  # SC_MINIMIZE
            wait_for(lambda: win.IsIconic(hwnd))
            launch_again("minimized")
            win.check(win.PostMessage(hwnd, 0x0112, 0xF030, 0))  # SC_MAXIMIZE
            wait_for(lambda: win.IsZoomed(hwnd))
            win.check(win.PostMessage(hwnd, 0x0010, 0, 0))
            wait_for(lambda: not win.IsWindowVisible(hwnd))
            launch_again("maximized-hidden")
            if not win.IsZoomed(hwnd):
                raise RuntimeError("Waking a hidden window lost its maximized state")
            win.check(win.PostMessage(hwnd, 0x0112, 0xF020, 0))
            wait_for(lambda: win.IsIconic(hwnd))
            launch_again("maximized-minimized")
            if not win.IsZoomed(hwnd):
                raise RuntimeError("Restoring a minimized window lost its maximized state")
            print("Real source-app duplicate launch scenarios passed; active user instance was not touched.")
        finally:
            if hwnd and process.poll() is None:
                win.PostMessage(hwnd, 0x0011, 0, 0)  # Graceful shutdown of this isolated test instance only.
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5)
            elif process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            if process.returncode:
                print(process.stderr.read(), file=sys.stderr)
            process.stdout.close()
            process.stderr.close()


if __name__ == "__main__":
    main()
