"""Export a candidate DLL, source revisions, and notices without touching the active runtime."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def export(build: Path, output: Path) -> None:
    dll = build / "packages/mpv-prefix/src/mpv-build/libmpv-2.dll"
    if not dll.is_file():
        raise RuntimeError(f"Built DLL not found: {dll}")
    output.mkdir(parents=True, exist_ok=True)
    target = output / dll.name
    shutil.copy2(dll, target)
    revisions = {}
    for line in (build / "slim-sources.tsv").read_text().splitlines():
        name, directory = line.split("\t", 1)
        source = Path(directory)
        if (source / ".git").exists():
            result = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"],
                                    check=True, capture_output=True, text=True)
            upstream = subprocess.run(["git", "-C", str(source), "rev-parse", "@{upstream}"],
                                      capture_output=True, text=True)
            revisions[name] = {"built_head": result.stdout.strip(),
                               "upstream_head": upstream.stdout.strip() if upstream.returncode == 0
                               else result.stdout.strip()}
        else:
            revisions[name] = "release archive; URL/hash in build recipe"
        notices = [path for path in source.iterdir() if path.is_file() and
                   path.name.lower().startswith(("copying", "license", "copyright", "notice"))]
        if notices:
            directory = output / "licenses" / name
            directory.mkdir(parents=True, exist_ok=True)
            for path in notices:
                shutil.copy2(path, directory / path.name)
    recipes = build.parent / "toolchain-src"
    recipe_output = output / "build-recipes"
    for name in revisions:
        path = recipes / "packages" / f"{name}.cmake"
        if not path.is_file():
            path = recipes / "toolchain" / (f"gcc/{name}.cmake" if name.startswith("gcc") else f"{name}.cmake")
        if path.is_file():
            recipe_output.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, recipe_output / f"{name}.cmake")
            for patch in path.parent.glob(f"{name}-*.patch"):
                shutil.copy2(patch, recipe_output / patch.name)
    manifest = {
        "profile": "wallpaper-d3d11", "target": "x86_64-w64-mingw32", "cpu_baseline": "x86-64",
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "bytes": target.stat().st_size,
        "sources": revisions,
        "validation": "Candidate only; Windows runtime tests must pass before deployment.",
        "requested_features": {
            "libmpv": True, "local_files": True, "d3d11": True, "d3d11va": True, "dxva2": True,
            "wasapi": True, "gpu_shaders": True, "builtin_decoders": True, "dav1d_av1": True,
            "video_encoders": False, "png_test_encoder": True, "network": False,
            "vulkan": False, "opengl": False, "opus_dnn": False,
            "lua": "retained for application option compatibility; scripts disabled by application",
            "libass": "mandatory in mpv 0.42; subtitles disabled by application",
        },
        "license_note": "GPL build. Review bundled notices and source-delivery obligations before redistribution.",
    }
    (output / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    for name in ("ffmpeg", "mpv", "libplacebo"):
        source = build / f"packages/{name}-prefix/src/{name}-build"
        for path in (source / "config.h", source / "meson-info/intro-buildoptions.json",
                     source / "ffbuild/config.mak"):
            if path.is_file():
                destination = output / "build-config" / name
                destination.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination / path.name)
    print(f"Candidate: {target}; {target.stat().st_size / 1_000_000:.2f} MB")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("build", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    export(args.build, args.output)
