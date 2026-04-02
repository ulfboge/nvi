"""
compute_indices.py
Beräknar NVI-delindex från svenska datakällor:

  Strukturindex   – SLU Skogliga Grunddata (biomassa/höjd) eller NMD-proxy
  Kontinuitetsindex – Skogsstyrelsen avverkningsraster + NDVI-stabilitet
  Fuktindex       – TWI från Lantmäteriet Höjddata (eller fallback DEM)

Körordning:
  1. python scripts/python/download_data.py
  2. (Valfritt) GEE-export → data/raw/gee_exports/
  2b. (Valfritt) LAZ → lidar_laz/ (t.ex. --forest-laz-confirm) sedan compute_lidar_chm.py
  3. python scripts/python/compute_indices.py
"""

import sys
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    AOI_BBOX, AOI_NAME, EPSG_SWEREF,
    NMD_DIR, NMD_BASSKIKT, NMD_OBJHOJD, NMD_TRADSLAG_DIR,
    SKOGSST_DIR, LM_DIR, SLU_DIR, S2_EXPORT_DIR,
    PROC_DIR, WEIGHTS,
    NMD_FOREST_CLASSES, NMD_WETLAND_CLASSES,
    HANSEN_DIR, WORLDCOVER_DIR, DEM_FALLBACK_DIR,
    NYCKELBIOTOP_DIR, NNK_DIR,
    SPECIES_OUTPUT_DIR,
)

try:
    import rasterio
    from rasterio.merge import merge
    from rasterio.mask import mask as rio_mask
    from rasterio.warp import reproject, Resampling, calculate_default_transform
    from rasterio.transform import from_bounds
    from shapely.geometry import box, mapping
    from pyproj import Transformer
    from scipy.ndimage import generic_filter, uniform_filter
except ImportError as e:
    sys.exit(f"[FEL] Saknar paket: {e}\n  Kor: pip install -r requirements.txt")


# ── Koordinatgränser ──────────────────────────────────────────────────────────

def aoi_geom_wgs84():
    return [mapping(box(
        AOI_BBOX["min_lon"], AOI_BBOX["min_lat"],
        AOI_BBOX["max_lon"], AOI_BBOX["max_lat"]
    ))]


def aoi_sweref():
    t = Transformer.from_crs(4326, EPSG_SWEREF, always_xy=True)
    x_min, y_min = t.transform(AOI_BBOX["min_lon"], AOI_BBOX["min_lat"])
    x_max, y_max = t.transform(AOI_BBOX["max_lon"], AOI_BBOX["max_lat"])
    return x_min, y_min, x_max, y_max


def geom_sweref():
    x_min, y_min, x_max, y_max = aoi_sweref()
    return [mapping(box(x_min, y_min, x_max, y_max))]


# ── Raster-hjälpare ───────────────────────────────────────────────────────────

def clip_and_read(tif_path: Path, crs_is_sweref: bool = True):
    """Klipper GeoTIFF till AOI. Returnerar (array, metadata)."""
    geom = geom_sweref() if crs_is_sweref else aoi_geom_wgs84()
    with rasterio.open(tif_path) as src:
        arr, tf = rio_mask(src, geom, crop=True)
        meta = src.meta.copy()
        meta.update({"height": arr.shape[1], "width": arr.shape[2], "transform": tf})
    return arr[0].astype(float), meta


