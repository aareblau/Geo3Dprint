#!/usr/bin/env python3
"""Gefuehrter Modus fuer Geo3Dprint mit reduzierten Einstellungen."""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import requests

from geo_input import (
    GeocodingError,
    MAX_SIDE_M,
    _parse_manual_coords,
    _wgs84_to_lv95,
    get_coords,
    validate_bbox_size,
)
from terrain_pipeline import (
    alloc_grid,
    err,
    fetch_tile_bytes,
    log,
    paste_tile,
    read_tile,
    search_tiles,
    write_stl_binary,
)


COLLECTION = "ch.swisstopo.swisssurface3d-raster"
MAP_URL = "https://map.geo.admin.ch/"
REFERENCE_RES_M = 0.5
DEFAULT_WIDTH = 1000.0
DEFAULT_HEIGHT = 1000.0
Z_EX = 1.0
BASE_THICKNESS_MM = 3.0
FIXED_INTERP = "bilinear"
MODEL_LONG_SIDE_MM = 100.0
PLANAR_MERGE = True
PLANAR_TOLERANCE = None
PLANAR_MAX_SECONDS = 0.0
MERGE_WORKERS = 0
WALL_MERGE = True
WALL_SLOPE_THRESHOLD = 1.5
HELP_WORDS = {"?", "h", "hilfe", "help"}
CANCEL_WORDS = {"abbrechen", "stop", "stopp", "quit", "q", "exit", "ende"}
YES_WORDS = {"", "j", "ja", "y", "yes", "start", "los", "ok"}
NO_WORDS = {"n", "nein", "no", "zurueck", "back", "aendern"}
LENGTH_ALIASES_M = {
    "klein": 500.0,
    "kurz": 500.0,
    "normal": DEFAULT_WIDTH,
    "standard": DEFAULT_WIDTH,
    "mittel": DEFAULT_WIDTH,
    "gross": 2000.0,
    "lang": 2000.0,
}

# 1000 m x 1000 m mit 0.5 m Raster ergibt 4'000'000 Rasterpunkte.
# Groessere Flaechen bekommen automatisch eine groebere Aufloesung.
REFERENCE_AREA_M2 = DEFAULT_WIDTH * DEFAULT_HEIGHT
MAX_RASTER_POINTS = REFERENCE_AREA_M2 / (REFERENCE_RES_M * REFERENCE_RES_M)


class UserAbort(Exception):
    """Raised when the user intentionally leaves the guided assistant."""


@dataclass
class MapWindow:
    """Browser process opened only for the coordinate selection map."""

    process: subprocess.Popen | None = None
    profile_dir: Path | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _finished: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        with self._lock:
            process = self.process
        if process is None:
            _maximize_console_window()
            return
        print("Schliesse Kartenfenster.")
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self._finish()

    def start_monitor(self) -> None:
        if self.process is None:
            return
        thread = threading.Thread(target=self._wait_for_browser, daemon=True)
        thread.start()

    def _wait_for_browser(self) -> None:
        with self._lock:
            process = self.process
        if process is None:
            return
        process.wait()
        self._finish()

    def _finish(self) -> None:
        with self._lock:
            if self._finished:
                return
            self._finished = True
            profile_dir = self.profile_dir
            self.process = None
            self.profile_dir = None
        if profile_dir is not None:
            shutil.rmtree(profile_dir, ignore_errors=True)
        _maximize_console_window()


def _windows_work_area() -> tuple[int, int, int, int] | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT()
        spi_getworkarea = 0x0030
        if not ctypes.windll.user32.SystemParametersInfoW(
            spi_getworkarea, 0, ctypes.byref(rect), 0
        ):
            return None
        return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
    except Exception:
        return None


def _move_console_window(*, left: int, top: int, width: int, height: int) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if not hwnd:
            return
        sw_restore = 9
        ctypes.windll.user32.ShowWindow(hwnd, sw_restore)
        ctypes.windll.user32.MoveWindow(hwnd, left, top, width, height, True)
    except Exception:
        return


