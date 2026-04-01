"""
config.py – Gemensam konfiguration för NVI Python-workflow
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# ── Aktivt AOI – byt kommentar för att växla område ─────────────────────────

# Djupedal, Hisingen, Göteborg – NVI utförd av Länsstyrelsen Västra Götaland
# (Rapport 2022:42, Svensk Naturförvaltning AB). Ekdominerad blandskog, ca 113 ha
# inventerad yta. Klass 2: 43 ha, Klass 3: 56 ha. Bra valideringsfall.
AOI_NAME  = "djupedal_hisingen"
AOI_LABEL = "Djupedal, Hisingen, Göteborg"
AOI_BBOX = {
    "min_lon": 11.935,
    "max_lon": 11.973,
    "min_lat": 57.806,
    "max_lat": 57.828,
}

# Fiby urskog, Uppland – välkänd gammelskog, ursprungligt testfall
# AOI_NAME = "fiby_urskog"
# AOI_BBOX = {
#     "min_lon": 17.02,
#     "max_lon": 17.12,
#     "min_lat": 59.85,
#     "max_lat": 59.92,
# }
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
LM_DIR           = RAW_DIR / "lantmateriet"   # Lantmäteriet höjddata (DTM/DSM COG)
# Punktmoln LAZ (Laserdata skog öppen data, ev. NH): samma arbetsmapp för compute_lidar_chm.py
LM_LIDAR_LAZ_DIR = LM_DIR / "lidar_laz"
LM_NH_LAZ_DIR    = LM_LIDAR_LAZ_DIR  # alias (tidigare nh_laz)
# Öppen hämtplats för Laserdata Skog (CC0). Överstyr med LM_FOREST_LAZ_BASE_URL om Lantmäteriet ändrar värd.
LM_FOREST_LAZ_BASE_URL = os.environ.get(
    "LM_FOREST_LAZ_BASE_URL",
    "https://download-opendata.lantmateriet.se",
).rstrip("/")
# Om HTTPS blockeras: samma data via anonym FTP (port 21), samma katalogstruktur.
LM_FOREST_LAZ_FTP_HOST = os.environ.get(
    "LM_FOREST_LAZ_FTP_HOST",
    "download-opendata.lantmateriet.se",
)
LM_FOREST_LAZ_CONNECT_TIMEOUT = int(os.environ.get("LM_FOREST_LAZ_CONNECT_TIMEOUT", "120"))
# Efter misslyckad HTTPS: forsok FTP (1/true). Satt LM_FOREST_LAZ_TRY_FTP=0 for att stanga.
LM_FOREST_LAZ_TRY_FTP = os.environ.get("LM_FOREST_LAZ_TRY_FTP", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
# Satt till 1 om bara FTP fungerar pa ditt nat
LM_FOREST_LAZ_FTP_FIRST = os.environ.get("LM_FOREST_LAZ_FTP_FIRST", "0").strip().lower() in (
    "1",
    "true",
    "yes",
)
# Produktionsstatus för skogslaser – vilka skanningsområden som är "på lager"
LM_UTFALL_LASER_SKOG_URL = os.environ.get(
    "LM_UTFALL_LASER_SKOG_URL",
    "https://www.lantmateriet.se/External/geolex/bild_hojd/utfall/utfall_laserdata_skog.json",
)
SLU_DIR          = RAW_DIR / "slu"            # SLU Skogliga Grunddata
S2_EXPORT_DIR    = RAW_DIR / "gee_exports"    # GEE-exporterade GeoTIFF:er
# Naturvårdsverket – skyddad natur (INSPIRE Protected Sites, WFS)
NATURVARDSVERKET_DIR = RAW_DIR / "naturvardsverket"
PROTECTED_SITES_DIR  = NATURVARDSVERKET_DIR / "skyddad_natur"
# Skogsstyrelsen – nyckelbiotoper (öppet API, samma källa som avverkningar)
NYCKELBIOTOP_DIR = SKOGSST_DIR  # sparas i skogsstyrelsen/-mappen
# Naturvårdsverket – Natura Naturtypskartan (NNK), nedladdning per län
NNK_DIR = NATURVARDSVERKET_DIR / "nnk"
# Länsbokstav för NNK-nedladdning (O = Västra Götaland, C = Uppsala/Fiby etc.)
NNK_LAN = "O"

for d in [NMD_DIR, SKOGSST_DIR, LM_DIR, LM_LIDAR_LAZ_DIR, SLU_DIR, S2_EXPORT_DIR,
          NATURVARDSVERKET_DIR, PROTECTED_SITES_DIR, NNK_DIR,
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
# Föredragen metod: consumer_key + consumer_secret i .env → automatisk token-hämtning
# Fallback: statisk LANTMATERIET_API_KEY (JWT, löper ut)
LANTMATERIET_API_KEY      = os.environ.get("LANTMATERIET_API_KEY", "")
LANTMATERIET_CONSUMER_KEY    = os.environ.get("consumer_key", "")
LANTMATERIET_CONSUMER_SECRET = os.environ.get("consumer_secret", "")
# Om NH-punktmoln exponeras som egna STAC-kollektioner: kommaseparerade id, t.ex. nh-64_3,nh-64_2
LM_STAC_NH_COLLECTIONS = [
    c.strip()
    for c in os.environ.get("LM_STAC_NH_COLLECTIONS", "").split(",")
    if c.strip()
]

def get_lantmateriet_token() -> str:
    """Hämtar en färsk Bearer-token via OAuth2 client_credentials.
    Faller tillbaka på statisk LANTMATERIET_API_KEY om consumer_key saknas."""
    if LANTMATERIET_CONSUMER_KEY and LANTMATERIET_CONSUMER_SECRET:
        import requests as _req
        r = _req.post(
            "https://api.lantmateriet.se/token",
            data={"grant_type": "client_credentials"},
            auth=(LANTMATERIET_CONSUMER_KEY, LANTMATERIET_CONSUMER_SECRET),
            timeout=15,
        )
        r.raise_for_status()
        return r.json()["access_token"]
    return LANTMATERIET_API_KEY

# ── NMD-klassomvandling (klass → bredare kategori) ───────────────────────────
# NMD2023 basskikt v2.1 använder hierarkiska koder:
#   1xx (100–199) = Produktiv skogsmark (barrskog 111–118, lövblandad 114–118, lövskog 121–128)
#   2xx           = Jordbruksmark (åker 211–, betesmark 221–)
#   3              = Blandskog (äldre enkel klass, kan förekomma vid kanteffekter)
#   4xx/5xx       = Öppen mark, hyggen
#   6xx           = Våtmark
#   7xx           = Exploaterad mark
#   8xx           = Vatten
# Äldre enkel NMD: 1=barrskog, 2=lövskog, 3=blandskog — stöds som fallback
NMD_FOREST_CLASSES  = list(range(100, 200)) + [1, 2, 3]   # 1xx + äldre enkla koder
NMD_WETLAND_CLASSES = list(range(600, 700)) + [6]
NMD_OPEN_CLASSES    = list(range(200, 600)) + [4, 5]
