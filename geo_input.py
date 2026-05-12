#!/usr/bin/env python3
"""User input helper utilities for GeoPrint."""

from dataclasses import dataclass
import html
import re
from typing import Dict, List, Optional, Tuple

import requests

SEARCH_URL = "https://api3.geo.admin.ch/rest/services/api/SearchServer"
MAX_SIDE_M = 5000.0
LV95_E_RANGE = (2_420_000.0, 2_900_000.0)
LV95_N_RANGE = (1_030_000.0, 1_350_000.0)
WGS84_LAT_RANGE = (45.7, 47.9)
WGS84_LON_RANGE = (5.7, 10.8)


@dataclass
class UserSelection:
    resolution_m: float
    bbox: Tuple[float, float, float, float]
    label: str
    center: Tuple[float, float]
    z_ex: float
    base_thickness: float
    interp: str
    out_path: str
    scale_denominator: float


class GeocodingError(RuntimeError):
    """Raised when the swiss geo.admin search API returns no data."""


def _fmt(value: float) -> str:
    text = f"{value:.6f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _in_range(value: float, bounds: Tuple[float, float]) -> bool:
    return bounds[0] <= value <= bounds[1]


def validate_lv95_point(easting: float, northing: float) -> None:
    if _in_range(easting, LV95_N_RANGE) and _in_range(northing, LV95_E_RANGE):
        raise ValueError(
            "LV95-Koordinaten scheinen vertauscht zu sein. "
            "Erwartet wird: E N, z. B. 2600000 1200000."
        )
    if not _in_range(easting, LV95_E_RANGE) or not _in_range(northing, LV95_N_RANGE):
        raise ValueError(
            "Koordinaten liegen ausserhalb des plausiblen Schweizer LV95-Bereichs. "
            f"Erwartet E {_fmt(LV95_E_RANGE[0])}-{_fmt(LV95_E_RANGE[1])}, "
            f"N {_fmt(LV95_N_RANGE[0])}-{_fmt(LV95_N_RANGE[1])}."
        )


