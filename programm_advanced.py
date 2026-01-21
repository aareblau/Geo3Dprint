#!/usr/bin/env python3
"""
LV95-BBox -> swissSURFACE3D Raster (DSM) -> STL (Binary)

Erweiterter interaktiver oder rein parametrischer Modus fuer Geo3Dprint.
"""

import argparse
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests

from geo_input import validate_bbox, prompt_user_bbox_config
from terrain_pipeline import (
    alloc_grid,
    err,
    log,
    paste_tile,
    read_tile,
    search_tiles,
    write_stl_binary,
)

# ================ SETTINGS (zentral) =================
COLLECTION = "ch.swisstopo.swisssurface3d-raster"
OUT_PATH = "terrain.stl"
RES_M = 0.5
Z_EX = 1.0
BASE_THICKNESS = 50.0
DEFAULT_WIDTH = 1000.0
DEFAULT_HEIGHT = 1000.0
SCALE_DEFAULT = 10000.0
OUTPUT_UNIT = "m"
OUTPUT_UNIT_FACTOR = 1.0  # meters -> meters
# =====================================================


def _fmt(value: float) -> str:
    text = f"{value:.6f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def main() -> None:
    parser = argparse.ArgumentParser(
        description="BBox (LV95) -> swissSURFACE3D Raster (DSM) -> STL"
    )
    parser.add_argument("--out", default=OUT_PATH, help="STL-Ausgabedatei")
    parser.add_argument(
        "--res",
        type=float,
        default=RES_M,
        help="Rasteraufloesung in Metern/Pixel (Default fuer den interaktiven Modus)",
    )
    parser.add_argument("--zex", type=float, default=Z_EX, help="Z-Verstaerkung")
    parser.add_argument(
        "--base",
        type=float,
        default=BASE_THICKNESS,
        help="Sockelhoehe (Extrusion nach unten, Meter)",
    )
    parser.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        default=None,
        metavar=("MIN_E", "MIN_N", "MAX_E", "MAX_N"),
        help="BBox in LV95 (mindestens 2 Punkte).",
    )
    parser.add_argument("--collection", default=COLLECTION, help="STAC Collection-ID")
    parser.add_argument(
        "--interp",
        choices=["nn", "bilinear"],
        default="bilinear",
        help="Resampling (nn=Nearest, bilinear)",
    )
    parser.add_argument(
        "--no-interactive",
        dest="interactive",
        action="store_false",
        help="Deaktiviert die Abfragen und nutzt nur CLI-Parameter.",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=SCALE_DEFAULT,
        help="Modell-Massstab als Nenner (1:x, default 10000).",
    )
    parser.add_argument(
        "--unit",
        choices=["m", "mm"],
        default=OUTPUT_UNIT,
        help="Einheit fuer STL-Koordinaten (m oder mm; Default m).",
    )
    parser.add_argument(
        "--planar-merge",
        dest="planar_merge",
        action="store_true",
        help="Aktiviert das konservative Zusammenfassen kaum abweichender Rasterrechtecke.",
    )
    parser.add_argument(
        "--no-planar-merge",
        dest="planar_merge",
        action="store_false",
        help="Deaktiviert das Zusammenfassen kaum abweichender Rasterrechtecke.",
    )
    parser.add_argument(
        "--planar-tolerance",
        type=float,
        default=None,
        help="Maximale Hoehenabweichung fuer Rechteck-Merge in STL-Modell-Einheiten.",
    )
    parser.add_argument(
        "--planar-max-seconds",
        type=float,
        default=0.0,
        help="Zeitbudget fuer Rechteck-Merge; 0 bedeutet kein Zeitlimit.",
    )
    parser.add_argument(
        "--merge-workers",
        type=int,
        default=0,
        help="Worker-Threads fuer Rechteck-Merge (0 = automatisch).",
    )
    parser.add_argument(
        "--tile-workers",
        type=int,
        default=4,
        help="Parallele Downloads/Dekodierungen fuer GeoTIFF-Kacheln (1 = seriell).",
    )
    parser.add_argument(
        "--no-wall-merge",
        dest="wall_merge",
        action="store_false",
        help="Deaktiviert das Zusammenfassen steiler zusammenhaengender Wandkanten.",
    )
    parser.add_argument(
        "--wall-slope-threshold",
        type=float,
        default=1.5,
        help="Minimale Steigung dz/dxy, ab der Punktkanten als Wand gelten.",
    )
    parser.set_defaults(planar_merge=True)
    parser.set_defaults(wall_merge=True)
    parser.set_defaults(interactive=True)

    args = parser.parse_args()

    res = float(args.res)
    zex = float(args.zex)
    base = float(args.base)
    scale_denominator = float(args.scale)
    out_unit = args.unit
    planar_merge = bool(args.planar_merge)
    planar_tolerance = args.planar_tolerance
    planar_max_seconds = float(args.planar_max_seconds)
    merge_workers = int(args.merge_workers)
    tile_workers = int(args.tile_workers)
    wall_merge = bool(args.wall_merge)
    wall_slope_threshold = float(args.wall_slope_threshold)
    unit_factor = OUTPUT_UNIT_FACTOR if out_unit == "mm" else 1.0
    if res <= 0:
        raise ValueError("--res muss > 0 sein.")
    if zex <= 0:
        raise ValueError("--zex muss > 0 sein.")
    if base < 0:
        raise ValueError("--base muss >= 0 sein.")
    if scale_denominator <= 0:
        raise ValueError("--scale muss > 0 sein.")
    if planar_tolerance is not None and planar_tolerance < 0:
        raise ValueError("--planar-tolerance muss >= 0 sein.")
    if planar_max_seconds < 0:
        raise ValueError("--planar-max-seconds muss >= 0 sein.")
    if merge_workers < 0:
        raise ValueError("--merge-workers muss >= 0 sein.")
    if tile_workers <= 0:
        raise ValueError("--tile-workers muss > 0 sein.")
    if wall_slope_threshold <= 0:
        raise ValueError("--wall-slope-threshold muss > 0 sein.")

    bbox = tuple(args.bbox) if args.bbox is not None else None
    label = None
    center = None
    out_path = args.out
    interp = args.interp

    if args.interactive:
        selection = prompt_user_bbox_config(
            res,
            DEFAULT_WIDTH,
            DEFAULT_HEIGHT,
            zex,
            base,
            interp,
            out_path,
            SCALE_DEFAULT,
        )
        if selection.bbox is None:
            raise ValueError("BBox konnte nicht bestimmt werden.")
        res = selection.resolution_m
        zex = selection.z_ex
        base = selection.base_thickness
        bbox = selection.bbox
        interp = selection.interp
        out_path = selection.out_path
        label = selection.label
        center = selection.center
        scale_denominator = selection.scale_denominator
        log(
            "Eingabe: "
            f"{label or 'BBox'} "
            f"E[{_fmt(bbox[0])}, {_fmt(bbox[2])}] "
            f"N[{_fmt(bbox[1])}, {_fmt(bbox[3])}] @ {_fmt(res)} m, "
            f"Z-Skalierung {zex}, Sockel {base} m, "
            f"Resampling {interp}, Massstab 1:{scale_denominator}, "
            f"Out '{out_path}', Einheit {out_unit}"
        )
    elif bbox is None:
        raise ValueError("--bbox ist Pflicht, wenn --no-interactive gesetzt ist.")
    validate_bbox(bbox)

    start = time.time()
    log("Starte Verarbeitung ...")
    log("Suche passende swisstopo Tiles ...")
    urls = search_tiles(*bbox, args.collection)

    center_e = (bbox[0] + bbox[2]) / 2.0
    center_n = (bbox[1] + bbox[3]) / 2.0
    width_m = bbox[2] - bbox[0]
    height_m = bbox[3] - bbox[1]
    log(f"Berechneter Mittelpunkt: E={_fmt(center_e)}, N={_fmt(center_n)}")
    log(f"Zielgroesse: {width_m:.2f} m x {height_m:.2f} m (1:{scale_denominator}, Einheit {out_unit})")
    if center:
        delta_e = center_e - center[0]
        delta_n = center_n - center[1]
        log(f"Abweichung zum gewaehlten Punkt: dE={_fmt(delta_e)} m, dN={_fmt(delta_n)} m")

    def fetch_tile(item):
        idx, url = item
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        arr, meta = read_tile(response.content)
        return idx, url, arr, meta

    # Fetch first tile up front to snap the target grid to the source raster lattice.
    log(f"[1/{len(urls)}] Lade und dekodiere Tile: {urls[0]}")
    _idx, _url, first_arr, first_meta = fetch_tile((1, urls[0]))
    is_area = first_meta.get("pixel_is_area", True)
    anchor_e = first_meta["x0"] + (0.5 * first_meta["sx"] if is_area else 0.0)
    anchor_n = first_meta["y0"] - (0.5 * first_meta["sy"] if is_area else 0.0)
    dem, mask, origin_e, origin_n = alloc_grid(*bbox, res, anchor=(anchor_e, anchor_n))
    log(f"[1/{len(urls)}] Fuege Tile ins Zielraster ein ...")
    taken = paste_tile(
        dem, mask, first_meta, first_arr, bbox, origin_e, origin_n, res, mode=interp
    )
    log(f"    uebernommen: {taken} Zellen")

    remaining = list(enumerate(urls[1:], 2))
    if remaining and tile_workers > 1:
        workers = min(tile_workers, len(remaining))
        log(f"Lade und dekodiere {len(remaining)} weitere Tiles parallel (workers={workers}) ...")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = executor.map(fetch_tile, remaining)
            for idx, url, arr, meta in results:
                log(f"[{idx}/{len(urls)}] Tile geladen und dekodiert: {url}")
                log(f"[{idx}/{len(urls)}] Fuege Tile ins Zielraster ein ...")
                taken = paste_tile(
                    dem, mask, meta, arr, bbox, origin_e, origin_n, res, mode=interp
                )
                log(f"    uebernommen: {taken} Zellen")
    else:
        for idx, url in remaining:
            log(f"[{idx}/{len(urls)}] Lade und dekodiere Tile: {url}")
            _idx, _url, arr, meta = fetch_tile((idx, url))
            log(f"[{idx}/{len(urls)}] Fuege Tile ins Zielraster ein ...")
            taken = paste_tile(
                dem, mask, meta, arr, bbox, origin_e, origin_n, res, mode=interp
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
    x_coords = origin_e + np.arange(width, dtype=np.float32) * res
    y_coords = origin_n - np.arange(height, dtype=np.float32) * res
    xx, yy = np.meshgrid(x_coords, y_coords)
    zz = (dem - z_min) * zex
    scale_factor = unit_factor / scale_denominator
    xx = xx * scale_factor
    yy = yy * scale_factor
    zz = zz * scale_factor
    base = base * scale_factor

    log("Starte STL-Export ...")
    final_out = write_stl_binary(
        out_path,
        xx,
        yy,
        zz,
        base_thickness=base,
        planar_merge=planar_merge,
        planar_tolerance=planar_tolerance,
        planar_max_seconds=planar_max_seconds,
        merge_workers=merge_workers,
        wall_merge=wall_merge,
        wall_slope_threshold=wall_slope_threshold,
    )
    tag = f" ({label})" if label else ""
    log(f"Fertig: {final_out}{tag} (Dauer {time.time() - start:.1f}s)")


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        err(str(exc))
        sys.exit(1)
    except Exception:
        err("Unerwarteter Fehler")
        traceback.print_exc()
        sys.exit(1)
