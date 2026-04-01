"""
download_data.py
Svenska datakällor för NVI-screening:

  1. NMD 2023 (Naturvårdsverket)           – marktäcke 10m, INGEN autentisering
     OBS: ~2,7 GB nationell fil. Bekräfta med --nmd-confirm.
  2. Skogsstyrelsen avverkningsanmälningar  – störningshistorik via ArcGIS REST,
     INGEN autentisering
  3. SLU Skogliga Grunddata                 – biomassa + trädhöjd (kräver
     tillgång via https://www.slu.se/skogligagrunddata)
  4. Lantmäteriet GSD-Höjddata (STAC)      – lidar-DEM 2m
     KRÄVER gratis nyckel: https://opendata.lantmateriet.se/
     export LANTMATERIET_API_KEY=din_nyckel
  5. Naturvårdsverket skyddad natur (WFS)  – polygoner inom AOI + marginal, ingen nyckel
     INSPIRE ps:ProtectedSite — nationella zip-paket finns också på geodata.naturvardsverket.se

Kör:
  python scripts/python/download_data.py
  python scripts/python/download_data.py --nmd-confirm   # inkl NMD (2,7 GB)
  python scripts/python/download_data.py --protected-sites
"""

import sys
import math
import json
import argparse
import urllib.parse
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    AOI_BBOX,
    AOI_NAME,
    NMD_DIR,
    SKOGSST_DIR,
    LM_DIR,
    SLU_DIR,
    PROTECTED_SITES_DIR,
    NNK_DIR,
    NNK_LAN,
    LANTMATERIET_API_KEY,
    get_lantmateriet_token,
    EPSG_SWEREF,
)

try:
    import requests
    from pyproj import Transformer
    import geopandas as gpd
    import rasterio
    from rasterio.features import rasterize
    from rasterio.transform import from_bounds
    from shapely.geometry import box
except ImportError as e:
    sys.exit(f"[FEL] Saknar paket: {e}\n  Kor: pip install -r requirements.txt")


# ── Koordinattransformation WGS84 → SWEREF99TM ───────────────────────────────
_to_sweref = Transformer.from_crs(4326, EPSG_SWEREF, always_xy=True)

def aoi_sweref():
    x_min, y_min = _to_sweref.transform(AOI_BBOX["min_lon"], AOI_BBOX["min_lat"])
    x_max, y_max = _to_sweref.transform(AOI_BBOX["max_lon"], AOI_BBOX["max_lat"])
    return x_min, y_min, x_max, y_max


# ── Nedladdningshjälpare ──────────────────────────────────────────────────────

def download_file(url: str, dest: Path, overwrite: bool = False,
                  headers: dict = None, stream: bool = True) -> bool:
    if dest.exists() and not overwrite:
        print(f"  [skip] {dest.name}")
        return True
    print(f"  >> {dest.name} ...")
    try:
        resp = requests.get(url, headers=headers or {}, stream=stream, timeout=300)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=2 << 20):
                f.write(chunk)
        size_mb = dest.stat().st_size / 1_000_000
        print(f"  [ok]   {dest.name}  ({size_mb:.0f} MB)")
        return True
    except Exception as e:
        print(f"  [FEL]  {dest.name}: {e}")
        if dest.exists():
            dest.unlink()
        return False


# ── 1. NMD 2023 – Naturvårdsverket ───────────────────────────────────────────
#
# Nationell Marktäckedatabas (10m) från Naturvårdsverket.
# WCS-tjänsten exponerar inga coverages – nedladdning krävs.
# Ingen autentisering krävs.
#
# Lager som laddas ner med --nmd-confirm:
#   Basskikt   – marktäcke klass 1-8       ~2,7 GB  (obligatorisk)
#   Tradslag   – barr/lov/bland            ~5,5 GB  (bättre strukturindex)
#   Objekthojd – trädhöjd + täckning       ~4,8 GB  (ersätter delvis LM-DEM)

NMD_BASE = "https://geodata.naturvardsverket.se/nedladdning/marktacke/NMD2023"

NMD_LAYERS = [
    {
        "url":      f"{NMD_BASE}/Basskikt_v2_x/NMD2023_basskikt_v2_1.zip",
        "zip_name": "NMD2023_basskikt_v2_1.zip",
        "label":    "Basskikt (marktacke)",
        "size_gb":  2.71,
    },
    {
        "url":      f"{NMD_BASE}/Tillaggsskikt/NMD2023_Tradslag_v1_0.zip",
        "zip_name": "NMD2023_Tradslag_v1_0.zip",
        "label":    "Tradslag (barr/lov/bland)",
        "size_gb":  5.51,
    },
    {
        "url":      f"{NMD_BASE}/Tillaggsskikt/NMD2023_Tillaggsskikt_Objekthojd_objekttackning_v1_1.zip",
        "zip_name": "NMD2023_Objekthojd_v1_1.zip",
        "label":    "Objekthojd + tackning",
        "size_gb":  4.78,
    },
]


