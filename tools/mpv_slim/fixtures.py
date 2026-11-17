"""Generate silent codec fixtures using the existing full-featured library, not the candidate."""

import argparse
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from dynamic_wallpaper.mpv import Mpv, find_library
from test_mpv import silent_test_video

CODECS = (
    ("h264-aac.mp4", "mp4", "libx264", "aac", "preset=ultrafast,crf=18"),
    ("hevc-aac.mkv", "matroska", "libx265", "aac", "preset=ultrafast,crf=20"),
    ("vp9-opus.webm", "webm", "libvpx-vp9", "libopus", "deadline=realtime,cpu-used=6,crf=24,b=0"),
    ("av1-opus.mkv", "matroska", "libaom-av1", "libopus", "cpu-used=8,crf=32,b=0"),
)


def encode(lib, source: Path, output: Path, container: str, video: str, audio: str, options: str) -> None:
    player = Mpv.__new__(Mpv)
    player.lib = lib
    player.handle = lib.mpv_create()
    player._directory = player._shader_directory = None
    player._probe = False
    if not player.handle:
        raise RuntimeError("Cannot create the fixture encoder")
    try:
        settings = {
            "config": "no", "load-scripts": "no", "ytdl": "no", "osc": "no",
            "terminal": "no", "o": str(output), "of": container,
            "ovc": video, "oac": audio, "ovcopts": options,
            "vf": "lavfi=[scale=320:240,format=yuv420p]",
            "af": "lavfi=[aresample=48000]", "idle": "yes",
        }
        for name, value in settings.items():
            player._check(lib.mpv_set_option_string(player.handle, name.encode(), value.encode("utf-8")), name)
        player._check(lib.mpv_initialize(player.handle), "initialize encoder")
        player.command("loadfile", str(source), "replace")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            event, error, end = player.event()
            if error < 0:
                player._check(error, "encode event")
            if event == Mpv.END_FILE and end:
                reason, error = end
                if error < 0 or reason != 0:
                    raise RuntimeError(f"Encoder failed for {output.name}: reason={reason}, error={error}")
                break
            if event == Mpv.SHUTDOWN:
                raise RuntimeError(f"Encoder stopped before EOF for {output.name}")
            time.sleep(0.01)
        else:
            raise RuntimeError(f"Encoding timed out for {output.name}")
    finally:
        player.close()
    if not output.is_file() or output.stat().st_size < 100:
        raise RuntimeError(f"Encoder did not create {output}")
    print(f"Created {output.name}: {output.stat().st_size} bytes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--library", type=Path, default=ROOT / "vendor/mpv/libmpv-2.dll")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    source = args.output / "uncompressed-source.avi"
    silent_test_video(source)
    library = Mpv(find_library(str(args.library)), probe=True)
    try:
        for filename, container, video, audio, options in CODECS:
            encode(library.lib, source, args.output / filename, container, video, audio, options)
    finally:
        library.close()
