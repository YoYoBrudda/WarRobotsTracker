"""
War Robots Screenshot Tracker - Version 19 Debug Crops
No-code desktop app for turning War Robots team result screenshots into a CSV stat log.

Version 19 changes:
- Stores settings in the user AppData folder instead of next to the EXE.
- Defaults tracker data folders to the same folder as the downloaded EXE.
- Lets users choose a tracker data folder so the app can still be customized.
- Saves the selected parser crop for each screenshot into a Crops folder for manual debugging.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
from PIL import Image, ImageOps, ImageEnhance
import pytesseract

try:
    import cv2
    import numpy as np
except Exception:  # app still opens without OpenCV, but OCR may be weaker
    cv2 = None
    np = None

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

APP_NAME = "War Robots Screenshot Tracker"


def get_app_folder() -> Path:
    """Return the folder that contains the running app.

    When built as a PyInstaller EXE, this is the folder containing
    WarRobotsTracker.exe. When running from source, this is the source folder.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = get_app_folder()


def get_app_data_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "WarRobotsTracker"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "WarRobotsTracker"
    return Path.home() / ".war_robots_tracker"


APP_DATA_DIR = get_app_data_dir()
DEFAULT_TRACKER_DIR = BASE_DIR
CONFIG_PATH = APP_DATA_DIR / "config.json"
SUPPORTED_IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}
DEBUG_CROPS_FOLDER: Optional[Path] = None
COLUMNS = [
    "Date Processed",
    "Screenshot Date",
    "Result",
    "Player Name",
    "Team Place",
    "Honor Points",
    "Damage + Healing",
    "Assists",
    "Kills",
    "Beacons",
    "Screenshot File",
]


@dataclass
class ParseResult:
    result: str = "UNKNOWN"
    player_name: str = ""
    team_place: str = ""
    honor_points: str = ""
    damage_healing: str = ""
    assists: str = ""
    kills: str = ""
    beacons: str = "N/A"
    raw_preview: str = ""
    needs_review: bool = False
    review_reason: str = ""


def default_folder_config(root: Optional[Path] = None) -> Dict[str, str]:
    root = root or DEFAULT_TRACKER_DIR
    return {
        "tracker_data_folder": str(root),
        "screenshot_folder": str(root / "screenshots"),
        "processed_folder": str(root / "processed"),
        "duplicates_folder": str(root / "duplicates"),
        "needs_review_folder": str(root / "needs_review"),
        "crops_folder": str(root / "Crops"),
        "output_folder": str(root / "output"),
    }


def normalize_config_paths(config: Dict[str, object]) -> Dict[str, object]:
    folders = default_folder_config()
    # If the user already has a tracker root, keep using it. Otherwise use the app folder.
    tracker_root_raw = str(config.get("tracker_data_folder") or folders["tracker_data_folder"]).strip()
    tracker_root = Path(tracker_root_raw).expanduser()

    # V15.1 portable-app migration: if an old V15 config still points to the
    # previous Documents default and the user has not marked a custom folder,
    # move the default to the EXE/download folder.
    old_documents_default = (Path.home() / "Documents" / "War Robots Tracker")
    if (
        str(config.get("storage_mode", "")) != "portable_app_folder"
        and tracker_root == old_documents_default
        and not bool(config.get("user_chose_tracker_folder", False))
    ):
        tracker_root = DEFAULT_TRACKER_DIR

    if not tracker_root.is_absolute():
        tracker_root = DEFAULT_TRACKER_DIR
    config["tracker_data_folder"] = str(tracker_root)
    config["storage_mode"] = "portable_app_folder"

    # Convert old project-relative values like "screenshots" into real user folders.
    subfolder_names = {
        "screenshot_folder": "screenshots",
        "processed_folder": "processed",
        "duplicates_folder": "duplicates",
        "needs_review_folder": "needs_review",
        "crops_folder": "Crops",
        "output_folder": "output",
    }
    for key, sub in subfolder_names.items():
        value = str(config.get(key, "")).strip()
        if not value:
            config[key] = str(tracker_root / sub)
            continue
        path = Path(value).expanduser()
        if not path.is_absolute():
            config[key] = str(tracker_root / value)
        else:
            config[key] = str(path)
    return config


def load_config() -> Dict[str, object]:
    default = {
        "player_name": "",
        **default_folder_config(),
        "match_log_csv": "match_log.csv",
        "auto_watch_seconds": 30,
        "move_processed_files": True,
        "tesseract_path": "",
        "manual_team_place": "",
    }
    if CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as f:
                default.update(json.load(f))
        except Exception:
            pass
    return normalize_config_paths(default)


def save_config(config: Dict[str, object]) -> None:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    config = normalize_config_paths(config)
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = DEFAULT_TRACKER_DIR / path
    return path


def ensure_folders(config: Dict[str, object]) -> None:
    config = normalize_config_paths(config)
    for key in ["screenshot_folder", "processed_folder", "duplicates_folder", "needs_review_folder", "crops_folder", "output_folder"]:
        resolve_path(str(config[key])).mkdir(parents=True, exist_ok=True)


def configure_tesseract(config: Dict[str, object]) -> Optional[str]:
    custom = str(config.get("tesseract_path", "")).strip().strip('"')

    # Users often choose/paste the folder. If so, automatically append tesseract.exe.
    if custom:
        custom_path = Path(custom)
        if custom_path.is_dir():
            candidate = custom_path / "tesseract.exe"
            if candidate.exists():
                pytesseract.pytesseract.tesseract_cmd = str(candidate)
                return str(candidate)
        if custom_path.exists():
            pytesseract.pytesseract.tesseract_cmd = str(custom_path)
            return str(custom_path)

    common_paths = [
        r"C:\Users\yoosh\AppData\Local\Programs\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        "/opt/homebrew/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/usr/bin/tesseract",
    ]
    for path in common_paths:
        if Path(path).exists():
            pytesseract.pytesseract.tesseract_cmd = path
            return path
    return None


