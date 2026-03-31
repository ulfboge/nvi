"""
species_overlay_a.py — Fas A: koppla observationer till Rödlistan och överlagra mot AOI.

  * Läser punktdata (GeoPackage / GeoJSON / Shapefile / CSV med lon+lat).
  * Läser rödlisttabell (CSV) med art + kategori.
  * Filtrerar till AOI + buffer (SWEREF99 TM), joinar, sparar GeoPackage.
  * Rastreriserar till samma raster som befintligt index (eller 10 m-grid från AOI).

Kör (exempel):
  python scripts/python/species_overlay_a.py \\
    --obs data/raw/arter/observations/fynd.gpkg \\
    --rodlista data/raw/arter/rodlista/rodlista.csv

  python scripts/python/species_overlay_a.py \\
    --obs examples/species/obs_minimal.csv \\
    --rodlista examples/species/rodlista_minimal.csv

Kräver: geopandas, pandas, rasterio, shapely, pyproj (requirements.txt).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_bounds
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    AOI_BBOX,
    AOI_NAME,
    EPSG_SWEREF,
    EPSG_WGS84,
    PROC_DIR,
    RASTERS_DIR,
    SPECIES_AOI_BUFFER_M,
    SPECIES_OUTPUT_DIR,
)

try:
    from pyproj import Transformer
except ImportError:
    sys.exit("Saknar pyproj — pip install pyproj")


# Kategori → numerisk hotnivå (högre = större risk). DD/okänd = -1 (räknas ej som hotad i raster om min är VU).
_THREAT_RANK: dict[str, int] = {}
for _k, _v in [
    ("LC", 0),
    ("LIVSKRAFTIG", 0),
    ("NT", 1),
    ("NÄRA HOTAD", 1),
    ("VU", 2),
    ("SÅRBAR", 2),
    ("EN", 3),
    ("STARKT HOTAD", 3),
    ("CR", 4),
    ("AKUT HOTAD", 4),
    ("RE", 5),
    ("REGIONALT UTDÖD", 5),
    ("NATIONELLT UTDÖD", 5),
    ("UTDÖD", 5),
    ("DD", -1),
    ("KUNSKAPSBRIST", -1),
    ("DATA DEFICIENT", -1),
    ("NE", -1),  # ej utvärderad
]:
    _THREAT_RANK[_k] = _v


def _norm_cat(s: str) -> str:
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return ""
    t = str(s).strip().upper()
    t = re.sub(r"\s+", " ", t)
    return t


def threat_rank_for_category(raw: str) -> tuple[str, int]:
    """Returnerar (normaliserad etikett, rank). Okänd → ('', -1)."""
    key = _norm_cat(raw)
    if not key:
        return "", -1
    if key in _THREAT_RANK:
        return key, _THREAT_RANK[key]
    # Försök korta koder i början av strängen (t.ex. "VU (Sårbar)")
    m = re.match(r"^(RE|CR|EN|VU|NT|LC|DD|NE)\b", key)
    if m:
        code = m.group(1)
        return code, _THREAT_RANK.get(code, -1)
    return key, -1


def aoi_polygon_sweref(buffer_m: float) -> "gpd.GeoSeries":
    t = Transformer.from_crs(EPSG_WGS84, EPSG_SWEREF, always_xy=True)
    xs, ys = [], []
    for lon in (AOI_BBOX["min_lon"], AOI_BBOX["max_lon"]):
        for lat in (AOI_BBOX["min_lat"], AOI_BBOX["max_lat"]):
            x, y = t.transform(lon, lat)
            xs.append(x)
            ys.append(y)
    b = box(min(xs), min(ys), max(xs), max(ys))
    if buffer_m and buffer_m > 0:
        b = b.buffer(buffer_m)
    return gpd.GeoSeries([b], crs=f"EPSG:{EPSG_SWEREF}")


def load_rodlista(
    path: Path,
    species_col: str,
    category_col: str,
) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    if species_col not in df.columns:
        sys.exit(f"Rödlista saknar kolumn '{species_col}'. Tillgängliga: {list(df.columns)}")
    if category_col not in df.columns:
        sys.exit(f"Rödlista saknar kolumn '{category_col}'. Tillgängliga: {list(df.columns)}")
    df = df.rename(columns={species_col: "_species", category_col: "_cat_raw"})
    df["_species_key"] = df["_species"].str.strip()
    df["_cat_label"], df["_threat_rank"] = zip(
        *[threat_rank_for_category(x) for x in df["_cat_raw"]]
    )
    # Vid dubbletter: behåll högsta hot (max rank)
    df = df.sort_values("_threat_rank").drop_duplicates(subset=["_species_key"], keep="last")
    return df


def load_observations(
    path: Path,
    lon_col: str | None,
    lat_col: str | None,
    species_col: str,
    layer: str | None,
) -> gpd.GeoDataFrame:
    suf = path.suffix.lower()
    if suf == ".csv":
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        if not lon_col or not lat_col:
            for a, b in [
                ("decimalLongitude", "decimalLatitude"),
                ("longitude", "latitude"),
                ("lon", "lat"),
                ("x", "y"),
            ]:
                if a in df.columns and b in df.columns:
                    lon_col, lat_col = a, b
                    break
            else:
                sys.exit(
                    "CSV: ange --obs-lon-col / --obs-lat-col eller kolumner "
                    "decimalLongitude+decimalLatitude, longitude+latitude, lon+lat."
                )
        if lon_col not in df.columns or lat_col not in df.columns:
            sys.exit(f"CSV saknar lon/lat-kolumner. Har: {list(df.columns)}")
        if species_col not in df.columns:
            sys.exit(f"CSV saknar artkolumn '{species_col}'. Har: {list(df.columns)}")
        gdf = gpd.GeoDataFrame(
            df,
            geometry=gpd.points_from_xy(
                pd.to_numeric(df[lon_col], errors="coerce"),
                pd.to_numeric(df[lat_col], errors="coerce"),
                crs=f"EPSG:{EPSG_WGS84}",
            ),
            crs=f"EPSG:{EPSG_WGS84}",
        )
    else:
        kwargs = {}
        if layer:
            kwargs["layer"] = layer
        gdf = gpd.read_file(path, **kwargs)
        if gdf.crs is None:
            sys.exit("Vektorfil saknar CRS — sätt CRS eller använd CSV med WGS84 lon/lat.")
        if species_col not in gdf.columns:
            sys.exit(f"Observationer saknar kolumn '{species_col}'. Har: {list(gdf.columns)}")
    return gdf


def pick_reference_raster(explicit: Path | None) -> tuple[Path, tuple[int, int], dict] | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(explicit)
    candidates.extend(
        [
            PROC_DIR / f"{AOI_NAME}_structure_index.tif",
            PROC_DIR / f"{AOI_NAME}_nvi_score.tif",
            RASTERS_DIR / f"{AOI_NAME}_hotspot_class.tif",
        ]
    )
    p = next((c for c in candidates if c.exists()), None)
    if p is None:
        return None
    with rasterio.open(p) as src:
        meta = src.meta.copy()
        shape = (src.height, src.width)
        transform = src.transform
    return p, shape, {"transform": transform, "crs": meta["crs"], "meta": meta}


def make_aoi_grid_profile(cell_m: float) -> dict:
    t = Transformer.from_crs(EPSG_WGS84, EPSG_SWEREF, always_xy=True)
    xs, ys = [], []
    for lon in (AOI_BBOX["min_lon"], AOI_BBOX["max_lon"]):
        for lat in (AOI_BBOX["min_lat"], AOI_BBOX["max_lat"]):
            x, y = t.transform(lon, lat)
            xs.append(x)
            ys.append(y)
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    w = max(1, int(np.ceil((xmax - xmin) / cell_m)))
    h = max(1, int(np.ceil((ymax - ymin) / cell_m)))
    transform = from_bounds(xmin, ymin, xmax, ymax, w, h)
    meta = {
        "driver": "GTiff",
        "dtype": "uint16",
        "nodata": 0,
        "width": w,
        "height": h,
        "count": 1,
        "crs": f"EPSG:{EPSG_SWEREF}",
        "transform": transform,
    }
    return {"transform": transform, "crs": meta["crs"], "meta": meta, "shape": (h, w)}


def rasterize_counts(
    gdf_tm: gpd.GeoDataFrame,
    transform,
    shape: tuple[int, int],
    mask_threat: bool,
    min_rank: int,
) -> np.ndarray:
    """shape = (height, width). Räknar punkter per pixel."""
    h, w = shape
    out = np.zeros((h, w), dtype=np.uint16)
    for _, row in gdf_tm.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        tr = row.get("_threat_rank", -1)
        if pd.isna(tr):
            tr = -1
        else:
            tr = int(tr)
        if mask_threat and tr < min_rank:
            continue
        if hasattr(geom, "x"):
            gx, gy = float(geom.x), float(geom.y)
        else:
            c = geom.centroid
            gx, gy = float(c.x), float(c.y)
        col, row_i = rasterio.transform.rowcol(transform, gx, gy)
        if 0 <= row_i < h and 0 <= col < w:
            if out[row_i, col] < 65535:
                out[row_i, col] += 1
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Fas A: artobservationer + Rödlista → overlay")
    ap.add_argument("--obs", type=Path, required=True, help="Observationer (.gpkg, .geojson, .shp, .csv)")
    ap.add_argument("--rodlista", type=Path, required=True, help="Rödlist-CSV")
    ap.add_argument("--obs-species-col", default="scientific_name", help="Artkolumn i observationer")
    ap.add_argument("--obs-lon-col", default=None, help="Longitude för CSV (t.ex. decimalLongitude)")
    ap.add_argument("--obs-lat-col", default=None, help="Latitude för CSV")
    ap.add_argument("--obs-layer", default=None, help="Lagernamn för GPKG")
    ap.add_argument("--rodlista-species-col", default="scientific_name", help="Artkolumn i rödlista")
    ap.add_argument("--rodlista-category-col", default="redlist_category", help="Kategorikolumn i rödlista")
    ap.add_argument("--buffer-m", type=float, default=float(SPECIES_AOI_BUFFER_M), help="Buffer runt AOI (m)")
    ap.add_argument("--reference-tif", type=Path, default=None, help="Referensraster för pixelgrid (valfritt)")
    ap.add_argument("--cell-m", type=float, default=10.0, help="Cellstorlek om inget referensraster finns")
    ap.add_argument(
        "--min-threat-rank",
        type=int,
        default=2,
        help="Minsta hotnivå i hot-raster (2=VU, 3=EN, 4=CR)",
    )
    ap.add_argument("--no-raster", action="store_true", help="Skippa GeoTIFF (bara GPKG)")
    args = ap.parse_args()

    if not args.obs.exists():
        sys.exit(f"Saknar observationsfil: {args.obs}")
    if not args.rodlista.exists():
        sys.exit(f"Saknar rödlista: {args.rodlista}")

    SPECIES_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    rod = load_rodlista(
        args.rodlista,
        args.rodlista_species_col,
        args.rodlista_category_col,
    )
    gdf = load_observations(
        args.obs,
        args.obs_lon_col,
        args.obs_lat_col,
        args.obs_species_col,
        args.obs_layer,
    )
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()

    # Till SWEREF
    gdf_tm = gdf.to_crs(epsg=EPSG_SWEREF)
    aoi_buf = aoi_polygon_sweref(args.buffer_m)
    aoi_geom = aoi_buf.iloc[0]
    gdf_tm = gdf_tm[gdf_tm.intersects(aoi_geom)].copy()
    if gdf_tm.empty:
        sys.exit("Inga observationer inom AOI+buffer — kontrollera bbox, buffer och CRS.")

    gdf_tm["_species_key"] = gdf_tm[args.obs_species_col].astype(str).str.strip()
    merged = gdf_tm.merge(
        rod[
            [
                "_species_key",
                "_cat_raw",
                "_cat_label",
                "_threat_rank",
            ]
        ],
        on="_species_key",
        how="left",
    )
    # NaN i _threat_rank = arten fanns inte i rödlistfilen (DD/LC m.m. har numeriskt rank)
    merged["_rodlista_match"] = merged["_threat_rank"].notna()
    merged.loc[~merged["_rodlista_match"], "_cat_raw"] = "Ej matchad i rödlista"
    merged.loc[~merged["_rodlista_match"], "_threat_rank"] = -1
    merged.loc[~merged["_rodlista_match"], "_cat_label"] = ""
    merged["_threat_rank"] = merged["_threat_rank"].astype("int64")

    out_gpkg = SPECIES_OUTPUT_DIR / f"{AOI_NAME}_observations_rodlista.gpkg"
    merged.to_file(out_gpkg, driver="GPKG", layer="observations")
    print(f"[ok] GeoPackage: {out_gpkg}")

    # Sammanfattning
    n_tot = len(merged)
    n_hot = int((merged["_threat_rank"] >= args.min_threat_rank).sum())
    print(f"     Observationer i AOI+buffer: {n_tot}  (hot >= rank {args.min_threat_rank}: {n_hot})")

    if args.no_raster:
        print("[klar] (inga raster)")
        return

    ref = pick_reference_raster(args.reference_tif)
    if ref:
        ref_path, shape, info = ref
        transform = info["transform"]
        print(f"     Referensraster: {ref_path.name}  ({shape[0]}×{shape[1]})")
    else:
        info = make_aoi_grid_profile(args.cell_m)
        transform = info["transform"]
        shape = info["shape"]
        print(f"     Inget referensraster — använder {args.cell_m} m-grid från AOI ({shape[0]}×{shape[1]})")

    h, w = shape
    meta = info.get("meta")
    if meta is None:
        meta = info["meta"]

    count_all = rasterize_counts(merged, transform, (h, w), mask_threat=False, min_rank=0)
    count_hot = rasterize_counts(
        merged, transform, (h, w), mask_threat=True, min_rank=args.min_threat_rank
    )

    out_all = SPECIES_OUTPUT_DIR / f"{AOI_NAME}_species_obs_count.tif"
    out_hot = SPECIES_OUTPUT_DIR / f"{AOI_NAME}_species_threat_obs_count.tif"
    m = meta.copy()
    m.update({"dtype": "uint16", "nodata": 0, "count": 1, "height": h, "width": w})

    for arr, path in [(count_all, out_all), (count_hot, out_hot)]:
        with rasterio.open(path, "w", **m) as dst:
            dst.write(arr, 1)
        print(f"[ok] Raster: {path.name}")

    print("\n[klar] Öppna GPKG + raster i QGIS tillsammans med hotspot/NVI.")


if __name__ == "__main__":
    main()