def download_nmd(confirm: bool = False) -> None:
    print("\n[NMD 2023 – Naturvårdsverket marktacke]")

    if not confirm:
        total_gb = sum(l["size_gb"] for l in NMD_LAYERS)
        print(
            f"  Tre lager, totalt ~{total_gb:.1f} GB. Hoppar over automatisk nedladdning.\n"
            "  Kor med --nmd-confirm for att ladda ner alla, eller manuellt fran:\n"
            f"  {NMD_BASE}/"
        )
        # Visa vad som redan finns
        existing = list(NMD_DIR.glob("*.tif"))
        if existing:
            print(f"  Redan nedladdat: {[f.name for f in existing]}")
        return

    import zipfile
    for layer in NMD_LAYERS:
        # Hoppa over om redan extraherat
        label_slug = layer["zip_name"].replace(".zip", "")
        already = list(NMD_DIR.glob(f"*{label_slug[:12]}*.tif"))
        if already:
            print(f"  [skip] {layer['label']} (finns redan)")
            continue

        print(f"\n  -- {layer['label']} ({layer['size_gb']:.1f} GB) --")
        zip_dest = NMD_DIR / layer["zip_name"]
        ok = download_file(layer["url"], zip_dest)
        if ok:
            print(f"  Packar upp {zip_dest.name} ...")
            with zipfile.ZipFile(zip_dest, "r") as z:
                for name in z.namelist():
                    if name.endswith(".tif"):
                        z.extract(name, NMD_DIR)
            zip_dest.unlink()
            extracted = list(NMD_DIR.glob("*.tif"))
            print(f"  [ok]  {len(extracted)} .tif-fil(er) i {NMD_DIR.name}/")


# ── 2. Skogsstyrelsen avverkningsanmälningar (ArcGIS REST) ───────────────────
#
# Hämtar utförda/anmälda avverkningar för AOI via Skogsstyrelsens öppna API.
# Sparas som GeoPackage + rasteriserat 10m-raster.
# API-dok: https://www.skogsstyrelsen.se/rest

SKOGSST_URL = (
    "https://geodpags.skogsstyrelsen.se/arcgis/rest/services/"
    "Geodataportal/GeodataportalVisaAvverkningsanmalan/MapServer/0/query"
)

def download_skogsstyrelsen() -> None:
    print("\n[Skogsstyrelsen avverkningsanmalningar]")

    gpkg = SKOGSST_DIR / "avverkningar_aoi.gpkg"
    raster = SKOGSST_DIR / "avverkningar_raster_10m.tif"

    x_min, y_min, x_max, y_max = aoi_sweref()

    params = {
        "geometry":       f"{x_min:.0f},{y_min:.0f},{x_max:.0f},{y_max:.0f}",
        "geometryType":   "esriGeometryEnvelope",
        "spatialRel":     "esriSpatialRelIntersects",
        "inSR":           str(EPSG_SWEREF),
        "outSR":          str(EPSG_SWEREF),
        "outFields":      "OBJECTID,Avverktyp,Skogstyp,Inkomdatum,AvvHa,AvverkningsanmalanKlass",
        "returnGeometry": "true",
        "f":              "geojson",
    }

    try:
        print(f"  Fragor REST-API ({SKOGSST_URL[:60]}...)")
        resp = requests.get(SKOGSST_URL, params=params, timeout=60)
        resp.raise_for_status()

        data = resp.json()
        features = data.get("features", [])
        print(f"  Hittade {len(features)} avverkningsparceller")

        if not features:
            print("  Inga avverkningar i AOI – skapar tomt stoerningsraster")
            gdf = gpd.GeoDataFrame(geometry=[], crs=f"EPSG:{EPSG_SWEREF}")
        else:
            gdf = gpd.GeoDataFrame.from_features(features, crs=f"EPSG:{EPSG_SWEREF}")

        gdf.to_file(gpkg, driver="GPKG")
        print(f"  [ok]   {gpkg.name}")

        _rasterize_disturbance(gdf, x_min, y_min, x_max, y_max, raster)

    except Exception as e:
        print(f"  [FEL]  {e}")


def _rasterize_disturbance(gdf: "gpd.GeoDataFrame",
                            x_min: float, y_min: float,
                            x_max: float, y_max: float,
                            out_path: Path,
                            resolution: float = 10.0) -> None:
    """Rasteriserar avverkningspolygoner till 10m-störningsraster."""
    width  = max(1, int((x_max - x_min) / resolution))
    height = max(1, int((y_max - y_min) / resolution))
    transform = from_bounds(x_min, y_min, x_max, y_max, width, height)

    if len(gdf) > 0 and gdf.geometry.notna().any():
        shapes = [
            (geom, 1) for geom in gdf.geometry
            if geom is not None and not geom.is_empty
        ]
        arr = rasterize(
            shapes, out_shape=(height, width),
            transform=transform, fill=0, dtype="uint8"
        )
    else:
        import numpy as np
        arr = __import__("numpy").zeros((height, width), dtype="uint8")

    with rasterio.open(
        out_path, "w", driver="GTiff",
        height=height, width=width, count=1,
        dtype="uint8", crs=f"EPSG:{EPSG_SWEREF}",
        transform=transform, nodata=255
    ) as dst:
        dst.write(arr, 1)
    print(f"  [ok]   {out_path.name}  ({arr.sum()} storningspixlar av {arr.size})")


# ── 3. Skogsstyrelsen nyckelbiotoper (ArcGIS REST) ───────────────────────────
#
# Nyckelbiotoper indikerar höga naturvärden (ekvivalent med NVI klass 1–2).
# Används som bonuslager i strukturindex.
# Samma API-host som avverkningar, layer 0 i Nyckelbiotoper-tjänsten.