def tesseract_works() -> bool:
    try:
        _ = pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def preprocess_for_ocr(image_path: Path) -> Image.Image:
    image = Image.open(image_path).convert("RGB")
    image = ImageOps.grayscale(image)
    image = ImageEnhance.Contrast(image).enhance(2.0)

    if cv2 is not None and np is not None:
        arr = np.array(image)
        arr = cv2.resize(arr, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        arr = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        return Image.fromarray(arr)

    w, h = image.size
    return image.resize((w * 2, h * 2))


def normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def clean_number(value: str) -> str:
    if not value:
        return ""
    return re.sub(r"[^0-9]", "", value)


def ocr_image_text(image_path: Path) -> str:
    image = preprocess_for_ocr(image_path)
    return pytesseract.image_to_string(image, config="--psm 6")


def ocr_words(image_path: Path) -> Tuple[List[Dict[str, object]], int, int]:
    """Return OCR word boxes from an upscaled image. Coordinates match the processed image."""
    image = preprocess_for_ocr(image_path)
    w, h = image.size
    data = pytesseract.image_to_data(image, config="--psm 6", output_type=pytesseract.Output.DICT)
    words: List[Dict[str, object]] = []
    for i, text in enumerate(data.get("text", [])):
        text = str(text).strip()
        if not text:
            continue
        try:
            conf = float(data.get("conf", [0])[i])
        except Exception:
            conf = 0
        if conf < 0:
            continue
        left = int(data["left"][i])
        top = int(data["top"][i])
        width = int(data["width"][i])
        height = int(data["height"][i])
        words.append({
            "text": text,
            "norm": normalize_token(text),
            "left": left,
            "top": top,
            "width": width,
            "height": height,
            "cx": left + width / 2,
            "cy": top + height / 2,
            "conf": conf,
        })
    return words, w, h


def guess_result_from_image(image_path: Path) -> str:
    """Read the top result banner with OCR, then use banner color as fallback.

    V13 improves this for video/YouTube screenshots where the top area may include
    black bars or overlay text. It crops the actual center banner area and then
    checks for a strong green/red color signal.
    """
    try:
        image = Image.open(image_path).convert("RGB")
        w, h = image.size

        # OCR the upper-center banner. Include a little extra height for screenshots
        # with black bars/YouTube overlays.
        crop = image.crop((int(w * 0.25), 0, int(w * 0.75), int(h * 0.20)))
        gray = ImageOps.grayscale(crop)
        gray = ImageEnhance.Contrast(gray).enhance(3.5)
        gray = ImageEnhance.Sharpness(gray).enhance(2.5)
        gray = gray.resize((gray.width * 4, gray.height * 4))
        texts = []
        for psm in [7, 8, 11, 12, 6]:
            try:
                texts.append(pytesseract.image_to_string(gray, config=f"--psm {psm}").strip())
            except Exception:
                pass
        upper = " ".join(texts).upper()
        if re.search(r"\b(WIN|VICTORY)\b", upper):
            return "WIN"
        if re.search(r"\b(LOSE|LOSS|DEFEAT)\b", upper):
            return "LOSE"
        if re.search(r"\b(DRAW|TIE)\b", upper):
            return "DRAW"

        # Color fallback. The result banner is usually a bright green or red band
        # near the top-center. Ignore very dark pixels so black bars do not dilute it.
        color_crop = image.crop((int(w * 0.18), int(h * 0.00), int(w * 0.82), int(h * 0.18)))
        pixels = list(color_crop.getdata())
        bright = [px for px in pixels if (px[0] + px[1] + px[2]) / 3 > 45]
        if bright:
            green_votes = sum(1 for r, g, b in bright if g > r * 1.18 and g > b * 1.05 and g > 70)
            red_votes = sum(1 for r, g, b in bright if r > g * 1.18 and r > b * 1.05 and r > 70)
            total = len(bright)
            if green_votes / total > 0.08 and green_votes > red_votes * 1.5:
                return "WIN"
            if red_votes / total > 0.08 and red_votes > green_votes * 1.5:
                return "LOSE"

        # Final sample around the exact WIN/LOSE word position for normal 16:9 images.
        tight = image.crop((int(w * 0.42), int(h * 0.02), int(w * 0.58), int(h * 0.12)))
        pxs = list(tight.getdata())
        if pxs:
            r = sum(p[0] for p in pxs) / len(pxs)
            g = sum(p[1] for p in pxs) / len(pxs)
            b = sum(p[2] for p in pxs) / len(pxs)
            if g > r * 1.15 and g > b * 1.05:
                return "WIN"
            if r > g * 1.15 and r > b * 1.05:
                return "LOSE"
    except Exception:
        pass
    return "UNKNOWN"


def guess_result_from_text(text: str) -> str:
    upper = text.upper()
    if re.search(r"\b(WIN|VICTORY)\b", upper):
        return "WIN"
    if re.search(r"\b(LOSE|LOSS|DEFEAT)\b", upper):
        return "LOSE"
    if re.search(r"\b(DRAW|TIE)\b", upper):
        return "DRAW"
    return "UNKNOWN"


def fuzzy_score(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, normalize_token(a), normalize_token(b)).ratio()


def is_plain_trackable_name(player_name: str) -> bool:
    """Return True for names where OCR matching should be reliable enough to require it.

    For names mostly made of English letters/numbers, a missing name usually means the
    screenshot belongs to someone else. For symbol-heavy/non-English names, OCR may not
    match reliably, so the app falls back to YOUR PLACE ON THE TEAM.
    """
    norm = normalize_token(player_name)
    if len(norm) < 3:
        return False
    plain_chars = sum(1 for ch in player_name if ch.isascii() and (ch.isalnum() or ch in " _-[]()."))
    visible_chars = sum(1 for ch in player_name if not ch.isspace()) or 1
    return (plain_chars / visible_chars) >= 0.70


def row_y_to_place(row_y: float, img_h: int, rows_top: float, row_h: float) -> Optional[int]:
    ratio = float(row_y) / float(img_h)
    place = int(round((ratio - rows_top - row_h / 2) / row_h)) + 1
    if 1 <= place <= 6:
        return place
    return None


def find_player_row(words: List[Dict[str, object]], player_name: str, img_w: int) -> Optional[float]:
    """Find the y-coordinate of the user's row using fuzzy OCR matching."""
    player_norm = normalize_token(player_name)
    if not player_norm:
        return None

    # Only search the allies/player half. Enemy table is on the right.
    left_side = [w for w in words if float(w["cx"]) < img_w * 0.52]

    # First try single-token exact-ish matches.
    best_word = None
    best_score = 0.0
    for word in left_side:
        score = fuzzy_score(str(word["text"]), player_name)
        # Extra boost if one contains the other after normalization.
        norm = str(word["norm"])
        if norm and (norm in player_norm or player_norm in norm):
            score = max(score, 0.92)
        if score > best_score:
            best_score = score
            best_word = word

    if best_word is not None and best_score >= 0.58:
        return float(best_word["cy"])

    # Then try joining nearby words on each row in case OCR splits the username.
    rows: Dict[int, List[Dict[str, object]]] = {}
    for word in left_side:
        key = int(float(word["cy"]) // 28)
        rows.setdefault(key, []).append(word)

    best_y = None
    best_row_score = 0.0
    for row_words in rows.values():
        row_words = sorted(row_words, key=lambda x: float(x["left"]))
        row_text = " ".join(str(w["text"]) for w in row_words)
        score = fuzzy_score(row_text, player_name)
        if score > best_row_score:
            best_row_score = score
            best_y = sum(float(w["cy"]) for w in row_words) / len(row_words)
    if best_y is not None and best_row_score >= 0.42:
        return float(best_y)
    return None


def ocr_text_loose(crop: Image.Image, numeric: bool = False) -> str:
    """OCR helper used for low-quality row/name crops."""
    try:
        img = crop.convert("RGB")
        gray = ImageOps.grayscale(img)
        gray = ImageEnhance.Contrast(gray).enhance(2.6)
        gray = ImageEnhance.Sharpness(gray).enhance(1.8)
        scale = 4 if max(gray.size) < 900 else 3
        gray = gray.resize((max(1, gray.width * scale), max(1, gray.height * scale)))
        cfg = "--psm 7"
        if numeric:
            cfg += " -c tessedit_char_whitelist=0123456789M.m, "
        return pytesseract.image_to_string(gray, config=cfg).strip()
    except Exception:
        return ""


def find_player_row_by_name_cells(image: Image.Image, player_name: str, rows_top: float, row_h: float) -> Tuple[Optional[int], Optional[float], str, float]:
    """Find the player's row by OCRing the name cell of each ally row.

    This is the V18 fallback for dim/letterboxed screenshots. Full-screen OCR can miss
    the username because the game table is small, but row-by-row name crops usually keep
    enough pixels to match names such as YoYoBrudda_WR.
    Returns: (place, y_center_ratio, best_text, best_score).
    """
    norm_target = normalize_token(player_name)
    if not norm_target:
        return None, None, "", 0.0
    w, h = image.size
    try:
        x1r, x2r = estimate_ally_table_x_bounds(image, rows_top)
    except Exception:
        x1r, x2r = 0.0, 0.52
    table_w = x2r - x1r
    # Name cell is the left part of the ally table. Include the clan tag and some
    # breathing room, but stop before the info icon/stat columns.
    name_x1 = x1r + table_w * 0.005
    name_x2 = x1r + table_w * 0.405
    best = (None, None, "", 0.0)
    for place in range(1, 7):
        y1 = rows_top + (place - 1) * row_h + row_h * 0.02
        y2 = rows_top + place * row_h - row_h * 0.02
        box = cell_bbox(w, h, name_x1, y1, name_x2, y2)
        txts = []
        crop = image.crop(box)
        txts.append(ocr_text_loose(crop, numeric=False))
        # Try the upper and middle bands; some War Robots titles under the name confuse OCR.
        if crop.height > 20:
            txts.append(ocr_text_loose(crop.crop((0, 0, crop.width, int(crop.height * 0.62))), numeric=False))
        combined = " ".join(t for t in txts if t).strip()
        score = fuzzy_score(combined, player_name)
        n = normalize_token(combined)
        if norm_target and (norm_target in n or n in norm_target):
            score = max(score, 0.94)
        if score > best[3]:
            best = (place, (y1 + y2) / 2.0, combined, score)
    # Plain names need a decent match; symbol-heavy names are handled by banner/highlight fallback.
    threshold = 0.50 if is_plain_trackable_name(player_name) else 0.38
    if best[0] is not None and best[3] >= threshold:
        return best
    return None, None, best[2], best[3]


def number_tokens_in_row(words: List[Dict[str, object]], row_y: float, img_w: int, img_h: int) -> List[Dict[str, object]]:
    # Wider tolerance because the beacon icon/count can sit lower than the main row text.
    tolerance = max(28, img_h * 0.018)
    row_words = [w for w in words if abs(float(w["cy"]) - row_y) <= tolerance and float(w["cx"]) < img_w * 0.53]
    numeric = []
    for w in row_words:
        digits = clean_number(str(w["text"]))
        if digits:
            item = dict(w)
            item["digits"] = digits
            numeric.append(item)
    return sorted(numeric, key=lambda x: float(x["left"]))


def join_digits(tokens: List[Dict[str, object]]) -> str:
    return "".join(str(t["digits"]) for t in sorted(tokens, key=lambda x: float(x["left"])))


def parse_by_screen_position(words: List[Dict[str, object]], player_name: str, img_w: int, img_h: int) -> Dict[str, str]:
    """
    War Robots team table columns on the ALLIES side are approximately:
    name | honor | damage+healing | assists | kills | beacons.

    This parser groups OCR numbers by x-position instead of relying only on OCR text order.
    """
    row_y = find_player_row(words, player_name, img_w)
    if row_y is None:
        return {}

    nums = number_tokens_in_row(words, row_y, img_w, img_h)
    groups = {
        "honor_points": [],
        "damage_healing": [],
        "assists": [],
        "kills": [],
        "beacons": [],
    }

    for token in nums:
        x = float(token["cx"]) / img_w
        # These ranges are intentionally a little broad for different screen sizes.
        if 0.235 <= x < 0.305:
            groups["honor_points"].append(token)
        elif 0.305 <= x < 0.390:
            groups["damage_healing"].append(token)
        elif 0.390 <= x < 0.425:
            groups["assists"].append(token)
        elif 0.425 <= x < 0.465:
            groups["kills"].append(token)
        elif 0.465 <= x < 0.515:
            groups["beacons"].append(token)

    parsed = {
        "honor_points": join_digits(groups["honor_points"]),
        "damage_healing": join_digits(groups["damage_healing"]),
        "assists": join_digits(groups["assists"]),
        "kills": join_digits(groups["kills"]),
        "beacons": join_digits(groups["beacons"]),
    }

    # If OCR missed the beacon column, keep N/A rather than zero. N/A means no detected beacon column/value.
    if not parsed["beacons"]:
        parsed["beacons"] = "N/A"

    # Fallback for cases where x grouping misses because OCR shifts the row text.
    if not any(parsed[k] for k in ["honor_points", "damage_healing", "assists", "kills"]):
        all_digits = [str(t["digits"]) for t in nums]
        # Expected order: honor, damage pieces, assists, kills, beacon.
        if len(all_digits) >= 5:
            parsed["honor_points"] = all_digits[0]
            parsed["damage_healing"] = "".join(all_digits[1:-3]) if len(all_digits) > 5 else all_digits[1]
            parsed["assists"] = all_digits[-3]
            parsed["kills"] = all_digits[-2]
            parsed["beacons"] = all_digits[-1]
        elif len(all_digits) >= 4:
            parsed["honor_points"] = all_digits[0]
            parsed["damage_healing"] = "".join(all_digits[1:-2]) or all_digits[1]
            parsed["assists"] = all_digits[-2]
            parsed["kills"] = all_digits[-1]
            parsed["beacons"] = "N/A"

    return parsed




def preprocess_cell_for_ocr(cell: Image.Image, numeric: bool = False) -> Image.Image:
    """Prepare one small table cell for OCR. Cell-level OCR is much more accurate than whole-screen OCR."""
    cell = cell.convert("RGB")
    cell = ImageOps.grayscale(cell)
    cell = ImageEnhance.Contrast(cell).enhance(2.8)
    cell = ImageEnhance.Sharpness(cell).enhance(2.0)
    scale = 4 if numeric else 3
    cell = cell.resize((max(1, cell.width * scale), max(1, cell.height * scale)))
    if cv2 is not None and np is not None:
        arr = np.array(cell)
        arr = cv2.GaussianBlur(arr, (3, 3), 0)
        arr = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        cell = Image.fromarray(arr)
    return cell


def ocr_cell(cell: Image.Image, numeric: bool = False) -> str:
    cell = preprocess_cell_for_ocr(cell, numeric=numeric)
    if numeric:
        cfgs = [
            "--psm 7 -c tessedit_char_whitelist=0123456789",
            "--psm 8 -c tessedit_char_whitelist=0123456789",
            "--psm 6 -c tessedit_char_whitelist=0123456789",
        ]
    else:
        cfgs = ["--psm 7", "--psm 6"]
    best = ""
    for cfg in cfgs:
        try:
            text = pytesseract.image_to_string(cell, config=cfg).strip()
            if len(clean_number(text)) > len(clean_number(best)):
                best = text
            elif not best and text:
                best = text
        except Exception:
            pass
    return best.strip()


def guess_place_from_text(text: str) -> Optional[int]:
    """Find YOUR PLACE ON THE TEAM from noisy OCR text.

    V13 handles imperfect reads such as:
    - YOUR PLACE ~ N THE TEAM: 2
    - YOUR PLACE ON THE TEAM 1
    - PLACE ON THE TEAM: 6
    """
    if not text:
        return None
    upper = text.upper()
    upper = upper.replace("|", " ").replace("~", " ").replace("—", " ").replace("_", " ")
    upper = re.sub(r"\s+", " ", upper)

    patterns = [
        r"YOUR\s+PLACE.{0,45}?TEAM\D{0,8}([1-6])",
        r"PLACE.{0,35}?TEAM\D{0,8}([1-6])",
        r"TEAM\D{0,4}([1-6])",
    ]
    for pat in patterns:
        m = re.search(pat, upper)
        if m:
            try:
                value = int(m.group(1))
                if 1 <= value <= 6:
                    return value
            except Exception:
                pass
    return None


def player_name_supported_by_filename_or_text(player_name: str, image_path: Path, text: str) -> bool:
    """Allow place-banner fallback when OCR misses the username but filename/text supports it.

    This protects against logging totally unrelated screenshots, while still allowing
    good screenshots where OCR mangles names like [HOWL] Wolfblood7.
    """
    norm_name = normalize_token(player_name)
    if not norm_name:
        return True
    haystack = normalize_token(image_path.stem + " " + text)
    if norm_name and norm_name in haystack:
        return True

    # Split into useful name pieces. Also compare versions with trailing digits removed
    # because player names often have clan tags and numbers.
    pieces = re.findall(r"[A-Za-z0-9]{4,}", player_name)
    for piece in pieces:
        n = normalize_token(piece)
        candidates = {n, re.sub(r"\d+$", "", n)}
        for c in candidates:
            if len(c) >= 4 and c in haystack:
                return True
    return False


def guess_place_from_image(image_path: Path) -> Optional[int]:
    """Read YOUR PLACE ON THE TEAM from the top banner.

    V13 uses several wide relative crops because YouTube/fullscreen/mobile screenshots
    can shift the banner vertically. This fixes cases where the old crop missed the
    YOUR PLACE line and then selected the wrong table row.
    """
    try:
        image = Image.open(image_path).convert("RGB")
        w, h = image.size
        crop_boxes = [
            (0.22, 0.03, 0.78, 0.18),
            (0.25, 0.06, 0.75, 0.22),
            (0.18, 0.04, 0.82, 0.24),
            (0.30, 0.08, 0.70, 0.17),
        ]
        texts = []
        for x1, y1, x2, y2 in crop_boxes:
            crop = image.crop((int(w * x1), int(h * y1), int(w * x2), int(h * y2)))
            crop = ImageOps.grayscale(crop)
            crop = crop.resize((crop.width * 4, crop.height * 4))
            crop = ImageEnhance.Contrast(crop).enhance(3.5)
            crop = ImageEnhance.Sharpness(crop).enhance(2.5)
            for psm in [6, 7, 11, 12]:
                try:
                    texts.append(pytesseract.image_to_string(crop, config=f"--psm {psm}"))
                except Exception:
                    pass
        combined = " ".join(texts).upper()
        combined = combined.replace("|", " ").replace("_", " ").replace("~", " ")
        combined = re.sub(r"\s+", " ", combined)

        patterns = [
            r"YOUR\s+PLACE.{0,55}?TEAM\D{0,12}([1-6])",
            r"PLACE.{0,45}?TEAM\D{0,12}([1-6])",
            r"TEAM\D{0,8}([1-6])",
        ]
        for pat in patterns:
            m = re.search(pat, combined)
            if m:
                n = int(m.group(1))
                if 1 <= n <= 6:
                    return n
    except Exception:
        pass
    return None


def cell_bbox(w: int, h: int, x1: float, y1: float, x2: float, y2: float) -> Tuple[int, int, int, int]:
    return (int(w * x1), int(h * y1), int(w * x2), int(h * y2))


def clean_stat_cell_text(text: str, allow_m: bool = False) -> str:
    text = (text or "").strip().upper()
    text = text.replace("O", "0").replace("I", "1").replace("L", "1")
    if allow_m and "M" in text:
        m = re.search(r"(\d+(?:[\.,]\d+)?)\s*M", text)
        if m:
            return m_value_to_full_number(m.group(1))
    digits = re.sub(r"[^0-9]", "", text)
    return digits


def ocr_numeric_region(image: Image.Image, box: Tuple[int, int, int, int], allow_m: bool = False) -> str:
    """OCR one stat cell/region with several preprocessing attempts.

    This is more stable than OCRing the whole row because column spacing changes
    across devices and YouTube/fullscreen screenshots.
    """
    x1, y1, x2, y2 = box
    # Small padding prevents vertical grid lines from cutting off digits.
    pad_x = max(2, int((x2 - x1) * 0.05))
    pad_y = max(1, int((y2 - y1) * 0.08))
    crop = image.crop((max(0, x1 - pad_x), max(0, y1 - pad_y), min(image.width, x2 + pad_x), min(image.height, y2 + pad_y)))

    variants = []
    base = crop.convert("RGB")
    gray = ImageOps.grayscale(base)
    variants.append(gray)
    variants.append(ImageEnhance.Contrast(gray).enhance(3.2))
    variants.append(ImageEnhance.Sharpness(ImageEnhance.Contrast(gray).enhance(3.8)).enhance(2.4))

    if cv2 is not None and np is not None:
        arr = np.array(gray)
        arr = cv2.resize(arr, None, fx=5, fy=5, interpolation=cv2.INTER_CUBIC)
        arr = cv2.GaussianBlur(arr, (3, 3), 0)
        thresh = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        variants.append(Image.fromarray(thresh))
        variants.append(Image.fromarray(255 - thresh))

    whitelist = "0123456789M.m," if allow_m else "0123456789"
    cfgs = [
        f"--psm 7 -c tessedit_char_whitelist={whitelist}",
        f"--psm 8 -c tessedit_char_whitelist={whitelist}",
        f"--psm 13 -c tessedit_char_whitelist={whitelist}",
    ]
    candidates = []
    for variant in variants:
        if variant.width < 250:
            variant = variant.resize((variant.width * 5, variant.height * 5))
        for cfg in cfgs:
            try:
                raw = pytesseract.image_to_string(variant, config=cfg).strip()
                cleaned = clean_stat_cell_text(raw, allow_m=allow_m)
                if cleaned:
                    candidates.append(cleaned)
            except Exception:
                pass

    if not candidates:
        return ""

    # Prefer plausible damage values for M/large cell, otherwise choose the most common/longest.
    if allow_m:
        big = [c for c in candidates if c.isdigit() and int(c) >= 100000]
        if big:
            return max(big, key=lambda x: (len(x), candidates.count(x)))
    return max(candidates, key=lambda x: (candidates.count(x), len(x)))


def estimate_ally_table_x_bounds(image: Image.Image, rows_top: float) -> Tuple[float, float]:
    """Estimate the left/right edge of the ALLIES table from the blue header.

    V18 uses the blue header background itself, not the white OCR icons, so black
    bars and different screen widths do not shift stat columns.
    """
    w, h = image.size
    if cv2 is None or np is None:
        return 0.0, 0.52
    try:
        arr = np.array(image.convert("RGB")).astype("float32")
        # Sample a vertical window immediately above the first player row, where
        # the ALLIES blue header is located.
        y = max(0, min(h - 1, int(rows_top * h) - int(h * 0.025)))
        band_h = max(4, int(h * 0.018))
        band = arr[max(0, y - band_h):min(h, y + band_h), :, :]
        r, g, b = band[:, :, 0], band[:, :, 1], band[:, :, 2]
        bright = (r + g + b) / 3.0
        blue_score = ((g + b) / 2.0) - r + bright * 0.10
        col_score = blue_score.mean(axis=0)
        col_bright = bright.mean(axis=0)

        # Absolute threshold catches the full blue header. Percentile-based
        # thresholds over-select the white column icons on some screenshots.
        mask = (col_score > 38.0) & (col_bright > 35.0)

        if mask.any():
            # Fill small gaps caused by text/icons/grid lines.
            gap = max(4, int(w * 0.015))
            idx = np.where(mask)[0]
            filled = mask.copy()
            for a, bidx in zip(idx[:-1], idx[1:]):
                if 1 < (bidx - a) <= gap:
                    filled[a:bidx + 1] = True
            mask = filled

        segments = []
        start = None
        for i, ok in enumerate(mask):
            if ok and start is None:
                start = i
            if (not ok or i == len(mask) - 1) and start is not None:
                end = i if not ok else i + 1
                if end - start > w * 0.15:
                    segments.append((start, end))
                start = None

        left_segments = [seg for seg in segments if seg[0] < w * 0.58]
        if left_segments:
            x1, x2 = max(left_segments, key=lambda s: s[1] - s[0])
            return max(0.0, x1 / w), min(0.58, x2 / w)
    except Exception:
        pass
    return 0.0, 0.52


def detect_highlighted_ally_row_bounds(image: Image.Image, rows_top: float) -> Optional[Tuple[float, float, float]]:
    """Find the visibly highlighted player row in the ALLIES table.

    This is more reliable than OCR for normal user screenshots because War Robots
    highlights the local player's row with a brighter blue/teal background. The
    function returns (y1_ratio, y2_ratio, center_ratio). It ignores the blue ALLIES
    header and works with black bars/wide screenshots because it uses the detected
    ally-table x bounds.
    """
    if cv2 is None or np is None:
        return None
    try:
        w, h = image.size
        arr = np.array(image.convert("RGB")).astype("float32")
        x1r, x2r = estimate_ally_table_x_bounds(image, rows_top)
        # Avoid the far-right reward/key icons; use the table body/name+stats area.
        x1 = max(0, int(w * x1r))
        x2 = min(w, int(w * (x1r + (x2r - x1r) * 0.92)))
        y1 = max(0, int(h * (rows_top - 0.08)))
        y2 = min(h, int(h * min(0.95, rows_top + 0.50)))
        crop = arr[y1:y2, x1:x2, :]
        if crop.size == 0:
            return None
        r, g, b = crop[:, :, 0], crop[:, :, 1], crop[:, :, 2]
        # Selected rows are brighter teal/blue than normal rows. This threshold
        # intentionally avoids relying on text OCR or absolute row pixel positions.
        mask = (r > 28) & (g > 65) & (b > 80) & ((b - r) > 35)
        row_score = mask.mean(axis=1).astype("float32")
        # Smooth without depending on scipy.
        kernel = np.ones(9, dtype="float32") / 9.0
        smooth = np.convolve(row_score, kernel, mode="same")

        segments = []
        in_seg = False
        start = 0
        for i, val in enumerate(smooth):
            if val > 0.30 and not in_seg:
                start = i
                in_seg = True
            if (val <= 0.30 or i == len(smooth) - 1) and in_seg:
                end = i if val <= 0.30 else i + 1
                if end - start >= max(12, int(h * 0.018)):
                    yy1, yy2 = y1 + start, y1 + end
                    center = (yy1 + yy2) / 2.0
                    # Ignore the blue ALLIES header, which is above the rows.
                    if center > h * (rows_top + 0.015):
                        strength = float(smooth[start:end].max())
                        segments.append((yy1, yy2, center, strength))
                in_seg = False
        if not segments:
            return None
        # The highlighted player row usually has the strongest, widest teal band.
        yy1, yy2, center, _strength = max(segments, key=lambda t: (t[3], t[1] - t[0]))
        # Slightly trim vertical bounds so OCR sees only the selected row text.
        pad = max(1, int((yy2 - yy1) * 0.05))
        yy1 += pad
        yy2 -= pad
        return yy1 / h, yy2 / h, center / h
    except Exception:
        return None

def parse_stats_from_cells(image: Image.Image, place: int, rows_top: float, row_h: float) -> Dict[str, str]:
    """Read stat cells by region rather than by OCR word order.

    The regions are relative to the detected ALLIES table width, so they survive
    different device resolutions and black bars better than fixed pixels.
    """
    w, h = image.size
    x1r, x2r = estimate_ally_table_x_bounds(image, rows_top)
    table_w = x2r - x1r
    # Use the central band of the row. Older versions expanded vertically,
    # which sometimes pulled numbers from the row above or below.
    y1 = rows_top + (place - 1) * row_h + row_h * 0.04
    y2 = rows_top + place * row_h - row_h * 0.04

    def bx(a: float, b: float) -> Tuple[int, int, int, int]:
        return cell_bbox(w, h, x1r + table_w * a, y1, x1r + table_w * b, y2)

    # Column percentages within the allies table. These intentionally overlap a bit.
    boxes = {
        "honor_points": bx(0.42, 0.54),
        "damage_healing": bx(0.53, 0.70),
        "assists": bx(0.68, 0.76),
        "kills": bx(0.75, 0.84),
        "beacons": bx(0.83, 0.92),
    }

    parsed = {
        "honor_points": ocr_numeric_region(image, boxes["honor_points"]),
        "damage_healing": ocr_numeric_region(image, boxes["damage_healing"], allow_m=True),
        "assists": ocr_numeric_region(image, boxes["assists"]),
        "kills": ocr_numeric_region(image, boxes["kills"]),
        "beacons": ocr_numeric_region(image, boxes["beacons"]),
    }

    # Basic sanity rules. If a cell read is unreasonable, leave it blank so row-text fallback can help.
    if parsed["honor_points"] and len(parsed["honor_points"]) > 5:
        parsed["honor_points"] = ""
    for k in ["assists", "kills", "beacons"]:
        if parsed[k] and (len(parsed[k]) > 2 or int(parsed[k]) > 99):
            parsed[k] = ""
    if not parsed["beacons"]:
        parsed["beacons"] = "N/A"
    return parsed



def estimate_ally_table_geometry(image: Image.Image) -> Tuple[float, float, float]:
    """Estimate ally table row positions using the blue ALLIES header.

    Returns (rows_top, rows_bottom, row_h) as ratios of image height.
    The blue header may be split into several bands because of white icons/text,
    so nearby bands are merged before choosing the header bottom.
    """
    w, h = image.size
    fallback_top = 0.248
    fallback_bottom = 0.680
    fallback_h = (fallback_bottom - fallback_top) / 6

    if cv2 is None or np is None:
        return fallback_top, fallback_bottom, fallback_h

    try:
        arr = np.array(image.convert("RGB"))
        y_start = int(h * 0.12)
        y_end = int(h * 0.50)
        x1 = int(w * 0.00)
        x2 = int(w * 0.52)
        crop = arr[y_start:y_end, x1:x2, :].astype("float32")
        if crop.size == 0:
            return fallback_top, fallback_bottom, fallback_h

        r = crop[:, :, 0]
        g = crop[:, :, 1]
        b = crop[:, :, 2]
        brightness = (r + g + b) / 3.0
        score_by_y = (((g + b) / 2.0) - r + brightness * 0.12).mean(axis=1)
        bright_by_y = brightness.mean(axis=1)
        threshold = max(float(np.percentile(score_by_y, 88)), 35.0)
        mask = (score_by_y > threshold) & (bright_by_y > 50)

        raw_bands = []
        current = None
        for i, ok in enumerate(mask):
            if ok and current is None:
                current = i
            if (not ok or i == len(mask) - 1) and current is not None:
                end = i if not ok else i + 1
                if (end - current) >= 4:
                    raw_bands.append([y_start + current, y_start + end])
                current = None

        if not raw_bands:
            return fallback_top, fallback_bottom, fallback_h

        # Merge bands from the same ALLIES header. White icons/letters can split the header.
        merged = []
        for band in raw_bands:
            if not merged or band[0] - merged[-1][1] > max(22, int(h * 0.025)):
                merged.append(band)
            else:
                merged[-1][1] = band[1]

        # Choose the widest/longest merged blue band below the top UI.
        merged = [b for b in merged if b[0] > h * 0.16]
        if not merged:
            return fallback_top, fallback_bottom, fallback_h
        header_top_px, header_bottom_px = max(merged, key=lambda b: b[1] - b[0])
        header_h_px = max(1, header_bottom_px - header_top_px)

        rows_top_px = header_bottom_px + int(header_h_px * 0.02)

        # V18 fix: row height must be based on the visible scoreboard/header height,
        # not image width. Older builds used w * 0.036, which breaks on ultrawide
        # or YouTube screenshots with side black bars and shifts the selected row down.
        row_h_px = max(int(header_h_px * 1.18), int(h * 0.052))
        rows_bottom_px = rows_top_px + row_h_px * 6
        return rows_top_px / h, min(rows_bottom_px / h, 0.97), row_h_px / h
    except Exception:
        return fallback_top, fallback_bottom, fallback_h


def learn_player_name_from_image(image_path: Path, forced_place: Optional[int] = None) -> Tuple[str, Optional[int]]:
    """Learn the player's displayed name from a screenshot.

    The app reads YOUR PLACE ON THE TEAM, crops that ally row's name cell,
    OCRs only that name area, and returns the detected name plus place.
    """
    image = Image.open(image_path).convert("RGB")
    w, h = image.size
    place = forced_place if forced_place is not None else guess_place_from_image(image_path)
    if place is None:
        raise RuntimeError("Could not read 'YOUR PLACE ON THE TEAM' from this screenshot. Try a clearer full results screenshot.")

    rows_top, _rows_bottom, row_h = estimate_ally_table_geometry(image)
    y1 = rows_top + (place - 1) * row_h + row_h * 0.03
    y2 = rows_top + place * row_h - row_h * 0.03
    # Name is in the left ally row before the info icon/stat columns.
    name_crop = image.crop(cell_bbox(w, h, 0.010, y1, 0.210, y2))
    name_img = preprocess_cell_for_ocr(name_crop, numeric=False)

    candidates = []
    for cfg in ["--psm 7", "--psm 6", "--psm 11"]:
        try:
            txt = pytesseract.image_to_string(name_img, config=cfg).strip()
            txt = " ".join(line.strip() for line in txt.splitlines() if line.strip())
            if txt:
                candidates.append(txt)
        except Exception:
            pass

    if not candidates:
        raise RuntimeError("Could not read the player name from the selected row. Try a clearer screenshot.")

    # Preserve numbers, symbols, and non-English characters.
    # We only remove obvious title-only lines if OCR accidentally grabbed them.
    bad_words = {"COMMANDER", "CONQUEROR", "GLADIATOR", "SCORCHED", "FRIENDLY", "COLLECTOR", "SCOUT", "HAPPINESS", "TITAN", "SLAYER"}
    cleaned = []
    for c in candidates:
        c = re.sub(r"\s{2,}", " ", c).strip()
        lines = [x.strip() for x in re.split(r"[\r\n|]+", c) if x.strip()] or [c]
        useful_lines = [x for x in lines if x.upper() not in bad_words]
        candidate = useful_lines[0] if useful_lines else c
        cleaned.append(candidate.strip())

    # Prefer the longest visible string, not the most English-looking string.
    learned = max(cleaned, key=lambda x: len(x.strip())).strip()
    if not learned:
        raise RuntimeError("Could not read a usable player name from the selected row.")
    return learned, place


def ocr_scoreboard_row(row_crop: Image.Image) -> str:
    """OCR a single ally stat row as one line.

    V18 keeps this deliberately simple. The previous heavy thresholding sometimes
    erased pale War Robots digits. Direct enlarged OCR on the lower part of the
    row preserves the numbers and prevents column-shift errors.
    """
    texts = []
    candidates = [row_crop]
    # Digits sit in the lower half of the row; this crop removes the empty top band.
    if row_crop.height > 30:
        candidates.append(row_crop.crop((0, int(row_crop.height * 0.28), row_crop.width, row_crop.height)))
    for candidate in candidates:
        gray = ImageOps.grayscale(candidate)
        gray = ImageEnhance.Contrast(gray).enhance(2.4)
        gray = ImageEnhance.Sharpness(gray).enhance(1.6)
        scale = 5 if candidate.height < 90 else 4
        enlarged = gray.resize((gray.width * scale, gray.height * scale))
        for psm in [6, 7, 11]:
            for whitelist in [
                "0123456789M.m, ",
                "0123456789 ",
            ]:
                try:
                    cfg = f"--psm {psm} -c tessedit_char_whitelist={whitelist}"
                    texts.append(pytesseract.image_to_string(enlarged, config=cfg).strip())
                except Exception:
                    pass
    return max(texts, key=lambda t: (len(re.findall(r"\d+", t)), len(t)), default="")

def m_value_to_full_number(value: str) -> str:
    """Convert War Robots M shorthand into a full integer string.

    Examples:
    - 10.3M -> 10300000
    - 10,7 M -> 10700000
    - 3M -> 3000000

    Some OCR runs drop the decimal point, so 103M is treated as 10.3M rather than
    103,000,000 because War Robots normally displays values like 10.3 M.
    """
    value = value.strip().upper().replace("O", "0")
    value = value.replace(",", ".")
    value = re.sub(r"[^0-9.]", "", value)
    if not value:
        return ""
    try:
        if "." in value:
            millions = float(value)
        else:
            raw = int(value)
            # OCR sometimes reads 10.3 M as 103 M. Interpret 3-digit values as one decimal.
            if 100 <= raw <= 999:
                millions = raw / 10.0
            else:
                millions = float(raw)
        return str(int(round(millions * 1_000_000)))
    except Exception:
        return ""


def looks_like_small_stat(value: str) -> bool:
    """Assists/kills/beacons are normally 0-30, so they are short numbers."""
    if not value or not value.isdigit():
        return False
    try:
        return 0 <= int(value) <= 99 and len(value) <= 2
    except Exception:
        return False


def choose_honor_and_damage(prefix_nums: List[str]) -> Tuple[str, str]:
    """Split the numbers before assists/kills/beacons into honor and damage.

    OCR sometimes inserts stray small numbers from icons, video overlays, or the info
    button before the real honor score. The real honor score is usually a 3-5 digit
    value, followed by damage pieces such as 9 381 477 or 5 088 991.
    """
    nums = [n for n in prefix_nums if n]
    if not nums:
        return "", ""

    # Prefer the first plausible honor value that leaves at least one number for damage.
    # This fixes rows like: 0 4567 9 381 477 4 13 6
    # where the leading 0 is OCR noise and honor should be 4567.
    honor_index = None
    for i, n in enumerate(nums[:-1]):
        if 3 <= len(n) <= 5:
            # Avoid treating a 3-digit damage group as honor when there is an earlier
            # plausible 4-digit value. Honor is usually before grouped damage.
            honor_index = i
            break

    # Fallback: use the number right before the damage-looking groups.
    if honor_index is None:
        for i, n in enumerate(nums[:-1]):
            try:
                v = int(n)
            except Exception:
                continue
            if 50 <= v <= 99999:
                honor_index = i
                break

    if honor_index is None:
        honor_index = 0

    honor = nums[honor_index]
    damage_parts = nums[honor_index + 1:]

    damage = "".join(damage_parts)
    return honor, damage


def parse_numbers_from_row_text(row_text: str) -> Dict[str, str]:
    """Turn a single row OCR result into War Robots stats.

    Expected stat order after the player name/info icon is:
      Non-beacon mode: Honor | Damage+Healing | Assists | Kills
      Beacon mode:     Honor | Damage+Healing | Assists | Kills | Beacons

    V13 is more careful about column splitting. It ignores stray OCR digits before
    the honor column and then treats the final small numbers as assists/kills/beacons.
    It also understands War Robots M shorthand: 10.3M, 10.3 M, 3M, etc.
    """
    parsed = {
        "honor_points": "",
        "damage_healing": "",
        "assists": "",
        "kills": "",
        "beacons": "N/A",
    }

    normalized = row_text.replace("O", "0").replace("o", "0")
    normalized = normalized.replace("I", "1").replace("l", "1")

    # Handle damage values shown as millions, e.g. "10.3 M" or "10.3M".
    m_match = re.search(r"(\d+(?:[\.,]\d+)?)\s*[Mm]\b", normalized)
    if m_match:
        before = normalized[:m_match.start()]
        after = normalized[m_match.end():]
        before_nums = re.findall(r"\d+", before)
        after_nums = re.findall(r"\d+", after)

        honor, _unused_damage = choose_honor_and_damage(before_nums + ["999999"])  # dummy lets helper choose honor robustly
        if honor == "999999":
            honor = ""
        parsed["honor_points"] = honor
        parsed["damage_healing"] = m_value_to_full_number(m_match.group(1))

        # Use the final small stat values after the M value. This avoids key/drone icon
        # numbers being mistaken as assists/kills/beacons.
        small_after = [n for n in after_nums if looks_like_small_stat(n)]
        if len(small_after) >= 3:
            parsed["assists"], parsed["kills"], parsed["beacons"] = small_after[0], small_after[1], small_after[2]
        elif len(small_after) >= 2:
            parsed["assists"], parsed["kills"] = small_after[0], small_after[1]
            parsed["beacons"] = "N/A"

        if parsed["honor_points"] and parsed["damage_healing"] and parsed["assists"] and parsed["kills"]:
            return parsed

    nums = re.findall(r"\d+", normalized)
    nums = [n for n in nums if n]
    if len(nums) < 4:
        return parsed

    # Decide whether this row has a beacon column by looking for three small values at
    # the end. For non-beacon games, only assists and kills appear at the end.
    if len(nums) >= 5 and all(looks_like_small_stat(n) for n in nums[-3:]):
        stat_tail = nums[-3:]
        prefix = nums[:-3]
        parsed["assists"], parsed["kills"], parsed["beacons"] = stat_tail
    elif len(nums) >= 4 and all(looks_like_small_stat(n) for n in nums[-2:]):
        stat_tail = nums[-2:]
        prefix = nums[:-2]
        parsed["assists"], parsed["kills"] = stat_tail
        parsed["beacons"] = "N/A"
    else:
        # Conservative fallback: use the last two values as assists/kills.
        prefix = nums[:-2]
        parsed["assists"], parsed["kills"] = nums[-2], nums[-1]
        parsed["beacons"] = "N/A"

    honor, damage = choose_honor_and_damage(prefix)
    parsed["honor_points"] = honor
    parsed["damage_healing"] = damage

    # Final sanity cleanup.
    if len(parsed.get("honor_points", "")) > 5:
        parsed["honor_points"] = ""
    if parsed.get("damage_healing", ""):
        # Damage should normally be at least 5 digits unless it was a very unusual match.
        # Do not delete it, but remove leading zero noise.
        parsed["damage_healing"] = parsed["damage_healing"].lstrip("0") or "0"
    for key in ["assists", "kills", "beacons"]:
        if parsed.get(key) != "N/A" and len(parsed.get(key, "")) > 2:
            parsed[key] = parsed[key][-2:]

    return parsed



def parse_by_adaptive_layout(image_path: Path, player_name: str = "", manual_place: Optional[int] = None, full_text: str = "") -> Dict[str, str]:
    """
    Adaptive scoreboard parser.

    V18 priority order:
    1) Find the configured player by OCRing ally name cells row-by-row.
    2) If that fails, use the regular word-box name match.
    3) If name matching is unavailable/unreliable, use the visible highlighted row.
    4) Last fallback: YOUR PLACE ON THE TEAM banner.

    The key V18 change is that once a player-name row is found, the app crops stats
    from that exact row instead of blindly trusting the banner or highlight detector.
    """
    image = Image.open(image_path).convert("RGB")
    w, h = image.size

    rows_top, _rows_bottom, row_h = estimate_ally_table_geometry(image)
    place: Optional[int] = None
    source = ""
    row_center_ratio: Optional[float] = None
    name_found = False

    banner_place = guess_place_from_text(full_text) if full_text else None
    if banner_place is None:
        banner_place = guess_place_from_image(image_path)

    # Best first pass: OCR each ally name cell independently. This handles black bars,
    # compressed screenshots, and dim highlights better than whole-image OCR.
    if player_name:
        try:
            cell_place, cell_y, _cell_text, cell_score = find_player_row_by_name_cells(image, player_name, rows_top, row_h)
            if cell_place is not None and cell_y is not None:
                place = cell_place
                row_center_ratio = cell_y
                source = "name_cell"
                name_found = True
        except Exception:
            pass

    # Second pass: older word-box fuzzy match. Use the matched row itself, not the banner.
    if place is None and player_name:
        try:
            words, iw, ih = ocr_words(image_path)
            row_y = find_player_row(words, player_name, iw)
            if row_y is not None:
                guessed = row_y_to_place(row_y, ih, rows_top, row_h)
                if guessed is not None:
                    place = guessed
                    row_center_ratio = float(row_y) / float(ih)
                    source = "name_words"
                    name_found = True
        except Exception:
            pass

    # If this is a plain typed name and it was not found anywhere, do not log a random
    # row unless the filename/text clearly supports that player. This preserves the V13
    # wrong-player guard.
    if player_name and is_plain_trackable_name(player_name) and not name_found:
        if banner_place is not None and player_name_supported_by_filename_or_text(player_name, image_path, full_text):
            place = banner_place
            source = "place_banner_filename_fallback"
        else:
            return {"needs_review": "player_name_not_found"}

    # Visual highlighted row fallback. Only use this when name matching did not find a row.
    highlighted = detect_highlighted_ally_row_bounds(image, rows_top)
    if place is None and highlighted is not None:
        hy1, hy2, hcenter = highlighted
        hp = int(round((hcenter - rows_top - row_h / 2) / row_h)) + 1
        if 1 <= hp <= 6:
            place = hp
        row_center_ratio = hcenter
        source = "highlight"

    # Final fallback: top banner place.
    if place is None:
        place = banner_place
        if place is not None:
            source = "place_banner"

    if place is None:
        return {"needs_review": "place_not_found"}

    # Pick the stat-row crop. If name matching or highlight provided a precise center,
    # use it directly. This is what fixes dim/letterboxed screenshots like 204703.
    if row_center_ratio is not None:
        y1 = row_center_ratio - row_h * 0.48
        y2 = row_center_ratio + row_h * 0.55
    elif highlighted is not None:
        y1, y2, _center = highlighted
    else:
        y1 = rows_top + (place - 1) * row_h - row_h * 0.02
        y2 = rows_top + place * row_h + row_h * 0.15
    y1 = max(0.0, y1)
    y2 = min(1.0, y2)

    x1r, x2r = estimate_ally_table_x_bounds(image, rows_top)
    table_w = x2r - x1r

    # Debug crop: save exactly the ally row region that the parser selected.
    # This lets users inspect what the OCR/parser is seeing for manual tuning.
    try:
        if DEBUG_CROPS_FOLDER is not None:
            DEBUG_CROPS_FOLDER.mkdir(parents=True, exist_ok=True)
            debug_crop = image.crop(cell_bbox(w, h, x1r, y1, x2r, y2))
            debug_crop.save(DEBUG_CROPS_FOLDER / image_path.name)
    except Exception:
        pass

    # Crop only the stat side of the ally row. Starting at 0.37 keeps the info icon/name
    # out but still includes honor for narrow/shifted screenshots.
    row_crop = image.crop(cell_bbox(w, h, x1r + table_w * 0.37, y1, x1r + table_w * 0.93, y2))
    row_text = ocr_scoreboard_row(row_crop)
    parsed = parse_numbers_from_row_text(row_text)

    # Cell OCR fallback/repair. Use the exact row bounds when possible.
    incomplete = not (parsed.get("honor_points") and parsed.get("damage_healing") and parsed.get("assists") and parsed.get("kills"))
    suspicious_damage = parsed.get("damage_healing", "") in {"", "0"} or (parsed.get("damage_healing", "").isdigit() and int(parsed.get("damage_healing", "0")) < 1000)
    if incomplete or suspicious_damage:
        synthetic_row_h = max(0.001, y2 - y1)
        cell_parsed = parse_stats_from_cells(image, 1, y1, synthetic_row_h)
        for key in ["honor_points", "damage_healing", "assists", "kills", "beacons"]:
            if (not parsed.get(key) or parsed.get(key) == "N/A" or (key == "damage_healing" and suspicious_damage)) and cell_parsed.get(key):
                parsed[key] = cell_parsed[key]

    parsed["place"] = str(place)
    parsed["row_ocr"] = row_text
    parsed["source"] = source
    return parsed

def parse_stats(image_path: Path, text: str, player_name: str, manual_place: Optional[int] = None) -> ParseResult:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    result_text = guess_result_from_text(text)
    result_image = guess_result_from_image(image_path)
    result = result_image if result_image != "UNKNOWN" else result_text

    parsed = ParseResult(
        result=result,
        player_name=player_name,
        raw_preview=" | ".join(lines[:8])[:500],
    )

    # V13 hotfix: before any fallback parser is allowed to log a row, make sure
    # a normal English/number username is actually present in the screenshot OR
    # clearly supported by the filename. This prevents a saved name like
    # "[Melted] Metal" from accidentally logging an unrelated YoYoBrudda/Adrian file.
    name_verified_for_fallback = False
    if player_name and is_plain_trackable_name(player_name):
        if player_name_supported_by_filename_or_text(player_name, image_path, text):
            name_verified_for_fallback = True
        else:
            try:
                _words_check, _iw_check, _ih_check = ocr_words(image_path)
                if find_player_row(_words_check, player_name, _iw_check) is not None:
                    name_verified_for_fallback = True
            except Exception:
                name_verified_for_fallback = False
    else:
        # For symbol-heavy or non-English names, OCR name matching can be unreliable.
        # These users can still rely on learned/manual row behavior.
        name_verified_for_fallback = True

    # Try adaptive row/cell cropping first. This uses YOUR PLACE ON THE TEAM to pick
    # the right ally row, then OCRs the numeric stats from that row.
    try:
        # In V13, manual_place is intentionally ignored during normal processing.
        layout_data = parse_by_adaptive_layout(image_path, player_name, None, text)
        if layout_data.get("needs_review"):
            parsed.needs_review = True
            parsed.review_reason = layout_data.get("needs_review", "needs_review")
            return parsed
        if layout_data and any(layout_data.get(k, "") for k in ["honor_points", "damage_healing", "assists", "kills"]):
            parsed.honor_points = layout_data.get("honor_points", "")
            parsed.damage_healing = layout_data.get("damage_healing", "")
            parsed.assists = layout_data.get("assists", "")
            parsed.kills = layout_data.get("kills", "")
            parsed.beacons = layout_data.get("beacons", "N/A") or "N/A"
            parsed.team_place = layout_data.get("place", "")
            parsed.player_name = player_name or "N/A"
            parsed.raw_preview = (parsed.raw_preview + f" | Adaptive row used: place {layout_data.get('place', '?')}")[:500]
            return parsed
    except Exception:
        pass

    # If the strict saved-name check failed, do NOT let older fallbacks guess a row.
    if player_name and is_plain_trackable_name(player_name) and not name_verified_for_fallback:
        parsed.needs_review = True
        parsed.review_reason = "player_name_not_found"
        return parsed

    # Fallback: older V3 word-box parser.
    try:
        words, img_w, img_h = ocr_words(image_path)
        position_data = parse_by_screen_position(words, player_name, img_w, img_h)
        if position_data:
            parsed.honor_points = position_data.get("honor_points", "")
            parsed.damage_healing = position_data.get("damage_healing", "")
            parsed.assists = position_data.get("assists", "")
            parsed.kills = position_data.get("kills", "")
            parsed.beacons = position_data.get("beacons", "N/A") or "N/A"
            return parsed
    except Exception:
        pass

    # Text-only fallback. Less accurate, but useful for rough recovery.
    target_line = ""
    if player_name:
        best_line = ""
        best_score = 0.0
        for line in lines:
            score = fuzzy_score(line, player_name)
            if score > best_score:
                best_score = score
                best_line = line
        if best_score >= 0.40:
            target_line = best_line

    if target_line:
        nums = [clean_number(n) for n in re.findall(r"\d[\d,\s]{0,12}", target_line)]
        nums = [n for n in nums if n]
        if len(nums) >= 5:
            parsed.honor_points = nums[0]
            parsed.damage_healing = "".join(nums[1:-3]) or nums[1]
            parsed.assists = nums[-3]
            parsed.kills = nums[-2]
            parsed.beacons = nums[-1]
        elif len(nums) >= 4:
            parsed.honor_points = nums[0]
            parsed.damage_healing = "".join(nums[1:-2]) or nums[1]
            parsed.assists = nums[-2]
            parsed.kills = nums[-1]
            parsed.beacons = "N/A"

    return parsed


def get_output_csv(config: Dict[str, object]) -> Path:
    return resolve_path(str(config["output_folder"])) / str(config["match_log_csv"])


def already_logged(csv_path: Path, filename: str) -> bool:
    if not csv_path.exists():
        return False
    try:
        df = pd.read_csv(csv_path)
        return filename in set(df.get("Screenshot File", []))
    except Exception:
        return False


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def hashes_path(config: Dict[str, object]) -> Path:
    return resolve_path(str(config["output_folder"])) / "processed_hashes.json"


def load_processed_hashes(config: Dict[str, object]) -> Dict[str, str]:
    path = hashes_path(config)
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception:
            pass
    return {}


def save_processed_hashes(config: Dict[str, object], hashes: Dict[str, str]) -> None:
    path = hashes_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(hashes, f, indent=2)


def move_unique_file(src: Path, dest_folder: Path) -> Path:
    dest_folder.mkdir(parents=True, exist_ok=True)
    target = dest_folder / src.name
    if target.exists():
        target = dest_folder / f"{src.stem}_{int(time.time())}{src.suffix}"
    shutil.move(str(src), str(target))
    return target


def append_row(csv_path: Path, row: Dict[str, str]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def update_summary(config: Dict[str, object]) -> Path:
    csv_path = get_output_csv(config)
    summary_path = resolve_path(str(config["output_folder"])) / "summary.html"
    if not csv_path.exists():
        summary_path.write_text("<h1>No matches processed yet.</h1>", encoding="utf-8")
        return summary_path

    df = pd.read_csv(csv_path, keep_default_na=False)
    total = len(df)
    wins = int((df["Result"] == "WIN").sum()) if "Result" in df else 0
    losses = int((df["Result"] == "LOSE").sum()) if "Result" in df else 0
    winrate = round((wins / total) * 100, 2) if total else 0

    numeric_cols = ["Team Place", "Honor Points", "Damage + Healing", "Assists", "Kills"]
    for col in numeric_cols:
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    beacon_numeric = pd.to_numeric(df["Beacons"], errors="coerce") if "Beacons" in df else pd.Series(dtype=float)

    avg_damage = round(float(df["Damage + Healing"].mean()), 0) if "Damage + Healing" in df and df["Damage + Healing"].notna().any() else 0
    avg_kills = round(float(df["Kills"].mean()), 2) if "Kills" in df and df["Kills"].notna().any() else 0
    avg_beacons = round(float(beacon_numeric.dropna().mean()), 2) if not beacon_numeric.dropna().empty else "N/A"
    avg_place = round(float(df["Team Place"].mean()), 2) if "Team Place" in df and df["Team Place"].notna().any() else "N/A"
    total_kills = int(df["Kills"].sum()) if "Kills" in df and df["Kills"].notna().any() else 0
    total_beacons = int(beacon_numeric.dropna().sum()) if not beacon_numeric.dropna().empty else 0

    recent = df.tail(10)
    recent_wins = int((recent["Result"] == "WIN").sum()) if "Result" in recent else 0
    recent_wr = round((recent_wins / len(recent)) * 100, 2) if len(recent) else 0

    display_df = df.tail(15).copy()
    display_df = display_df.fillna("N/A")
    for col in ["Team Place", "Honor Points", "Damage + Healing", "Assists", "Kills"]:
        if col in display_df:
            display_df[col] = display_df[col].apply(lambda x: "N/A" if x == "" or pd.isna(x) else f"{int(float(x)):,}" if str(x).replace('.', '', 1).isdigit() else x)
    if "Beacons" in display_df:
        display_df["Beacons"] = display_df["Beacons"].apply(lambda x: "N/A" if str(x).strip() == "" or str(x).lower() == "nan" else x)

    html = f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>War Robots Tracker Summary</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 32px; background: #111827; color: #f9fafb; }}
.card {{ background: #1f2937; padding: 20px; border-radius: 16px; margin: 12px 0; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; }}
.big {{ font-size: 32px; font-weight: bold; }}
table {{ width: 100%; border-collapse: collapse; background: #1f2937; font-size: 13px; }}
th, td {{ border-bottom: 1px solid #374151; padding: 8px; text-align: left; }}
.small {{ color: #d1d5db; font-size: 13px; }}
</style>
</head>
<body>
<h1>War Robots Tracker Summary</h1>
<div class="grid">
  <div class="card"><div>Total Matches</div><div class="big">{total}</div></div>
  <div class="card"><div>Win Rate</div><div class="big">{winrate}%</div></div>
  <div class="card"><div>Wins / Losses</div><div class="big">{wins} / {losses}</div></div>
  <div class="card"><div>Last 10 Win Rate</div><div class="big">{recent_wr}%</div></div>
  <div class="card"><div>Average Damage + Healing</div><div class="big">{avg_damage:,.0f}</div></div>
  <div class="card"><div>Average Place</div><div class="big">{avg_place}</div><div class="small">1 = top teammate, 6 = bottom teammate</div></div>
  <div class="card"><div>Average Kills</div><div class="big">{avg_kills}</div></div>
  <div class="card"><div>Total Kills</div><div class="big">{total_kills}</div></div>
  <div class="card"><div>Average Beacons</div><div class="big">{avg_beacons}</div><div class="small">Beacon-mode games only</div></div>
  <div class="card"><div>Total Beacon Caps</div><div class="big">{total_beacons}</div></div>
</div>
<h2>Recent Matches</h2>
{display_df.to_html(index=False, escape=False)}
</body>
</html>
"""
    summary_path.write_text(html, encoding="utf-8")
    return summary_path


def clear_tracker_data(config: Dict[str, object]) -> List[Path]:
    """Remove saved tracker output without deleting screenshots or processed images."""
    output_folder = resolve_path(str(config["output_folder"]))
    paths_to_remove = [
        get_output_csv(config),
        output_folder / "summary.html",
        hashes_path(config),
    ]
    removed: List[Path] = []
    for path in paths_to_remove:
        try:
            if path.exists():
                path.unlink()
                removed.append(path)
        except Exception:
            pass
    update_summary(config)
    return removed


def process_screenshots(config: Dict[str, object], log_callback=print) -> int:
    ensure_folders(config)
    configure_tesseract(config)
    if not tesseract_works():
        raise RuntimeError("Tesseract OCR is not installed or was not found.")

    screenshot_folder = resolve_path(str(config["screenshot_folder"]))
    processed_folder = resolve_path(str(config["processed_folder"]))
    duplicates_folder = resolve_path(str(config.get("duplicates_folder", "duplicates")))
    needs_review_folder = resolve_path(str(config.get("needs_review_folder", "needs_review")))
    crops_folder = resolve_path(str(config.get("crops_folder", "Crops")))
    crops_folder.mkdir(parents=True, exist_ok=True)
    global DEBUG_CROPS_FOLDER
    DEBUG_CROPS_FOLDER = crops_folder
    csv_path = get_output_csv(config)
    player_name = str(config.get("player_name", "")).strip()
    processed_hashes = load_processed_hashes(config)

    files = sorted([p for p in screenshot_folder.iterdir() if p.suffix.lower() in SUPPORTED_IMAGES])
    count = 0
    for image_path in files:
        image_hash = file_sha256(image_path)
        if already_logged(csv_path, image_path.name) or image_hash in processed_hashes:
            moved_to = move_unique_file(image_path, duplicates_folder)
            log_callback(f"Duplicate moved to duplicates: {moved_to.name}")
            continue

        log_callback(f"Reading: {image_path.name}")
        # V5 avoids OCRing the entire screenshot first because it is slower and less accurate.
        # The adaptive parser reads only the top banner and the player row.
        text = ""
        parsed = parse_stats(image_path, text, player_name, None)
        if parsed.needs_review or not any([parsed.honor_points, parsed.damage_healing, parsed.assists, parsed.kills]):
            moved_to = move_unique_file(image_path, needs_review_folder)
            reason = parsed.review_reason or "could_not_read_stats"
            log_callback(f"Needs review moved to needs_review: {moved_to.name} ({reason})")
            continue
        if not parsed.player_name or parsed.player_name == "":
            parsed.player_name = player_name or "N/A"
        mtime = datetime.fromtimestamp(image_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        append_row(csv_path, {
            "Date Processed": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Screenshot Date": mtime,
            "Result": parsed.result,
            "Player Name": parsed.player_name,
            "Team Place": parsed.team_place,
            "Honor Points": parsed.honor_points,
            "Damage + Healing": parsed.damage_healing,
            "Assists": parsed.assists,
            "Kills": parsed.kills,
            "Beacons": parsed.beacons or "N/A",
            "Screenshot File": image_path.name,
        })
        processed_hashes[image_hash] = image_path.name
        save_processed_hashes(config, processed_hashes)
        count += 1
        if bool(config.get("move_processed_files", True)):
            move_unique_file(image_path, processed_folder)
    update_summary(config)
    return count


class TrackerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("900x700")
        self.config_data = load_config()
        ensure_folders(self.config_data)
        configure_tesseract(self.config_data)
        self.watch_running = False
        self.watch_thread = None
        self.create_widgets()
        self.refresh_status()

    def create_widgets(self):
        pad = {"padx": 12, "pady": 6}
        title = ttk.Label(self, text=f"{APP_NAME} - Version 19 Debug", font=("Arial", 18, "bold"))
        title.pack(anchor="w", **pad)

        setup = ttk.LabelFrame(self, text="One-time setup")
        setup.pack(fill="x", **pad)

        ttk.Label(setup, text="Your exact in-game name:").grid(row=0, column=0, sticky="w", **pad)
        self.player_var = tk.StringVar(value=str(self.config_data.get("player_name", "")))
        ttk.Entry(setup, textvariable=self.player_var, width=45).grid(row=0, column=1, sticky="ew", **pad)

        ttk.Label(setup, text="Manual row for Learn Name only (1-6):").grid(row=1, column=0, sticky="w", **pad)
        self.manual_place_var = tk.StringVar(value=str(self.config_data.get("manual_team_place", "")))
        ttk.Entry(setup, textvariable=self.manual_place_var, width=10).grid(row=1, column=1, sticky="w", **pad)

        ttk.Label(setup, text="Tracker data folder:").grid(row=2, column=0, sticky="w", **pad)
        self.tracker_dir_var = tk.StringVar(value=str(resolve_path(str(self.config_data.get("tracker_data_folder", DEFAULT_TRACKER_DIR)))))
        ttk.Entry(setup, textvariable=self.tracker_dir_var, width=60).grid(row=2, column=1, sticky="ew", **pad)
        ttk.Button(setup, text="Choose folder", command=self.choose_tracker_folder).grid(row=2, column=2, **pad)

        ttk.Label(setup, text="Screenshot folder:").grid(row=3, column=0, sticky="w", **pad)
        self.folder_var = tk.StringVar(value=str(resolve_path(str(self.config_data.get("screenshot_folder", str(DEFAULT_TRACKER_DIR / "screenshots"))))))
        ttk.Entry(setup, textvariable=self.folder_var, width=60).grid(row=3, column=1, sticky="ew", **pad)
        ttk.Button(setup, text="Choose folder", command=self.choose_folder).grid(row=3, column=2, **pad)

        ttk.Label(setup, text="Tesseract OCR path:").grid(row=4, column=0, sticky="w", **pad)
        self.tess_var = tk.StringVar(value=str(self.config_data.get("tesseract_path", "")))
        ttk.Entry(setup, textvariable=self.tess_var, width=60).grid(row=4, column=1, sticky="ew", **pad)
        ttk.Button(setup, text="Find file", command=self.choose_tesseract).grid(row=4, column=2, **pad)
        setup.columnconfigure(1, weight=1)

        actions = ttk.LabelFrame(self, text="Main buttons")
        actions.pack(fill="x", **pad)
        ttk.Button(actions, text="Save setup", command=self.save_setup).grid(row=0, column=0, **pad)
        ttk.Button(actions, text="Learn name from screenshot", command=self.learn_name).grid(row=0, column=1, **pad)
        ttk.Button(actions, text="Process screenshots now", command=self.process_now).grid(row=0, column=2, **pad)
        self.watch_btn = ttk.Button(actions, text="Start auto-watch", command=self.toggle_watch)
        self.watch_btn.grid(row=0, column=3, **pad)
        ttk.Button(actions, text="Open match_log.csv", command=self.open_csv).grid(row=0, column=4, **pad)
        ttk.Button(actions, text="Open summary", command=self.open_summary).grid(row=0, column=5, **pad)
        ttk.Button(actions, text="Clear data", command=self.clear_data).grid(row=0, column=6, **pad)
        ttk.Button(actions, text="Open data folder", command=self.open_data_folder).grid(row=0, column=7, **pad)

        help_box = ttk.LabelFrame(self, text="Status")
        help_box.pack(fill="x", **pad)
        self.status_var = tk.StringVar(value="")
        ttk.Label(help_box, textvariable=self.status_var, wraplength=840).pack(anchor="w", **pad)

        log_frame = ttk.LabelFrame(self, text="Activity log")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(log_frame, height=16, wrap="word")
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)

        bottom = ttk.Label(self, text="V18: Portable EXE storage + improved stat parsing. Folders are created beside the app by default, but you can choose another folder.")
        bottom.pack(anchor="w", **pad)

    def log(self, message: str):
        self.log_text.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n")
        self.log_text.see("end")
        self.update_idletasks()

    def choose_tracker_folder(self):
        folder = filedialog.askdirectory(title="Choose where War Robots Tracker should store its folders")
        if folder:
            root = Path(folder)
            self.tracker_dir_var.set(str(root))
            self.folder_var.set(str(root / "screenshots"))
            self.config_data["tracker_data_folder"] = str(root)
            self.config_data["screenshot_folder"] = str(root / "screenshots")
            self.config_data["processed_folder"] = str(root / "processed")
            self.config_data["duplicates_folder"] = str(root / "duplicates")
            self.config_data["needs_review_folder"] = str(root / "needs_review")
            self.config_data["crops_folder"] = str(root / "Crops")
            self.config_data["output_folder"] = str(root / "output")
            self.config_data["user_chose_tracker_folder"] = True
            self.config_data["storage_mode"] = "portable_app_folder"
            save_config(self.config_data)
            ensure_folders(self.config_data)
            self.refresh_status()
            self.log("Tracker data folder updated.")

    def choose_folder(self):
        folder = filedialog.askdirectory(title="Choose your War Robots screenshot folder")
        if folder:
            self.folder_var.set(folder)

    def choose_tesseract(self):
        filetypes = [("Tesseract executable", "tesseract.exe"), ("All files", "*")]
        path = filedialog.askopenfilename(title="Choose tesseract executable", filetypes=filetypes)
        if path:
            self.tess_var.set(path)

    def save_setup(self):
        self.config_data["player_name"] = self.player_var.get().strip()
        manual_place = self.manual_place_var.get().strip()
        if manual_place and (not manual_place.isdigit() or not (1 <= int(manual_place) <= 6)):
            messagebox.showerror("Invalid row number", "Manual team row must be blank or a number from 1 to 6.")
            return
        self.config_data["manual_team_place"] = manual_place
        tracker_root = Path(self.tracker_dir_var.get().strip() or str(DEFAULT_TRACKER_DIR)).expanduser()
        self.config_data["tracker_data_folder"] = str(tracker_root)
        self.config_data["screenshot_folder"] = self.folder_var.get().strip() or str(tracker_root / "screenshots")
        self.config_data["processed_folder"] = str(tracker_root / "processed")
        self.config_data["duplicates_folder"] = str(tracker_root / "duplicates")
        self.config_data["needs_review_folder"] = str(tracker_root / "needs_review")
        self.config_data["crops_folder"] = str(tracker_root / "Crops")
        self.config_data["output_folder"] = str(tracker_root / "output")
        self.config_data["tesseract_path"] = self.tess_var.get().strip()
        self.config_data["storage_mode"] = "portable_app_folder"
        save_config(self.config_data)
        ensure_folders(self.config_data)
        configure_tesseract(self.config_data)
        self.refresh_status()
        self.log("Setup saved.")

    def refresh_status(self):
        ok = tesseract_works()
        csv_path = get_output_csv(self.config_data)
        msg = "OCR ready." if ok else "OCR not found. Use Find file and choose tesseract.exe, then save setup."
        self.status_var.set(f"{msg}\nOutput file: {csv_path}")

    def learn_name(self):
        self.save_setup()
        filetypes = [("Image files", "*.png *.jpg *.jpeg *.webp *.bmp *.tiff"), ("All files", "*")]
        path = filedialog.askopenfilename(
            title="Choose a clear War Robots result screenshot",
            initialdir=self.folder_var.get().strip() or str(DEFAULT_TRACKER_DIR),
            filetypes=filetypes,
        )
        if not path:
            return
        try:
            configure_tesseract(self.config_data)
            if not tesseract_works():
                raise RuntimeError("Tesseract OCR is not ready. Use Find file and choose tesseract.exe first.")
            detected_place = guess_place_from_image(Path(path))
            default_place = str(detected_place or self.manual_place_var.get().strip() or "")
            place_text = simpledialog.askstring(
                "Confirm team row",
                "Which row should I read?\n\n1 = top ally row, 6 = bottom ally row.\n\nAuto-detected row: " + (str(detected_place) if detected_place else "not found") + "\n\nType the correct row number:",
                initialvalue=default_place,
                parent=self,
            )
            if place_text is None:
                self.log("Learn name canceled before saving.")
                return
            place_text = place_text.strip()
            if not place_text.isdigit() or not (1 <= int(place_text) <= 6):
                raise RuntimeError("Team row must be a number from 1 to 6.")
            selected_place = int(place_text)

            try:
                learned_name, place = learn_player_name_from_image(Path(path), forced_place=selected_place)
            except Exception:
                learned_name = ""
                place = selected_place

            manual_name = simpledialog.askstring(
                "Confirm or type player name",
                "I read this name from the selected row. Edit it if needed.\n\nNumbers, spaces, symbols, and non-English characters are allowed.",
                initialvalue=learned_name,
                parent=self,
            )
            if manual_name is None:
                self.log("Learn name canceled before saving.")
                return
            manual_name = manual_name.strip()
            if not manual_name:
                raise RuntimeError("No player name was entered, so nothing was saved.")

            if messagebox.askyesno("Confirm player name", f"Save this as your player name?\n\n{manual_name}\n\nLearning row used: {place}"):
                self.player_var.set(manual_name)
                self.manual_place_var.set(str(place))
                self.config_data["player_name"] = manual_name
                self.config_data["manual_team_place"] = str(place)
                save_config(self.config_data)
                self.refresh_status()
                self.log(f"Saved player name: {manual_name} (learn row {place})")
            else:
                self.log("Player name was not saved.")
        except Exception as e:
            self.log(f"Learn name error: {e}")
            messagebox.showerror("Learn name error", str(e))

    def process_now(self):
        self.save_setup()
        def run():
            try:
                count = process_screenshots(self.config_data, self.log)
                self.log(f"Done. Added {count} new match(es).")
                self.refresh_status()
            except Exception as e:
                self.log(f"Error: {e}")
                messagebox.showerror("Tracker error", str(e))
        threading.Thread(target=run, daemon=True).start()

    def toggle_watch(self):
        if self.watch_running:
            self.watch_running = False
            self.watch_btn.config(text="Start auto-watch")
            self.log("Auto-watch stopped.")
            return
        self.save_setup()
        self.watch_running = True
        self.watch_btn.config(text="Stop auto-watch")
        self.watch_thread = threading.Thread(target=self.watch_loop, daemon=True)
        self.watch_thread.start()
        self.log("Auto-watch started.")

    def watch_loop(self):
        while self.watch_running:
            try:
                count = process_screenshots(self.config_data, self.log)
                if count:
                    self.log(f"Auto-watch added {count} match(es).")
            except Exception as e:
                self.log(f"Auto-watch error: {e}")
            time.sleep(int(self.config_data.get("auto_watch_seconds", 30)))

    def clear_data(self):
        warning = (
            "This will clear your saved tracker data, including:\n\n"
            "- match_log.csv\n"
            "- summary.html\n"
            "- duplicate tracking history\n\n"
            "Your screenshots, processed images, and duplicate images will NOT be deleted.\n\n"
            "Are you sure you want to continue?"
        )
        if not messagebox.askyesno("Confirm clear data", warning, icon="warning"):
            self.log("Clear data canceled.")
            return
        try:
            self.save_setup()
            removed = clear_tracker_data(self.config_data)
            self.refresh_status()
            if removed:
                self.log(f"Cleared tracker data ({len(removed)} file(s) removed).")
            else:
                self.log("Tracker data was already empty.")
            messagebox.showinfo("Data cleared", "Tracker data has been cleared. Your screenshots were not deleted.")
        except Exception as e:
            self.log(f"Clear data error: {e}")
            messagebox.showerror("Clear data error", str(e))

    def open_data_folder(self):
        ensure_folders(self.config_data)
        open_file(resolve_path(str(self.config_data.get("tracker_data_folder", DEFAULT_TRACKER_DIR))))

    def open_csv(self):
        path = get_output_csv(self.config_data)
        if not path.exists():
            messagebox.showinfo("No CSV yet", "Process screenshots first to create match_log.csv.")
            return
        open_file(path)

    def open_summary(self):
        path = update_summary(self.config_data)
        webbrowser.open(path.as_uri())


def open_file(path: Path):
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


if __name__ == "__main__":
    app = TrackerApp()
    app.mainloop()
