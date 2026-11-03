from __future__ import annotations

import os

MOD_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_ROMFS = os.environ.get(
    "ASSETTOOL_ROMFS", os.environ.get("LDTOOL_ROMFS", os.path.join(MOD_ROOT, "boot", "romfs")))
ASSET_DIR = os.path.join("motor_asset_re", "KIDS_PLATFORM_OUNCE")
MOVIE_DIR = os.path.join("movie_asset", "ounce")

TYPE_TEXTURE = 0xAFBEC60C
TYPE_SPRITE_SET = 0xF20DE437
TYPE_OBJDB = 0x20A6A0BB
TYPE_LAYOUT = 0x5599AA51

OBJ_TEXTURE_BINDING = 0x79991E79

LANGUAGES = ("ja", "en", "fr", "it", "de", "es", "zh-Hant", "ko", "zh-Hans")
SOURCE_LANGUAGE = "es"
COUNTERPART_LANGUAGE = "en"

LANG_KTID_OFFSETS = {
    "ja": 0x190ABFB6,
    "en": 0x00000000,
    "fr": 0xC742C669,
    "it": 0xEF3728C8,
    "de": 0x455E358A,
    "es": 0x07AA0DFA,
    "zh-Hant": 0x619F3AB3,
    "ko": 0x45651C90,
    "zh-Hans": 0x05DDA5B0,
}
SCALE_EXPONENTS = tuple(range(-4, 17))
MIN_FINGERPRINT_VOTES = 4

LABEL_SPRITES = {
    "ja": "0030_prt_language_7",
    "en": "0030_prt_language_1",
    "fr": "0030_prt_language_4",
    "it": "0030_prt_language_6",
    "de": "0030_prt_language_5",
    "es": "0030_prt_language_2",
    "zh-Hant": "0030_prt_language_10",
    "ko": "0030_prt_language_8",
    "zh-Hans": "0030_prt_language_9",
}

LATAM_LABEL_ASSET = "language_select__0030_prt_language_2"
LATAM_LABEL_SPRITE = "0030_prt_language_3"
LABEL_ATLAS = 0xBC2C37F5
LABEL_ATLAS_SUB = 32
LABEL_SPRITE_SET = 0x987344AA
LATAM_LABEL_RECT = (0, 1776, 360, 1840)
LABEL_TABLE_RECORD = 1552
LABEL_TABLE_HEADER = 64
LABEL_TABLE_ROW = 8
LABEL_ROW_SPAIN = 0x101
LABEL_ROW_LATAM = 0x102
LABEL_UI_ASSET = 10

HOST_FDATA = 0x4B13D561


def kt_hash(name: str) -> int:
    h = 0
    for c in reversed(name.encode("utf-8")):
        h = ((h + c) * 31) & 0xFFFFFFFF
    return h


CATEGORY_RULES = (
    ("0030_prt_language_", "language_select", "Language-selection button label"),
    ("still_tutorial_", "tutorial", "Tutorial screenshot shown in the help pages"),
    ("8000_title_", "title", "Title-screen graphic"),
    ("8000_dlc_logo", "title", "Title logo / expansion graphic"),
    ("8000_minigame_", "minigame", "Mini-game banner"),
    ("8000_num_", "hud", "Counter label"),
    ("8000_txt_", "banner", "Event banner"),
    ("0800_txt_", "banner", "Event banner"),
)
DEFAULT_CATEGORY = ("misc", "Localized UI graphic")

GAME_INFO = {"name": "Pokemon Pokopia", "version": "2.0.1", "update": 655360,
             "platform": "Switch 2 (G1T platform 0x18)"}

MOVIE_NOTE = ("One copy for all languages: a single VP9 1920x1080 video track (language 'und'), "
              "no audio or subtitle tracks; no text seen in frames sampled every 8 s. Not localized.")

