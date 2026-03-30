"""
generate_showcase.py
Genererar showcase-figurer för GitHub Pages.
Kör: python scripts/python/generate_showcase.py
"""

import sys
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.patches as mpatches
import matplotlib.gridspec as gridspec
from matplotlib.ticker import NullFormatter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import AOI_NAME, PROC_DIR, RASTERS_DIR

DOCS_ASSETS = Path(__file__).resolve().parents[2] / "docs" / "assets"
DOCS_ASSETS.mkdir(parents=True, exist_ok=True)

try:
    import rasterio
except ImportError:
    sys.exit("Saknar rasterio – kor: pip install rasterio")


def load(suffix: str) -> np.ndarray:
    # hotspot_class sparas i outputs/rasters/, övriga i data/processed/
    p = RASTERS_DIR / f"{AOI_NAME}_{suffix}.tif"
    if not p.exists():
        p = PROC_DIR / f"{AOI_NAME}_{suffix}.tif"
    with rasterio.open(p) as src:
        arr = src.read(1).astype(float)
        nd  = src.nodata
    if nd is not None:
        arr[arr == nd] = np.nan
    return arr


def make_hotspot_figure() -> None:
    """Stor showcase-figur: hotspot-karta + tre delindex."""

    cls       = np.nan_to_num(load("hotspot_class"))
    score     = np.nan_to_num(load("nvi_score"))
    structure = np.nan_to_num(load("structure_index"))
    cont      = np.nan_to_num(load("continuity_index"))
    moisture  = np.nan_to_num(load("moisture_index"))

    # ── Layout ────────────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(18, 11), facecolor="#f5f7f5")
    gs  = gridspec.GridSpec(
        2, 4,
        left=0.02, right=0.98, top=0.88, bottom=0.06,
        wspace=0.04, hspace=0.22
    )

    # ── Hotspot-karta (stor, vänster) ─────────────────────────────────────────
    ax_main = fig.add_subplot(gs[:, :2])

    cmap_cls = mcolors.ListedColormap(["#4575b4", "#fee090", "#d73027"])
    norm_cls = mcolors.BoundaryNorm([0.5, 1.5, 2.5, 3.5], 3)

    ax_main.imshow(
        np.where(cls > 0, cls, np.nan),
        cmap=cmap_cls, norm=norm_cls, interpolation="nearest",
        aspect="equal"
    )
    ax_main.set_title(
        "Hotspot-klassifikation\nNVI-prioritering baserad på EO-screening",
        fontsize=13, fontweight="bold", pad=10, color="#1b4332"
    )
    ax_main.axis("off")

    legend_patches = [
        mpatches.Patch(color="#d73027", label="Klass 3 – Hotspot (intensiv inventering)"),
        mpatches.Patch(color="#fee090", label="Klass 2 – Mellanklass (stickprov)"),
        mpatches.Patch(color="#4575b4", label="Klass 1 – Låg prioritet (snabb verif.)"),
    ]
    ax_main.legend(
        handles=legend_patches,
        loc="lower left", fontsize=10,
        framealpha=0.92, edgecolor="#ccc",
        title="NVI-prioritet", title_fontsize=10
    )

    # ── Delindex (höger, 2x2) ─────────────────────────────────────────────────
    panels = [
        (score,     "NVI-poäng\n(sammansatt)",          "RdYlGn",   0,   1),
        (structure, "Strukturindex\n(NMD + trädhöjd)",  "YlGn",     0,   1),
        (cont,      "Kontinuitetsindex\n(Skogsstyrelsen avverk.)", "Blues", 0, 1),
        (moisture,  "Fuktindex\n(Lantmäteriet lidar-TWI)", "GnBu",  0,   1),
    ]

    for i, (arr, title, cmap, vmin, vmax) in enumerate(panels):
        row, col = divmod(i, 2)
        ax = fig.add_subplot(gs[row, col + 2])
        im = ax.imshow(arr, cmap=cmap, vmin=vmin, vmax=vmax,
                       interpolation="bilinear", aspect="equal")
        ax.set_title(title, fontsize=10, fontweight="bold",
                     pad=5, color="#1b4332")
        ax.axis("off")
        cb = plt.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
        cb.ax.tick_params(labelsize=8)

    # ── Rubrik ────────────────────────────────────────────────────────────────
    fig.text(
        0.5, 0.955,
        "EO-driven Naturvärdesinventering  |  Testområde: Fiby urskog, Uppland",
        ha="center", fontsize=15, fontweight="bold", color="#1b4332"
    )
    fig.text(
        0.5, 0.925,
        "Datakällor: NMD 2023 (Naturvårdsverket)  ·  GSD-Höjddata 1m (Lantmäteriet)  "
        "·  Avverkningsanmälningar (Skogsstyrelsen)",
        ha="center", fontsize=9, color="#555", style="italic"
    )

    out = DOCS_ASSETS / "hotspot_showcase.png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"[ok] {out}")
    plt.close()


def make_method_diagram() -> None:
    """Enkel processkarta – 6 steg som pil-diagram."""

    steps = [
        ("1", "EO-screening",      "NMD 2023 · Lidar DTM\nSkogsstyrelsen",  "#2d6a4f"),
        ("2", "Hotspot-modell",    "Struktur + Kontinuitet\n+ Fukt (viktat)", "#40916c"),
        ("3", "Sampling-design",   "Stratifiering\nHotspot / Mellan / Låg",  "#52b788"),
        ("4", "Fältinventering",   "Riktad NVI\nSignalarter · Substrat",     "#74c69d"),
        ("5", "Kvantifiering",     "Naturvärdesindex\nf(struktur+kont.+art)", "#95d5b2"),
        ("6", "Reproducerbarhet",  "GEE · Python · QGIS\nNytt område = ny kör", "#b7e4c7"),
    ]

    fig, ax = plt.subplots(figsize=(14, 3.2), facecolor="#f5f7f5")
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 1)
    ax.axis("off")

    step_w = 14 / len(steps)
    for i, (num, title, desc, color) in enumerate(steps):
        x = i * step_w + step_w * 0.1
        w = step_w * 0.75

        # Box
        rect = mpatches.FancyBboxPatch(
            (x, 0.12), w, 0.76,
            boxstyle="round,pad=0.02",
            facecolor=color, edgecolor="white", linewidth=2,
            zorder=2
        )
        ax.add_patch(rect)

        # Pil
        if i < len(steps) - 1:
            ax.annotate(
                "", xy=(x + w + step_w * 0.25, 0.5),
                xytext=(x + w + 0.02, 0.5),
                arrowprops=dict(arrowstyle="->", color="#555", lw=1.8),
                zorder=3
            )

        # Text
        ax.text(x + w/2, 0.72, f"Steg {num}", ha="center", va="center",
                fontsize=8, color="white", fontweight="bold", zorder=3)
        ax.text(x + w/2, 0.52, title, ha="center", va="center",
                fontsize=9, color="white", fontweight="bold", zorder=3)
        ax.text(x + w/2, 0.28, desc, ha="center", va="center",
                fontsize=7, color="white", alpha=0.9, zorder=3,
                linespacing=1.4)

    fig.text(
        0.5, 0.97,
        "Metodöversikt: EO-driven NVI-workflow",
        ha="center", fontsize=11, fontweight="bold", color="#1b4332"
    )

    out = DOCS_ASSETS / "method_diagram.png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"[ok] {out}")
    plt.close()


if __name__ == "__main__":
    print("Genererar showcase-figurer ...")
    make_hotspot_figure()
    make_method_diagram()
    print("Klart – se docs/assets/")