NYCKELBIOTOP_URL = (
    "https://geodpags.skogsstyrelsen.se/arcgis/rest/services/"
    "Geodataportal/GeodataportalVisaNyckelbiotop/MapServer/0/query"
)


def download_nyckelbiotoper() -> None:
    print("\n[Skogsstyrelsen nyckelbiotoper]")

    from config import NYCKELBIOTOP_DIR
    gpkg   = NYCKELBIOTOP_DIR / "nyckelbiotoper_aoi.gpkg"
    raster = NYCKELBIOTOP_DIR / "nyckelbiotoper_raster_10m.tif"

    x_min, y_min, x_max, y_max = aoi_sweref()

    params = {
        "geometry":       f"{x_min:.0f},{y_min:.0f},{x_max:.0f},{y_max:.0f}",
        "geometryType":   "esriGeometryEnvelope",
        "spatialRel":     "esriSpatialRelIntersects",
        "inSR":           str(EPSG_SWEREF),
        "outSR":          str(EPSG_SWEREF),
        "outFields":      "*",
        "returnGeometry": "true",
        "f":              "geojson",
    }

    try:
        print(f"  Fragor REST-API ({NYCKELBIOTOP_URL[:60]}...)")
        resp = requests.get(NYCKELBIOTOP_URL, params=params, timeout=60)
        resp.raise_for_status()

        features = resp.json().get("features", [])
        print(f"  Hittade {len(features)} nyckelbiotoper")

        if features:
            gdf = gpd.GeoDataFrame.from_features(features, crs=f"EPSG:{EPSG_SWEREF}")
        else:
            gdf = gpd.GeoDataFrame(geometry=[], crs=f"EPSG:{EPSG_SWEREF}")

        gdf.to_file(gpkg, driver="GPKG")
        print(f"  [ok]   {gpkg.name}")

        _rasterize_disturbance(gdf, x_min, y_min, x_max, y_max, raster)
        # Byt namn på utskriften – _rasterize_disturbance skriver "störningspixlar"
        # vilket är lite missvisande för nyckelbiotoper, men funktionen fungerar rätt.

    except Exception as e:
        print(f"  [FEL]  {e}")


# ── 4. Naturvårdsverket NNK (Natura Naturtypskartan) ─────────────────────────
#
# NNK kartlägger Natura 2000-naturtyper med direkta kvalitetsindikationer.
# Skogliga N2000-typer (9010, 9070, 9160, 9190, 91D0 etc.) korrelerar med NVI klass 1–2.
# Öppen nedladdning per län från geodata.naturvardsverket.se.

NNK_BASE = "https://geodata.naturvardsverket.se/nedladdning/naturtypskartan"

# NNK NATURTYP-värden för skogsliga naturtyper med högt naturvärde (NVI klass 1–2).
# NNK använder egna koder som avviker från N2000-koder:
#   9740 = Skogbevuxen myr (91D0)
#   9810 = Osäker Taiga/ickenatura-skog  (möjlig 9010)
#   9820 = Obestämd ädellövskog (9020, 9850, 9860)
# Vi inkluderar även trädklädd betesmark (6913) och ädellövskogskoder.
NNK_FOREST_CODES = {
    # Säkra skogsliga N2000-typer
    "9010", "9010A", "9010B",
    "9020", "9050", "9060", "9070", "9080",
    "9160", "9190", "91D0", "91E0",
    # NNK-egna koder
    "9740",   # Skogbevuxen myr (= 91D0)
    "9820",   # Obestämd ädellövskog
    "6913",   # Trädbärande kultiverad betesmark (= 9070)
}


