"""
hotspot_model.py
Klassificerar NVI-poäng till hotspot-karta (3 klasser) och genererar figurer.

Klasser (NVI-kompatibel logik):
  3 = Hotspot          → intensiv inventering (alla artgrupper)
  2 = Mellanklass      → stickprov
  1 = Låg prioritet    → snabb verifiering

Kör:
  python scripts/python/hotspot_model.py
"""

import sys
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.patches as mpatches
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import AOI_NAME, PROC_DIR, FIGURES_DIR, RASTERS_DIR

try:
    import rasterio
except ImportError:
    sys.exit("[FEL] Saknar rasterio – kör: pip install -r requirements.txt")


# ── Klassificering ────────────────────────────────────────────────────────────

def classify_hotspots(score: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Percentilbaserad 3-klassklassificering (p33 / p67)."""
    valid = score[score > 0]
    p33 = float(np.percentile(valid, 33))
    p67 = float(np.percentile(valid, 67))

    cls = np.zeros_like(score, dtype=np.uint8)
    cls[score > 0]    = 1   # Låg prioritet
    cls[score > p33]  = 2   # Mellanklass
    cls[score > p67]  = 3   # Hotspot

    print(f"  Trosklar: p33={p33:.3f}  p67={p67:.3f}")
    return cls, p33, p67


# ── Statistik ─────────────────────────────────────────────────────────────────

def area_statistics(cls: np.ndarray, pixel_size_m: float = 30.0) -> None:
    px_ha = pixel_size_m**2 / 10_000
    total = int(np.sum(cls > 0))
    labels = {
        3: "Hotspot (intensiv inventering)",
        2: "Mellanklass (stickprov)",
        1: "Låg prioritet (snabb verif.)"
    }
    print("\n  Areal per klass:")
    for k in [3, 2, 1]:
        n = int(np.sum(cls == k))
        ha = n * px_ha
        pct = 100 * n / total if total > 0 else 0
        print(f"    Klass {k}  {labels[k]:<35} {ha:7.1f} ha  ({pct:.0f}%)")


# ── Visualisering ─────────────────────────────────────────────────────────────

def plot_results(cls: np.ndarray, score: np.ndarray, indices: dict) -> None:
    fig = plt.figure(figsize=(16, 10))
    fig.suptitle(
        f"EO-driven NVI-screening  |  AOI: {AOI_NAME}",
        fontsize=14, fontweight='bold', y=0.98
    )

    # Färgschema
    class_cmap = mcolors.ListedColormap(['#4575b4', '#fee090', '#d73027'])
    class_norm = mcolors.BoundaryNorm([0.5, 1.5, 2.5, 3.5], 3)

    # ── Hotspot-karta (stor, vänster) ──
    ax1 = fig.add_subplot(2, 3, (1, 4))
    im1 = ax1.imshow(np.where(cls > 0, cls, np.nan),
                     cmap=class_cmap, norm=class_norm, interpolation='nearest')
    ax1.set_title('Hotspot-klassifikation', fontsize=12, fontweight='bold')
    ax1.axis('off')
    patches = [
        mpatches.Patch(color='#d73027', label='Klass 3 – Hotspot'),
        mpatches.Patch(color='#fee090', label='Klass 2 – Mellanklass'),
        mpatches.Patch(color='#4575b4', label='Klass 1 – Låg prioritet'),
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

    cls, p33, p67 = classify_hotspots(score)
    area_statistics(cls, pixel_size_m=px_m if px_m > 1 else 30.0)

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
