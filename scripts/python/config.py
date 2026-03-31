"""
config.py – Gemensam konfiguration för NVI Python-workflow
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# ── Testområde ───────────────────────────────────────────────────────────────
# Fiby urskog, Uppland, Sverige – välkänd gammelskog, bra testfall
AOI_NAME = "fiby_urskog"
AOI_BBOX = {
    "min_lon": 17.02,
    "max_lon": 17.12,
    "min_lat": 59.85,
    "max_lat": 59.92,
}
EPSG_WGS84  = 4326
EPSG_SWEREF = 3006   # SWEREF99TM – svenska geodata använder detta
EPSG_UTM33  = 32633  # UTM zon 33N – alternativ projicering

# ── Katalogstruktur ──────────────────────────────────────────────────────────
REPO_DIR = Path(__file__).resolve().parents[2]

DATA_DIR      = REPO_DIR / "data"
RAW_DIR       = DATA_DIR / "raw"
PROC_DIR      = DATA_DIR / "processed"
OUTPUTS_DIR   = REPO_DIR / "outputs"
FIGURES_DIR   = OUTPUTS_DIR / "figures"
RASTERS_DIR   = OUTPUTS_DIR / "rasters"
SPECIES_OUTPUT_DIR = OUTPUTS_DIR / "species"  # Fas A: overlay (gpkg + raster)

# Artdata (fas A) – lägg manuellt under data/raw/arter/ (mappen ignoreras av git)
ARTER_ROOT         = RAW_DIR / "arter"
ARTER_OBS_DIR      = ARTER_ROOT / "observations"
ARTER_RODLISTA_DIR = ARTER_ROOT / "rodlista"
ARTER_DERIVED_DIR  = ARTER_ROOT / "derived"
# Buffer runt AOI (meter) när observationer filtreras (WGS84-bbox expanderas ungefärligt)
SPECIES_AOI_BUFFER_M = 5000

# Svenska datakällor
_NMD_ROOT        = Path("E:/nmd")
NMD_DIR          = _NMD_ROOT                   # Naturvårdsverket NMD (marktäcke 10m) – extern disk
NMD_BASSKIKT     = _NMD_ROOT / "NMD2023_basskikt_v2_1"        / "NMD2023bas_v2_1.tif"
NMD_OBJHOJD      = _NMD_ROOT / "NMD2023_Tillaggsskikt_Objekthojd_objekttackning_v1_1" / "NMD2023_Objekt_hojd_intervall_5_till_45_v1_1.tif"
NMD_TRADSLAG_DIR = _NMD_ROOT / "NMD2023_Tradslag_v1_0"
SKOGSST_DIR      = RAW_DIR / "skogsstyrelsen" # Skogsstyrelsen avverkningar
LM_DIR           = RAW_DIR / "lantmateriet"   # Lantmäteriet höjddata
SLU_DIR          = RAW_DIR / "slu"            # SLU Skogliga Grunddata
S2_EXPORT_DIR    = RAW_DIR / "gee_exports"    # GEE-exporterade GeoTIFF:er

for d in [NMD_DIR, SKOGSST_DIR, LM_DIR, SLU_DIR, S2_EXPORT_DIR,
          PROC_DIR, FIGURES_DIR, RASTERS_DIR, SPECIES_OUTPUT_DIR,
          ARTER_OBS_DIR, ARTER_RODLISTA_DIR, ARTER_DERIVED_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ── Fallback-sökvägar (globala dataset från tidigare körning) ────────────────
# Används automatiskt om svenska primärkällor saknas.
HANSEN_DIR     = RAW_DIR / "hansen"
WORLDCOVER_DIR = RAW_DIR / "worldcover"
DEM_FALLBACK_DIR = RAW_DIR / "dem"

# ── Viktning NVI-delindex (summa = 1.0) ──────────────────────────────────────
WEIGHTS = {
    "structure":   0.40,
    "continuity":  0.40,
    "moisture":    0.20,
}

# ── Svenska datakällor – API-nycklar ─────────────────────────────────────────
# Lantmäteriet: gratis konto på https://opendata.lantmateriet.se/
# Sätt LANTMATERIET_API_KEY som miljövariabel eller i .env-fil
LANTMATERIET_API_KEY = os.environ.get("LANTMATERIET_API_KEY", "")

# ── NMD-klassomvandling (klass → bredare kategori) ───────────────────────────
# NMD2023: 1=barrskog, 2=lövskog, 3=blandskog, 4=åkermark, 5=öppen mark,
#          6=våtmark, 7=exploaterad mark, 8=vatten
NMD_FOREST_CLASSES  = [1, 2, 3]      # Alla skogsklasser
NMD_WETLAND_CLASSES = [6]             # Våtmark – viktig för fuktindex
NMD_OPEN_CLASSES    = [4, 5]          # Jordbruk + öppen mark