def download_nnk() -> None:
    print(f"\n[Naturvardsverket NNK – Naturtypskartan (lan {NNK_LAN})]")

    gpkg_out = NNK_DIR / f"nnk_{NNK_LAN.lower()}_skogstyper_aoi.gpkg"
    if gpkg_out.exists():
        print(f"  [skip] {gpkg_out.name}")
        return

    import zipfile, tempfile, os

    url      = f"{NNK_BASE}/Naturtypskartan_{NNK_LAN}.zip"
    zip_dest = NNK_DIR / f"Naturtypskartan_{NNK_LAN}.zip"

    if not download_file(url, zip_dest):
        return

    print(f"  Packar upp {zip_dest.name} ...")
    try:
        with zipfile.ZipFile(zip_dest, "r") as z:
            names = z.namelist()
            gpkg_names = [n for n in names if n.lower().endswith(".gpkg")]
            shp_names  = [n for n in names if n.lower().endswith(".shp")]
            print(f"  Innehall: {len(names)} filer  ({len(gpkg_names)} gpkg, {len(shp_names)} shp)")

            # Prioritera YTA (polygoner) framför LIN/PKT
            yta_names = [n for n in shp_names if "YTA" in n.upper()]
            if yta_names:
                target = yta_names[0]
            elif gpkg_names:
                target = gpkg_names[0]
            elif shp_names:
                target = shp_names[0]
            else:
                print("  [FEL] Hittade ingen gpkg/shp i zip-filen")
                return

            tmp_dir = NNK_DIR / "_tmp_nnk"
            tmp_dir.mkdir(exist_ok=True)
            for name in names:
                if Path(name).suffix.lower() in (".gpkg", ".shp", ".dbf", ".prj", ".shx", ".cpg"):
                    z.extract(name, tmp_dir)

        # Läs in, filtrera på skogsliga N2000-koder, klipp till AOI
        x_min, y_min, x_max, y_max = aoi_sweref()
        aoi_box = box(x_min, y_min, x_max, y_max)

        src_path = tmp_dir / target
        print(f"  Laser {src_path.name} ...")
        gdf = gpd.read_file(src_path)
        print(f"  Totalt {len(gdf)} naturtypspolygoner i lanet")

        # Hitta naturtyps-kolumn
        nat_col = None
        for c in gdf.columns:
            if c.lower() in ("naturtyp", "n2000kod", "natura2000", "kod", "code", "naturtyps_kod"):
                nat_col = c
                break
        if nat_col is None:
            print(f"  [VARNING] Hittade ingen naturtypskolumn. Kolumner: {list(gdf.columns)}")
            gdf_aoi = gdf[gdf.geometry.intersects(aoi_box)]
        else:
            print(f"  Naturtypskolumn: '{nat_col}'  Unika koder: {gdf[nat_col].nunique()}")
            # Matcha på NNK_FOREST_CODES – koden kan ha ett suffix " - Beskrivning"
            def _code_matches(val):
                s = str(val).strip()
                # Exakt match
                if s in NNK_FOREST_CODES:
                    return True
                # Koden kan vara "9740 - Skogbevuxen myr (91D0)" – ta första delen
                prefix = s.split(" ")[0].split("-")[0].strip()
                return prefix in NNK_FOREST_CODES
            forest_mask = gdf[nat_col].apply(_code_matches)
            gdf_forest  = gdf[forest_mask]
            gdf_aoi     = gdf_forest[gdf_forest.geometry.intersects(aoi_box)]
            print(f"  Skogsliga N2000-typer i lanet: {len(gdf_forest)}  inom AOI: {len(gdf_aoi)}")

        if gdf_aoi.crs is None:
            gdf_aoi = gdf_aoi.set_crs(epsg=EPSG_SWEREF)
        elif gdf_aoi.crs.to_epsg() != EPSG_SWEREF:
            gdf_aoi = gdf_aoi.to_crs(epsg=EPSG_SWEREF)

        gdf_aoi.to_file(gpkg_out, driver="GPKG")
        print(f"  [ok]   {gpkg_out.name}  ({len(gdf_aoi)} polygoner)")

    except Exception as e:
        print(f"  [FEL]  {e}")
    finally:
        # Rensa temporär mapp och zip
        import shutil
        if (NNK_DIR / "_tmp_nnk").exists():
            shutil.rmtree(NNK_DIR / "_tmp_nnk", ignore_errors=True)
        if zip_dest.exists():
            zip_dest.unlink()


# ── 3. SLU Skogliga Grunddata ─────────────────────────────────────────────────
#
# Biomassa och trädhöjd – bästa strukturproxy för NVI.
# SLU distribuerar data via Skogsstyrelsen. Kräver registrering.
# Prova WCS från SLU:s GeoServer (maps.slu.se).

SLU_WCS = "https://maps.slu.se/geoserver/slu/wcs"

def download_slu_grunddata() -> None:
    print("\n[SLU Skogliga Grunddata]")

    x_min, y_min, x_max, y_max = aoi_sweref()

    # Prova WCS GetCapabilities
    caps_url = (
        f"{SLU_WCS}?SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCapabilities"
    )
    try:
        resp = requests.get(caps_url, timeout=20)
        # GeoServer returnerar HTML om tjänsten är en SPA – inte WCS
        if "<!doctype html" in resp.text.lower() or resp.status_code != 200:
            raise ValueError("WCS-endpoint returnerar inte giltig XML")

        import re
        ids = re.findall(r'<[^>]*Identifier[^>]*>(.*?)</', resp.text)
        if not ids:
            raise ValueError("Inga coverages i WCS")

        print(f"  WCS tillganglig – hittade coverages: {ids[:5]}")

        for layer_id, fname in [
            (ids[0], "slu_biomassa_aoi.tif"),
            (ids[1] if len(ids) > 1 else ids[0], "slu_hojd_aoi.tif"),
        ]:
            wcs_url = (
                f"{SLU_WCS}?SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage"
                f"&COVERAGEID={urllib.parse.quote(layer_id)}"
                f"&SUBSET=E({x_min:.0f},{x_max:.0f})"
                f"&SUBSET=N({y_min:.0f},{y_max:.0f})"
                "&FORMAT=image/geotiff"
            )
            download_file(wcs_url, SLU_DIR / fname)

    except Exception as e:
        print(f"  [INFO] SLU WCS ej tillganglig: {e}")
        print(
            "  Registrera for SLU Skogliga Grunddata:\n"
            "  https://www.slu.se/miljoanalys/statistik-och-miljodata/"
            "miljodatakatalogen/skogliga-grunddata/"
        )


# ── 4. Lantmäteriet GSD-Höjddata (STAC API) ──────────────────────────────────
#
# Lidar-baserad markhöjdmodell (DTM), 2m upplösning, Cloud Optimized GeoTIFF.
# OBS: Lantmäteriets WCS lades ner 2025-05-28. Ny tjänst: STAC API.
# Registrera gratis: https://opendata.lantmateriet.se/
# STAC-dok: https://api.lantmateriet.se/stac-hojd/v1/api.html

LM_STAC_URL = "https://api.lantmateriet.se/stac-hojd/v1/search"

