#!/usr/bin/env python3
"""Helper functions for building LV95 terrain rasters and STL exports."""

import io
import math
import os
import struct
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Sequence, Tuple

import numpy as np
import requests

try:
    import tifffile as tiff
except ImportError:
    tiff = None

STAC_URL = "https://data.geo.admin.ch/api/stac/v1/search"


def log(message: str) -> None:
    text = str(message)
    if text.startswith(("Fertig", "Done:", "Abgeschlossen")):
        print(f"[INFO] Done: {text}", flush=True)
    else:
        print(f"[INFO] Processing: {text}", flush=True)


def err(message: str) -> None:
    print(f"[ERROR] {message}", flush=True)


def lv95_to_wgs84(e: float, n: float) -> Tuple[float, float]:
    y = (e - 2600000.0) / 1_000_000.0
    x = (n - 1200000.0) / 1_000_000.0
    lon = (
        2.6779094
        + 4.728982 * y
        + 0.791484 * y * x
        + 0.130600 * y * (x**2)
        - 0.043600 * (y**3)
    )
    lat = (
        16.9023892
        + 3.238272 * x
        - 0.270978 * (y**2)
        - 0.002528 * (x**2)
        - 0.044700 * (y**2) * x
        - 0.014000 * (x**3)
    )
    return lon * 100 / 36, lat * 100 / 36


def bbox_lv95_to_wgs84(
    min_e: float, min_n: float, max_e: float, max_n: float
) -> Tuple[float, float, float, float]:
    lon1, lat1 = lv95_to_wgs84(min_e, min_n)
    lon2, lat2 = lv95_to_wgs84(max_e, max_n)
    return min(lon1, lon2), min(lat1, lat2), max(lon1, lon2), max(lat1, lat2)


def search_tiles(
    min_e: float,
    min_n: float,
    max_e: float,
    max_n: float,
    collection: str,
    *,
    stac_url: str = STAC_URL,
    timeout: int = 60,
) -> Sequence[str]:
    west, south, east, north = bbox_lv95_to_wgs84(min_e, min_n, max_e, max_n)
    payload = {
        "collections": [collection],
        "bbox": [west, south, east, north],
        "limit": 1000,
    }
    log(f"STAC bbox WGS84: {west:.6f},{south:.6f},{east:.6f},{north:.6f}")
    try:
        response = requests.post(stac_url, json=payload, timeout=timeout)
        response.raise_for_status()
    except Exception:
        err("STAC Anfrage fehlgeschlagen.")
        traceback.print_exc()
        sys.exit(1)

    features = response.json().get("features", [])
    urls = []
    for feature in features:
        assets = feature.get("assets", {})
        picked = None
        for asset in assets.values():
            href = (asset.get("href") or "")
            title = (asset.get("title") or "").lower()
            mime = (asset.get("type") or "").lower()
            if href.lower().endswith(".tif") and (
                "elevation" in title
                or "height" in title
                or "dom" in title
                or "raster" in title
                or "tif" in mime
            ):
                picked = href
                break
        if not picked:
            for asset in assets.values():
                href = (asset.get("href") or "")
                if href.lower().endswith(".tif"):
                    picked = href
                    break
        if picked:
            urls.append(picked)

    urls = sorted(set(urls))
    if not urls:
        err("Keine Tiles gefunden (Collection/BBox prüfen).")
        sys.exit(1)
    log(f"Tiles: {len(urls)}")
    return urls


def read_tile(tif_bytes: bytes):
    if tiff is None:
        raise RuntimeError(
            "Das Paket 'tifffile' fehlt. Installiere die Abhaengigkeiten mit: "
            "python -m pip install -r requirements.txt"
        )
    with tiff.TiffFile(io.BytesIO(tif_bytes)) as tf:
        page = tf.pages[0]
        try:
            arr = page.asarray().astype(np.float32)
        except ValueError as exc:
            if "imagecodecs" in str(exc):
                err("Kompression erkannt. Installiere: pip install imagecodecs")
            raise
        mps = page.tags.get(33550)  # ModelPixelScaleTag
        mtp = page.tags.get(33922)  # ModelTiepointTag
        if mps is None or mtp is None:
            err("GeoTIFF ohne GeoTags (PixelScale/Tiepoint).")
            sys.exit(1)
        scale = np.array(mps.value, dtype=float)
        tie = np.array(mtp.value, dtype=float)
        i, j, _, x, y, _ = tie[:6]
        geo = getattr(tf, "geotiff_metadata", {}) or {}
        raster_type = int(geo.get("GTRasterTypeGeoKey", 1))
        meta = {
            "sx": float(scale[0]),
            "sy": float(scale[1]),
            "i0": float(i),
            "j0": float(j),
            "x0": float(x),
            "y0": float(y),
            "w": int(page.imagewidth),
            "h": int(page.imagelength),
            "pixel_is_area": raster_type != 2,  # 1=Area, 2=Point
        }
        return arr, meta


def tile_bounds(meta):
    sx = meta["sx"]
    sy = meta["sy"]
    i0 = meta["i0"]
    j0 = meta["j0"]
    x0 = meta["x0"]
    y0 = meta["y0"]
    w = meta["w"]
    h = meta["h"]
    is_area = meta.get("pixel_is_area", True)
    if is_area:
        min_e = x0 + (0 - i0) * sx
        max_e = x0 + (w - i0) * sx
        max_n = y0 - (0 - j0) * sy
        min_n = y0 - (h - j0) * sy
    else:
        min_e = x0 + (0 - i0) * sx
        max_e = x0 + ((w - 1) - i0) * sx
        max_n = y0 - (0 - j0) * sy
        min_n = y0 - ((h - 1) - j0) * sy
    return (min(min_e, max_e), min(min_n, max_n), max(min_e, max_e), max(min_n, max_n))


def _target_bounds(origin_e, origin_n, res_m, shape, roi):
    rows, cols = shape
    min_e, min_n, max_e, max_n = roi
    eps = 1e-9  # avoid rounding holes at tile edges
    col_min = max(0, int(math.ceil((min_e - origin_e) / res_m - eps)))
    col_max = min(cols, int(math.floor((max_e - origin_e) / res_m + eps)) + 1)
    row_min = max(0, int(math.ceil((origin_n - max_n) / res_m - eps)))
    row_max = min(rows, int(math.floor((origin_n - min_n) / res_m + eps)) + 1)
    return row_min, row_max, col_min, col_max


def _world_to_pixel(meta, ee, nn):
    sx = meta["sx"]
    sy = meta["sy"]
    i0 = meta["i0"]
    j0 = meta["j0"]
    x0 = meta["x0"]
    y0 = meta["y0"]
    if meta.get("pixel_is_area", True):
        u = i0 - 0.5 + (ee - x0) / sx
        v = j0 - 0.5 + (y0 - nn) / sy
    else:
        u = i0 + (ee - x0) / sx
        v = j0 + (y0 - nn) / sy
    return u, v


def alloc_grid(min_e, min_n, max_e, max_n, res_m, *, anchor=None):
    """
    Create target grid and snap it to a reference lattice so adjacent tiles align.

    If anchor is given, it must be a tuple (anchor_e, anchor_n) describing a
    known pixel center of the source dataset. The output grid origin is snapped
    to that lattice; otherwise it falls back to a bbox-relative origin.
    """
    if anchor is None:
        anchor_e = min_e + 0.5 * res_m
        anchor_n = max_n - 0.5 * res_m
    else:
        anchor_e, anchor_n = anchor

    # Snap pixel centers to the anchor lattice while keeping the grid tight (inclusive on bbox)
    col_start = math.ceil((min_e - anchor_e) / res_m - 1e-9)
    col_end = math.floor((max_e - anchor_e) / res_m + 1e-9)
    row_start = math.ceil((anchor_n - max_n) / res_m - 1e-9)
    row_end = math.floor((anchor_n - min_n) / res_m + 1e-9)

    origin_e = anchor_e + col_start * res_m
    origin_n = anchor_n - row_start * res_m

    cols = int(col_end - col_start + 1)
    rows = int(row_end - row_start + 1)

    if cols <= 0 or rows <= 0:
        err("BBox zu klein fuer Rasteraufloesung.")
        sys.exit(1)
    cells = rows * cols
    log(f"Raster: {cols}x{rows} (~{cells/1e6:.2f} Mio) @ {res_m} m (Pixelzentren)")
    if cells > 80_000_000:
        log("Hinweis: Sehr grosses Raster - Speicher- und Laufzeitbedarf kann hoch sein.")
    dem = np.full((rows, cols), np.nan, dtype=np.float32)
    mask = np.zeros((rows, cols), dtype=bool)
    return dem, mask, origin_e, origin_n