def _position_console_right() -> None:
    area = _windows_work_area()
    if area is None:
        return
    left, top, width, height = area
    half_width = max(400, width // 2)
    _move_console_window(
        left=left + half_width,
        top=top,
        width=width - half_width,
        height=height,
    )


def _maximize_console_window() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if not hwnd:
            return
        sw_maximize = 3
        ctypes.windll.user32.ShowWindow(hwnd, sw_maximize)
    except Exception:
        return


def _browser_left_window_args() -> list[str]:
    area = _windows_work_area()
    if area is None:
        return []
    left, top, width, height = area
    half_width = max(400, width // 2)
    return [
        f"--window-position={left},{top}",
        f"--window-size={half_width},{height}",
    ]


def _fmt(value: float) -> str:
    text = f"{value:.6f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _parse_length_m(raw: str) -> float:
    cleaned = raw.strip().lower()
    cleaned = cleaned.replace("'", "").replace("_", "").replace(",", ".")
    cleaned = cleaned.replace(" ", "")
    if cleaned in LENGTH_ALIASES_M:
        return LENGTH_ALIASES_M[cleaned]
    cleaned = cleaned.replace("meter", "m").replace("metern", "m")
    cleaned = cleaned.replace("kilometer", "km").replace("kilometern", "km")
    if cleaned.endswith("km"):
        return float(cleaned[:-2]) * 1000.0
    if cleaned.endswith("m"):
        return float(cleaned[:-1])
    return float(cleaned)


def _clean_number_token(token: str) -> str:
    cleaned = token.strip().lower()
    cleaned = re.sub(r"^[enxyostnordwest]+[:=]?", "", cleaned)
    cleaned = cleaned.replace("'", "").replace("`", "").replace("_", "")
    cleaned = cleaned.replace("’", "").replace("‘", "").replace("´", "")
    cleaned = cleaned.replace(" ", "")
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif cleaned.count(",") == 1:
        cleaned = cleaned.replace(",", ".")
    elif cleaned.count(",") > 1:
        cleaned = cleaned.replace(",", "")
    if cleaned.count(".") > 1:
        parts = cleaned.split(".")
        cleaned = "".join(parts[:-1]) + "." + parts[-1]
    return cleaned


def _number_candidates(raw: str) -> list[float]:
    tokens = re.findall(r"[-+]?\d(?:[\d'`’‘´_.]*\d)?(?:[,.]\d+)?", raw)
    values = []
    for token in tokens:
        try:
            values.append(float(_clean_number_token(token)))
        except ValueError:
            continue
    return values


def _parse_coords_forgiving(raw: str) -> tuple[float, float] | None:
    coords = _parse_manual_coords(raw)
    if coords:
        return coords

    normalized = raw.strip()
    normalized = normalized.replace(";", " ").replace("|", " ").replace("/", " ")
    normalized = normalized.replace("(", " ").replace(")", " ")
    parts = [part for part in normalized.split() if part]
    if len(parts) == 2:
        try:
            first = float(_clean_number_token(parts[0]))
            second = float(_clean_number_token(parts[1]))
        except ValueError:
            return None
        if first > 2_000_000 and second > 1_000_000:
            return first, second
        if abs(first) <= 90 and abs(second) <= 180:
            return _wgs84_to_lv95(first, second)

    comma_parts = [part.strip() for part in raw.split(",") if part.strip()]
    if len(comma_parts) == 2:
        try:
            first = float(_clean_number_token(comma_parts[0]))
            second = float(_clean_number_token(comma_parts[1]))
        except ValueError:
            pass
        else:
            if first > 2_000_000 and second > 1_000_000:
                return first, second
            if abs(first) <= 90 and abs(second) <= 180:
                return _wgs84_to_lv95(first, second)

    values = _number_candidates(raw)
    for first, second in zip(values, values[1:]):
        if first > 2_000_000 and second > 1_000_000:
            return first, second
    for first, second in zip(values, values[1:]):
        if abs(first) <= 90 and abs(second) <= 180:
            return _wgs84_to_lv95(first, second)

    return None


def _browser_candidates() -> list[Path]:
    paths = []
    names = ["msedge", "chrome", "brave"]
    for name in names:
        found = shutil.which(name)
        if found:
            paths.append(Path(found))

    if sys.platform == "win32":
        roots = [
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        ]
        relative_paths = [
            ("Microsoft", "Edge", "Application", "msedge.exe"),
            ("Google", "Chrome", "Application", "chrome.exe"),
            ("BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
        ]
        for root in roots:
            if not root:
                continue
            for relative in relative_paths:
                paths.append(Path(root, *relative))

    unique_paths = []
    seen = set()
    for path in paths:
        key = str(path).lower()
        if key not in seen and path.exists():
            unique_paths.append(path)
            seen.add(key)
    return unique_paths


def _open_map() -> MapWindow:
    print(f"Oeffne Karte im Browser: {MAP_URL}")
    _position_console_right()
    for browser_path in _browser_candidates():
        profile_dir = Path(tempfile.mkdtemp(prefix="geo3dprint-map-"))
        args = [
            str(browser_path),
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            *_browser_left_window_args(),
            f"--app={MAP_URL}",
        ]
        try:
            process = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            shutil.rmtree(profile_dir, ignore_errors=True)
            continue
        map_window = MapWindow(process=process, profile_dir=profile_dir)
        map_window.start_monitor()
        return map_window

    try:
        opened = webbrowser.open(MAP_URL, new=2)
    except Exception as exc:
        print(f"Die Karte konnte nicht automatisch geoeffnet werden: {exc}")
        print(f"Oeffne sie bei Bedarf manuell: {MAP_URL}")
        return MapWindow()
    if not opened:
        print(f"Falls kein Browser aufgeht, oeffne diese Adresse manuell: {MAP_URL}")
    else:
        print("Hinweis: Dieses Browserfenster kann nicht automatisch geschlossen werden.")
    return MapWindow()


def _open_model(path: str) -> None:
    model_path = Path(path).resolve()
    print()
    print(f"Oeffne STL-Modell: {model_path}")
    try:
        os.startfile(model_path)  # type: ignore[attr-defined]
    except AttributeError:
        print("Automatisches Oeffnen wird auf diesem Betriebssystem nicht unterstuetzt.")
    except OSError as exc:
        print(f"Das Modell konnte nicht automatisch geoeffnet werden: {exc}")
        print("Oeffne die STL-Datei manuell mit dem 3D Viewer oder einem Slicer.")


def _print_map_help() -> None:
    print()
    print("Koordinaten aus der Karte verwenden")
    print("-" * 35)
    print("1. Suche in der Karte einen beliebigen Ort.")
    print("2. Klicke dort mit der rechten Maustaste auf die Karte.")
    print("3. Es erscheinen Koordinaten.")
    print("4. Wenn du diese Koordinaten verwenden willst:")
    print("   Kopiere das oberste Koordinatenpaar und fuege es hier ein.")
    print()
    print("Beispiel fuer so ein Koordinatenpaar:")
    print("  2'620'760.50, 1'158'843.35")
    print()


def _print_intro() -> None:
    print()
    print("Geo3Dprint gefuehrter Modus")
    print("=" * 29)
    print("Dieses Programm erstellt eine STL-Datei aus Schweizer Hoehendaten.")
    print("Du musst nichts ueber die Kommandozeile wissen.")
    print()
    print("Du gibst nur vier Dinge ein:")
    print("  1. Ort oder Koordinaten")
    print("  2. Breite der Flaeche")
    print("  3. Hoehe der Flaeche")
    print("  4. Name der STL-Datei")
    print()
    print("Tipp: Du kannst jederzeit 'hilfe' eingeben.")
    print("Zum Abbrechen: 'abbrechen' eingeben oder Ctrl+C druecken.")
    print()


def _print_location_help() -> None:
    print()
    print("Ort oder Koordinaten eingeben:")
    print("  - Ortsname: Bern")
    print("  - Berg/Ort: Matterhorn")
    print("  - LV95: 2620760.50 1158843.35")
    print("  - LV95 mit Apostroph: 2'620'760.50, 1'158'843.35")
    print("  - Aus geo.admin kopiert: oberstes Koordinatenpaar einfuegen")
    print("  - WGS84: 46.948 7.447")
    print()


def _print_length_help() -> None:
    print()
    print("Seitenlaenge eingeben:")
    print("  - 1000")
    print("  - 1000m")
    print("  - 1km")
    print("  - klein, mittel oder gross")
    print()
    print(f"Maximal erlaubt sind {_fmt(MAX_SIDE_M)} m pro Seite.")
    print("Diese Eingabe wird fuer Breite und Hoehe gleich gelesen.")
    print()


def _safe_model_filename(raw_name: str) -> str | None:
    name = raw_name.strip()
    if not name:
        return None
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name)
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"_+", "_", name)
    name = name.strip(" ._")
    if not name:
        return None
    if not name.lower().endswith(".stl"):
        name = f"{name}.stl"
    stem = name[:-4].rstrip(" ._")
    reserved_names = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }
    if stem.upper() in reserved_names:
        name = f"{stem}_modell.stl"
    return name


