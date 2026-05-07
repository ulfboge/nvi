"""
hotspot_model.py
Klassificerar NVI-poäng till hotspot-karta (4 klasser, SS 199000:2023) och genererar figurer.

Klasser (SS 199000:2023 – samma numrering som standarden och fältrapporter):
  1 = Mycket högt naturvärde  → intensiv inventering (alla artgrupper)
  2 = Högt naturvärde         → noggrann inventering
  3 = Påtagligt naturvärde    → stickprov
  4 = Visst naturvärde        → snabb verifiering

Kör:
  python scripts/python/hotspot_model.py
"""

import sys
import os
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.patches as mpatches
from pathlib import Path
from scipy.ndimage import label as _label

sys.path.insert(0, str(Path(__file__).parent))
from config import AOI_NAME, PROC_DIR, FIGURES_DIR, RASTERS_DIR

try:
    import rasterio
except ImportError:
    sys.exit("[FEL] Saknar rasterio – kör: pip install -r requirements.txt")


# ── Klassificering ────────────────────────────────────────────────────────────

def classify_hotspots(score: np.ndarray) -> tuple[np.ndarray, float, float, float]:
    """Percentilbaserad 4-klassklassificering (SS 199000:2023) med absoluta golv.

    Numrering följer SS 199000:2023 och Länsstyrelsens fältrapporter direkt:
      klass 1 (Mycket högt) ≈ topp  7 % av skogsmark  → intensiv inventering
      klass 2 (Högt)        ≈ nästa 18 %               → noggrann inventering
      klass 3 (Påtagligt)   ≈ nästa 50 %               → stickprov
      klass 4 (Visst)       ≈ nedre 25 %               → snabb verifiering

    Absoluta golv förhindrar att låg-poängsytor (t.ex. granplantager)
    klassas upp enbart av sin relativa rank:
      MIN_KLASS3 = 0.38 – minst 38 % av maxpoäng för klass 3 (Påtagligt)
      MIN_KLASS2 = 0.72 – minst 72 % för klass 2 (Högt)
      MIN_KLASS1 = 0.85 – minst 85 % för klass 1 (Mycket högt)
    """
    valid = score[score > 0]
    p25 = float(np.percentile(valid, 25))
    p75 = float(np.percentile(valid, 75))
    p93 = float(np.percentile(valid, 93))

    MIN_KLASS3 = 0.38
    MIN_KLASS2 = 0.72
    MIN_KLASS1 = 0.85
    p25 = max(p25, MIN_KLASS3)
    p75 = max(p75, MIN_KLASS2)
    p93 = max(p93, MIN_KLASS1)

    cls = np.zeros_like(score, dtype=np.uint8)
    cls[score > 0]    = 4   # Visst naturvärde
    cls[score > p25]  = 3   # Påtagligt naturvärde
    cls[score > p75]  = 2   # Högt naturvärde
    cls[score > p93]  = 1   # Mycket högt naturvärde

    print(f"  Trosklar: p25={p25:.3f}  p75={p75:.3f}  p93={p93:.3f}")
    print(f"  (golv klass3>={MIN_KLASS3}, klass2>={MIN_KLASS2}, klass1>={MIN_KLASS1})")
    return cls, p25, p75, p93


def apply_patch_filter(cls: np.ndarray, pixel_size_m: float,
                       min_ha: float = 0.5) -> np.ndarray:
    """Nedgraderar isolerade höga klasser som är för små för fältinventering.

    Sammanhängande pixelgrupper (4-konnektivitet) under min_ha hectar
    degraderas ett steg (mot lägre värde = högre siffra): klass 1→2, 2→3, 3→4.
    Klass 4 (Visst) påverkas inte – låg prioritet behöver inget minimimått.
    """
    min_pixels = max(1, int(min_ha * 10_000 / pixel_size_m**2))
    result = cls.copy()

    for k in [1, 2, 3]:   # klass 1=Mycket högt → 2=Högt → 3=Påtagligt
        mask = (cls == k).astype(np.uint8)
        labeled, n_features = _label(mask)
        for i in range(1, n_features + 1):
            patch = labeled == i
            if patch.sum() < min_pixels:
                result[patch] = k + 1   # degradera ett steg (högre siffra = lägre värde)

    n_degraded = int(np.sum(result != cls))
    print(f"  Patchfilter ({min_ha} ha): {n_degraded} pixlar nedgraderade")
    return result


# ── Statistik ─────────────────────────────────────────────────────────────────

def area_statistics(cls: np.ndarray, pixel_size_m: float = 30.0) -> None:
    px_ha = pixel_size_m**2 / 10_000
    total = int(np.sum(cls > 0))
    labels = {
        1: "Mycket högt naturvärde (intensiv inv.)",
        2: "Högt naturvärde (noggrann inv.)",
        3: "Påtagligt naturvärde (stickprov)",
        4: "Visst naturvärde (snabb verif.)",
    }
    print("\n  Areal per klass (SS 199000:2023):")
    for k in [1, 2, 3, 4]:
        n = int(np.sum(cls == k))
        ha = n * px_ha
        pct = 100 * n / total if total > 0 else 0
        print(f"    Klass {k}  {labels[k]:<42} {ha:7.1f} ha  ({pct:.0f}%)")


# ── Visualisering ─────────────────────────────────────────────────────────────