def _stac_search_all(headers: dict) -> list:
    """Hämtar alla STAC-items för AOI via paginering."""
    payload = {
        "bbox":  [AOI_BBOX["min_lon"], AOI_BBOX["min_lat"],
                  AOI_BBOX["max_lon"], AOI_BBOX["max_lat"]],
        "limit": 100,
    }
    items = []
    url = LM_STAC_URL
    while url:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        items.extend(data.get("features", []))
        # Följ next-länk om den finns
        next_link = next(
            (l["href"] for l in data.get("links", []) if l.get("rel") == "next"),
            None
        )
        url = next_link
        payload = {}   # next-länk har redan parametrar inbakade
    return items


# ── 5. Naturvårdsverket – skyddad natur (INSPIRE Protected Sites, WFS) ───────
#
# WFS 1.1.0 + bbox i EPSG:4258 fungerar mot GeoServer (WFS 2.0 bbox gav 0 träffar i test).
# Nationell bulk: https://geodata.naturvardsverket.se/nedladdning/Inspire/ps/
# ATOM: https://geodata.naturvardsverket.se/atom/inspire/ps/SE_ProtectedSites_serviceFeed.xml

NV_PROTECTED_WFS = "https://geodata.naturvardsverket.se/inspire/ps/wfs"

# INSPIRE / Natura 2000 / IUCN — tekniska kod-suffix → kort svensk förklaring (attributtabell).
_PROTECTED_SITE_DESIGNATION_SV: dict[str, str] = {
    "siteOfCommunityImportance": "Natura 2000 – livsmiljö (SCI, samhällsviktig)",
    "specialAreaOfConservation": "Natura 2000 – särskilt bevarandeområde (SAC)",
    "specialProtectionArea": "Natura 2000 – särskilt skyddsområde för fågel (SPA)",
    "nationalPark": "Nationalpark",
    "natureReserve": "Naturreservat",
    "protectedLandscapeSeascape": "Skyddat landskap / havsområde",
    "wildernessArea": "Vildmarksområde",
    "strictNatureReserve": "Strikt naturreservat (IUCN Ia)",
    "habitatSpeciesManagementArea": "Livsmiljö- och arters förvaltningsområde (IUCN IV)",
    "speciesManagementArea": "Arters förvaltningsområde",
    "biotopeOrHabitatProtectionArea": "Biotop- eller livsmiljöskydd",
    "birdSanctuary": "Fågelskyddsområde",
    "protectedAreaWithSustainableUseOfNaturalResources": "Skyddat område med hållbart nyttjande (IUCN VI)",
    "nationalNatureMonument": "Naturminne / geologiskt naturminne",
    "managedNatureReserve": "Skött naturreservat",
    "commonDesignation": "Skyddsform (generisk beteckning)",
}
_PROTECTED_SITE_SCHEME_SV: dict[str, str] = {
    "natura2000": "EU Natura 2000",
    "IUCN": "IUCN:s skyddskategorier",
    "national": "Nationellt skyddsregister",
    "regional": "Regionalt skyddsregister",
    "local": "Kommunalt / lokalt skydd",
}
_PROTECTED_SITE_PROTECTION_SV: dict[str, str] = {
    "natureConservation": "Skydd av natur och biologisk mångfald",
    "culturalHeritage": "Skydd av kulturarv",
    "landscapeProtection": "Landskapsskydd",
    "coastalZoneManagement": "Kustzon / kustförvaltning",
    "waterCatchmentProtection": "Vattenskyddsområde",
    "floodRiskManagement": "Översvämningsrisk / vattenförvaltning",
}


def _inspire_code_tail(href_or_code: object) -> str:
    """Sista delen av INSPIRE-@href eller redan kort kod."""
    if href_or_code is None:
        return ""
    s = str(href_or_code).strip()
    if not s:
        return ""
    if "#" in s:
        s = s.rsplit("#", 1)[-1]
    if "/" in s:
        s = s.rstrip("/").rsplit("/", 1)[-1]
    return s


def _fallback_label(code: str) -> str:
    """Om kod saknas i lexikon: gör CamelCase något läsbarare."""
    if not code:
        return ""
    out = []
    for ch in code:
        if ch.isupper() and out and out[-1] != " ":
            out.append(" ")
        out.append(ch)
    return "".join(out).strip()


