from __future__ import annotations

import argparse
import glob
import os
import shutil
import sys

TITLE_ID = "0400A3A01EE4C000"
SAVE_ROOT = os.path.join(os.environ.get("APPDATA", ""), "eden", "nand", "user", "save",
                         "0000000000000000")
KEEP = {".yuzu_save_size"}


def save_items(folder: str) -> list[str]:
    return sorted(n for n in os.listdir(folder) if n not in KEEP)


def save_folders(profile: str | None) -> list[str]:
    pattern = os.path.join(SAVE_ROOT, profile or "*", TITLE_ID)
    folders = sorted(p for p in glob.glob(pattern) if os.path.isdir(p))
    return folders if profile else [f for f in folders if save_items(f)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Delete the Pokopia save data in Eden, so the next boot "
                                             "shows the first-boot language screen again.")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    ap.add_argument("--profile", help="Eden profile (user) id, if there is more than one")
    a = ap.parse_args(argv)

    folders = save_folders(a.profile)
    if not folders:
        print(f"no save data for {TITLE_ID} under {SAVE_ROOT}; nothing to do")
        return 0
    if len(folders) > 1:
        print("more than one profile has a save; choose one with --profile:")
        for f in folders:
            print(f"  {os.path.basename(os.path.dirname(f))}")
        return 1
    folder = folders[0]
    items = save_items(folder)
    if not items:
        print(f"{folder} has no save data")
        return 0
    print(f"save folder: {folder}")
    for name in items:
        print(f"  {name}")
    if not a.yes:
        answer = input(f"Delete these {len(items)} item(s), including the game progress? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("cancelled")
            return 1
    for name in items:
        path = os.path.join(folder, name)
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
    print("save deleted; the next boot shows the language screen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