def merge_and_clip(paths: list, crs_is_sweref: bool = True):
    """Sätter ihop brickor och klipper till AOI."""
    # Filtrera bort tiles som inte överlappar AOI
    aoi_box = box(*aoi_sweref()) if crs_is_sweref else box(
        AOI_BBOX["min_lon"], AOI_BBOX["min_lat"],
        AOI_BBOX["max_lon"], AOI_BBOX["max_lat"],
    )
    overlapping = []
    for p in paths:
        with rasterio.open(p) as src:
            b = src.bounds
            tile_box = box(b.left, b.bottom, b.right, b.top)
            if aoi_box.intersects(tile_box):
                overlapping.append(p)
    if not overlapping:
        raise ValueError("Input shapes do not overlap raster.")
    paths = overlapping

    if len(paths) == 1:
        return clip_and_read(paths[0], crs_is_sweref)
    datasets = [rasterio.open(p) for p in paths]
    merged, tf = merge(datasets)
    for d in datasets:
        d.close()
    meta = datasets[0].meta.copy()
    meta.update({"transform": tf, "width": merged.shape[2], "height": merged.shape[1]})
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp:
        tp = Path(tmp.name)
    with rasterio.open(tp, "w", **meta) as dst:
        dst.write(merged)
    result = clip_and_read(tp, crs_is_sweref)
    os.unlink(tp)
    return result


def normalize(arr: np.ndarray, p_lo: float = 2, p_hi: float = 98) -> np.ndarray:
    valid = arr[np.isfinite(arr) & (arr > -9000)]
    lo = np.percentile(valid, p_lo)
    hi = np.percentile(valid, p_hi)
    return np.clip((arr - lo) / (hi - lo + 1e-9), 0, 1)


def save_raster(arr: np.ndarray, meta: dict, suffix: str) -> Path:
    out = PROC_DIR / f"{AOI_NAME}_{suffix}.tif"
    m = meta.copy()
    m.update({"dtype": "float32", "count": 1, "nodata": -9999.0,
              "crs": f"EPSG:{EPSG_SWEREF}"})
    with rasterio.open(out, "w", **m) as dst:
        dst.write(np.where(np.isfinite(arr), arr.astype("float32"), -9999.0), 1)
    print(f"  >> {out.name}")
    return out


def resample_to(arr: np.ndarray, target_shape: tuple) -> np.ndarray:
    """Omsampling av array till önskad form via scipy zoom."""
    if arr.shape == target_shape:
        return arr
    from scipy.ndimage import zoom
    factors = (target_shape[0] / arr.shape[0], target_shape[1] / arr.shape[1])
    return zoom(arr, factors, order=1)


# ── TWI-beräkning ─────────────────────────────────────────────────────────────

def compute_twi(dem: np.ndarray, cell_m: float = 2.0) -> np.ndarray:
    """Topographic Wetness Index (TWI = ln(a / tan(beta))).

    Försöker pysheds D8-flödesackumulation för korrekt upslope-area.
    Faller tillbaka på enkel 3×3-gradientmetod om pysheds saknas/misslyckas.
    """
    valid_mask = np.isfinite(dem) & (dem > -1000)
    dem_clean = np.where(valid_mask, dem, np.nanmedian(dem[valid_mask]))

    # ── Försök med pysheds D8 ──────────────────────────────────────────────────
    try:
        import tempfile, os
        from pysheds.grid import Grid

        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            import rasterio
            from rasterio.transform import from_origin
            # Bygg ett minimalt GeoTIFF som pysheds kan läsa
            h, w = dem_clean.shape
            transform = from_origin(0, h * cell_m, cell_m, cell_m)
            with rasterio.open(
                tmp_path, "w", driver="GTiff", height=h, width=w,
                count=1, dtype="float32", crs="EPSG:3006", transform=transform,
                nodata=-9999.0,
            ) as dst:
                dst.write(dem_clean.astype("float32"), 1)

            grid = Grid.from_raster(tmp_path)
            dem_g = grid.read_raster(tmp_path)
            pit_filled = grid.fill_pits(dem_g)
            flooded = grid.fill_depressions(pit_filled)
            inflated = grid.resolve_flats(flooded)
            fdir = grid.flowdir(inflated)
            acc = grid.accumulation(fdir)

            # Lutning från gradient
            dy, dx = np.gradient(dem_clean, cell_m)
            slope = np.arctan(np.sqrt(dx**2 + dy**2))
            tan_slope = np.clip(np.tan(slope), 0.001, None)

            # Upslope area (pixelenhet × cell_m²)
            upslope_area = (np.array(acc, dtype=float) + 1.0) * cell_m**2
            twi = np.log(upslope_area / tan_slope)
            print("  TWI: D8-flödesackumulation (pysheds)")
            return np.where(valid_mask, twi, np.nan)
        finally:
            os.unlink(tmp_path)

    except Exception as _e:
        print(f"  TWI: pysheds misslyckades ({_e}) – faller tillbaka till enkel gradient")

    # ── Fallback: enkel 3×3-gradientmetod ────────────────────────────────────
    dy, dx = np.gradient(dem_clean, cell_m)
    slope = np.arctan(np.sqrt(dx**2 + dy**2))
    tan_slope = np.clip(np.tan(slope), 0.001, None)
    flow_acc = generic_filter(dem_clean, lambda p: float(np.sum(p > p[4])), size=3) + 1.0
    twi = np.log(flow_acc / tan_slope)
    return np.where(valid_mask, twi, np.nan)


