"""
download_nature_layers.py
Hämtar naturlager för NVI-förstärkning inom AOI:

  1) Nyckelbiotoper (Skogsstyrelsen)
  2) Naturkultur / objekt med naturvärden / sumpskog (Skogsstyrelsen)
  3) Naturvårdsavtal (om lager hittas i naturkulturtjänsten)

Sparar både GPKG och 10 m-binära raster för snabb användning i compute_indices.py.

Kör:
  python scripts/python/download_nature_layers.py
  python scripts/python/download_nature_layers.py --overwrite
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    AOI_BBOX,
    EPSG_SWEREF,
    NATURE_LAYERS_DIR,
    NATURE_NYCKELBIOTOPER_GPKG,
    NATURE_NYCKELBIOTOPER_RASTER,
    NATURE_NATURKULTUR_GPKG,
    NATURE_NATURKULTUR_RASTER,
    NATURE_SUMPSKOG_GPKG,
    NATURE_SUMPSKOG_RASTER,
    NATURE_NATURVARDSAVTAL_GPKG,
    NATURE_NATURVARDSAVTAL_RASTER,
)

try:
    import requests
    import geopandas as gpd
    import numpy as np
    from pyproj import Transformer
    from rasterio.features import rasterize
    from rasterio.transform import from_bounds
    import rasterio
except ImportError as e:
    sys.exit(f"[FEL] Saknar paket: {e}\n  Kor: pip install -r requirements.txt")


NYCKELBIOTOP_URL = (
    "https://geodpags.skogsstyrelsen.se/arcgis/rest/services/"
    "Geodataportal/GeodataportalVisaNyckelbiotop/MapServer/0/query"
)
NATURKULTUR_SERVICE = (
    "https://geodpags.skogsstyrelsen.se/arcgis/rest/services/"
    "Geodataportal/GeodataportalVisaNaturkultur/MapServer"
)


def aoi_sweref():
    t = Transformer.from_crs(4326, EPSG_SWEREF, always_xy=True)
    x_min, y_min = t.transform(AOI_BBOX["min_lon"], AOI_BBOX["min_lat"])
    x_max, y_max = t.transform(AOI_BBOX["max_lon"], AOI_BBOX["max_lat"])
    return x_min, y_min, x_max, y_max


def _query_layer(query_url: str) -> gpd.GeoDataFrame:
    x_min, y_min, x_max, y_max = aoi_sweref()
    params = {
        "geometry": f"{x_min:.2f},{y_min:.2f},{x_max:.2f},{y_max:.2f}",
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": str(EPSG_SWEREF),
        "outSR": str(EPSG_SWEREF),
        "outFields": "*",
        "returnGeometry": "true",
        "f": "geojson",
    }
    r = requests.get(query_url, params=params, timeout=90)
    r.raise_for_status()
    features = r.json().get("features", [])
    if not features:
        return gpd.GeoDataFrame(geometry=[], crs=f"EPSG:{EPSG_SWEREF}")
    return gpd.GeoDataFrame.from_features(features, crs=f"EPSG:{EPSG_SWEREF}")


def _save_gpkg_and_raster(
    gdf: "gpd.GeoDataFrame",
    gpkg_path: Path,
    raster_path: Path,
    *,
    resolution: float = 10.0,
    overwrite: bool = False,
) -> None:
    if gpkg_path.exists() and not overwrite:
        print(f"  [skip] {gpkg_path.name}")
    else:
        if gpkg_path.exists():
            gpkg_path.unlink(missing_ok=True)
        gdf.to_file(gpkg_path, driver="GPKG")
        print(f"  [ok]   {gpkg_path.name} ({len(gdf)} objekt)")

    x_min, y_min, x_max, y_max = aoi_sweref()
    width = max(1, int((x_max - x_min) / resolution))
    height = max(1, int((y_max - y_min) / resolution))
    transform = from_bounds(x_min, y_min, x_max, y_max, width, height)

    if len(gdf) > 0 and gdf.geometry.notna().any():
        shapes = [(geom, 1) for geom in gdf.geometry if geom is not None and not geom.is_empty]
        arr = rasterize(shapes, out_shape=(height, width), transform=transform, fill=0, dtype="uint8")
    else:
        arr = np.zeros((height, width), dtype="uint8")

    if raster_path.exists() and not overwrite:
        print(f"  [skip] {raster_path.name}")
        return

    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype="uint8",
        crs=f"EPSG:{EPSG_SWEREF}",
        transform=transform,
        nodata=255,
    ) as dst:
        dst.write(arr, 1)
    print(f"  [ok]   {raster_path.name} ({int(arr.sum())} träffpixlar)")


def _discover_naturkultur_layers() -> dict:
    url = f"{NATURKULTUR_SERVICE}?f=pjson"
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    data = r.json()
    layers = data.get("layers", [])
    picked = {"naturkultur": None, "sumpskog": None, "naturvardsavtal": None}
    for lyr in layers:
        name = str(lyr.get("name", "")).lower()
        lid = lyr.get("id")
        if lid is None:
            continue
        if picked["naturkultur"] is None and ("naturvärde" in name or "naturvarde" in name or "naturkultur" in name):
            picked["naturkultur"] = int(lid)
        if picked["sumpskog"] is None and "sumpskog" in name:
            picked["sumpskog"] = int(lid)
        if picked["naturvardsavtal"] is None and ("naturvårdsavtal" in name or "naturvardsavtal" in name):
            picked["naturvardsavtal"] = int(lid)
    return picked


def _query_naturkultur_layer(layer_id: int) -> gpd.GeoDataFrame:
    return _query_layer(f"{NATURKULTUR_SERVICE}/{layer_id}/query")


def run(overwrite: bool = False) -> None:
    print("=" * 60)
    print("Hämtar naturlager för NVI-förstärkning")
    print("=" * 60)
    NATURE_LAYERS_DIR.mkdir(parents=True, exist_ok=True)

    print("\n[1/3] Nyckelbiotoper")
    try:
        gdf_nb = _query_layer(NYCKELBIOTOP_URL)
        _save_gpkg_and_raster(
            gdf_nb,
            NATURE_NYCKELBIOTOPER_GPKG,
            NATURE_NYCKELBIOTOPER_RASTER,
            overwrite=overwrite,
        )
    except Exception as e:
        print(f"  [FEL] Nyckelbiotoper: {e}")

    print("\n[2/3] Naturkultur + sumpskog + naturvårdsavtal (lagerupptäckt)")
    try:
        discovered = _discover_naturkultur_layers()
        print(f"  Upptäckta lager-id: {discovered}")

        if discovered["naturkultur"] is not None:
            gdf_nk = _query_naturkultur_layer(discovered["naturkultur"])
            _save_gpkg_and_raster(
                gdf_nk,
                NATURE_NATURKULTUR_GPKG,
                NATURE_NATURKULTUR_RASTER,
                overwrite=overwrite,
            )
        else:
            print("  [info] Hittade inget naturkultur-/naturvärdeslager i tjänsten")

        if discovered["sumpskog"] is not None:
            gdf_sumpskog = _query_naturkultur_layer(discovered["sumpskog"])
            _save_gpkg_and_raster(
                gdf_sumpskog,
                NATURE_SUMPSKOG_GPKG,
                NATURE_SUMPSKOG_RASTER,
                overwrite=overwrite,
            )
        else:
            print("  [info] Hittade inget sumpskogslager i tjänsten")

        if discovered["naturvardsavtal"] is not None:
            gdf_avtal = _query_naturkultur_layer(discovered["naturvardsavtal"])
            _save_gpkg_and_raster(
                gdf_avtal,
                NATURE_NATURVARDSAVTAL_GPKG,
                NATURE_NATURVARDSAVTAL_RASTER,
                overwrite=overwrite,
            )
        else:
            print("  [info] Hittade inget naturvårdsavtal-lager i tjänsten")

    except Exception as e:
        print(f"  [FEL] Naturkulturtjänst: {e}")

    print("\n[klar] Naturlager färdiga.")


def main():
    p = argparse.ArgumentParser(description="Hämta naturlager för NVI")
    p.add_argument("--overwrite", action="store_true", help="Skriv över befintliga GPKG/TIFF")
    args = p.parse_args()
    run(overwrite=args.overwrite)


if __name__ == "__main__":
    main()