def paste_tile(dem, mask, meta, arr, roi, origin_e, origin_n, res_m, mode="bilinear"):
    """Paste a tile into the DEM using nearest or bilinear sampling."""
    w = meta["w"]
    h = meta["h"]
    tb = tile_bounds(meta)
    overlap = (
        max(roi[0], tb[0]),
        max(roi[1], tb[1]),
        min(roi[2], tb[2]),
        min(roi[3], tb[3]),
    )
    if not (overlap[0] < overlap[2] and overlap[1] < overlap[3]):
        return 0
    r_min, r_max, c_min, c_max = _target_bounds(
        origin_e, origin_n, res_m, dem.shape, overlap
    )
    if r_min >= r_max or c_min >= c_max:
        return 0

    cols = np.arange(c_min, c_max)
    rows = np.arange(r_min, r_max)
    ee, nn = np.meshgrid(origin_e + cols * res_m, origin_n - rows * res_m)

    u, v = _world_to_pixel(meta, ee, nn)

    if mode == "nn":
        ui = np.rint(u).astype(int)
        vi = np.rint(v).astype(int)
        inside = (ui >= 0) & (ui < w) & (vi >= 0) & (vi < h)
        if not inside.any():
            return 0
        vals = np.full(inside.shape, np.nan, dtype=np.float32)
        vals[inside] = arr[vi[inside], ui[inside]]
        dem_block = dem[rows[:, None], cols[None, :]]
        mask_block = mask[rows[:, None], cols[None, :]]
        write_mask = inside & np.isfinite(vals) & (~mask_block)
        if not write_mask.any():
            return 0
        dem[rows[:, None], cols[None, :]] = np.where(write_mask, vals, dem_block)
        mask[rows[:, None], cols[None, :]] |= write_mask
        return int(write_mask.sum())

    # bilinear
    u0 = np.floor(u).astype(int)
    v0 = np.floor(v).astype(int)
    du = u - u0
    dv = v - v0
    u1 = u0 + 1
    v1 = v0 + 1

    valid = (u0 >= 0) & (u0 < w) & (v0 >= 0) & (v0 < h)
    if not valid.any():
        return 0

    q00 = np.full(u.shape, np.nan, dtype=np.float32)
    q10 = np.full(u.shape, np.nan, dtype=np.float32)
    q01 = np.full(u.shape, np.nan, dtype=np.float32)
    q11 = np.full(u.shape, np.nan, dtype=np.float32)

    u0c = np.clip(u0, 0, w - 1)
    u1c = np.clip(u1, 0, w - 1)
    v0c = np.clip(v0, 0, h - 1)
    v1c = np.clip(v1, 0, h - 1)

    q00[valid] = arr[v0c[valid], u0c[valid]]
    q10[valid] = arr[v0c[valid], u1c[valid]]
    q01[valid] = arr[v1c[valid], u0c[valid]]
    q11[valid] = arr[v1c[valid], u1c[valid]]
    val = (1 - du) * (1 - dv) * q00 + du * (1 - dv) * q10 + (1 - du) * dv * q01 + du * dv * q11

    dem_block = dem[rows[:, None], cols[None, :]]
    mask_block = mask[rows[:, None], cols[None, :]]
    write_mask = valid & np.isfinite(val) & (~mask_block)
    dem[rows[:, None], cols[None, :]] = np.where(write_mask, val, dem_block)
    mask[rows[:, None], cols[None, :]] |= write_mask
    return int(write_mask.sum())


_TRI_DTYPE = np.dtype(
    [
        ("normal", "<f4", 3),
        ("p0", "<f4", 3),
        ("p1", "<f4", 3),
        ("p2", "<f4", 3),
        ("attr", "<u2"),
    ]
)
_ZERO_NORMAL = np.array([0.0, 0.0, 1.0], dtype=np.float32)


def _col_stack(x, y, z):
    return np.column_stack((x, y, z)).astype(np.float32, copy=False)


def _write_triangle_block(fh, p0, p1, p2):
    if p0.size == 0:
        return
    v1 = p1 - p0
    v2 = p2 - p0
    normals = np.cross(v1, v2)
    lengths = np.linalg.norm(normals, axis=1)
    normals = normals.astype(np.float32, copy=False)
    valid = lengths > 0
    if valid.any():
        normals[valid] /= lengths[valid][:, None].astype(np.float32, copy=False)
    if (~valid).any():
        normals[~valid] = _ZERO_NORMAL
    block = np.empty(p0.shape[0], dtype=_TRI_DTYPE)
    block["normal"] = normals
    block["p0"] = p0.astype(np.float32, copy=False)
    block["p1"] = p1.astype(np.float32, copy=False)
    block["p2"] = p2.astype(np.float32, copy=False)
    block["attr"] = 0
    fh.write(block.tobytes())