def _desktop_output_path(model_name: str) -> Path:
    filename = _safe_model_filename(model_name)
    if filename is None:
        raise ValueError("Der Modellname darf nicht leer sein.")
    desktop = _desktop_dir()
    desktop.mkdir(parents=True, exist_ok=True)
    return desktop / filename


def _desktop_dir() -> Path:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            ) as key:
                value, _kind = winreg.QueryValueEx(key, "Desktop")
            return Path(os.path.expandvars(value))
        except OSError:
            pass
    return Path.home() / "Desktop"


def _prompt_model_output_path() -> Path:
    while True:
        raw = input("Schritt 4/4 - Name der STL-Datei: ").strip()
        lowered = raw.lower()
        if lowered in CANCEL_WORDS:
            raise UserAbort
        if lowered in HELP_WORDS:
            print()
            print("Gib einen Namen fuer die fertige STL-Datei ein.")
            print("Beispiel: Matterhorn oder Berner_Oberland.stl")
            print("Die Datei wird auf dem Desktop gespeichert.")
            print()
            continue
        try:
            out_path = _desktop_output_path(raw)
        except ValueError as exc:
            print()
            print(f"{exc}")
            print("Bitte gib einen Namen ein, z. B. Matterhorn.")
            continue
        if out_path.exists():
            print()
            print(f"Diese Datei gibt es bereits: {out_path}")
            print("Bitte gib einen anderen Modellnamen ein.")
            continue
        return out_path