_T = "Tutorial screenshot: "
DESCRIPTIONS: dict[str, str] = {
    "banner__0800_txt_new_craft": "Banner shown when a new creation (recipe) is unlocked.",
    "banner__8000_txt_atlas": "Atlas of request/mission banners (completed, important request x2, "
                              "get to work).",
    "banner__8000_txt_gate_open": "Banner: access opened.",
    "hud__8000_num_dev_max_atlas": "'MAX' counter labels (big number with MAX tag, small MAX tag).",
    "language_select__0030_prt_language_2": "Language-selection label 'Español (España)' (360x64 crop "
                                            "of a shared atlas). The es-419 copy is the label of the "
                                            "10th (es-419) button: the build adds it as the new sprite "
                                            "0030_prt_language_3. The Spain button keeps the original.",
    "minigame__8000_minigame_atlas": "Mini-game banners: well done, too bad, it's over, result, "
                                     "let's play, time's up.",
    "title__8000_dlc_logo": "Game logo with the expansion subtitle (MountPoint_1 language set).",
    "title__8000_dlc_logo_atlas.t0": "Expansion artwork cards with a small subtitle (sub-texture 0 of 2).",
    "title__8000_dlc_logo_atlas.t1": "Game logo with the expansion subtitle (sub-texture 1 of 2; "
                                     "the English G1T has no second sub-texture).",
    "title__8000_title_atlas": "Title screen: game logo and 'press A' prompt.",
    "tutorial__still_tutorial_004": _T + "item pop-up with an item name tag over a field.",
    "tutorial__still_tutorial_005": _T + "item pop-up with an item name tag (variant).",
    "tutorial__still_tutorial_006": _T + "item bar with materials.",
    "tutorial__still_tutorial_009": _T + "material pop-up with a furniture name tag.",
    "tutorial__still_tutorial_024": _T + "main menu with the 'take me home' option.",
    "tutorial__still_tutorial_029": _T + "move selector (Water Gun).",
    "tutorial__still_tutorial_030": _T + "move selector (Leafage).",
    "tutorial__still_tutorial_054": _T + "Pokemon speech bubble.",
    "tutorial__still_tutorial_057": _T + "storage box / item transfer screen.",
    "tutorial__still_tutorial_063": _T + "conversation with a choice menu.",
    "tutorial__still_tutorial_070": _T + "camera viewfinder.",
    "tutorial__still_tutorial_071": _T + "speech bubble in a town square.",
    "tutorial__still_tutorial_081": _T + "challenges menu, online play card.",
    "tutorial__still_tutorial_088": _T + "Pokemon details: specialty, ideal habitat, likes.",
    "tutorial__still_tutorial_112": _T + "habitat comfort meter.",
    "tutorial__still_tutorial_126": _T + "Pokemon asking for an item.",
    "tutorial__still_tutorial_127": _T + "conversation menu asking about comfort.",
    "tutorial__still_tutorial_132": _T + "Pokemon details: specialty, ideal habitat, likes (variant).",
    "tutorial__still_tutorial_133": _T + "Pokemon with a small name tag (root data).",
    "tutorial__still_tutorial_133.mp1": _T + "Pokemon with a small name tag (MountPoint_1 copy; "
                                        "differs from the root one).",
    "tutorial__still_tutorial_137": _T + "Pokemon details (variant).",
    "tutorial__still_tutorial_146": _T + "outfit set menu.",
    "tutorial__still_tutorial_147": _T + "colour customisation menu.",
    "tutorial__still_tutorial_150": _T + "shop item list with a notice line.",
    "tutorial__still_tutorial_176": _T + "conversation choice menu (go somewhere together).",
    "tutorial__still_tutorial_186": _T + "cooking pop-up.",
    "tutorial__still_tutorial_187": _T + "cooking result with dish description.",
    "tutorial__still_tutorial_206": _T + "decoration request sheet (paints, order button).",
    "tutorial__still_tutorial_207": _T + "main menu with the Habitat Dex selected.",
    "tutorial__still_tutorial_209": _T + "daily challenges board.",
    "tutorial__still_tutorial_211": _T + "daily shop item list.",
    "tutorial__still_tutorial_213": _T + "daily shop item list (one row).",
    "tutorial__still_tutorial_214": _T + "environment quality card with a details button.",
    "tutorial__still_tutorial_215": _T + "stamp card and stamp collection.",
    "tutorial__still_tutorial_235": _T + "online play menu (invite, visit, online/local).",
    "tutorial__still_tutorial_236": _T + "online session menu (end session, password, settings).",
    "tutorial__still_tutorial_238": _T + "speech bubble during a search.",
    "tutorial__still_tutorial_255": _T + "code-lock keypad.",
    "tutorial__still_tutorial_256": _T + "code-lock keypad with a wrong-code message.",
}


def describe(name: str, default: str) -> str:
    return DESCRIPTIONS.get(name, default)
