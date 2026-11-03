import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
MOD_ROOT = os.path.dirname(TESTS_DIR)
TOOLS_DIR = os.path.join(MOD_ROOT, "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from ldtool.gamedata import DEFAULT_ROMFS

ROMFS = os.environ.get("LDTOOL_ROMFS", DEFAULT_ROMFS)
PTBR_ROMFS = os.environ.get(
    "LDTOOL_PTBR_ROMFS",
    r"C:\Proyectos\switch\p2\mods\pokemon-pokopia-pt-br-main\2.0.1\romfs")


def _has_pair(directory: str) -> bool:
    return (os.path.isfile(os.path.join(directory, "LI_C0_R.IDX"))
            and os.path.isfile(os.path.join(directory, "LD_C0_R.BIN")))


HAS_ROMFS = _has_pair(ROMFS)
HAS_PTBR = _has_pair(PTBR_ROMFS)

needs_romfs = unittest.skipUnless(HAS_ROMFS, f"game romfs not found ({ROMFS}); set LDTOOL_ROMFS")
needs_ptbr = unittest.skipUnless(HAS_ROMFS and HAS_PTBR,
                                 f"PT-BR mod not found ({PTBR_ROMFS}); set LDTOOL_PTBR_ROMFS")


def quiet(_msg: str) -> None:
    pass
