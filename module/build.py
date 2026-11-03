from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import nso
import patches

MOD_ROOT = os.path.dirname(HERE)
SRC = os.path.join(HERE, "src")
BUILD = os.path.join(HERE, "build")
DEFAULT_MAIN = os.path.join(MOD_ROOT, "boot", "exefs", "main")

CFLAGS = [
    "--target=aarch64-none-elf", "-march=armv8-a", "-O2", "-std=c11",
    "-ffreestanding", "-fno-builtin", "-nostdlib", "-fPIC", "-fvisibility=hidden",
    "-mgeneral-regs-only", "-ffixed-x18", "-fno-stack-protector",
    "-fno-asynchronous-unwind-tables", "-fno-unwind-tables", "-fno-jump-tables",
    "-Wall", "-Wextra", "-Werror",
]
LDFLAGS = ["-shared", "-Bsymbolic", "--hash-style=sysv", "--no-undefined", "-z", "text",
           "--build-id=none", "--no-rosegment"]


class BuildError(RuntimeError):
    pass


def tool(name: str) -> str:
    candidates = []
    if os.environ.get("LLVM_BIN"):
        candidates.append(os.path.join(os.environ["LLVM_BIN"], name))
    candidates.append(os.path.join(r"C:\Program Files\LLVM\bin", name))
    for c in candidates:
        for ext in ("", ".exe"):
            if os.path.isfile(c + ext):
                return c + ext
    found = shutil.which(name)
    if found:
        return found
    raise BuildError(f"{name} not found (set LLVM_BIN)")


def run(cmd: list[str]) -> str:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode:
        raise BuildError(f"command failed: {' '.join(cmd)}\n{res.stdout}{res.stderr}")
    return res.stdout


def main_image(main_path: str, log) -> bytes:
    data = open(main_path, "rb").read()
    if nso.build_id(data) != patches.BUILD_ID:
        raise BuildError(f"{main_path}: build id {nso.build_id(data)} is not {patches.BUILD_ID}")
    key = hashlib.sha256(data).hexdigest()[:16]
    cache = os.path.join(BUILD, "cache", f"main-{key}.img")
    if os.path.isfile(cache):
        return open(cache, "rb").read()
    log(f"decompressing {main_path} (once; cached in {os.path.dirname(cache)})")
    img = nso.load_image(data)
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(cache + ".tmp", "wb") as fh:
        fh.write(img)
    os.replace(cache + ".tmp", cache)
    return img


def symbols(elf: str) -> dict[str, int]:
    out = {}
    for line in run([tool("llvm-nm"), "--defined-only", elf]).splitlines():
        parts = line.split()
        if len(parts) == 3:
            out[parts[2]] = int(parts[0], 16)
    return out


def check_elf(elf: bytes, syms: dict[str, int]) -> None:
    sections = nso._elf_sections(elf)
    for name in (".rela.dyn", ".rela.plt", ".rel.dyn", ".relr.dyn"):
        if sections.get(name, (0, 0))[1]:
            raise BuildError(f"module has dynamic relocations ({name}); it must be position-independent "
                             "without them (hidden visibility, no pointers in initialized data)")
    if syms.get("__module_start") != 0:
        raise BuildError("__module_start is not at offset 0 (header.S must come first)")


def build(debug: bool, script: int, main_path: str, log=print) -> dict:
    variant = "release" if not debug else f"debug-s{script}"
    out = os.path.join(BUILD, variant)
    inc = os.path.join(BUILD, "include")
    os.makedirs(out, exist_ok=True)
    os.makedirs(inc, exist_ok=True)
    with open(os.path.join(inc, "game_offsets.h"), "w", newline="\n") as fh:
        fh.write(patches.header_text())

    defines = ["-DLATAM_DEBUG", f"-DLATAM_DEBUG_SCRIPT={script}"] if debug else []
    sources = ["header.S", "hooks.S", "latam.c"] + (["debug.c"] if debug else [])
    objs = []
    for src in sources:
        obj = os.path.join(out, os.path.splitext(src)[0] + ".o")
        run([tool("clang"), *CFLAGS, *defines, "-I", inc, "-I", SRC, "-c",
             os.path.join(SRC, src), "-o", obj])
        objs.append(obj)
    elf_path = os.path.join(out, "module.elf")
    run([tool("ld.lld"), *LDFLAGS, "-T", os.path.join(HERE, "module.ld"), "-o", elf_path, *objs])
    elf = open(elf_path, "rb").read()
    syms = symbols(elf_path)
    check_elf(elf, syms)

    chosen = patches.selected(debug)
    problems = patches.check_original(chosen, main_image(main_path, log))
    if problems:
        raise BuildError("patch table does not match this main:\n  " + "\n  ".join(problems))
    resolved = patches.resolve(chosen, syms)
    ips = patches.ips32([(p.offset, data) for p, data in resolved])
    ips_path = os.path.join(out, f"{patches.BUILD_ID}.ips")
    with open(ips_path, "wb") as fh:
        fh.write(ips)

    module_id = b"ES-LATAM" + hashlib.sha256(elf).digest()[:12]
    nso_path = os.path.join(out, "subsdk9")
    with open(nso_path, "wb") as fh:
        fh.write(nso.elf_to_nso(elf, module_id))
    log(f"module ({variant}): {nso_path} ({os.path.getsize(nso_path)} bytes)")
    log(f"patch: {ips_path} ({len(resolved)} patches)")
    return {"variant": variant, "subsdk9": nso_path, "ips": ips_path, "elf": elf_path,
            "symbols": syms, "patches": [(p.name, p.offset, d.hex()) for p, d in resolved]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Build the es-419 language module (subsdk9) and the matching `main` patch.")
    ap.add_argument("--debug", action="store_true", help="debug build (logging, scripted input)")
    ap.add_argument("--debug-script", type=int, default=0,
                    help="input script of the debug build: 0 none, 1 pick Latam, 2 pick Spain, "
                         "3 pick Latam, then keep tapping A, Left, Plus")
    ap.add_argument("--main", default=DEFAULT_MAIN, help="game main NSO (for the patch check)")
    ap.add_argument("--list", action="store_true", help="print the resolved patches")
    a = ap.parse_args(argv)
    try:
        res = build(a.debug, a.debug_script, a.main)
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if a.list:
        for name, off, data in res["patches"]:
            print(f"  {name:20s} main+{off:#09x}  {data}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