# ── 1. Strukturindex ──────────────────────────────────────────────────────────
#
# Prioritetsordning:
#   A. SLU Skogliga Grunddata (biomassa + trädhöjd) — bäst
#   B. GEE-exporterad NDVI-baserad struktur          — bra
#   C. NMD skogsklass som binär proxy               — fallback

def build_structure_index():
    # A. SLU Skogliga Grunddata
    slu_biomass = sorted(SLU_DIR.glob("*biomassa*.tif"))
    slu_height  = sorted(SLU_DIR.glob("*hojd*.tif"))

    if slu_biomass:
        print("  Kalla: SLU Skogliga Grunddata (biomassa)")
        bio, meta = merge_and_clip(slu_biomass)
        bio[bio < 0] = np.nan
        structure = normalize(bio)
        if slu_height:
            hgt, _ = merge_and_clip(slu_height)
            hgt[hgt < 0] = np.nan
            hgt_norm = normalize(hgt)
            hgt_norm = resample_to(hgt_norm, structure.shape)
            structure = structure * 0.6 + hgt_norm * 0.4
        return structure, meta

    # B. GEE-exporterad NDVI-struktur
    gee_files = list(S2_EXPORT_DIR.glob("*Delindex*.tif"))
    if gee_files:
        print("  Kalla: GEE-exporterad NDVI (struktur band 1)")
        arr, meta = clip_and_read(gee_files[0], crs_is_sweref=False)
        return normalize(arr), meta

    # C. NMD – basskikt + trädslag + objekthöjd
    if NMD_BASSKIKT.exists():
        has_tradslag = NMD_TRADSLAG_DIR.exists()
        has_objhojd  = NMD_OBJHOJD.exists()
        print("  Kalla: NMD Basskikt" +
              (" + Tradslag" if has_tradslag else "") +
              (" + Objekthojd" if has_objhojd else ""))

        nmd, meta = clip_and_read(NMD_BASSKIKT, crs_is_sweref=True)
        forest_mask = np.isin(nmd.astype(int), NMD_FOREST_CLASSES).astype(float)

        # Patch interior area: avstånd till skogsrand (i pixlar) normaliserat till [0,1].
        # Pixlar nära kanten (stig, hygge, öppen mark) får lägre texturvärde.
        # Ersätter glidande std som gynnade gränspixlar oavsett miljökvalitet.
        from scipy.ndimage import distance_transform_edt as _dist_edt
        interior_dist = _dist_edt(forest_mask > 0)   # avstånd i pixlar från icke-skog
        max_dist = np.percentile(interior_dist[interior_dist > 0], 98) if interior_dist.max() > 0 else 1.0
        texture = np.clip(interior_dist / (max_dist + 1e-9), 0, 1) * forest_mask

        # Läs gran-raster INNAN baseline sätts för differentierad startpunkt.
        # Gran-dominerad skog (>60 %) startar på 0.20 istället för 0.50.
        gran = np.zeros(forest_mask.shape)
        gran_files: list = []
        if has_tradslag:
            gran_files = list(NMD_TRADSLAG_DIR.glob("*gran*.tif"))
            if gran_files:
                gran_raw, _ = clip_and_read(gran_files[0], crs_is_sweref=True)
                gran = resample_to(gran_raw.clip(0, 1), forest_mask.shape)

        gran_dom = (gran > 0.45) & (forest_mask > 0)   # sänkt tröskel: fler granpixlar fångas
        structure = np.where(gran_dom,
                             forest_mask * 0.15,   # plantage-gran: låg bas
                             forest_mask * 0.50)   # övrig skog: normal bas
        structure = structure + normalize(texture) * 0.2

        # Trädslag: ädellöv och trivial löv ger bonus; gran ger ytterligare malus
        # Filer: adel, bok, ekovradel = högt värde  |  trivial = måttligt  |  gran = negativt
        if has_tradslag:
            adel_files = list(NMD_TRADSLAG_DIR.glob("*adel*.tif")) + \
                         list(NMD_TRADSLAG_DIR.glob("*bok*.tif"))  + \
                         list(NMD_TRADSLAG_DIR.glob("*ekovradel*.tif"))
            trivial_files = list(NMD_TRADSLAG_DIR.glob("*trivial*.tif"))

            if adel_files:
                adel, _ = clip_and_read(adel_files[0], crs_is_sweref=True)
                adel = resample_to(adel.clip(0, 1), structure.shape)
                structure += adel * 0.20   # starkt bonus ädellöv

            if trivial_files:
                trivial, _ = clip_and_read(trivial_files[0], crs_is_sweref=True)
                trivial = resample_to(trivial.clip(0, 1), structure.shape)
                structure += trivial * 0.05  # lövbonus (björk, asp etc.) – reducerat för sekundär lövskog

            if gran_files:
                structure -= gran * 0.12   # ytterligare malus (utöver lägre bas)

        # Sekundär lövskog-malus: NMD lövskogsklass (12x) utan ädellöv → troligt ung sekundär lövskog.
        nmd_int = nmd.astype(int)
        secondary_lov = np.isin(nmd_int, list(range(120, 130))) & (forest_mask > 0)
        if secondary_lov.any():
            sec_malus = secondary_lov.astype(float) * 0.08
            if has_tradslag and adel_files:
                # Minska malusen där ädellöv finns (äkta ädellövskog ska inte straffas)
                adel_mask = resample_to((adel > 0.15).astype(float), structure.shape)
                sec_malus_r = resample_to(sec_malus, structure.shape)
                structure -= sec_malus_r * (1.0 - adel_mask)
            else:
                structure -= resample_to(sec_malus, structure.shape)
            print("  + Sekundärlövskog-malus applicerad (NMD klass 12x)")

        # Objekthöjd 5–45 m: proxy för trädhöjd (gammal skog)
        if has_objhojd:
            oh, _ = clip_and_read(NMD_OBJHOJD, crs_is_sweref=True)
            oh = resample_to(oh.clip(0, None), structure.shape)
            structure += normalize(oh) * 0.10

        # LiDAR (LAZ → compute_lidar_chm.py): CHM, vertikal spridning, dödvedsproxy
        # Gran-dominerade pixlar får reducerad LiDAR-bonus: lång gran != högt naturvärde.
        gran_dom_r = resample_to(gran_dom.astype(float), structure.shape)
        lidar_chm = PROC_DIR / f"{AOI_NAME}_lidar_chm_max.tif"
        if lidar_chm.exists():
            chm_r, _ = clip_and_read(lidar_chm, crs_is_sweref=True)
            chm_r = np.where((chm_r > -9000) & np.isfinite(chm_r), chm_r, np.nan)
            chm_r = resample_to(np.nan_to_num(chm_r, nan=0.0), structure.shape)
            # Reducera bonus med 60 % för granpixlar (hög gran = inte naturvärde i sig)
            chm_weight = np.where(gran_dom_r > 0.5, 0.05, 0.12)
            structure += normalize(chm_r) * chm_weight
            print("  + LiDAR CHM (max) bonus (reducerad for gran)")
        lidar_vc = PROC_DIR / f"{AOI_NAME}_lidar_vert_complexity.tif"
        if lidar_vc.exists():
            vc, _ = clip_and_read(lidar_vc, crs_is_sweref=True)
            vc = np.where((vc > -9000) & np.isfinite(vc), vc, np.nan)
            vc = resample_to(np.nan_to_num(vc, nan=0.0), structure.shape)
            structure += normalize(vc) * 0.08
            print("  + LiDAR vertikal komplexitet (P90-P10) bonus")
        lidar_dw = PROC_DIR / f"{AOI_NAME}_lidar_deadwood_proxy.tif"
        if lidar_dw.exists():
            dw, _ = clip_and_read(lidar_dw, crs_is_sweref=True)
            dw = np.where((dw > -9000) & np.isfinite(dw), dw, np.nan)
            dw = resample_to(np.nan_to_num(dw, nan=0.0), structure.shape)
            structure += normalize(dw) * 0.05
            print("  + LiDAR dödvedsproxy (låga returer) bonus")

        # Nyckelbiotop-bonus: Skogsstyrelsens nyckelbiotoper indikerar höga naturvärden
        nb_raster = NYCKELBIOTOP_DIR / "nyckelbiotoper_raster_10m.tif"
        if nb_raster.exists():
            nb, _ = clip_and_read(nb_raster, crs_is_sweref=True)
            nb = resample_to((nb > 0).astype(float), structure.shape)
            # Gran-dominerade nyckelbiotoper får lägre bonus (0.10) än lövdominerade (0.25)
            nb_bonus = np.where(gran > 0.6, nb * 0.10, nb * 0.25)
            structure += nb_bonus
            print("  + Nyckelbiotop-bonus applicerad (trädslagsadekvat)")

        # NNK-bonus: Natura 2000-naturtyper (skogsliga) indikerar höga naturvärden
        nnk_files = list(NNK_DIR.glob("nnk_*_skogstyper_aoi.gpkg"))
        if nnk_files:
            try:
                import geopandas as gpd
                from rasterio.features import rasterize as _rasterize
                nnk_gdf = gpd.read_file(nnk_files[0])
                if len(nnk_gdf) > 0:
                    if nnk_gdf.crs is None or nnk_gdf.crs.to_epsg() != EPSG_SWEREF:
                        nnk_gdf = nnk_gdf.to_crs(epsg=EPSG_SWEREF)
                    # Rasterisera mot strukturindexets grid
                    geom = geom_sweref()
                    with rasterio.open(NMD_BASSKIKT) as _src:
                        _arr, _tf = rio_mask(_src, geom, crop=True)
                        _shape = (_arr.shape[1], _arr.shape[2])
                        _transform = _tf
                    shapes = [(g, 1) for g in nnk_gdf.geometry if g is not None and not g.is_empty]
                    if shapes:
                        nnk_arr = _rasterize(shapes, out_shape=_shape,
                                             transform=_transform, fill=0, dtype="uint8")
                        nnk_arr = resample_to(nnk_arr.astype(float), structure.shape)
                        structure += (nnk_arr > 0).astype(float) * 0.20
                        print(f"  + NNK-bonus applicerad ({len(nnk_gdf)} naturtypspolygoner)")
            except Exception as _e:
                print(f"  [VARNING] NNK-bonus misslyckades: {_e}")

        # SLU virkesförråd (VOL) – ålderspoxy: hög volym = gammal skog = högt naturvärde.
        # Avgörande för att skilja gammal naturskog från ung sekundär lövskog med
        # liknande trädhöjd. Ladda ner med: python download_data.py --slu
        slu_vol_files = sorted(SLU_DIR.glob("slu_vol_aoi.tif"))
        if slu_vol_files:
            vol, _ = clip_and_read(slu_vol_files[0], crs_is_sweref=True)
            vol = np.where((vol > -9000) & np.isfinite(vol), vol, 0.0)
            vol = resample_to(vol, structure.shape)
            vol_norm = normalize(vol, p_lo=5, p_hi=95)
            structure += vol_norm * 0.12
            print("  + SLU VOL alderspoxy (12 % vikt)")

        return np.clip(structure, 0, 1), meta

    # D. Fallback: Hansen treecover2000 (global, alltid tillgänglig)
    tc_files = sorted(HANSEN_DIR.glob("*treecover2000*.tif"))
    if tc_files:
        print("  Kalla: Hansen GFC treecover2000 (global fallback)")
        tc, meta = merge_and_clip(tc_files, crs_is_sweref=False)
        tc[tc < 0] = 0
        texture = generic_filter(tc, np.std, size=5)
        structure = normalize(tc, 0, 100) * 0.7 + normalize(texture) * 0.3
        return structure, meta

    raise FileNotFoundError(
        "Ingen strukturdata hittad. Kor download_data.py eller --nmd-confirm."
    )


