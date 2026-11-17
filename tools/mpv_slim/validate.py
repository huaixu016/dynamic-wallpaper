"""Validate an isolated candidate on Windows; leave the deployed library unchanged."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dynamic_wallpaper import win32 as win
from dynamic_wallpaper.__main__ import configure_dpi
from dynamic_wallpaper.models import DisplayMode
from dynamic_wallpaper.mpv import Mpv, find_library


def check_codecs(library: Path, fixtures: Path) -> list[dict]:
    configure_dpi()
    results = []
    for source in sorted(fixtures.iterdir()):
        if source.suffix not in {".mp4", ".mkv", ".webm"}:
            continue
        hwnd = win.check(win.CreateWindowEx(0x08000080, "STATIC", "candidate-codec-test", 0x80000000,
                                           0, 0, 80, 60, None, None, win.GetModuleHandle(None), None))
        player = None
        try:
            player = Mpv(library, hwnd)
            player.command("loadfile", str(source.resolve()), "replace")
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                for _ in range(128):
                    event, error, end = player.event()
                    if event == Mpv.NONE:
                        break
                    player._check(error, "codec test event")
                    if event == Mpv.END_FILE and end and end[1] < 0:
                        player._check(end[1], "decode test file")
                if player.video_size() == (320, 240) and player.flag("vo-configured"):
                    break
                time.sleep(0.02)
            else:
                raise RuntimeError(f"Video did not configure: {source.name}")
            hardware = player.string("hwdec-current")
            player.set_display_mode(DisplayMode.TILE, 80, 60)
            player.set_audio(True)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and player.number("audio-params/samplerate") != 48000:
                player.event()
                time.sleep(0.02)
            if player.number("audio-params/samplerate") != 48000:
                raise RuntimeError(f"Audio did not decode: {source.name}")
            player.set_audio(False)
            result = {"file": source.name, "video_size": [320, 240], "audio_rate": 48000,
                      "hardware_decoder": hardware}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False))
        finally:
            if player:
                player.close()
            win.DestroyWindow(hwnd)
    if len(results) != 4:
        raise RuntimeError("Expected H.264, HEVC, VP9 and AV1 fixtures")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("library", type=Path)
    parser.add_argument("--fixtures", type=Path, default=ROOT / ".integration-test/mpv-slim-validation")
    args = parser.parse_args()
    library = find_library(str(args.library.resolve()))
    command = [sys.executable, "-m", "dynamic_wallpaper", "--diagnose", "--mpv-library", str(library)]
    diagnosis = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True)
    report = json.loads(diagnosis.stdout)
    if "mpv_error" in report:
        raise RuntimeError(report["mpv_error"])
    print(f"Candidate initialization passed: {report['mpv_version']}")
    codecs = check_codecs(library, args.fixtures)
    env = os.environ.copy()
    env["DYNAMIC_WALLPAPER_MPV_DLL"] = str(library)
    env["DYNAMIC_WALLPAPER_NATIVE_TESTS"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
                   cwd=ROOT, env=env, check=True)
    result = {"library": str(library), "bytes": library.stat().st_size, "codecs": codecs,
              "native_regressions": "passed"}
    (library.parent / "windows-validation.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    manifest = library.parent / "build-manifest.json"
    if manifest.is_file():
        data = json.loads(manifest.read_text())
        data["validation"] = "Windows initialization, four codec fixtures, and native regression suite passed."
        manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Candidate passed validation: {library.stat().st_size / 1_000_000:.2f} MB")
