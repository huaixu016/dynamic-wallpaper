"""Start or resume the isolated Linux cross-build; never replace vendor/mpv."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "ghcr.io/shinchiro/archlinux:latest"
CONTAINER = "dynamic-wallpaper-mpv-slim-builder"
VOLUME = "dynamic-wallpaper-mpv-slim-work"


def docker(*args, check=True, capture=False):
    executable = shutil.which("docker")
    if not executable:
        raise RuntimeError("Docker CLI is not available")
    return subprocess.run([executable, *args], check=check, capture_output=capture,
                          text=True, encoding="utf-8", errors="replace")


def proxy_environment(info: dict) -> list[str]:
    args = []
    for field, key in (("HttpProxy", "HTTP_PROXY"), ("HttpsProxy", "HTTPS_PROXY")):
        if info.get(field):
            args.extend(("--env", f"{key}={info[field]}", "--env", f"{key.lower()}={info[field]}"))
    if info.get("NoProxy"):
        args.extend(("--env", f"NO_PROXY={info['NoProxy']}", "--env", f"no_proxy={info['NoProxy']}"))
    return args


def start(jobs: int) -> None:
    result = docker("container", "inspect", CONTAINER, check=False, capture=True)
    if result.returncode == 0:
        state = json.loads(result.stdout)[0]["State"]
        if state["Running"]:
            print(f"Build already running: {CONTAINER}")
        else:
            docker("start", CONTAINER)
            print(f"Resumed build: {CONTAINER}")
        return
    docker("image", "inspect", IMAGE, capture=True)
    source = ROOT / "vendor/mpv-winbuild-cmake"
    if not (source / "CMakeLists.txt").is_file():
        raise RuntimeError("vendor/mpv-winbuild-cmake is missing")
    docker("volume", "create", VOLUME)
    info = json.loads(docker("info", "--format", "{{json .}}", capture=True).stdout)
    proxy_args = proxy_environment(info)
    if proxy_args:
        print("Using Docker's configured proxy for source downloads")
    docker("run", "--detach", "--name", CONTAINER, "--cpus", "3", "--memory", "5g",
           "--mount", f"type=volume,source={VOLUME},target=/work",
           "--mount", f"type=bind,source={source.as_posix()},target=/toolchain,readonly",
           "--mount", f"type=bind,source={(ROOT / 'tools/mpv_slim').as_posix()},target=/profile,readonly",
           "--env", f"BUILD_JOBS={jobs}", *proxy_args, IMAGE, "bash", "/profile/build.sh")
    print(f"Build started: {CONTAINER}; cached files are in Docker volume {VOLUME}")


def collect() -> None:
    result = docker("container", "inspect", CONTAINER, capture=True)
    state = json.loads(result.stdout)[0]["State"]
    if state["Running"] or state["ExitCode"] != 0:
        raise RuntimeError("Build has not completed successfully; inspect docker logs first")
    destination = ROOT / "vendor/mpv-slim-candidate"
    destination.mkdir(parents=True, exist_ok=True)
    docker("cp", f"{CONTAINER}:/work/output/.", str(destination))
    print(f"Candidate exported to {destination}; vendor/mpv was not changed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "collect"))
    parser.add_argument("--jobs", type=int, default=2, choices=range(1, 5))
    args = parser.parse_args()
    try:
        start(args.jobs) if args.action == "start" else collect()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
