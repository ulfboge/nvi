"""
compute_lidar_chm.py
Bygger rastrar från punktmoln (LAZ) i SWEREF99 TM – t.ex. Laserdata Skog eller NH:

  CHM_max           – max(trädhöjd) per cell, trädhöjd = z − DTM (Lantmäteriets COG)
  vert_complexity   – P90 − P10 av trädhöjd inom cell (flera returhöjder)
  deadwood_proxy    – antal punkter med 0,5–2 m höjd över mark (normaliseras i compute_indices)

Kräver DTM i data/raw/lantmateriet/*.tif och .laz i data/raw/lantmateriet/lidar_laz/
(samt ev. äldre data/raw/lantmateriet/nh_laz/). Hämta skogslaser med:
  python scripts/python/download_data.py --forest-laz-confirm

  python scripts/python/compute_lidar_chm.py
  python scripts/python/compute_lidar_chm.py --resolution 2.0
"""

import sys
import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from config import AOI_NAME, AOI_BBOX, EPSG_SWEREF, LM_DIR, LM_LIDAR_LAZ_DIR, PROC_DIR

try:
    import laspy
    import rasterio
    from rasterio.transform import from_bounds
    from rasterio.warp import reproject, Resampling
    from pyproj import Transformer
except ImportError as e:
    sys.exit(f"[FEL] Saknar paket: {e}\n  Kor: pip install -r requirements.txt")

from compute_indices import merge_and_clip


def _collect_laz_paths():
    """Alla .laz under lidar_laz/ samt bakåtkompatibelt nh_laz/."""
    paths = []
    for pat in ("*.laz", "*.LAZ"):
        paths.extend(LM_LIDAR_LAZ_DIR.rglob(pat))
    legacy = LM_DIR / "nh_laz"
    if legacy.is_dir() and legacy.resolve() != LM_LIDAR_LAZ_DIR.resolve():
        for pat in ("*.laz", "*.LAZ"):
            paths.extend(legacy.rglob(pat))
    uniq = {}
    for p in paths:
        uniq[p.resolve()] = p
    return sorted(uniq.values(), key=lambda x: str(x).lower())


def _aoi_sweref_bounds():
    t = Transformer.from_crs(4326, EPSG_SWEREF, always_xy=True)
    x0, y0 = t.transform(AOI_BBOX["min_lon"], AOI_BBOX["min_lat"])
    x1, y1 = t.transform(AOI_BBOX["max_lon"], AOI_BBOX["max_lat"])
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def _dtm_on_output_grid(lm_tifs: list, x_min: float, y_min: float, x_max: float, y_max: float, res: float):
    dem, meta = merge_and_clip(lm_tifs)
    dem = dem.astype(np.float64)
    dem[(dem < -500) | ~np.isfinite(dem)] = np.nan

    out_w = max(1, int(np.ceil((x_max - x_min) / res)))
    out_h = max(1, int(np.ceil((y_max - y_min) / res)))
    out_transform = from_bounds(x_min, y_min, x_max, y_max, out_w, out_h)
    dst = np.empty((out_h, out_w), dtype=np.float64)
    dst.fill(np.nan)

    src_crs = meta.get("crs") or f"EPSG:{EPSG_SWEREF}"
    reproject(
        source=dem,
        destination=dst,
        src_transform=meta["transform"],
        src_crs=src_crs,
        dst_transform=out_transform,
        dst_crs=f"EPSG:{EPSG_SWEREF}",
        resampling=Resampling.bilinear,
    )
    return dst, out_transform, out_h, out_w


def _canopy_points(classification: np.ndarray, chm: np.ndarray) -> np.ndarray:
    """Punkter som räknas till krona (inte mark/vatten enligt ASPRS där det finns)."""
    cls = classification.astype(np.int32)
    mask = (cls != 2) & (cls != 9)
    if np.sum(mask) < max(50, int(0.01 * len(cls))):
        mask = chm > 0.35
    return mask


