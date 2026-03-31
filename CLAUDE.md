# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo does

Geodata-driven Naturvärdesinventering (NVI): a reproducible pipeline that uses **Swedish open geodata** (primary path) to produce a prioritized hotspot map for targeted field inventory. Three sub-indices (structure 40%, continuity 40%, moisture 20%) are combined into a weighted NVI score and classified into three priority classes (1–3).

A static GitHub Pages site (`docs/index.html`) showcases the **primary** data story (NMD, Lantmäteriet, Skogsstyrelsen). The Python code can still use **optional** fallbacks (GEE exports, Hansen, Copernicus DEM) when those folders are populated—see below.

**Metod för människor (svenska):** se [`PIPELINE_METOD.md`](PIPELINE_METOD.md) i repots rot — steg-för-steg vad varje skript gör, vilka filer som skapas, och en bilaga om NMD-filnamn, GEE och Lantmäteriets STAC. **Artdata / Rödlista** (hämtning + Fas A-överlagring): [`SPECIES_RODLISTA.md`](SPECIES_RODLISTA.md). Senare faser (B–C) där är fortfarande roadmap.

## Running the pipeline

```bash
# Install dependencies
pip install -r requirements.txt

# 1. Download Swedish geodata (NMD ~13 GB, requires explicit confirmation)
python scripts/python/download_data.py --nmd-confirm

# 2. Compute sub-indices
python scripts/python/compute_indices.py

# 3. Classify hotspots and save raster
python scripts/python/hotspot_model.py

# 4. Regenerate showcase figures for GitHub Pages (hämtar bakgrundskarta; kräver nätverk)
python scripts/python/generate_showcase.py

# 5a. (Optional) Hämta öppna observationer för AOI från GBIF (ingen nyckel)
python scripts/python/fetch_public_observations.py

# 5b. (Optional, fas A) Artobservationer + Rödlista → GPKG + overlay-raster i outputs/species/
python scripts/python/species_overlay_a.py --obs data/raw/arter/observations/gbif_fiby_urskog.gpkg --rodlista data/raw/arter/rodlista/…
```

Step 2 depends on step 1 outputs. Step 4 depends on step 3 output (`outputs/rasters/fiby_urskog_hotspot_class.tif`) and step 2 outputs in `data/processed/`.

## Configuration (`scripts/python/config.py`)

All scripts import from `config.py`. Key settings to change when adapting to a new area:

- `AOI_NAME` — prefix for all output file names
- `AOI_BBOX` — bounding box in WGS84 (lon/lat)
- `_NMD_ROOT = Path("E:/nmd")` — NMD data lives on an external drive, not in the repo

API key for Lantmäteriet goes in `.env` at repo root as `LANTMATERIET_API_KEY`. For **SLU SOS** (Artdatabanken): `SOS_API_BASE` and `SOS_SUBSCRIPTION_KEY`. The `.env` is loaded automatically by `config.py` via `python-dotenv`.

## Data sources and paths (primary)

| Source | Local path | What it feeds |
|---|---|---|
| NMD 2023 Basskikt | `E:/nmd/NMD2023_basskikt_v2_1/NMD2023bas_v2_1.tif` | Strukturindex |
| NMD Objekthöjd | `E:/nmd/NMD2023_Tillaggsskikt_.../NMD2023_Objekt_hojd_...tif` | Strukturindex |
| NMD Trädslag | `E:/nmd/NMD2023_Tradslag_v1_0/` | Strukturindex (ädellövsbonus) |
| Skogsstyrelsen avverkningar | `data/raw/skogsstyrelsen/` | Kontinuitetsindex |
| Lantmäteriet lidar DTM | `data/raw/lantmateriet/` | Fuktindex (TWI) |
| Naturvårdsverket skyddad natur (optional) | `data/raw/naturvardsverket/skyddad_natur/protected_sites_<AOI>.gpkg` | Showcase only (`hotspot_protected_context.png`); not used in NVI weights |

## Optional inputs and fallbacks (`compute_indices.py`)

The pipeline tries sources **in order** and uses the first that has data on disk.

- **Strukturindex**: SLU Skogliga Grunddata (biomassa/höjd) → GEE export in `data/raw/gee_exports/` (*Delindex* GeoTIFF) → **NMD** basskikt + trädslag + objekthöjd → Hansen treecover in `data/raw/hansen/` (global fallback)

- **Kontinuitetsindex**: Skogsstyrelsen avverkningsraster → GEE export (NDVI stability bands, if multi-band file) → neutral 0.5 if nothing found

- **Fuktindex**: Lantmäteriet DTM (TWI) → NMD våtmark-only proxy → Copernicus DEM in `data/raw/dem/` → neutral 0.5

The public showcase describes the **NMD + Lantmäteriet + Skogsstyrelsen** path only; optional/fallback rows are for developers extending or running with partial Swedish data.

## Coordinate systems

All Swedish datasets are in SWEREF99TM (EPSG:3006). Global fallback datasets are in WGS84. The `clip_and_read()` function in `compute_indices.py` takes a `crs_is_sweref=True/False` parameter to handle both.

## GitHub Pages

`docs/index.html` is a self-contained single-page site (no build step, no JS framework). It references three showcase images: `docs/assets/hotspot_showcase.png`, `docs/assets/method_diagram.png`, and optionally `docs/assets/hotspot_protected_context.png` (only produced when the protected-sites GPKG exists; see `download_data.py --protected-sites`). Regenerate with `generate_showcase.py` and commit the PNGs when updating figures.

Enable Pages: GitHub repo Settings → Pages → branch `main`, folder `/docs`.

## Optional: Google Earth Engine script

`scripts/gee/01_nvi_screening.js` is a **standalone** experiment: paste into the GEE Code Editor. It mirrors AOI and weights but uses Sentinel-2/1 and SRTM. Exports go to Google Drive; place rasters under `data/raw/gee_exports/` if you want them to participate in the Python fallback chain above. It is **not** required for the Swedish-only workflow highlighted on the site.