# ── 2. Kontinuitetsindex ──────────────────────────────────────────────────────
#
# Prioritetsordning:
#   A. Skogsstyrelsen GPKG med datum → tidsavvägd störningsraster (bäst)
#   B. Skogsstyrelsen binärt raster (fallback om GPKG saknas)
#   C. GEE-export med NDVI std (komplement)

def _time_decay_disturbance(gpkg_path: Path, x_min, y_min, x_max, y_max,
                             resolution: float = 10.0,
                             decay_halflife_years: float = 15.0) -> np.ndarray:
    """Rasteriserar avverkningar med tidsavklingning.

    Störningsvikten avtar exponentiellt med ålder:
      w = exp(-age_years / decay_halflife_years)
    Nyligen avverkad (0 år) → w ≈ 1.0 (hög störning)
    15 år gammal           → w ≈ 0.37
    30 år gammal           → w ≈ 0.14
    50 år gammal           → w ≈ 0.03

    Retur: 2D-array med störningsvikter [0, 1] (0 = ostört, 1 = nyligen avverkat).
    """
    try:
        import geopandas as gpd
        from rasterio.features import rasterize as _rasterize
        from rasterio.transform import from_bounds as _from_bounds
        import datetime
    except ImportError:
        return None

    gdf = gpd.read_file(gpkg_path)
    if len(gdf) == 0:
        return None

    width  = max(1, int((x_max - x_min) / resolution))
    height = max(1, int((y_max - y_min) / resolution))
    transform = _from_bounds(x_min, y_min, x_max, y_max, width, height)

    now_year = datetime.datetime.now().year
    disturbance = np.zeros((height, width), dtype=float)

    for _, row in gdf.iterrows():
        if row.geometry is None or row.geometry.is_empty:
            continue
        # Inkomdatum är ms sedan epoch (ArcGIS REST-format)
        try:
            ts_ms = float(row.get("Inkomdatum", 0) or 0)
            year = datetime.datetime.fromtimestamp(ts_ms / 1000).year
        except Exception:
            year = now_year  # okänt datum → behandla som nyligen
        age_years = max(0, now_year - year)
        weight = float(np.exp(-age_years / decay_halflife_years))

        patch = _rasterize(
            [(row.geometry, weight)],
            out_shape=(height, width),
            transform=transform,
            fill=0.0,
            dtype="float32",
        )
        disturbance = np.maximum(disturbance, patch)

    return disturbance