def _process_laz_files(
    laz_paths: list,
    dtm: np.ndarray,
    x_min: float,
    y_min: float,
    x_max: float,
    y_max: float,
    res: float,
    out_h: int,
    out_w: int,
    chunk_size: int,
):
    flat_max = np.full(out_h * out_w, -np.inf, dtype=np.float64)
    flat_dw = np.zeros(out_h * out_w, dtype=np.int32)
    cell_chm_samples = defaultdict(list)
    max_samples_per_cell = 800

    for path in laz_paths:
        print(f"  Läser {path.name} ...")
        try:
            reader = laspy.open(path)
        except Exception as e:
            print(f"  [skip] Kunde inte öppna: {e}")
            continue

        with reader:
            for chunk in reader.chunk_iterator(chunk_size):
                x = np.asarray(chunk.x, dtype=np.float64)
                y = np.asarray(chunk.y, dtype=np.float64)
                z = np.asarray(chunk.z, dtype=np.float64)
                cls = np.asarray(chunk.classification, dtype=np.int32)

                inside = (
                    (x >= x_min)
                    & (x <= x_max)
                    & (y >= y_min)
                    & (y <= y_max)
                    & np.isfinite(z)
                )
                if not np.any(inside):
                    continue

                x = x[inside]
                y = y[inside]
                z = z[inside]
                cls = cls[inside]

                col = np.floor((x - x_min) / res).astype(np.int64)
                row = np.floor((y_max - y) / res).astype(np.int64)
                valid_rc = (row >= 0) & (row < out_h) & (col >= 0) & (col < out_w)
                x, y, z, cls = x[valid_rc], y[valid_rc], z[valid_rc], cls[valid_rc]
                row, col = row[valid_rc], col[valid_rc]

                dtm_z = dtm[row, col]
                ok = np.isfinite(dtm_z)
                x, y, z, cls, row, col, dtm_z = (
                    x[ok],
                    y[ok],
                    z[ok],
                    cls[ok],
                    row[ok],
                    col[ok],
                    dtm_z[ok],
                )
                if len(z) == 0:
                    continue

                chm = z - dtm_z
                canopy = _canopy_points(cls, chm)

                flat_idx = row * out_w + col
                np.maximum.at(flat_max, flat_idx, chm)

                low = canopy & (chm >= 0.5) & (chm <= 2.0)
                if np.any(low):
                    np.add.at(flat_dw, flat_idx[low], 1)

                fi_c = flat_idx[canopy]
                chm_c = chm[canopy]
                m = len(chm_c)
                if m > 80_000:
                    sub = np.random.choice(m, 80_000, replace=False)
                    fi_c = fi_c[sub]
                    chm_c = chm_c[sub]
                for idx_i, cv in zip(fi_c, chm_c):
                    if cv <= 0:
                        continue
                    idx = int(idx_i)
                    lst = cell_chm_samples[idx]
                    if len(lst) < max_samples_per_cell:
                        lst.append(float(cv))
                    elif np.random.random() < 0.02:
                        lst[np.random.randint(0, max_samples_per_cell)] = float(cv)

    chm_max = flat_max.reshape(out_h, out_w)
    chm_max[np.isneginf(chm_max)] = np.nan

    vert = np.full((out_h, out_w), np.nan, dtype=np.float64)
    for idx, vals in cell_chm_samples.items():
        if len(vals) < 8:
            continue
        v = np.asarray(vals, dtype=np.float64)
        vert.flat[idx] = np.percentile(v, 90) - np.percentile(v, 10)

    cell_area = res * res
    deadwood = flat_dw.reshape(out_h, out_w).astype(np.float64) / cell_area

    return chm_max, vert, deadwood


def _write_raster(arr: np.ndarray, transform, name_suffix: str) -> Path:
    out = PROC_DIR / f"{AOI_NAME}_{name_suffix}.tif"
    meta = {
        "driver": "GTiff",
        "dtype": "float32",
        "nodata": -9999.0,
        "width": arr.shape[1],
        "height": arr.shape[0],
        "count": 1,
        "crs": f"EPSG:{EPSG_SWEREF}",
        "transform": transform,
        "compress": "deflate",
    }
    data = np.where(np.isfinite(arr), arr.astype(np.float32), -9999.0)
    with rasterio.open(out, "w", **meta) as dst:
        dst.write(data, 1)
    print(f"  >> {out.name}")
    return out


def main():
    parser = argparse.ArgumentParser(description="CHM och LiDAR-strukturraster från LAZ")
    parser.add_argument(
        "--resolution",
        type=float,
        default=2.0,
        metavar="M",
        help="Pixelstorlek i meter (standard 2, ofta samma storleksordning som DTM-COG)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=1_500_000,
        metavar="N",
        help="Antal punkter per laspy-chunk",
    )
    args = parser.parse_args()

    laz_paths = _collect_laz_paths()
    if not laz_paths:
        sys.exit(
            f"[FEL] Inga .laz i {LM_LIDAR_LAZ_DIR} (eller {LM_DIR / 'nh_laz'}). "
            "Kör: python scripts/python/download_data.py --forest-laz-confirm"
        )

    lm_tifs = sorted(LM_DIR.glob("*.tif"))
    if not lm_tifs:
        sys.exit(f"[FEL] Ingen DTM i {LM_DIR} – kör download_data med API-nyckel först.")

    x_min, y_min, x_max, y_max = _aoi_sweref_bounds()
    res = float(args.resolution)
    print(
        f"\nLiDAR → raster  |  AOI {AOI_NAME}  |  {res} m  |  "
        f"SWEREF [{x_min:.0f},{y_min:.0f}..{x_max:.0f},{y_max:.0f}]"
    )

    print("  DTM på utraster ...")
    dtm, out_transform, out_h, out_w = _dtm_on_output_grid(
        lm_tifs, x_min, y_min, x_max, y_max, res
    )

    print(f"  {len(laz_paths)} LAZ-fil(er), chunk {args.chunk_size} ...")
    chm_max, vert, deadw = _process_laz_files(
        laz_paths,
        dtm,
        x_min,
        y_min,
        x_max,
        y_max,
        res,
        out_h,
        out_w,
        args.chunk_size,
    )

    if not np.any(np.isfinite(chm_max)):
        sys.exit("[FEL] Ingen giltig CHM – kolla CRS (ska vara SWEREF), AOI och att LAZ täcker området.")

    _write_raster(chm_max, out_transform, "lidar_chm_max")
    _write_raster(vert, out_transform, "lidar_vert_complexity")
    _write_raster(deadw, out_transform, "lidar_deadwood_proxy")
    print("\n[klar] Kör compute_indices.py för att använda lagren i strukturindex.")


if __name__ == "__main__":
    main()