def _prompt_length_m(
    prompt: str,
    *,
    default: float,
    min_value: float,
    max_value: float = MAX_SIDE_M,
) -> float:
    while True:
        raw = input(f"{prompt} [{_fmt(default)}]: ").strip()
        if not raw:
            return default
        lowered = raw.lower()
        if lowered in CANCEL_WORDS:
            raise UserAbort
        if lowered in HELP_WORDS:
            _print_length_help()
            continue
        try:
            value = _parse_length_m(raw)
        except ValueError:
            print()
            print("Das konnte ich nicht als Laenge lesen.")
            print("Gib z. B. 1000, 1000m, 1km, klein, mittel oder gross ein.")
            print("Mit 'hilfe' bekommst du Beispiele.")
            continue
        if value < min_value:
            print()
            print(f"Das ist zu kurz. Bitte mindestens {_fmt(min_value)} m eingeben.")
            continue
        if value > max_value:
            print()
            print(
                "Das ist zu gross. Bitte maximal "
                f"{_fmt(max_value)} m pro Seite eingeben."
            )
            continue
        return value


def _prompt_location() -> tuple[float, float, str]:
    while True:
        raw = input(
            "Schritt 1/4 - Ort oder Koordinaten "
            "(Enter zeigt Beispiele): "
        ).strip()
        if not raw:
            _print_location_help()
            continue
        lowered = raw.lower()
        if lowered in CANCEL_WORDS:
            raise UserAbort
        if lowered in HELP_WORDS:
            _print_location_help()
            continue

        coords = _parse_coords_forgiving(raw)
        if coords:
            easting, northing = coords
            return easting, northing, f"Koordinaten E={_fmt(easting)}, N={_fmt(northing)}"

        try:
            easting, northing, label = get_coords(raw)
            return easting, northing, label
        except GeocodingError as exc:
            print()
            print(f"{exc}")
            print("Versuche einen naeheren Namen, z. B. 'Bern Bahnhof'.")
            print("Oder gib Koordinaten ein. Mit 'hilfe' bekommst du Beispiele.\n")
        except requests.RequestException as exc:
            print()
            print(f"Die Ortssuche konnte gerade nicht erreicht werden: {exc}")
            print("Du kannst stattdessen direkt LV95-Koordinaten eingeben.\n")


