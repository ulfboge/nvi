"""
compare_hotspot_runs.py
Jämför två hotspot-klassraster (t.ex. före/efter SLU Skogskarta + kol) och kör GPKG-validering mot båda.

Exempel:
  1) Kopiera nuvarande klassraster som referens:
       copy outputs\\rasters\\{AOI}_hotspot_class.tif outputs\\rasters\\{AOI}_hotspot_class_ref.tif
  2) Lägg in SLU-lager, kör om pipeline (compute_indices → hotspot_model).
  3) Jämför:
       python scripts/python/compare_hotspot_runs.py \\
         --ref outputs/rasters/kungsbacka_vastra_hotspot_class_ref.tif

  Utan --ref: jämför processed vs outputs om båda finns, annars skriver skriptet instruktioner.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import rasterio

sys.path.insert(0, str(Path(__file__).parent))
from config import AOI_NAME, PROC_DIR, RASTERS_DIR

from validate_against_gpkg import evaluate_hotspot_vs_gpkg, print_metrics


def _read_class(path: Path) -> tuple[np.ndarray, object]:
    with rasterio.open(path) as src:
        return src.read(1).astype(np.int16), src.transform


def _align_to(a: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    if a.shape == shape:
        return a
    from scipy.ndimage import zoom

    f = (shape[0] / a.shape[0], shape[1] / a.shape[1])
    return np.round(zoom(a, f, order=0)).astype(np.int16)


def raster_agreement(path_a: Path, path_b: Path) -> dict:
    aa, _ = _read_class(path_a)
    bb, _ = _read_class(path_b)
    if aa.shape != bb.shape:
        bb = _align_to(bb, aa.shape)
    valid = (aa > 0) & (bb > 0) & np.isfinite(aa) & np.isfinite(bb)
    n = int(valid.sum())
    if n == 0:
        return {"n": 0, "same_class_pct": 0.0}
    same = int((aa[valid] == bb[valid]).sum())
    return {"n": n, "same_class_pct": 100.0 * same / n}


def main() -> None:
    parser = argparse.ArgumentParser(description="Jamfor tva hotspot_class.tif + GPKG-metrics")
    parser.add_argument(
        "--ref",
        type=Path,
        default=None,
        help="Referens-raster (t.ex. kopierad klassning fore SLU-utokning)",
    )
    parser.add_argument(
        "--current",
        type=Path,
        default=None,
        help="Senaste raster (standard: outputs/rasters/{AOI}_hotspot_class.tif)",
    )
    args = parser.parse_args()

    cur = args.current or (RASTERS_DIR / f"{AOI_NAME}_hotspot_class.tif")
    if not cur.exists():
        sys.exit(f"[FEL] Saknar aktuell raster: {cur}")

    ref = args.ref
    if ref is None:
        cand = RASTERS_DIR / f"{AOI_NAME}_hotspot_class_ref.tif"
        if cand.exists():
            ref = cand
        else:
            alt = PROC_DIR / f"{AOI_NAME}_hotspot_class_ref.tif"
            if alt.exists():
                ref = alt

    print("\n" + "=" * 60)
    print("Jamfor NVI hotspot-klassraster")
    print("=" * 60)
    print(f"Aktuell: {cur}")

    m_cur = evaluate_hotspot_vs_gpkg(cur)
    if m_cur is None:
        print("[FEL] Kunde inte utvardera aktuell raster (GPKG?).")
        sys.exit(1)
    print("\n--- GPKG-validering: aktuell ---")
    print_metrics(m_cur, cur.name)

    if ref is None or not ref.exists():
        print(
            "\n[info] Ingen referensraster (--ref eller *_hotspot_class_ref.tif).\n"
            "        Kopiera nuvarande klassning innan du andrar modellen, t.ex.:\n"
            f"        copy {cur} {RASTERS_DIR / (AOI_NAME + '_hotspot_class_ref.tif')}"
        )
        return

    print(f"Referens: {ref}")
    agr = raster_agreement(ref, cur)
    print(
        f"\n--- Pixeloverensstammelse (skogspixlar klass>0, bada raster) ---\n"
        f"    Pixlar: {agr['n']:,}  |  samma klass: {agr['same_class_pct']:.2f} %"
    )

    m_ref = evaluate_hotspot_vs_gpkg(ref)
    if m_ref is None:
        print("[varning] Kunde inte utvardera referens mot GPKG.")
        return
    print("\n--- GPKG-validering: referens ---")
    print_metrics(m_ref, ref.name)

    print("\n--- Delta (aktuell minus referens) ---")
    print(
        f"    Exakt träff: {m_cur['exact_pct'] - m_ref['exact_pct']:+.1f} procentenheter\n"
        f"    Nära ±1:    {m_cur['near_pct'] - m_ref['near_pct']:+.1f} procentenheter\n"
        f"    Areal-vikt: {m_cur['area_weighted_pct'] - m_ref['area_weighted_pct']:+.1f} procentenheter"
    )


if __name__ == "__main__":
    main()
