"""Apply dependency patches once, even when ExternalProject re-runs the patch step."""

import argparse
from pathlib import Path
import subprocess


def apply(package: str, source: Path, recipes: Path) -> None:
    if (source / ".git/rebase-apply").exists():
        subprocess.run(["git", "-c", "user.name=Local mpv build",
                        "-c", "user.email=local-mpv-build@localhost",
                        "-C", str(source), "am", "--abort"], check=True)
    for patch in sorted(recipes.glob(f"{package}-*.patch")):
        command = ["git", "-C", str(source), "apply"]
        reverse = subprocess.run([*command, "--reverse", "--check", str(patch)], capture_output=True)
        if reverse.returncode == 0:
            print(f"Already applied: {patch.name}")
            continue
        subprocess.run([*command, "--check", str(patch)], check=True)
        subprocess.run([*command, str(patch)], check=True)
        print(f"Applied: {patch.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("package")
    parser.add_argument("source", type=Path)
    parser.add_argument("recipes", type=Path)
    args = parser.parse_args()
    apply(args.package, args.source, args.recipes)