def _confirm_start(
    *,
    label: str,
    width_m: float,
    height_m: float,
    resolution_m: float,
    scale_denominator: float,
    out_path: Path,
) -> bool:
    print()
    print("Zusammenfassung")
    print("-" * 15)
    print(f"Ort/Punkt:       {label}")
    print(f"Flaeche:         {_fmt(width_m)} m x {_fmt(height_m)} m")
    print("Modellgroesse:   laengere Seite 10 cm")
    print(f"Massstab:        1:{_fmt(scale_denominator)}")
    print(f"Aufloesung:      {_fmt(resolution_m)} m/Pixel")
    print(f"Ausgabe:         {out_path}")
    print()
    while True:
        raw = input("Jetzt STL erstellen? [Enter = ja, nein = Eingabe aendern]: ")
        lowered = raw.strip().lower()
        if lowered in CANCEL_WORDS:
            raise UserAbort
        if lowered in YES_WORDS:
            return True
        if lowered in NO_WORDS:
            return False
        print("Bitte Enter fuer ja oder 'nein' zum Aendern eingeben.")


def _resolution_for_size(width_m: float, height_m: float) -> float:
    area_m2 = width_m * height_m
    resolution = (area_m2 / MAX_RASTER_POINTS) ** 0.5
    return max(REFERENCE_RES_M, resolution)


def _scale_for_size(width_m: float, height_m: float) -> float:
    return max(width_m, height_m) * 1000.0 / MODEL_LONG_SIDE_MM


def _bbox_from_center(
    easting: float, northing: float, width_m: float, height_m: float
) -> tuple[float, float, float, float]:
    half_width = width_m / 2.0
    half_height = height_m / 2.0
    return (
        easting - half_width,
        northing - half_height,
        easting + half_width,
        northing + half_height,
    )


def _write_model(
    bbox: tuple[float, float, float, float],
    *,
    resolution_m: float,
    scale_denominator: float,
    label: str,
    center: tuple[float, float],
    out_path: Path,
) -> str:
    start = time.time()
    urls = search_tiles(*bbox, COLLECTION)

    center_e = (bbox[0] + bbox[2]) / 2.0
    center_n = (bbox[1] + bbox[3]) / 2.0
    width_m = bbox[2] - bbox[0]
    height_m = bbox[3] - bbox[1]
    log(f"Berechneter Mittelpunkt: E={_fmt(center_e)}, N={_fmt(center_n)}")
    log(
        f"Zielgroesse: {width_m:.2f} m x {height_m:.2f} m "
        f"(1:{_fmt(scale_denominator)}, Einheit mm)"
    )
    log(
        f"Abweichung zum gewaehlten Punkt: "
        f"dE={_fmt(center_e - center[0])} m, dN={_fmt(center_n - center[1])} m"
    )

    with requests.Session() as session:
        log(f"[1/{len(urls)}] Lade Tile: {urls[0]}")
        first_tile_bytes = fetch_tile_bytes(urls[0], session=session, timeout=120)
        first_arr, first_meta = read_tile(first_tile_bytes)
        is_area = first_meta.get("pixel_is_area", True)
        anchor_e = first_meta["x0"] + (0.5 * first_meta["sx"] if is_area else 0.0)
        anchor_n = first_meta["y0"] - (0.5 * first_meta["sy"] if is_area else 0.0)
        dem, mask, origin_e, origin_n = alloc_grid(
            *bbox, resolution_m, anchor=(anchor_e, anchor_n)
        )
        taken = paste_tile(
            dem,
            mask,
            first_meta,
            first_arr,
            bbox,
            origin_e,
            origin_n,
            resolution_m,
            mode=FIXED_INTERP,
        )
        log(f"    uebernommen: {taken} Zellen")

        for idx, url in enumerate(urls[1:], 2):
            log(f"[{idx}/{len(urls)}] Lade Tile: {url}")
            tile_bytes = fetch_tile_bytes(url, session=session, timeout=120)
            arr, meta = read_tile(tile_bytes)
            taken = paste_tile(
                dem,
                mask,
                meta,
                arr,
                bbox,
                origin_e,
                origin_n,
                resolution_m,
                mode=FIXED_INTERP,
            )
            log(f"    uebernommen: {taken} Zellen")

    coverage = int(mask.sum())
    total = dem.size
    log(f"Abdeckung: {coverage}/{total} = {coverage * 100 / total:.1f}%")
    if coverage == 0:
        raise ValueError("Keine Pixel befuellt - pruefe BBox/Collection.")

    log("Bereinige fehlende Rasterwerte ...")
    z_min = float(np.nanmin(dem))
    dem = np.nan_to_num(dem, nan=z_min)

    log("Skaliere Rasterkoordinaten und Hoehen fuer STL-Ausgabe ...")
    height, width = dem.shape
    x_coords = origin_e + np.arange(width, dtype=np.float32) * resolution_m
    y_coords = origin_n - np.arange(height, dtype=np.float32) * resolution_m
    xx, yy = np.meshgrid(x_coords, y_coords)
    zz = (dem - z_min) * Z_EX
    scale_factor = 1000.0 / scale_denominator
    xx = xx * scale_factor
    yy = yy * scale_factor
    zz = zz * scale_factor
    base = BASE_THICKNESS_MM

    log("Starte STL-Export ...")
    final_out = write_stl_binary(
        str(out_path),
        xx,
        yy,
        zz,
        base_thickness=base,
        planar_merge=PLANAR_MERGE,
        planar_tolerance=PLANAR_TOLERANCE,
        planar_max_seconds=PLANAR_MAX_SECONDS,
        merge_workers=MERGE_WORKERS,
        wall_merge=WALL_MERGE,
        wall_slope_threshold=WALL_SLOPE_THRESHOLD,
    )
    log(f"Fertig: {final_out} ({label}) (Dauer {time.time() - start:.1f}s)")
    return final_out