def build_continuity_index(target_shape: tuple):
    # A. Skogsstyrelsen GPKG med datum → tidsavvägd störning
    avverk_gpkg = SKOGSST_DIR / "avverkningar_aoi.gpkg"
    if avverk_gpkg.exists():
        print("  Kalla: Skogsstyrelsen avverkningar (tidsavvagd)")
        x_min, y_min, x_max, y_max = aoi_sweref()
        disturbance = _time_decay_disturbance(avverk_gpkg, x_min, y_min, x_max, y_max)
        if disturbance is not None:
            continuity = 1.0 - disturbance
            continuity = resample_to(continuity, target_shape)
            # Mjuka kanter kring störda parceller
            disturbed_buf = uniform_filter(disturbance, size=3)
            disturbed_buf = resample_to(disturbed_buf, target_shape)
            continuity[disturbed_buf > 0.1] *= 0.7
            print(f"  + Tidsvagd: decay-halvtid {15} ar (nyligen avverkad=lag, gammal=hog)")
            return continuity

    # B. Fallback: binärt raster
    avverk_files = sorted(SKOGSST_DIR.glob("*raster*.tif"))
    if avverk_files:
        print("  Kalla: Skogsstyrelsen avverkningsraster (binart)")
        arr, _ = merge_and_clip(avverk_files)
        continuity = 1.0 - arr.clip(0, 1)
        continuity = resample_to(continuity, target_shape)
        disturbed_buffered = uniform_filter(arr.clip(0, 1), size=3)
        disturbed_buffered = resample_to(disturbed_buffered, target_shape)
        continuity[disturbed_buffered > 0.1] *= 0.6
        return continuity

    # C. Fallback: om GEE-export med NDVI std finns
    gee_files = list(S2_EXPORT_DIR.glob("*Delindex*.tif"))
    if gee_files:
        print("  Kalla: GEE NDVI-stabilitet (band 5 = NDVI std)")
        with rasterio.open(gee_files[0]) as src:
            if src.count >= 5:
                ndvi_std_band = src.read(5).astype(float)
                ndvi_med_band = src.read(4).astype(float)
                cv = ndvi_std_band / (np.abs(ndvi_med_band) + 0.001)
                cont = (cv * -1 + cv.max()) / (cv.max() + 0.001)
                return resample_to(normalize(cont), target_shape)

    print("  [VARNING] Ingen kontinuitetsdata – anvander neutralt 0.5")
    return np.full(target_shape, 0.5)