def validate_wgs84_point(lat_deg: float, lon_deg: float) -> None:
    if _in_range(lat_deg, WGS84_LON_RANGE) and _in_range(lon_deg, WGS84_LAT_RANGE):
        raise ValueError(
            "WGS84-Koordinaten scheinen vertauscht zu sein. "
            "Erwartet wird: lat lon, z. B. 46.57 7.69."
        )
    if not _in_range(lat_deg, WGS84_LAT_RANGE) or not _in_range(lon_deg, WGS84_LON_RANGE):
        raise ValueError(
            "WGS84-Koordinaten liegen ausserhalb der Schweiz. "
            f"Erwartet lat {_fmt(WGS84_LAT_RANGE[0])}-{_fmt(WGS84_LAT_RANGE[1])}, "
            f"lon {_fmt(WGS84_LON_RANGE[0])}-{_fmt(WGS84_LON_RANGE[1])}."
        )


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _normalize(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _score_result(result: Dict, normalized_query: str) -> Tuple[float, int, int, int]:
    attrs = result.get("attrs") or {}
    object_class = (attrs.get("objectclass") or "").upper()
    origin = (attrs.get("origin") or "").lower()
    label_plain = _normalize(_strip_tags(html.unescape(attrs.get("label") or "")))
    detail_plain = _normalize(attrs.get("detail") or "")
    contains_query = int(
        normalized_query
        and (
            normalized_query in label_plain
            or normalized_query in detail_plain
            or normalized_query in _normalize(attrs.get("featureId", "") or "")
        )
    )
    is_named_feature = int("TLM_NAME" in object_class or "GIPFEL" in object_class)
    origin_score = 1 if origin in ("gazetteer", "gg25") else 0
    weight = float(result.get("weight") or 0.0)
    return (weight, is_named_feature, origin_score, contains_query)


def _pick_best_result(results: List[Dict], query: str) -> Dict:
    normalized_query = _normalize(query)
    return max(results, key=lambda res: _score_result(res, normalized_query))


def get_coords(query: str, *, limit: int = 5, timeout: int = 20) -> Tuple[float, float, str]:
    query = (query or "").strip()
    if not query:
        raise GeocodingError("Leerer Ort ohne Query.")
    if len(query) < 2:
        raise GeocodingError("Ortseingabe ist zu kurz.")

    response = requests.get(
        SEARCH_URL,
        params={"searchText": query, "type": "locations", "sr": "2056", "limit": limit},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    results = payload.get("results") or []
    if not results:
        raise GeocodingError(f"Kein Treffer für '{query}'.")

    best = _pick_best_result(results, query)
    attrs = best.get("attrs") or {}
    # API returns swapped x/y for LV95 -> swap back to get (E, N)
    try:
        easting = float(attrs["y"])
        northing = float(attrs["x"])
    except (KeyError, ValueError) as exc:
        raise GeocodingError("Antwort enthielt keine Koordinaten.") from exc
    try:
        validate_lv95_point(easting, northing)
    except ValueError as exc:
        raise GeocodingError(f"Treffer liegt ausserhalb des gueltigen Bereichs: {exc}") from exc

    label_html = attrs.get("label", query)
    label = _strip_tags(html.unescape(label_html)) or query
    return easting, northing, label


def _prompt_float(
    prompt: str,
    *,
    default: Optional[float] = None,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
) -> float:
    while True:
        raw = input(prompt).strip()
        if not raw:
            if default is not None:
                return default
            print("Bitte einen Wert eingeben.")
            continue
        try:
            value = float(raw)
        except ValueError:
            print("Ungültige Zahl, bitte erneut versuchen.")
            continue
        if min_value is not None and value < min_value:
            print(f"Der Wert muss >= {min_value} sein.")
            continue
        if max_value is not None and value > max_value:
            print(f"Der Wert muss <= {max_value} sein.")
            continue
        return value


def validate_bbox_size(
    bbox: Tuple[float, float, float, float],
    *,
    max_side_m: Optional[float] = MAX_SIDE_M,
) -> None:
    min_e, min_n, max_e, max_n = bbox
    width = max_e - min_e
    height = max_n - min_n
    if width <= 0 or height <= 0:
        raise ValueError("BBox muss positive Breite und Hoehe haben.")
    if max_side_m is not None and (width > max_side_m or height > max_side_m):
        raise ValueError(
            "Die Flaeche darf maximal "
            f"{_fmt(max_side_m)} m x {_fmt(max_side_m)} m gross sein. "
            f"Aktuell: {_fmt(width)} m x {_fmt(height)} m."
        )


def validate_bbox(
    bbox: Tuple[float, float, float, float],
    *,
    max_side_m: Optional[float] = MAX_SIDE_M,
) -> None:
    min_e, min_n, max_e, max_n = bbox
    validate_bbox_size(bbox, max_side_m=max_side_m)
    validate_lv95_point(min_e, min_n)
    validate_lv95_point(max_e, max_n)


def _prompt_choice(prompt: str, choices: List[str], default: str) -> str:
    choice_map = {c.lower(): c for c in choices}
    default_lower = default.lower()
    while True:
        raw = input(f"{prompt} [{default}]: ").strip().lower()
        if not raw:
            return choice_map[default_lower]
        if raw in choice_map:
            return choice_map[raw]
        print(f"Ungültige Wahl. Erlaubt: {', '.join(choices)}")


def _prompt_str(prompt: str, default: str) -> str:
    raw = input(f"{prompt} [{default}]: ").strip()
    return raw or default


def _clean_number_token(token: str) -> str:
    token = token.replace("'", "").replace("’", "").replace("`", "")
    token = token.replace("_", "")
    return token


def _number_tokens(raw: str) -> List[str]:
    normalized = raw.replace(";", " ")
    tokens = re.findall(r"[-+]?(?:\d[\d'_`’]*)(?:[.,]\d+)?", normalized)
    if len(tokens) == 1 and "," in tokens[0] and "." not in tokens[0]:
        left, right = tokens[0].split(",", 1)
        if len(left.replace("'", "").replace("_", "")) >= 4 and len(right) >= 4:
            return [left, right]
    return tokens


def _parse_number_token(token: str) -> float:
    cleaned = token.strip().strip(",;")
    cleaned = cleaned.replace("'", "").replace("â€™", "").replace("’", "").replace("`", "")
    cleaned = cleaned.replace("_", "")
    if "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    elif "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    return float(cleaned)


def _wgs84_to_lv95(lat_deg: float, lon_deg: float) -> Tuple[float, float]:
    lat_sec = lat_deg * 3600.0
    lon_sec = lon_deg * 3600.0
    lat_aux = (lat_sec - 169028.66) / 10000.0
    lon_aux = (lon_sec - 26782.5) / 10000.0
    y = (
        600072.37
        + 211455.93 * lon_aux
        - 10938.51 * lon_aux * lat_aux
        - 0.36 * lon_aux * (lat_aux**2)
        - 44.54 * (lon_aux**3)
    )
    x = (
        200147.07
        + 308807.95 * lat_aux
        + 3745.25 * (lon_aux**2)
        + 76.63 * (lat_aux**2)
        - 194.56 * (lon_aux**2) * lat_aux
        + 119.79 * (lat_aux**3)
    )
    return y + 2000000.0, x + 1000000.0


def _parse_manual_coords(raw: str) -> Optional[Tuple[float, float]]:
    parts = _number_tokens(raw)
    if len(parts) != 2:
        return None
    try:
        first = _parse_number_token(parts[0])
        second = _parse_number_token(parts[1])
    except ValueError:
        return None
    # LV95 ranges roughly 2.4-2.9 easting, 1.0-1.4 northing (in millions)
    if first > 2000000 and second > 1000000:
        validate_lv95_point(first, second)
        return first, second
    if first > 1000000 and second > 2000000:
        validate_lv95_point(first, second)
    # allow lat-lon (degrees)
    if abs(first) <= 90 and abs(second) <= 180:
        validate_wgs84_point(first, second)
        easting, northing = _wgs84_to_lv95(first, second)
        validate_lv95_point(easting, northing)
        return easting, northing
    if len(parts) == 2:
        raise ValueError(
            "Koordinatenformat nicht erkannt. Erlaubt sind LV95 'E N' "
            "oder WGS84 'lat lon'."
        )
    return None


def prompt_user_bbox_config(
    res_default: float,
    width_default: float,
    height_default: float,
    zex_default: float,
    base_default: float,
    interp_default: str,
    out_default: str,
    scale_default: float,
    max_side_m: Optional[float] = MAX_SIDE_M,
) -> UserSelection:
    print("Interaktiver Modus: Bitte Ort, Auflösung und Einstellungen eingeben.")
    easting = northing = None
    while True:
        ort = input(
            "Ort (z. B. Bern, Zürich, Matterhorn oder '2601000 1200500' bzw. '46.57 7.69'): "
        ).strip()
        if not ort:
            print("Bitte etwas eingeben.")
            continue
        try:
            coords = _parse_manual_coords(ort)
        except ValueError as exc:
            print(f"{exc} Bitte erneut versuchen.\n")
            continue
        if coords:
            easting, northing = coords
            label = f"Koordinaten E={_fmt(easting)}, N={_fmt(northing)}"
            print(f"Koordinaten akzeptiert: {label}")
            break
        try:
            easting, northing, label = get_coords(ort)
            print(f"Treffer: {label}  LV95: E={_fmt(easting)}, N={_fmt(northing)}")
            break
        except GeocodingError as exc:
            print(f"{exc} Bitte erneut versuchen.\n")
        except requests.RequestException as exc:
            print(f"HTTP-Fehler bei der Suche: {exc}. Bitte erneut versuchen.\n")

    resolution = _prompt_float(
        f"Auflösung in Metern pro Pixel [{res_default}]: ",
        default=res_default,
        min_value=1e-6,
    )
    width_prompt = "Rechteckbreite in Metern (E-W"
    if max_side_m is not None:
        width_prompt += f", max. {_fmt(max_side_m)}"
    width_prompt += f") [{width_default}]: "
    width = _prompt_float(
        width_prompt,
        default=width_default,
        min_value=10,
        max_value=max_side_m,
    )
    height_prompt = "Rechteckhöhe in Metern (N-S"
    if max_side_m is not None:
        height_prompt += f", max. {_fmt(max_side_m)}"
    height_prompt += f") [{height_default}]: "
    height = _prompt_float(
        height_prompt,
        default=height_default,
        min_value=10,
        max_value=max_side_m,
    )
    z_ex = _prompt_float(
        f"Z-Überhöhungsfaktor [{zex_default}]: ",
        default=zex_default,
        min_value=0.1,
    )
    base_thickness = _prompt_float(
        f"Sockelhöhe (Extrusion nach unten) [{base_default}]: ",
        default=base_default,
        min_value=0.0,
    )
    scale_denominator = _prompt_float(
        f"Massstab (1:x, kleiner = grösser) [{scale_default}]: ",
        default=scale_default,
        min_value=1e-6,
    )
    interp = _prompt_choice("Resampling (nn/bilinear)", ["nn", "bilinear"], interp_default)
    out_path = _prompt_str("Ausgabedatei (STL)", out_default)
    # Collection is fixed in programm.py; no prompt.

    half_w = width / 2.0
    half_h = height / 2.0
    bbox = (easting - half_w, northing - half_h, easting + half_w, northing + half_h)
    validate_bbox_size(bbox, max_side_m=max_side_m)
    return UserSelection(
        resolution_m=resolution,
        bbox=bbox,
        label=label,
        center=(easting, northing),
        z_ex=z_ex,
        base_thickness=base_thickness,
        interp=interp,
        out_path=out_path,
        scale_denominator=scale_denominator,
    )


__all__ = [
    "get_coords",
    "prompt_user_bbox_config",
    "UserSelection",
    "GeocodingError",
    "MAX_SIDE_M",
    "validate_bbox",
    "validate_bbox_size",
    "validate_lv95_point",
    "validate_wgs84_point",
]