def plot_results(cls: np.ndarray, score: np.ndarray, indices: dict) -> None:
    fig = plt.figure(figsize=(16, 10))
    fig.suptitle(
        f"EO-driven NVI-screening  |  AOI: {AOI_NAME}",
        fontsize=14, fontweight='bold', y=0.98
    )

    # Färgschema – klass 1=Mycket högt (mörkröd) → klass 4=Visst (ljusblå)
    # Följer SS 199000:2023 numrering direkt
    class_cmap = mcolors.ListedColormap(['#7b0012', '#d73027', '#fdae61', '#d1e5f0'])
    class_norm = mcolors.BoundaryNorm([0.5, 1.5, 2.5, 3.5, 4.5], 4)

    # ── Hotspot-karta (stor, vänster) ──
    ax1 = fig.add_subplot(2, 3, (1, 4))
    im1 = ax1.imshow(np.where(cls > 0, cls, np.nan),
                     cmap=class_cmap, norm=class_norm, interpolation='nearest')
    ax1.set_title('NVI-klassificering  (SS 199000:2023)', fontsize=12, fontweight='bold')
    ax1.axis('off')
    patches = [
        mpatches.Patch(color='#7b0012', label='Klass 1 – Mycket högt'),
        mpatches.Patch(color='#d73027', label='Klass 2 – Högt'),
        mpatches.Patch(color='#fdae61', label='Klass 3 – Påtagligt'),
        mpatches.Patch(color='#d1e5f0', label='Klass 4 – Visst'),
    ]
    ax1.legend(handles=patches, loc='lower left', fontsize=9)

    # ── NVI-poäng ──
    ax2 = fig.add_subplot(2, 3, 2)
    im2 = ax2.imshow(score, cmap='RdYlGn', vmin=0, vmax=1, interpolation='bilinear')
    ax2.set_title('NVI-poäng (sammansatt)', fontsize=10)
    plt.colorbar(im2, ax=ax2, label='[0–1]', fraction=0.046)
    ax2.axis('off')

    # ── Delindex ──
    subplot_cfg = [
        ('structure_index',  'Strukturindex\n(biomassa, komplexitet)', 'Greens'),
        ('continuity_index', 'Kontinuitetsindex\n(störningsfri skog)',  'Blues'),
        ('moisture_index',   'Fuktindex\n(TWI, hydrologisk pos.)',      'PuBuGn'),
    ]

    for i, (key, title, cmap_name) in enumerate(subplot_cfg):
        ax = fig.add_subplot(2, 3, 3 + i)
        if key in indices:
            im = ax.imshow(indices[key], cmap=cmap_name, vmin=0, vmax=1,
                           interpolation='bilinear')
            plt.colorbar(im, ax=ax, fraction=0.046)
        ax.set_title(title, fontsize=9)
        ax.axis('off')

    plt.tight_layout(rect=[0, 0, 1, 0.97])
    out = FIGURES_DIR / f"{AOI_NAME}_hotspot_map.png"
    fig.savefig(out, dpi=150, bbox_inches='tight')
    print(f"\n  Figur sparad: {out.name}", flush=True)
    plt.close()


# ── Läs delindex ──────────────────────────────────────────────────────────────

def load_index(fname: str):
    p = PROC_DIR / f"{AOI_NAME}_{fname}.tif"
    if not p.exists():
        return None
    with rasterio.open(p) as src:
        arr = src.read(1).astype(float)
        nodata = src.nodata
    if nodata is not None:
        arr[arr == nodata] = np.nan
    return arr


# ── Main ─────────────────────────────────────────────────────────────────────

def run():
    print("\n" + "=" * 60)
    print("Hotspot-klassificering")
    print("=" * 60)

    score_path = PROC_DIR / f"{AOI_NAME}_nvi_score.tif"
    if not score_path.exists():
        sys.exit(f"[FEL] Saknar {score_path.name} – kör compute_indices.py först.")

    with rasterio.open(score_path) as src:
        score = src.read(1).astype(float)
        meta  = src.meta.copy()
        px_m  = abs(src.transform[0])

    nodata = meta.get("nodata", -9999)
    score[score == nodata] = np.nan
    score = np.nan_to_num(score, nan=0.0)

    px = px_m if px_m > 1 else 10.0
    cls, p25, p75, p93 = classify_hotspots(score)
    skip_patch_filter = os.environ.get("HOTSPOT_SKIP_PATCH_FILTER", "0").strip().lower() in ("1", "true", "yes")
    if skip_patch_filter:
        print("  [info] Patchfilter hoppas over via HOTSPOT_SKIP_PATCH_FILTER")
    else:
        cls = apply_patch_filter(cls, pixel_size_m=px, min_ha=0.5)
    area_statistics(cls, pixel_size_m=px)

    # Ladda delindex för visualisering
    indices = {}
    for key in ["structure_index", "continuity_index", "moisture_index"]:
        arr = load_index(key)
        if arr is not None:
            indices[key] = np.nan_to_num(arr, nan=0.0)

    plot_results(cls, score, indices)

    # Spara klassraster
    out_path = RASTERS_DIR / f"{AOI_NAME}_hotspot_class.tif"
    out_meta = meta.copy()
    out_meta.update({"dtype": "uint8", "nodata": 0})
    with rasterio.open(out_path, "w", **out_meta) as dst:
        dst.write(cls, 1)
    print(f"  Raster sparad: {out_path.name}")

    print("\n[klar] se outputs/")


if __name__ == "__main__":
    run()