# ── 3. Fuktindex ─────────────────────────────────────────────────────────────
#
# Prioritetsordning:
#   A. Lantmäteriet Höjddata 2m (lidar) — bäst
#   B. NMD-våtmark + topografi           — komplement

def build_moisture_index(target_shape: tuple):
    # A. Lantmäteriet Höjddata
    lm_files = sorted(LM_DIR.glob("*.tif"))
    if lm_files:
        print("  Kalla: Lantmateriet GSD-Hojddata")
        try:
            dem, _ = merge_and_clip(lm_files)
        except ValueError:
            print("  [VARNING] LM-tiles overlappar inte AOI – hoppar till nasta kalla")
            lm_files = []
    if lm_files:
        dem[dem < -1000] = np.nan
        cell_m = 2.0  # 2m-modell
        twi = compute_twi(dem, cell_m=cell_m)
        moisture = normalize(twi)
        moisture = resample_to(moisture, target_shape)

        # Komplettera med NMD våtmark om tillgänglig
        if NMD_BASSKIKT.exists():
            nmd, _ = clip_and_read(NMD_BASSKIKT, crs_is_sweref=True)
            wetland = np.isin(nmd.astype(int), NMD_WETLAND_CLASSES).astype(float)
            wetland = resample_to(wetland, target_shape)
            moisture = moisture * 0.7 + wetland * 0.3

        return moisture

    # B. Fallback: NMD-våtmark + generisk topografi
    if NMD_BASSKIKT.exists():
        print("  Kalla: NMD vatmark (fuktproxy)")
        nmd, _ = clip_and_read(NMD_BASSKIKT, crs_is_sweref=True)
        wetland = np.isin(nmd.astype(int), NMD_WETLAND_CLASSES).astype(float)
        return resample_to(normalize(wetland + 0.1), target_shape)

    # C. Fallback: Copernicus DEM (global, alltid tillgänglig)
    dem_files = sorted(DEM_FALLBACK_DIR.glob("*.tif"))
    if dem_files:
        print("  Kalla: Copernicus DEM (global fallback)")
        dem, _ = merge_and_clip(dem_files, crs_is_sweref=False)
        dem[dem < -1000] = np.nan
        twi = compute_twi(dem, cell_m=30.0)
        return resample_to(normalize(twi), target_shape)

    print("  [VARNING] Ingen hojddata – fuktindex satt till 0.5")
    return np.full(target_shape, 0.5)


