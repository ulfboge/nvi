"""
sweep_test_presets.py
Gemensamma AOI-preset for kalibrering/svep: stort LstE eller mindre Kungsbacka (config-bbox).

Kungsbacka anvandar inte miljovariable for AOI utan AOI_NAME/AOI_BBOX i config.py.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from config import REPO_DIR, RASTERS_DIR

_AOIVAR_KEYS = (
    "AOI_NAME_OVERRIDE",
    "AOI_LABEL_OVERRIDE",
    "AOI_MIN_LON",
    "AOI_MAX_LON",
    "AOI_MIN_LAT",
    "AOI_MAX_LAT",
)

PRESETS: dict[str, dict[str, Any]] = {
    "lste": {
        "extra_env": {
            "AOI_NAME_OVERRIDE": "lste_ostergotland",
            "AOI_LABEL_OVERRIDE": "LstE Naturvardsobjekt i vatten",
            "AOI_MIN_LON": "14.563198634931489",
            "AOI_MAX_LON": "16.87699060919273",
            "AOI_MIN_LAT": "57.69909546328842",
            "AOI_MAX_LAT": "58.95371775026091",
            "HOTSPOT_SKIP_PATCH_FILTER": "1",
        },
        "hotspot_aoi_name": "lste_ostergotland",
        "gpkg_path": REPO_DIR
        / "data"
        / "raw"
        / "gpkg_normalized"
        / "lste_naturvardesobjekt_vatten_210218_norm.gpkg",
        "class_field": "nvklass",
    },
    "kungsbacka": {
        "extra_env": {
            "HOTSPOT_SKIP_PATCH_FILTER": "1",
        },
        "hotspot_aoi_name": "kungsbacka_vastra",
        "gpkg_path": REPO_DIR
        / "data"
        / "raw"
        / "gpkg"
        / "Naturvärdesinventering västra Kungsbacka kommun.gpkg",
        "class_field": "nvklass",
    },
}

PRESET_CHOICES = tuple(sorted(PRESETS))


def clear_aoi_overrides(env: dict[str, str]) -> None:
    for k in _AOIVAR_KEYS:
        env.pop(k, None)


def get_preset(name: str) -> dict[str, Any]:
    key = name.strip().lower()
    if key not in PRESETS:
        raise ValueError(
            f"Okänd preset {name!r}. Välj: {', '.join(PRESET_CHOICES)}"
        )
    return PRESETS[key]


def hotspot_tif_for_preset(preset_name: str) -> Path:
    p = get_preset(preset_name)
    return RASTERS_DIR / f'{p["hotspot_aoi_name"]}_hotspot_class.tif'


def nvi_score_tif_for_preset(preset_name: str) -> Path:
    p = get_preset(preset_name)
    from config import PROC_DIR

    return PROC_DIR / f'{p["hotspot_aoi_name"]}_nvi_score.tif'


def subprocess_env_from_preset(preset_name: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    preset = get_preset(preset_name)
    env = os.environ.copy()
    clear_aoi_overrides(env)
    env.update(preset["extra_env"])
    if extra:
        env.update(extra)
    return env


def resolve_validation_paths(
    preset_name: str,
    gpkg_override: Path | None,
) -> tuple[Path, Path, str]:
    preset = get_preset(preset_name)
    gpkg = gpkg_override if gpkg_override is not None else preset["gpkg_path"]
    return hotspot_tif_for_preset(preset_name), gpkg, str(preset["class_field"])