def main() -> int:
    _print_intro()
    map_window = _open_map()
    _print_map_help()

    try:
        while True:
            easting, northing, label = _prompt_location()
            print(f"Gefunden: {label}\n")
            print("Hinweis: Je kleiner der Ausschnitt, desto besser wird die Detailqualitaet.")
            print("Waehle also nur so viel Umgebung, wie du wirklich brauchst.\n")

            width_m = _prompt_length_m(
                f"Schritt 2/4 - Breite der Flaeche (max. {_fmt(MAX_SIDE_M)} m)",
                default=DEFAULT_WIDTH,
                min_value=10.0,
            )
            height_m = _prompt_length_m(
                f"Schritt 3/4 - Hoehe der Flaeche (max. {_fmt(MAX_SIDE_M)} m)",
                default=DEFAULT_HEIGHT,
                min_value=10.0,
            )
            out_path = _prompt_model_output_path()

            resolution_m = _resolution_for_size(width_m, height_m)
            scale_denominator = _scale_for_size(width_m, height_m)
            bbox = _bbox_from_center(easting, northing, width_m, height_m)
            validate_bbox_size(bbox)

            if _confirm_start(
                label=label,
                width_m=width_m,
                height_m=height_m,
                resolution_m=resolution_m,
                scale_denominator=scale_denominator,
                out_path=out_path,
            ):
                break
            print()
            print("Kein Problem. Wir starten die Eingabe noch einmal.\n")
    finally:
        map_window.close()

    print()
    print("Starte jetzt. Das kann je nach Gebiet einige Minuten dauern.")
    print("Bitte dieses Fenster offen lassen.\n")

    final_out = _write_model(
        bbox,
        resolution_m=resolution_m,
        scale_denominator=scale_denominator,
        label=label,
        center=(easting, northing),
        out_path=out_path,
    )
    _open_model(final_out)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UserAbort:
        print()
        print("Abgebrochen. Es wurde keine neue STL-Datei erstellt.")
        sys.exit(0)
    except KeyboardInterrupt:
        print()
        print("Abgebrochen. Es wurde keine neue STL-Datei erstellt.")
        sys.exit(0)
    except ValueError as exc:
        err(str(exc))
        sys.exit(1)
    except requests.RequestException as exc:
        err(
            "Netzwerkfehler beim Laden der swisstopo-Daten. "
            "Bitte Internet/DNS pruefen und den Lauf erneut starten. "
            f"Details: {exc}"
        )
        sys.exit(1)
    except Exception:
        err("Unerwarteter Fehler")
        traceback.print_exc()
        sys.exit(1)