# ── Huvudfunktion ─────────────────────────────────────────────────────────────

def compute_indices():
    print("\n" + "=" * 60)
    print("Beraknar NVI-delindex (svenska datakallor)")
    print("=" * 60)

    print("\n[1/3] Strukturindex ...")
    structure, base_meta = build_structure_index()

    print("\n[2/3] Kontinuitetsindex ...")
    continuity = build_continuity_index(structure.shape)

    print("\n[3/3] Fuktindex ...")
    moisture = build_moisture_index(structure.shape)

    print("\nSammansatt NVI-poang ...")
    # Fyll kantpixlar (NaN från täckningsgap) med medianvärde
    for arr in [structure, continuity, moisture]:
        nan_mask = ~np.isfinite(arr)
        if nan_mask.any():
            arr[nan_mask] = np.nanmedian(arr)

    nvi_score = (
        structure  * WEIGHTS["structure"] +
        continuity * WEIGHTS["continuity"] +
        moisture   * WEIGHTS["moisture"]
    )

    # Maskera bort icke-skogsmark (bebyggelse, åker, vatten m.m.) så att
    # dessa pixlar inte kan hamna i hotspot-klasser enbart via fukt-/kontinuitetsindex.
    # NMD-klasserna 1–3 = skog; övriga (4=åker, 5=öppen, 7=exploaterad, 8=vatten) sätts till 0.
    # Artdata-bonus: observationer av hotade arter (VU/EN/CR från Rödlistan) lyfter
    # NVI-poängen i pixlar med fynd. Rastern skapas av species_overlay_a.py och
    # sparas i outputs/species/. Bonus appliceras FÖRE skogsmask så att hotade
    # arter i kantzoner inte raderas ut av masken.
    species_raster = SPECIES_OUTPUT_DIR / f"{AOI_NAME}_species_threat_obs_count.tif"
    if species_raster.exists():
        print("  Applicerar artdata-bonus (hotade arter från Rodlistan) ...")
        with rasterio.open(species_raster) as _src:
            sp_arr = _src.read(1).astype(float)
        sp_arr = resample_to(sp_arr, nvi_score.shape)
        # Normalisera: ≥1 fynd = 0.10 bonus, ≥3 fynd = 0.15 bonus (tak vid 0.15)
        sp_bonus = np.where(sp_arr >= 3, 0.15,
                   np.where(sp_arr >= 1, 0.10, 0.0))
        nvi_score = np.clip(nvi_score + sp_bonus, 0, 1)
        n_px = int((sp_arr >= 1).sum())
        print(f"  + {n_px} pixlar med hotade artfynd bonifierade")

    if NMD_BASSKIKT.exists():
        print("  Applicerar NMD-skogsmask på NVI-poäng ...")
        nmd_arr, _ = clip_and_read(NMD_BASSKIKT, crs_is_sweref=True)
        # Nearest-neighbour för kategoriska klassvärden (bilinär ger meningslösa mellanvärden)
        from scipy.ndimage import zoom as _zoom
        factors = (nvi_score.shape[0] / nmd_arr.shape[0], nvi_score.shape[1] / nmd_arr.shape[1])
        nmd_arr = _zoom(nmd_arr, factors, order=0)
        non_forest = ~np.isin(nmd_arr.astype(int), NMD_FOREST_CLASSES)
        nvi_score[non_forest] = 0.0

    for arr, suffix in [
        (structure,  "structure_index"),
        (continuity, "continuity_index"),
        (moisture,   "moisture_index"),
        (nvi_score,  "nvi_score"),
    ]:
        save_raster(arr, base_meta, suffix)

    print(
        f"\n  NVI-poang: min={nvi_score.min():.3f}  "
        f"medel={nvi_score.mean():.3f}  max={nvi_score.max():.3f}"
    )
    print("\n[klar] se data/processed/")
    return nvi_score, base_meta


if __name__ == "__main__":
    compute_indices()