def _write_grid(
    fh,
    xx,
    yy,
    zz,
    flip=False,
    skip_cells=None,
    log_fn=None,
    progress_label="STL: schreibe Raster",
):
    height, width = zz.shape
    total_rows = height - 1
    progress_interval = max(1, total_rows // 10) if total_rows >= 100 else None
    for r in range(total_rows):
        if skip_cells is None:
            cols = slice(None, -1)
            cols_next = slice(1, None)
        else:
            keep = ~skip_cells[r, :]
            if not keep.any():
                continue
            col_idx = np.nonzero(keep)[0]
            cols = col_idx
            cols_next = col_idx + 1
        p00 = _col_stack(xx[r, cols], yy[r, cols], zz[r, cols])
        p10 = _col_stack(xx[r, cols_next], yy[r, cols_next], zz[r, cols_next])
        p01 = _col_stack(xx[r + 1, cols], yy[r + 1, cols], zz[r + 1, cols])
        p11 = _col_stack(xx[r + 1, cols_next], yy[r + 1, cols_next], zz[r + 1, cols_next])
        if not flip:
            _write_triangle_block(fh, p00, p10, p01)
            _write_triangle_block(fh, p01, p10, p11)
        else:
            _write_triangle_block(fh, p00, p01, p10)
            _write_triangle_block(fh, p01, p11, p10)
        if log_fn is not None and progress_interval and (r + 1) % progress_interval == 0:
            log_fn(f"{progress_label}: {r + 1:,}/{total_rows:,} Zeilen ...")
    if log_fn is not None and progress_interval:
        log_fn(f"{progress_label}: {total_rows:,}/{total_rows:,} Zeilen fertig.")


def _plane_z(p0, p1, p2, x, y):
    normal = np.cross(p1 - p0, p2 - p0)
    if abs(float(normal[2])) < 1e-12:
        return None
    return p0[2] - (normal[0] * (x - p0[0]) + normal[1] * (y - p0[1])) / normal[2]


def _sample_indices(start, end, max_count):
    count = end - start + 1
    if count <= max_count:
        return np.arange(start, end + 1)
    return np.unique(np.linspace(start, end, max_count).round().astype(int))


def _patch_error(xx, yy, zz, r0, r1, c0, c1, diag, *, max_eval_vertices=4_096):
    p00 = np.array([xx[r0, c0], yy[r0, c0], zz[r0, c0]], dtype=np.float64)
    p10 = np.array([xx[r0, c1], yy[r0, c1], zz[r0, c1]], dtype=np.float64)
    p01 = np.array([xx[r1, c0], yy[r1, c0], zz[r1, c0]], dtype=np.float64)
    p11 = np.array([xx[r1, c1], yy[r1, c1], zz[r1, c1]], dtype=np.float64)

    rows_total = r1 - r0 + 1
    cols_total = c1 - c0 + 1
    if rows_total * cols_total > max_eval_vertices:
        side = max(2, int(math.sqrt(max_eval_vertices)))
        row_count = min(rows_total, side)
        col_count = min(cols_total, max(2, max_eval_vertices // row_count))
        rows = _sample_indices(r0, r1, row_count)
        cols = _sample_indices(c0, c1, col_count)
    else:
        rows = np.arange(r0, r1 + 1)
        cols = np.arange(c0, c1 + 1)
    sub_x = xx[rows[:, None], cols[None, :]]
    sub_y = yy[rows[:, None], cols[None, :]]
    sub_z = zz[rows[:, None], cols[None, :]]
    u = (rows[:, None] - r0) / max(r1 - r0, 1)
    t = (cols[None, :] - c0) / max(c1 - c0, 1)

    if diag == "anti":
        split = t + u <= 1.0
        z_a = _plane_z(p00, p10, p01, sub_x, sub_y)
        z_b = _plane_z(p01, p10, p11, sub_x, sub_y)
    else:
        split = t >= u
        z_a = _plane_z(p00, p10, p11, sub_x, sub_y)
        z_b = _plane_z(p00, p11, p01, sub_x, sub_y)
    if z_a is None or z_b is None:
        return float("inf")
    pred = np.where(split, z_a, z_b)
    return float(np.max(np.abs(pred - sub_z)))


def _patch_normal_z_abs(xx, yy, zz, r0, r1, c0, c1, diag):
    p00 = np.array([xx[r0, c0], yy[r0, c0], zz[r0, c0]], dtype=np.float64)
    p10 = np.array([xx[r0, c1], yy[r0, c1], zz[r0, c1]], dtype=np.float64)
    p01 = np.array([xx[r1, c0], yy[r1, c0], zz[r1, c0]], dtype=np.float64)
    p11 = np.array([xx[r1, c1], yy[r1, c1], zz[r1, c1]], dtype=np.float64)
    if diag == "anti":
        normal = np.cross(p10 - p00, p01 - p00)
    else:
        normal = np.cross(p10 - p00, p11 - p00)
    length = float(np.linalg.norm(normal))
    if length <= 0:
        return 1.0
    return abs(float(normal[2] / length))


def _fit_line_error(values, start, end):
    if end <= start + 1:
        return 0.0
    segment = values[start : end + 1].astype(np.float64, copy=False)
    t = np.linspace(0.0, 1.0, segment.size)
    pred = segment[0] + (segment[-1] - segment[0]) * t
    return float(np.max(np.abs(segment - pred)))


def _split_wall_run_by_linearity(a, b, start, end, tolerance, out):
    if end <= start + 1:
        out.append((start, end))
        return
    err = max(_fit_line_error(a, start, end), _fit_line_error(b, start, end))
    if err <= tolerance:
        out.append((start, end))
        return
    mid = (start + end) // 2
    if mid == start:
        out.append((start, end))
        return
    _split_wall_run_by_linearity(a, b, start, mid, tolerance, out)
    _split_wall_run_by_linearity(a, b, mid, end, tolerance, out)


def _component_cells(edge_cells, shape, *, log_fn=None, label="Komponenten"):
    active = np.zeros(shape, dtype=bool)
    visited = np.zeros(shape, dtype=bool)
    started = time.monotonic()
    last_log = started
    total = len(edge_cells)
    for r, c in edge_cells:
        active[r, c] = True
    components = []
    for idx, cell in enumerate(edge_cells, 1):
        if visited[cell]:
            continue
        stack = [cell]
        visited[cell] = True
        comp = []
        while stack:
            r, c = stack.pop()
            comp.append((r, c))
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    nr = r + dr
                    nc = c + dc
                    nxt = (nr, nc)
                    if (
                        0 <= nr < shape[0]
                        and 0 <= nc < shape[1]
                        and active[nr, nc]
                        and not visited[nr, nc]
                    ):
                        visited[nr, nc] = True
                        stack.append(nxt)
        components.append(comp)
        now = time.monotonic()
        if log_fn is not None and now - last_log >= 2.0:
            log_fn(
                f"{label}: {idx:,}/{total:,} Kandidaten geprueft, "
                f"{len(components):,} Gruppen gefunden "
                f"({now - started:.1f}s)."
            )
            last_log = now
    return components


def _fit_z_line(t, z):
    if t.size == 0:
        return 0.0, 0.0
    if t.size == 1 or float(np.max(t) - np.min(t)) <= 1e-12:
        return 0.0, float(z[0])
    slope, intercept = np.polyfit(t, z, 1)
    return float(slope), float(intercept)


def _line_wall_patch_from_component(xx, yy, zz, samples, cells, tolerance):
    mid_xy = np.array([sample["mid_xy"] for sample in samples], dtype=np.float64)
    if mid_xy.shape[0] < 2:
        return None
    origin = mid_xy.mean(axis=0)
    centered = mid_xy - origin
    _u, singular, vh = np.linalg.svd(centered, full_matrices=False)
    direction = vh[0]
    if direction[0] < 0 or (abs(direction[0]) < 1e-12 and direction[1] < 0):
        direction = -direction
    normal = np.array([-direction[1], direction[0]], dtype=np.float64)
    t_mid = centered @ direction
    if float(np.max(t_mid) - np.min(t_mid)) <= 1e-12:
        return None

    high_xy = np.array([sample["high_xy"] for sample in samples], dtype=np.float64)
    low_xy = np.array([sample["low_xy"] for sample in samples], dtype=np.float64)
    high_z = np.array([sample["high_z"] for sample in samples], dtype=np.float64)
    low_z = np.array([sample["low_z"] for sample in samples], dtype=np.float64)
    high_t = (high_xy - origin) @ direction
    low_t = (low_xy - origin) @ direction
    high_offset = (high_xy - origin) @ normal
    low_offset = (low_xy - origin) @ normal
    if np.mean(high_offset) < np.mean(low_offset):
        normal = -normal
        high_offset = -high_offset
        low_offset = -low_offset

    high_slope, high_intercept = _fit_z_line(high_t, high_z)
    low_slope, low_intercept = _fit_z_line(low_t, low_z)
    high_pred = high_slope * high_t + high_intercept
    low_pred = low_slope * low_t + low_intercept
    z_err = max(
        float(np.max(np.abs(high_pred - high_z))),
        float(np.max(np.abs(low_pred - low_z))),
    )
    if z_err > max(tolerance, 1e-12):
        return None

    t0 = float(min(np.min(high_t), np.min(low_t)))
    t1 = float(max(np.max(high_t), np.max(low_t)))
    high_off = float(np.mean(high_offset))
    low_off = float(np.mean(low_offset))
    p_high0_xy = origin + direction * t0 + normal * high_off
    p_high1_xy = origin + direction * t1 + normal * high_off
    p_low0_xy = origin + direction * t0 + normal * low_off
    p_low1_xy = origin + direction * t1 + normal * low_off

    patch = {
        "kind": "line",
        "p00": np.array([p_low0_xy[0], p_low0_xy[1], low_slope * t0 + low_intercept], dtype=np.float32),
        "p10": np.array([p_high0_xy[0], p_high0_xy[1], high_slope * t0 + high_intercept], dtype=np.float32),
        "p01": np.array([p_low1_xy[0], p_low1_xy[1], low_slope * t1 + low_intercept], dtype=np.float32),
        "p11": np.array([p_high1_xy[0], p_high1_xy[1], high_slope * t1 + high_intercept], dtype=np.float32),
        "origin": origin,
        "direction": direction,
        "normal": normal,
        "high_offset": high_off,
        "low_offset": low_off,
        "high_slope": high_slope,
        "high_intercept": high_intercept,
        "low_slope": low_slope,
        "low_intercept": low_intercept,
        "cells": cells,
        "segments": len(samples),
        "z_error": z_err,
    }
    return patch


def _detect_wall_patches(
    xx,
    yy,
    zz,
    *,
    slope_threshold=1.5,
    tolerance=0.0,
    log_fn=None,
):
    height, width = zz.shape
    if height < 2 or width < 2:
        return [], np.zeros((0, 0), dtype=bool), 0

    started = time.monotonic()
    if log_fn is not None:
        log_fn(
            f"Wand-Merge: starte Analyse fuer {width}x{height} Raster "
            f"(slope>={slope_threshold}, tolerance={tolerance:.6g})."
        )

    dx = float(np.nanmedian(np.abs(np.diff(xx, axis=1)))) if width > 1 else 0.0
    dy = float(np.nanmedian(np.abs(np.diff(yy, axis=0)))) if height > 1 else 0.0
    dx = max(dx, 1e-12)
    dy = max(dy, 1e-12)
    if log_fn is not None:
        log_fn("Wand-Merge: berechne Hoehenspruenge zwischen Rasterpunkten ...")
    x_edges = np.abs(np.diff(zz, axis=1)) / dx >= slope_threshold
    y_edges = np.abs(np.diff(zz, axis=0)) / dy >= slope_threshold
    raw_edges = int(x_edges.sum() + y_edges.sum())
    skip = np.zeros((height - 1, width - 1), dtype=bool)
    if raw_edges == 0:
        if log_fn is not None:
            log_fn("Wand-Merge: keine steilen Punktkanten gefunden.")
        return [], skip, 0
    if log_fn is not None:
        log_fn(f"Wand-Merge: {raw_edges:,} steile Punktkanten gefunden.")

    cell_samples = {}

    def add_sample(cell, ax, ay, a_z, bx, by, b_z):
        if a_z >= b_z:
            high_xy, high_z = (float(ax), float(ay)), float(a_z)
            low_xy, low_z = (float(bx), float(by)), float(b_z)
        else:
            high_xy, high_z = (float(bx), float(by)), float(b_z)
            low_xy, low_z = (float(ax), float(ay)), float(a_z)
        sample = {
            "mid_xy": (
                (high_xy[0] + low_xy[0]) * 0.5,
                (high_xy[1] + low_xy[1]) * 0.5,
            ),
            "high_xy": high_xy,
            "low_xy": low_xy,
            "high_z": high_z,
            "low_z": low_z,
        }
        cell_samples.setdefault(cell, []).append(sample)

    x_rows, x_cols = np.nonzero(x_edges)
    y_rows, y_cols = np.nonzero(y_edges)
    x_total = len(x_rows)
    y_total = len(y_rows)
    last_log = time.monotonic()
    for idx, (r, c) in enumerate(zip(x_rows, x_cols), 1):
        cell = (min(r, height - 2), c)
        add_sample(
            cell,
            xx[r, c],
            yy[r, c],
            zz[r, c],
            xx[r, c + 1],
            yy[r, c + 1],
            zz[r, c + 1],
        )
        now = time.monotonic()
        if log_fn is not None and now - last_log >= 2.0:
            log_fn(
                f"Wand-Merge: X-Kanten {idx:,}/{x_total:,} "
                f"in Zell-Samples umgewandelt."
            )
            last_log = now
    for idx, (r, c) in enumerate(zip(y_rows, y_cols), 1):
        cell = (r, min(c, width - 2))
        add_sample(
            cell,
            xx[r, c],
            yy[r, c],
            zz[r, c],
            xx[r + 1, c],
            yy[r + 1, c],
            zz[r + 1, c],
        )
        now = time.monotonic()
        if log_fn is not None and now - last_log >= 2.0:
            log_fn(
                f"Wand-Merge: Y-Kanten {idx:,}/{y_total:,} "
                f"in Zell-Samples umgewandelt."
            )
            last_log = now

    patches = []
    edge_cells = list(cell_samples.keys())
    if log_fn is not None:
        log_fn(
            f"Wand-Merge: suche zusammenhaengende Gruppen in "
            f"{len(edge_cells):,} betroffenen Rasterzellen ..."
        )
    components = _component_cells(
        edge_cells,
        skip.shape,
        log_fn=log_fn,
        label="Wand-Merge Gruppen",
    )
    if log_fn is not None:
        log_fn(
            f"Wand-Merge: {len(components):,} Gruppen gefunden, "
            "berechne glatte Wandflaechen ..."
        )
    last_log = time.monotonic()
    skipped_small = 0
    rejected = 0
    for idx, comp in enumerate(components, 1):
        samples = []
        for cell in comp:
            samples.extend(cell_samples[cell])
        if len(samples) < 2:
            skipped_small += 1
            continue
        patch = _line_wall_patch_from_component(xx, yy, zz, samples, comp, tolerance)
        if patch is None:
            rejected += 1
            continue
        patches.append(patch)
        for r, c in comp:
            skip[r, c] = True
        now = time.monotonic()
        if log_fn is not None and now - last_log >= 2.0:
            log_fn(
                f"Wand-Merge: Gruppen {idx:,}/{len(components):,} ausgewertet, "
                f"{len(patches):,} Wandflaechen akzeptiert, "
                f"{rejected:,} verworfen."
            )
            last_log = now
    if log_fn is not None:
        log_fn(
            f"Wand-Merge: Analyse fertig nach {time.monotonic() - started:.1f}s "
            f"({len(patches):,} Wandflaechen, {rejected:,} verworfen, "
            f"{skipped_small:,} zu klein)."
        )
    return patches, skip, raw_edges


def _wall_patch_area(patch):
    return max(1, len(patch.get("cells", [])))


def _wall_z_at(patch, xy, high_side):
    t = float((xy - patch["origin"]) @ patch["direction"])
    if high_side:
        return patch["high_slope"] * t + patch["high_intercept"]
    return patch["low_slope"] * t + patch["low_intercept"]


def _clip_wall_polygon(poly, patch, *, high_side):
    offset = patch["high_offset"] if high_side else patch["low_offset"]
    normal = patch["normal"]

    def signed(vertex):
        return float((vertex[:2] - patch["origin"]) @ normal - offset)

    def inside(vertex):
        value = signed(vertex)
        return value >= -1e-12 if high_side else value <= 1e-12

    def intersect(a, b):
        da = signed(a)
        db = signed(b)
        denom = da - db
        alpha = 0.0 if abs(denom) < 1e-12 else da / denom
        alpha = min(1.0, max(0.0, alpha))
        xy = a[:2] + (b[:2] - a[:2]) * alpha
        z = _wall_z_at(patch, xy, high_side)
        return np.array([xy[0], xy[1], z], dtype=np.float32)

    out = []
    prev = poly[-1]
    prev_inside = inside(prev)
    for cur in poly:
        cur_inside = inside(cur)
        if cur_inside:
            if not prev_inside:
                out.append(intersect(prev, cur))
            out.append(cur.astype(np.float32, copy=False))
        elif prev_inside:
            out.append(intersect(prev, cur))
        prev = cur
        prev_inside = cur_inside
    return out


def _clip_wall_band_polygon(poly, patch):
    low_offset = patch["low_offset"]
    high_offset = patch["high_offset"]
    if high_offset - low_offset <= 1e-12:
        return []
    normal = patch["normal"]

    def band_z(xy):
        low_z = _wall_z_at(patch, xy, high_side=False)
        high_z = _wall_z_at(patch, xy, high_side=True)
        alpha = (float((xy - patch["origin"]) @ normal) - low_offset) / (
            high_offset - low_offset
        )
        alpha = min(1.0, max(0.0, alpha))
        return low_z + (high_z - low_z) * alpha

    def with_band_z(vertex):
        xy = vertex[:2]
        return np.array([xy[0], xy[1], band_z(xy)], dtype=np.float32)

    def clip(poly_in, offset, keep_above):
        if len(poly_in) == 0:
            return []

        def signed(vertex):
            return float((vertex[:2] - patch["origin"]) @ normal - offset)

        def inside(vertex):
            value = signed(vertex)
            return value >= -1e-12 if keep_above else value <= 1e-12

        def intersect(a, b):
            da = signed(a)
            db = signed(b)
            denom = da - db
            alpha = 0.0 if abs(denom) < 1e-12 else da / denom
            alpha = min(1.0, max(0.0, alpha))
            xy = a[:2] + (b[:2] - a[:2]) * alpha
            return np.array([xy[0], xy[1], band_z(xy)], dtype=np.float32)

        out = []
        prev = poly_in[-1]
        prev_inside = inside(prev)
        for cur in poly_in:
            cur_inside = inside(cur)
            if cur_inside:
                if not prev_inside:
                    out.append(intersect(prev, cur))
                out.append(with_band_z(cur))
            elif prev_inside:
                out.append(intersect(prev, cur))
            prev = cur
            prev_inside = cur_inside
        return out

    band = clip(poly, low_offset, keep_above=True)
    return clip(band, high_offset, keep_above=False)


def _polygon_triangle_count(poly):
    return max(0, len(poly) - 2)


def _write_polygon_fan(fh, poly, flip=False):
    if len(poly) < 3:
        return
    vertices = np.asarray(poly, dtype=np.float32)
    p0 = np.repeat(vertices[0][None, :], vertices.shape[0] - 2, axis=0)
    p1 = vertices[1:-1]
    p2 = vertices[2:]
    if flip:
        _write_triangle_block(fh, p0, p2, p1)
    else:
        _write_triangle_block(fh, p0, p1, p2)


def _cell_polygon(xx, yy, zz, r, c):
    return np.array(
        [
            [xx[r, c], yy[r, c], zz[r, c]],
            [xx[r, c + 1], yy[r, c + 1], zz[r, c + 1]],
            [xx[r + 1, c + 1], yy[r + 1, c + 1], zz[r + 1, c + 1]],
            [xx[r + 1, c], yy[r + 1, c], zz[r + 1, c]],
        ],
        dtype=np.float32,
    )


def _wall_replacement_triangle_count(xx, yy, zz, patches):
    count = 0
    for patch in patches:
        replacement_polys = []
        for r, c in patch.get("cells", []):
            poly = _cell_polygon(xx, yy, zz, r, c)
            high_poly = _clip_wall_polygon(poly, patch, high_side=True)
            low_poly = _clip_wall_polygon(poly, patch, high_side=False)
            band_poly = _clip_wall_band_polygon(poly, patch)
            count += _polygon_triangle_count(high_poly)
            count += _polygon_triangle_count(low_poly)
            count += _polygon_triangle_count(band_poly)
            replacement_polys.append((high_poly, low_poly, band_poly))
        patch["_replacement_polys"] = replacement_polys
    return count


def _write_wall_replacement_cells(fh, xx, yy, zz, patches):
    for patch in patches:
        replacement_polys = patch.pop("_replacement_polys", None)
        if replacement_polys is None:
            replacement_polys = []
            for r, c in patch.get("cells", []):
                poly = _cell_polygon(xx, yy, zz, r, c)
                replacement_polys.append(
                    (
                        _clip_wall_polygon(poly, patch, high_side=True),
                        _clip_wall_polygon(poly, patch, high_side=False),
                        _clip_wall_band_polygon(poly, patch),
                    )
                )
        for high_poly, low_poly, band_poly in replacement_polys:
            _write_polygon_fan(fh, high_poly)
            _write_polygon_fan(fh, low_poly)
            _write_polygon_fan(fh, band_poly)


def _intervals_overlap(a0, a1, b0, b1):
    return max(a0, b0) < min(a1, b1)


def _summarize_steep_patches(xx, yy, zz, patches, *, zmax=0.45):
    steep = []
    for idx, patch in enumerate(patches):
        r0, r1, c0, c1, diag = patch
        if _patch_normal_z_abs(xx, yy, zz, r0, r1, c0, c1, diag) <= zmax:
            area = max(1, (r1 - r0) * (c1 - c0))
            steep.append((idx, r0, r1, c0, c1, area))
    if not steep:
        return {
            "patches": 0,
            "groups": 0,
            "largest_cells": 0,
            "connected_checked": True,
        }
    if len(steep) > 2_000:
        return {
            "patches": len(steep),
            "groups": None,
            "largest_cells": max(item[5] for item in steep),
            "connected_checked": False,
        }

    parent = list(range(len(steep)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        ra = find(a)
        rb = find(b)
        if ra != rb:
            parent[rb] = ra

    row_start = {}
    row_end = {}
    col_start = {}
    col_end = {}
    for local_idx, (_, r0, r1, c0, c1, _) in enumerate(steep):
        row_start.setdefault(r0, []).append((c0, c1, local_idx))
        row_end.setdefault(r1, []).append((c0, c1, local_idx))
        col_start.setdefault(c0, []).append((r0, r1, local_idx))
        col_end.setdefault(c1, []).append((r0, r1, local_idx))

    for boundary, left in row_end.items():
        for c0, c1, a in left:
            for d0, d1, b in row_start.get(boundary, []):
                if _intervals_overlap(c0, c1, d0, d1):
                    union(a, b)
    for boundary, left in col_end.items():
        for r0, r1, a in left:
            for d0, d1, b in col_start.get(boundary, []):
                if _intervals_overlap(r0, r1, d0, d1):
                    union(a, b)

    group_area = {}
    for local_idx, item in enumerate(steep):
        root = find(local_idx)
        group_area[root] = group_area.get(root, 0) + item[5]

    return {
        "patches": len(steep),
        "groups": len(group_area),
        "largest_cells": max(group_area.values()),
        "connected_checked": True,
    }


def _adaptive_grid_patches(
    xx,
    yy,
    zz,
    tolerance,
    *,
    max_eval_vertices=4_096,
    max_seconds=0.0,
    skip_cells=None,
    log_fn=log,
):
    height, width = zz.shape
    patches = []
    stack = [(0, height - 1, 0, width - 1)]
    started = time.monotonic()
    last_log = started
    tested = 0
    if max_seconds is not None and max_seconds > 0:
        limit_text = f"timeout={max_seconds:.0f}s"
    else:
        limit_text = "ohne Zeitlimit"
    log_fn(
        f"Rechteck-Merge: analysiere {width}x{height} Raster "
        f"({limit_text}, tolerance={tolerance:.6g})."
    )
    while stack:
        now = time.monotonic()
        if max_seconds is not None and max_seconds > 0 and now - started > max_seconds:
            log_fn(
                "Rechteck-Merge: Zeitlimit erreicht, verwende schnellen Rasterexport."
            )
            return None
        if now - last_log >= 2.0:
            log_fn(
                f"Rechteck-Merge: {tested:,} Bereiche geprueft, "
                f"{len(patches):,} Patches akzeptiert, {len(stack):,} offen."
            )
            last_log = now

        r0, r1, c0, c1 = stack.pop()
        rows = r1 - r0
        cols = c1 - c0
        if rows <= 0 or cols <= 0:
            continue
        if skip_cells is not None:
            skipped = skip_cells[r0:r1, c0:c1]
            if skipped.size and skipped.all():
                continue
            if skipped.size and skipped.any():
                if rows >= cols and rows > 1:
                    mid = (r0 + r1) // 2
                    stack.append((mid, r1, c0, c1))
                    stack.append((r0, mid, c0, c1))
                elif cols > 1:
                    mid = (c0 + c1) // 2
                    stack.append((r0, r1, mid, c1))
                    stack.append((r0, r1, c0, mid))
                continue
        if rows == 1 and cols == 1:
            patches.append((r0, r1, c0, c1, "anti"))
            continue

        tested += 1
        err_anti = _patch_error(
            xx,
            yy,
            zz,
            r0,
            r1,
            c0,
            c1,
            "anti",
            max_eval_vertices=max_eval_vertices,
        )
        err_main = _patch_error(
            xx,
            yy,
            zz,
            r0,
            r1,
            c0,
            c1,
            "main",
            max_eval_vertices=max_eval_vertices,
        )
        if err_anti <= err_main:
            err = err_anti
            diag = "anti"
        else:
            err = err_main
            diag = "main"
        if err <= tolerance:
            patches.append((r0, r1, c0, c1, diag))
            continue

        if rows >= cols and rows > 1:
            mid = (r0 + r1) // 2
            stack.append((mid, r1, c0, c1))
            stack.append((r0, mid, c0, c1))
        elif cols > 1:
            mid = (c0 + c1) // 2
            stack.append((r0, r1, mid, c1))
            stack.append((r0, r1, c0, mid))
        else:
            patches.append((r0, r1, c0, c1, "anti"))
    log_fn(
        f"Rechteck-Merge: Analyse fertig nach {time.monotonic() - started:.1f}s "
        f"({tested:,} Bereiche geprueft)."
    )
    return patches


def _write_adaptive_grid(fh, xx, yy, zz, patches, flip=False, log_fn=log):
    started = time.monotonic()
    last_log = started
    total = len(patches)
    for idx, (r0, r1, c0, c1, diag) in enumerate(patches, 1):
        now = time.monotonic()
        if now - last_log >= 2.0:
            log_fn(f"STL: schreibe Planar-Patches {idx:,}/{total:,} ...")
            last_log = now
        p00 = _col_stack(
            np.array([xx[r0, c0]]),
            np.array([yy[r0, c0]]),
            np.array([zz[r0, c0]]),
        )
        p10 = _col_stack(
            np.array([xx[r0, c1]]),
            np.array([yy[r0, c1]]),
            np.array([zz[r0, c1]]),
        )
        p01 = _col_stack(
            np.array([xx[r1, c0]]),
            np.array([yy[r1, c0]]),
            np.array([zz[r1, c0]]),
        )
        p11 = _col_stack(
            np.array([xx[r1, c1]]),
            np.array([yy[r1, c1]]),
            np.array([zz[r1, c1]]),
        )
        if diag == "anti":
            if not flip:
                _write_triangle_block(fh, p00, p10, p01)
                _write_triangle_block(fh, p01, p10, p11)
            else:
                _write_triangle_block(fh, p00, p01, p10)
                _write_triangle_block(fh, p01, p11, p10)
        else:
            if not flip:
                _write_triangle_block(fh, p00, p10, p11)
                _write_triangle_block(fh, p00, p11, p01)
            else:
                _write_triangle_block(fh, p00, p11, p10)
                _write_triangle_block(fh, p00, p01, p11)
    if total:
        log_fn(
            f"STL: {total:,} Planar-Patches geschrieben "
            f"({time.monotonic() - started:.1f}s)."
        )


def _adaptive_patch_triangle_count(patch):
    r0, r1, c0, c1, _diag = patch
    rows = r1 - r0
    cols = c1 - c0
    if rows <= 0 or cols <= 0:
        return 0
    if rows == 1 and cols == 1:
        return 2
    return 2 * (rows + cols)


def _adaptive_grid_triangle_count(patches):
    return sum(_adaptive_patch_triangle_count(patch) for patch in patches)


def _raw_patch_triangle_count(patch):
    r0, r1, c0, c1, _diag = patch
    return max(0, r1 - r0) * max(0, c1 - c0) * 2


def _patch_ring_vertices(xx, yy, zz, r0, r1, c0, c1):
    top_cols = np.arange(c0, c1 + 1)
    right_rows = np.arange(r0 + 1, r1 + 1)
    bottom_cols = np.arange(c1 - 1, c0 - 1, -1)
    left_rows = np.arange(r1 - 1, r0, -1)
    top = _col_stack(xx[r0, top_cols], yy[r0, top_cols], zz[r0, top_cols])
    right = _col_stack(xx[right_rows, c1], yy[right_rows, c1], zz[right_rows, c1])
    bottom = _col_stack(xx[r1, bottom_cols], yy[r1, bottom_cols], zz[r1, bottom_cols])
    left = _col_stack(xx[left_rows, c0], yy[left_rows, c0], zz[left_rows, c0])
    return np.vstack(
        tuple(part for part in (top, right, bottom, left) if part.size)
    )


def _write_adaptive_grid_conforming(fh, xx, yy, zz, patches, flip=False, log_fn=log):
    started = time.monotonic()
    last_log = started
    total = len(patches)
    for idx, (r0, r1, c0, c1, diag) in enumerate(patches, 1):
        now = time.monotonic()
        if now - last_log >= 2.0:
            log_fn(f"STL: schreibe Rechteck-Patches {idx:,}/{total:,} ...")
            last_log = now

        rows = r1 - r0
        cols = c1 - c0
        if rows == 1 and cols == 1:
            p00 = _col_stack(
                np.array([xx[r0, c0]]),
                np.array([yy[r0, c0]]),
                np.array([zz[r0, c0]]),
            )
            p10 = _col_stack(
                np.array([xx[r0, c1]]),
                np.array([yy[r0, c1]]),
                np.array([zz[r0, c1]]),
            )
            p01 = _col_stack(
                np.array([xx[r1, c0]]),
                np.array([yy[r1, c0]]),
                np.array([zz[r1, c0]]),
            )
            p11 = _col_stack(
                np.array([xx[r1, c1]]),
                np.array([yy[r1, c1]]),
                np.array([zz[r1, c1]]),
            )
            if diag == "anti":
                if not flip:
                    _write_triangle_block(fh, p00, p10, p01)
                    _write_triangle_block(fh, p01, p10, p11)
                else:
                    _write_triangle_block(fh, p00, p01, p10)
                    _write_triangle_block(fh, p01, p11, p10)
            else:
                if not flip:
                    _write_triangle_block(fh, p00, p10, p11)
                    _write_triangle_block(fh, p00, p11, p01)
                else:
                    _write_triangle_block(fh, p00, p11, p10)
                    _write_triangle_block(fh, p00, p01, p11)
            continue

        ring = _patch_ring_vertices(xx, yy, zz, r0, r1, c0, c1)
        center = np.mean(ring, axis=0, dtype=np.float64).astype(np.float32)[None, :]
        p0 = ring
        p1 = np.roll(ring, -1, axis=0)
        centers = np.repeat(center, ring.shape[0], axis=0)
        if not flip:
            _write_triangle_block(fh, p0, p1, centers)
        else:
            _write_triangle_block(fh, p0, centers, p1)

    if total:
        log_fn(
            f"STL: {total:,} Rechteck-Patches geschrieben "
            f"({time.monotonic() - started:.1f}s)."
        )


def _merge_block_sizes(height, width, *, min_block=8, max_block=256):
    max_cells = max(1, min(height - 1, width - 1, max_block))
    size = 1
    while size * 2 <= max_cells:
        size *= 2
    sizes = []
    while size >= min_block:
        sizes.append(size)
        size //= 2
    return sizes


def _rough_cell_prefix(zz, tolerance):
    height, width = zz.shape
    if height < 2 or width < 2:
        return None
    threshold = max(float(tolerance) * 0.25, 1e-12)
    rough_vertices = np.zeros((height, width), dtype=bool)
    if width > 2:
        rough_vertices[:, 1:-1] |= (
            np.abs(zz[:, 2:] - 2.0 * zz[:, 1:-1] + zz[:, :-2]) > threshold
        )
    if height > 2:
        rough_vertices[1:-1, :] |= (
            np.abs(zz[2:, :] - 2.0 * zz[1:-1, :] + zz[:-2, :]) > threshold
        )
    rough_cells = (
        rough_vertices[:-1, :-1]
        | rough_vertices[1:, :-1]
        | rough_vertices[:-1, 1:]
        | rough_vertices[1:, 1:]
    )
    return np.pad(
        rough_cells.astype(np.int32).cumsum(axis=0).cumsum(axis=1),
        ((1, 0), (1, 0)),
        mode="constant",
    )


def _rough_count(prefix, r0, r1, c0, c1):
    if prefix is None:
        return 0
    return int(prefix[r1, c1] - prefix[r0, c1] - prefix[r1, c0] + prefix[r0, c0])


def _fast_rect_grid_patches(
    xx,
    yy,
    zz,
    tolerance,
    *,
    max_eval_vertices=256,
    max_seconds=0.0,
    merge_workers=0,
    skip_cells=None,
    log_fn=log,
):
    height, width = zz.shape
    cell_h = height - 1
    cell_w = width - 1
    if cell_h <= 0 or cell_w <= 0:
        return [], np.zeros((0, 0), dtype=bool)

    occupied = (
        skip_cells.copy()
        if skip_cells is not None
        else np.zeros((cell_h, cell_w), dtype=bool)
    )
    merged = np.zeros((cell_h, cell_w), dtype=bool)
    rough_prefix = _rough_cell_prefix(zz, tolerance)
    patches = []
    sizes = _merge_block_sizes(height, width)
    worker_count = int(merge_workers or 0)
    if worker_count <= 0:
        worker_count = min(8, max(1, os.cpu_count() or 1))
    started = time.monotonic()
    last_log = started
    tested = 0
    if max_seconds is not None and max_seconds > 0:
        limit_text = f"timeout={max_seconds:.0f}s"
    else:
        limit_text = "ohne Zeitlimit"
    log_fn(
        f"Rechteck-Merge: schneller Blockscan {width}x{height} Raster "
        f"({limit_text}, workers={worker_count}, tolerance={tolerance:.6g})."
    )

    def evaluate(candidate):
        r0, r1, c0, c1 = candidate
        err_anti = _patch_error(
            xx,
            yy,
            zz,
            r0,
            r1,
            c0,
            c1,
            "anti",
            max_eval_vertices=max_eval_vertices,
        )
        err_main = _patch_error(
            xx,
            yy,
            zz,
            r0,
            r1,
            c0,
            c1,
            "main",
            max_eval_vertices=max_eval_vertices,
        )
        if err_anti <= err_main:
            err = err_anti
            diag = "anti"
        else:
            err = err_main
            diag = "main"
        patch = (r0, r1, c0, c1, diag)
        if (
            err <= tolerance
            and _adaptive_patch_triangle_count(patch) < _raw_patch_triangle_count(patch)
        ):
            return patch
        return None

    def evaluate_many(candidates):
        accepted = []
        for candidate in candidates:
            patch = evaluate(candidate)
            if patch is not None:
                accepted.append(patch)
        return accepted

    def candidate_chunks(candidates):
        if worker_count <= 1 or len(candidates) < worker_count * 8:
            return [candidates]
        chunk_size = max(1, math.ceil(len(candidates) / worker_count))
        return [
            candidates[start : start + chunk_size]
            for start in range(0, len(candidates), chunk_size)
        ]

    executor = ThreadPoolExecutor(max_workers=worker_count) if worker_count > 1 else None
    try:
        for block_size in sizes:
            accepted_for_size = 0
            candidates = []
            for r0 in range(0, cell_h, block_size):
                r1 = min(r0 + block_size, cell_h)
                rows = r1 - r0
                if rows < 2:
                    continue
                for c0 in range(0, cell_w, block_size):
                    c1 = min(c0 + block_size, cell_w)
                    cols = c1 - c0
                    if cols < 2:
                        continue
                    if occupied[r0:r1, c0:c1].any():
                        continue
                    if _rough_count(rough_prefix, r0, r1, c0, c1):
                        continue
                    candidates.append((r0, r1, c0, c1))

                now = time.monotonic()
                if max_seconds is not None and max_seconds > 0 and now - started > max_seconds:
                    log_fn(
                        "Rechteck-Merge: Zeitlimit erreicht, schreibe Rest als Raster."
                    )
                    return None, None
                if now - last_log >= 2.0:
                    log_fn(
                        f"Rechteck-Merge: Block {block_size}, "
                        f"{tested:,} Bereiche geprueft, "
                        f"{len(patches):,} Rechtecke akzeptiert."
                    )
                    last_log = now

            tested += len(candidates)
            chunks = candidate_chunks(candidates)
            if executor is None or len(chunks) == 1:
                accepted_chunks = map(evaluate_many, chunks)
            else:
                accepted_chunks = executor.map(evaluate_many, chunks)
            for accepted in accepted_chunks:
                for patch in accepted:
                    r0, r1, c0, c1, _diag = patch
                    if not occupied[r0:r1, c0:c1].any():
                        patches.append(patch)
                        occupied[r0:r1, c0:c1] = True
                        merged[r0:r1, c0:c1] = True
                        accepted_for_size += 1
            if accepted_for_size:
                log_fn(
                    f"Rechteck-Merge: Block {block_size} abgeschlossen, "
                    f"{accepted_for_size:,} Rechtecke akzeptiert."
                )
    finally:
        if executor is not None:
            executor.shutdown(wait=True)

    log_fn(
        f"Rechteck-Merge: schneller Blockscan fertig nach "
        f"{time.monotonic() - started:.1f}s ({tested:,} Bereiche geprueft)."
    )
    return patches, merged


def _default_planar_tolerance(xx, yy, zz):
    dx = float(np.nanmedian(np.abs(np.diff(xx, axis=1)))) if xx.shape[1] > 1 else 0.0
    dy = float(np.nanmedian(np.abs(np.diff(yy, axis=0)))) if yy.shape[0] > 1 else 0.0
    xy_step = max(dx, dy, 1e-9)
    z_span = float(np.nanmax(zz) - np.nanmin(zz)) if zz.size else 0.0
    return max(xy_step * 0.10, z_span * 0.00025)


def _base_triangle_count(height, width):
    return 2 * (height + width - 2)


def _write_base_fan(fh, xx, yy, base_plane):
    height, width = xx.shape
    ring = []

    for c in range(width):
        ring.append((float(xx[0, c]), float(yy[0, c])))
    for r in range(1, height):
        ring.append((float(xx[r, -1]), float(yy[r, -1])))
    for c in range(width - 2, -1, -1):
        ring.append((float(xx[-1, c]), float(yy[-1, c])))
    for r in range(height - 2, 0, -1):
        ring.append((float(xx[r, 0]), float(yy[r, 0])))

    coords = np.array(ring, dtype=np.float64)
    center = np.array(
        [float(np.mean(coords[:, 0])), float(np.mean(coords[:, 1])), base_plane],
        dtype=np.float32,
    )[None, :]
    base_vertices = np.column_stack(
        (
            coords[:, 0],
            coords[:, 1],
            np.full(coords.shape[0], base_plane, dtype=np.float64),
        )
    ).astype(np.float32, copy=False)

    p0 = base_vertices
    p1 = np.roll(base_vertices, -1, axis=0)
    centers = np.repeat(center, base_vertices.shape[0], axis=0)
    _write_triangle_block(fh, p0, centers, p1)


def _write_wall_strip(fh, top_a, top_b, base_a, base_b, flip=False):
    if flip:
        _write_triangle_block(fh, top_a, base_a, top_b)
        _write_triangle_block(fh, top_b, base_a, base_b)
    else:
        _write_triangle_block(fh, top_a, top_b, base_a)
        _write_triangle_block(fh, base_a, top_b, base_b)


def _write_walls(fh, xx, yy, top_zz, base_plane):
    eastings = xx.astype(np.float32, copy=False)
    northings = yy.astype(np.float32, copy=False)
    top = top_zz.astype(np.float32, copy=False)

    def base_col(arr):
        return np.full(arr.shape, base_plane, dtype=np.float32)

    west_top_a = _col_stack(eastings[:-1, 0], northings[:-1, 0], top[:-1, 0])
    west_top_b = _col_stack(eastings[1:, 0], northings[1:, 0], top[1:, 0])
    west_base_a = _col_stack(eastings[:-1, 0], northings[:-1, 0], base_col(eastings[:-1, 0]))
    west_base_b = _col_stack(eastings[1:, 0], northings[1:, 0], base_col(eastings[1:, 0]))
    _write_wall_strip(fh, west_top_a, west_top_b, west_base_a, west_base_b, flip=False)

    east_top_a = _col_stack(eastings[1:, -1], northings[1:, -1], top[1:, -1])
    east_top_b = _col_stack(eastings[:-1, -1], northings[:-1, -1], top[:-1, -1])
    east_base_a = _col_stack(eastings[1:, -1], northings[1:, -1], base_col(eastings[1:, -1]))
    east_base_b = _col_stack(eastings[:-1, -1], northings[:-1, -1], base_col(eastings[:-1, -1]))
    _write_wall_strip(fh, east_top_a, east_top_b, east_base_a, east_base_b, flip=False)

    north_top_a = _col_stack(eastings[0, :-1], northings[0, :-1], top[0, :-1])
    north_top_b = _col_stack(eastings[0, 1:], northings[0, 1:], top[0, 1:])
    north_base_a = _col_stack(eastings[0, :-1], northings[0, :-1], base_col(eastings[0, :-1]))
    north_base_b = _col_stack(eastings[0, 1:], northings[0, 1:], base_col(eastings[0, 1:]))
    _write_wall_strip(fh, north_top_a, north_top_b, north_base_a, north_base_b, flip=True)

    south_top_a = _col_stack(eastings[-1, 1:], northings[-1, 1:], top[-1, 1:])
    south_top_b = _col_stack(eastings[-1, :-1], northings[-1, :-1], top[-1, :-1])
    south_base_a = _col_stack(eastings[-1, 1:], northings[-1, 1:], base_col(eastings[-1, 1:]))
    south_base_b = _col_stack(eastings[-1, :-1], northings[-1, :-1], base_col(eastings[-1, :-1]))
    _write_wall_strip(fh, south_top_a, south_top_b, south_base_a, south_base_b, flip=True)


def _repair_stl_triangle_count(path):
    size = os.path.getsize(path)
    if size < 84:
        raise ValueError(f"STL-Datei ist zu klein: {path}")
    payload = size - 84
    if payload % 50 != 0:
        raise ValueError(
            f"STL-Dateigroesse passt nicht zu binaerem STL-Layout: {path}"
        )
    actual = payload // 50
    with open(path, "r+b") as fh:
        fh.seek(80)
        declared = struct.unpack("<I", fh.read(4))[0]
        if declared != actual:
            log(
                f"STL: korrigiere Triangle-Count im Header "
                f"({declared:,} -> {actual:,})."
            )
            fh.seek(80)
            fh.write(struct.pack("<I", actual))
    return int(actual)


def write_stl_binary(
    path,
    xx,
    yy,
    zz,
    *,
    base_thickness: float = 50.0,
    planar_merge: bool = True,
    planar_tolerance: float | None = None,
    planar_max_seconds: float = 0.0,
    merge_workers: int | None = None,
    wall_merge: bool = True,
    wall_slope_threshold: float = 1.5,
):
    """
    Schreibt ein binäres STL (solider Körper) aus einem LV95-Grid.

    - path: Ausgabepfad des STL
    - base_thickness: Dicke der Bodenplatte unter min(zz)
    - planar_merge: fasst nur kaum abweichende Rasterbereiche zu groesseren Rechtecken zusammen
    - planar_tolerance: maximal erlaubte Hoehenabweichung in Modell-Einheiten
    - planar_max_seconds: Zeitbudget fuer die Rechteck-Analyse; 0 deaktiviert das Zeitlimit
    - merge_workers: Anzahl Worker-Threads fuer die Rechteck-Analyse; None/0 = automatisch
    - wall_merge: erkennt steile zusammenhaengende Rasterkanten und schreibt sie als Wandflaechen
    - wall_slope_threshold: minimale Steigung dz/dxy fuer Wandkanten
    Rueckgabe: Pfad des geschriebenen STL
    """
    height, width = zz.shape
    if height < 2 or width < 2:
        err("Raster zu klein für STL-Export.")
        sys.exit(1)
    base_thickness = max(0.0, float(base_thickness))
    base_plane = float(np.min(zz) - base_thickness)
    log(
        "STL: bereite Export vor "
        f"({width}x{height} Rasterpunkte, Sockel {base_thickness:.3f})."
    )

    raw_grid_tris = (height - 1) * (width - 1) * 2  # top surface
    wall_patches = []
    skip_cells = None
    wall_replaced_cells = 0
    wall_replacement_tris = 0
    if wall_merge:
        log("Wand-Merge: analysiere steile Wandkanten ...")
        wall_tolerance = (
            _default_planar_tolerance(xx, yy, zz)
            if planar_tolerance is None
            else max(0.0, float(planar_tolerance))
        )
        wall_patches, skip_cells, raw_wall_edges = _detect_wall_patches(
            xx,
            yy,
            zz,
            slope_threshold=float(wall_slope_threshold),
            tolerance=wall_tolerance,
            log_fn=log,
        )
        if wall_patches:
            wall_replaced_cells = int(skip_cells.sum())
            wall_replacement_tris = _wall_replacement_triangle_count(
                xx, yy, zz, wall_patches
            )
            largest_wall = max(_wall_patch_area(patch) for patch in wall_patches)
            log(
                "Wand-Merge: "
                f"{raw_wall_edges:,} steile Punktkanten erkannt, "
                f"{len(wall_patches):,} glatte Wandflaechen geschrieben, "
                f"{wall_replaced_cells:,} Rasterzellen ersetzt, "
                f"{wall_replacement_tris:,} Ersatz-Dreiecke, "
                f"groesste Wand ~{largest_wall:,} Rastersegmente."
            )
        else:
            log("Wand-Merge: keine zusammenhaengenden steilen Wandkanten erkannt.")

    patches = None
    merge_cells = None
    if planar_merge:
        if planar_tolerance is None:
            planar_tolerance = _default_planar_tolerance(xx, yy, zz)
        planar_tolerance = max(0.0, float(planar_tolerance))
        patches, merge_cells = _fast_rect_grid_patches(
            xx,
            yy,
            zz,
            planar_tolerance,
            max_seconds=planar_max_seconds,
            merge_workers=merge_workers or 0,
            skip_cells=skip_cells,
        )
        if patches is not None:
            rect_raw_tris = sum(_raw_patch_triangle_count(patch) for patch in patches)
            rect_tris = _adaptive_grid_triangle_count(patches)
            source_tris = raw_grid_tris - wall_replaced_cells * 2
            grid_tris = source_tris - rect_raw_tris + rect_tris + wall_replacement_tris
            saved = rect_raw_tris - rect_tris
            saved_frac = saved / source_tris if source_tris else 0.0
            if saved_frac < 0.25:
                log(
                    "Rechteck-Merge: zu wenig Ersparnis "
                    f"({saved_frac*100:.1f}%), verwende schnellen Rasterexport."
                )
                patches = None
                merge_cells = None
                grid_tris = raw_grid_tris - wall_replaced_cells * 2 + wall_replacement_tris
            else:
                steep_info = _summarize_steep_patches(xx, yy, zz, patches)
                log(
                    "Rechteck-Merge: "
                    f"{source_tris:,} -> {grid_tris:,} Top-Dreiecke "
                    f"({saved_frac*100:.1f}% weniger, tolerance={planar_tolerance:.6g})."
                )
                if steep_info["patches"]:
                    if steep_info["connected_checked"]:
                        log(
                            "Rechteck-Merge: "
                            f"{steep_info['patches']:,} steile Wand-Patches in "
                            f"{steep_info['groups']:,} zusammenhaengenden Wandgruppen erkannt; "
                            f"groesste Wandgruppe ~{steep_info['largest_cells']:,} Rasterzellen."
                        )
                    else:
                        log(
                            "Rechteck-Merge: "
                            f"{steep_info['patches']:,} steile Wand-Patches erkannt "
                            "(zu viele fuer schnelle Gruppierung)."
                        )
                elif wall_patches:
                    log("Rechteck-Merge: steile Wandflaechen wurden bereits vom Wand-Merge behandelt.")
                else:
                    log("Rechteck-Merge: keine zusammenhaengenden steilen Wandflaechen erkannt.")
        else:
            grid_tris = raw_grid_tris - wall_replaced_cells * 2 + wall_replacement_tris
    else:
        grid_tris = raw_grid_tris - wall_replaced_cells * 2 + wall_replacement_tris

    wall_tris = 4 * (height + width - 2)  # two tris per edge segment
    base_tris = _base_triangle_count(height, width)
    tris = grid_tris + wall_tris + base_tris
    log(f"STL: {tris:,} Dreiecke (solider Koerper, detaillierte Waende/Boden)")

    with open(path, "wb") as fh:
        fh.write(b"Generated by GeoPrint".ljust(80, b" "))
        fh.write(struct.pack("<I", tris))
        if patches is not None:
            combined_skip = merge_cells
            if skip_cells is not None:
                combined_skip = np.logical_or(skip_cells, merge_cells)
            log("STL: schreibe Raster ohne vereinfachte Rechtecke ...")
            _write_grid(
                fh,
                xx,
                yy,
                zz,
                flip=False,
                skip_cells=combined_skip,
                log_fn=log,
                progress_label="STL: schreibe Raster ohne vereinfachte Rechtecke",
            )
            log(f"STL: schreibe {len(patches):,} Rechteck-Patches als vereinfachte Flaechen ...")
            _write_adaptive_grid_conforming(fh, xx, yy, zz, patches, flip=False, log_fn=log)
            if wall_patches:
                log(f"STL: schreibe {len(wall_patches):,} beliebig orientierte Wandflaechen ...")
                _write_wall_replacement_cells(fh, xx, yy, zz, wall_patches)
        else:
            if wall_patches:
                log(
                    "STL: schreibe Raster ohne erkannte Wandzellen "
                    f"und {len(wall_patches):,} glatte Wandflaechen ..."
                )
                _write_grid(
                    fh,
                    xx,
                    yy,
                    zz,
                    flip=False,
                    skip_cells=skip_cells,
                    log_fn=log,
                    progress_label="STL: schreibe Raster ohne Wandzellen",
                )
                _write_wall_replacement_cells(fh, xx, yy, zz, wall_patches)
            else:
                log("STL: schreibe volles Raster ohne Rechteck-Merge ...")
                _write_grid(
                    fh,
                    xx,
                    yy,
                    zz,
                    flip=False,
                    log_fn=log,
                    progress_label="STL: schreibe volles Raster",
                )
        log("STL: schreibe Boden und Seitenwaende ...")
        _write_base_fan(fh, xx, yy, base_plane)
        _write_walls(fh, xx, yy, zz, base_plane)
    log("STL: pruefe Triangle-Count und Dateigroesse ...")
    _repair_stl_triangle_count(path)
    return path

__all__ = [
    "log",
    "err",
    "search_tiles",
    "read_tile",
    "alloc_grid",
    "paste_tile",
    "write_stl_binary",
]