def _protected_site_add_readable_sv(props: dict) -> None:
    """Fyller nvr_*_sv och nvr_beskrivning_kort utifrån redan extraherade kodfält."""
    des_tail = _inspire_code_tail(props.get("nvr_designation"))
    props["nvr_designation_sv"] = _PROTECTED_SITE_DESIGNATION_SV.get(
        des_tail, _fallback_label(des_tail) or des_tail
    )

    scheme_tail = _inspire_code_tail(props.get("nvr_designation_scheme"))
    raw_scheme = props.get("nvr_designation_scheme")
    props["nvr_register_sv"] = _PROTECTED_SITE_SCHEME_SV.get(
        scheme_tail,
        scheme_tail or (str(raw_scheme) if raw_scheme else "") or "",
    )

    pclass = props.get("nvr_protection_class")
    pkey = str(pclass) if pclass is not None else ""
    props["nvr_skyddsyfte_sv"] = _PROTECTED_SITE_PROTECTION_SV.get(
        pkey, pkey or ""
    )

    ldf = props.get("nvr_legal_foundation_date")
    if ldf:
        s = str(ldf)
        props["nvr_handlingsdatum"] = s[:10] if len(s) >= 10 and s[4] == "-" else s

    ref = props.get("nvr_legal_document_ref")
    if ref:
        rs = str(ref).strip()
        if rs.lower().startswith("http"):
            props["nvr_handling_beskrivning"] = (
                "Länk till beslut/handling hos Naturvårdsverket (öppna URL i kolumnen nvr_legal_document_ref)"
            )
        else:
            props["nvr_handling_beskrivning"] = rs[:200] + ("…" if len(rs) > 200 else "")

    namn = props.get("nvr_site_name") or "Namn saknas i data"
    reg = props.get("nvr_register_sv") or ""
    skyddstyp = props.get("nvr_designation_sv") or ""
    parts = [namn, skyddstyp]
    if reg:
        parts.append(f"Register: {reg}")
    datum = props.get("nvr_handlingsdatum")
    if datum:
        parts.append(f"Besluts-/handlingsdatum: {datum}")
    props["nvr_beskrivning_kort"] = " — ".join(p for p in parts if p)


def _protected_site_enrich_properties(props: dict) -> None:
    """Lägger till läsbara fält bredvid INSPIRE-nästlade JSON-strukturer."""
    if not isinstance(props, dict):
        return
    iid = props.get("inspireID")
    if isinstance(iid, dict) and iid.get("localId"):
        props["nvr_inspire_local_id"] = str(iid["localId"])
    sn = props.get("siteName")
    try:
        text = sn["GeographicalName"]["spelling"]["text"]
        props["nvr_site_name"] = str(text) if text is not None else None
    except (TypeError, KeyError):
        props["nvr_site_name"] = None
    sd = props.get("siteDesignation")
    try:
        dt = sd["DesignationType"]
        des = dt["designation"]
        if isinstance(des, dict):
            props["nvr_designation"] = des.get("@href") or des.get("href")
        else:
            props["nvr_designation"] = str(des) if des is not None else None
        scheme = dt.get("designationScheme")
        if isinstance(scheme, dict):
            href = scheme.get("@href") or scheme.get("href")
            if href is not None:
                props["nvr_designation_scheme"] = str(href)
    except (TypeError, KeyError):
        props["nvr_designation"] = None
    lfd = props.get("legalFoundationDocument")
    if isinstance(lfd, dict):
        cit = lfd.get("CI_Citation")
        if isinstance(cit, dict) and cit.get("title") is not None:
            props["nvr_legal_document_ref"] = str(cit["title"])
    cl = props.get("siteProtectionClassification")
    props["nvr_protection_class"] = str(cl) if cl is not None else None
    ldate = props.get("legalFoundationDate")
    if ldate is not None:
        props["nvr_legal_foundation_date"] = str(ldate)

    _protected_site_add_readable_sv(props)


def _debug_print_protected_sites_gpkg(gdf: gpd.GeoDataFrame, tag: str = "") -> None:
    """
    Kontrollerar att GPKG innehåller ytor och om de skär strikt AOI-bbox (utan WFS-marginal).
    """
    lbl = f" {tag}" if tag else ""
    pre = f"  [debug]{lbl}"
    n = len(gdf)
    if n == 0:
        print(f"{pre} 0 rader i lagret — GPKG är tomt.")
        return
    valid = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    nv = len(valid)
    if nv == 0:
        print(f"{pre} {n} rader men alla geometrier saknas/tomma.")
        return
    if nv < n:
        print(f"{pre} {n - nv} rader med tom geometri (använder {nv} giltiga).")
    b = valid.total_bounds
    crs_s = valid.crs.to_string() if valid.crs else "?"
    print(
        f"{pre} {nv} ytor | total_bounds ({crs_s}): "
        f"{b[0]:.1f}, {b[1]:.1f} .. {b[2]:.1f}, {b[3]:.1f}"
    )
    aoi_poly = box(
        AOI_BBOX["min_lon"],
        AOI_BBOX["min_lat"],
        AOI_BBOX["max_lon"],
        AOI_BBOX["max_lat"],
    )
    aoi_gdf = gpd.GeoDataFrame(geometry=[aoi_poly], crs="EPSG:4326")
    try:
        aoi_in_data_crs = aoi_gdf.to_crs(valid.crs)
    except Exception as ex:
        print(f"{pre} kunde inte projicera AOI mot data-CRS: {ex}")
        return
    aoi_geom = aoi_in_data_crs.geometry.iloc[0]
    hits = valid.geometry.intersects(aoi_geom)
    n_hit = int(hits.sum())
    print(f"{pre} skär strikt AOI-bbox (config, utan WFS-marginal): {n_hit} / {nv}")
    if n_hit == 0:
        print(
            f"{pre} [varning] Inga ytor skär kärn-AOI — öka --protected-expand-deg, "
            "kolla AOI_BBOX, eller förvänta 0 om området saknar formellt skydd."
        )


_INSPIRE_BLOB_NAMES = frozenset(
    n.lower()
    for n in (
        "inspireID",
        "siteDesignation",
        "siteName",
        "legalFoundationDocument",
        "siteProtectionClassification",
        "legalFoundationDate",
        "featureType",
        "typeName",
    )
)


def _protected_sites_final_schema_cleanup(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Tar bort INSPIRE-råkolumner som annars kan följa med som dict/sträng-blobs i QGIS.
    Rensar även kolumner där första värdet är dict med @dataType eller ser ut som strängifierad dict.
    """
    if gdf.empty and len(gdf.columns) <= 1:
        return gdf
    geo_nm = gdf.geometry.name
    drop: list[str] = []
    for c in gdf.columns:
        if c == geo_nm:
            continue
        cl = str(c).lower()
        if cl in _INSPIRE_BLOB_NAMES or str(c).startswith("@"):
            drop.append(c)
            continue
        ser = gdf[c].dropna()
        if ser.empty:
            continue
        v = ser.iloc[0]
        if isinstance(v, dict) and "@dataType" in v:
            drop.append(c)
            continue
        if isinstance(v, str) and v.strip().startswith("{'@dataType'"):
            drop.append(c)
    if drop:
        gdf = gdf.drop(columns=drop, errors="ignore")
        print(f"  [info] Tog bort {len(drop)} rå-/blob-kolumner före spar (t.ex. INSPIRE JSON).")
    return gdf


def download_protected_sites(
    *,
    expand_deg: float = 0.05,
    max_features: int = 50_000,
    to_sweref: bool = True,
    overwrite: bool = False,
) -> None:
    """
    Hämtar ps:ProtectedSite som GeoJSON via WFS inom AOI_BBOX utökad med expand_deg (grader ~ lat).
    Sparar GeoPackage under data/raw/naturvardsverket/skyddad_natur/.
    """
    print("\n[Naturvardsverket - skyddad natur (WFS INSPIRE Protected Sites)]")
    print(f"  WFS: {NV_PROTECTED_WFS}")

    gpkg = PROTECTED_SITES_DIR / f"protected_sites_{AOI_NAME}.gpkg"
    meta = PROTECTED_SITES_DIR / f"protected_sites_{AOI_NAME}_metadata.txt"

    if gpkg.exists() and not overwrite:
        print(f"  [skip] {gpkg.name} finns redan (anvand --protected-sites-overwrite)")
        try:
            existing = gpd.read_file(gpkg, layer="protected_sites")
            _debug_print_protected_sites_gpkg(existing, "befintlig")
        except Exception as ex:
            print(f"  [debug] Kunde inte läsa {gpkg.name}: {ex}")
        return

    lo = AOI_BBOX["min_lon"] - expand_deg
    la0 = AOI_BBOX["min_lat"] - expand_deg
    hi = AOI_BBOX["max_lon"] + expand_deg
    la1 = AOI_BBOX["max_lat"] + expand_deg
    bbox = f"{lo},{la0},{hi},{la1},EPSG:4258"

    params = {
        "service": "WFS",
        "version": "1.1.0",
        "request": "GetFeature",
        "typeName": "ps:ProtectedSite",
        "bbox": bbox,
        "outputFormat": "application/json",
        "maxFeatures": str(max_features),
    }
    url = NV_PROTECTED_WFS + "?" + urllib.parse.urlencode(params)

    try:
        print(
            f"  Bbox EPSG:4258 (AOI + {expand_deg} deg): "
            f"{lo:.5f},{la0:.5f},{hi:.5f},{la1:.5f}"
        )
        resp = requests.get(url, timeout=300)
        resp.raise_for_status()
        data = resp.json()
        features = data.get("features") or []
        n = len(features)
        print(f"  Hamtade {n} ytor (max {max_features})")
        if n == 0:
            print(
                "  [varning] WFS returnerade inga polygoner i bbox — "
                "GPKG blir tomt. Kolla bbox, marginal och att tjänsten svarar."
            )
        if n >= max_features:
            print(
                "  [varning] Träffar maxFeatures-gransen -- vid behov: "
                "ladda nationella zip/ATOM fran "
                "https://geodata.naturvardsverket.se/nedladdning/Inspire/ps/"
            )

        for f in features:
            if isinstance(f, dict) and isinstance(f.get("properties"), dict):
                _protected_site_enrich_properties(f["properties"])

        if not features:
            gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:4258")
        else:
            gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4258")

        # Ta bort råa INSPIRE-kolumner (dict/JSON-blobs i CSV) — värden finns i nvr_*.
        _inspire_raw_cols = [
            "inspireID",
            "siteDesignation",
            "siteName",
            "legalFoundationDocument",
            "siteProtectionClassification",
            "legalFoundationDate",
        ]
        cols_to_drop = [c for c in _inspire_raw_cols if c in gdf.columns]
        if cols_to_drop:
            gdf = gdf.drop(columns=cols_to_drop)
        qgis_junk = [c for c in gdf.columns if str(c).startswith("@")]
        if qgis_junk:
            gdf = gdf.drop(columns=qgis_junk)

        # Människovänliga kolumner först (QGIS / CSV).
        _attr_order = [
            "nvr_beskrivning_kort",
            "nvr_site_name",
            "nvr_designation_sv",
            "nvr_register_sv",
            "nvr_skyddsyfte_sv",
            "nvr_handlingsdatum",
            "nvr_handling_beskrivning",
            "nvr_designation",
            "nvr_designation_scheme",
            "nvr_protection_class",
            "nvr_legal_foundation_date",
            "nvr_legal_document_ref",
            "nvr_inspire_local_id",
        ]
        if len(gdf.columns) and any(c in gdf.columns for c in _attr_order):
            geo_nm = gdf.geometry.name
            head = [c for c in _attr_order if c in gdf.columns]
            tail = [c for c in gdf.columns if c not in head and c != geo_nm]
            # Geometrikolumn måste finnas kvar — annars blir gdf en vanlig DataFrame utan .to_crs().
            gdf = gdf[head + tail + [geo_nm]]

        if to_sweref and len(gdf) > 0:
            gdf = gdf.to_crs(epsg=EPSG_SWEREF)

        gdf = _protected_sites_final_schema_cleanup(gdf)
        # Nytt GPKG från scratch — undvik att gammalt SQLite-schema/lager lämnar kvar fält (QGIS visar då siteName m.m.).
        if gpkg.exists():
            gpkg.unlink(missing_ok=True)
        gdf.to_file(gpkg, driver="GPKG", layer="protected_sites")
        print(f"  [ok]   {gpkg}")
        print(
            "  [tips] I QGIS: ta bort lagret ur projektet och lägg in GPKG på nytt "
            "om attributtabellen fortfarande visar gamla kolumner (cache)."
        )
        _debug_print_protected_sites_gpkg(gdf, "efter nedladdning")

        crs_note = f"EPSG:{EPSG_SWEREF}" if to_sweref else "EPSG:4258"
        meta.write_text(
            "Kalla: Naturvardsverket INSPIRE Protected Sites (WFS)\n"
            f"WFS: {NV_PROTECTED_WFS}\n"
            f"Typ: ps:ProtectedSite\n"
            f"AOI: {AOI_NAME}\n"
            f"Bbox (EPSG:4258, AOI+margin): {lo},{la0},{hi},{la1}\n"
            f"Antal ytor: {n}\n"
            f"CRS i GPKG: {crs_note}\n"
            "Nationell bulk / ATOM:\n"
            "  https://geodata.naturvardsverket.se/nedladdning/Inspire/ps/\n"
            "  https://geodata.naturvardsverket.se/atom/inspire/ps/SE_ProtectedSites_serviceFeed.xml\n",
            encoding="utf-8",
        )
        print(f"  [ok]   {meta.name}")
    except Exception as e:
        print(f"  [FEL]  {e}")


def download_lantmateriet_dem() -> None:
    print("\n[Lantmateriet GSD-Hojddata (STAC)]")

    try:
        token = get_lantmateriet_token()
    except Exception as e:
        token = ""
        print(f"  [VARNING] Kunde inte hämta token: {e}")

    if not token:
        print(
            "  [INFO] API-nyckel saknas – hoppar over Lantmateriet-data.\n"
            "  Registrera gratis pa: https://opendata.lantmateriet.se/\n"
            "  Satt consumer_key + consumer_secret i .env"
        )
        return

    headers = {"Authorization": f"Bearer {token}"}

    print("  Soker STAC-items for AOI ...")
    try:
        items = _stac_search_all(headers)
        print(f"  Hittade {len(items)} tiles")

        already = {f.stem for f in LM_DIR.glob("*.tif")}
        new_count = 0
        for item in items:
            tile_id = item["id"]
            if tile_id in already:
                continue
            data_asset = item.get("assets", {}).get("data", {})
            href = data_asset.get("href", "")
            if href.endswith(".tif"):
                ok = download_file(href, LM_DIR / f"{tile_id}.tif",
                                   headers=headers)
                if ok:
                    new_count += 1

        total = len(list(LM_DIR.glob("*.tif")))
        print(f"  Totalt {total} DEM-tiles i {LM_DIR.name}/")

    except Exception as e:
        print(f"  [FEL] {e}")
        print("  Kontrollera API-nyckel:\n  https://api.lantmateriet.se/stac-hojd/v1/api.html")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Ladda ner svenska NVI-data")
    parser.add_argument("--nmd-confirm", action="store_true",
                        help="Bekrafta nedladdning av NMD (~2,7 GB)")
    parser.add_argument(
        "--protected-sites",
        action="store_true",
        help="Hämta skyddade områden (Naturvårdsverket WFS) för AOI + marginal",
    )
    parser.add_argument(
        "--protected-sites-only",
        action="store_true",
        help="Kör endast skyddad natur (inga andra källor)",
    )
    parser.add_argument(
        "--protected-expand-deg",
        type=float,
        default=0.05,
        metavar="DEG",
        help="Marginal runt AOI_BBOX i grader (standard 0.05 ~ några km)",
    )
    parser.add_argument(
        "--protected-sites-overwrite",
        action="store_true",
        help="Skriv om befintlig protected_sites_<AOI>.gpkg",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Laddar ner svenska referensdata for NVI-screening")
    print("=" * 60)

    if args.protected_sites_only:
        download_protected_sites(
            expand_deg=args.protected_expand_deg,
            overwrite=args.protected_sites_overwrite,
        )
        print("\n[klar] Nedladdning avslutad.")
        return

    download_nmd(confirm=args.nmd_confirm)
    download_skogsstyrelsen()
    download_nyckelbiotoper()
    download_nnk()
    download_slu_grunddata()
    download_lantmateriet_dem()
    if args.protected_sites:
        download_protected_sites(
            expand_deg=args.protected_expand_deg,
            overwrite=args.protected_sites_overwrite,
        )

    print("\n[klar] Nedladdning avslutad.")


if __name__ == "__main__":
    main()
