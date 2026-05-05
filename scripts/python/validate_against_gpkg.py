"""
validate_against_gpkg.py
Jämför hotspot-klassning mot extern NVI i GeoPackage.

Antaganden:
  - GPKG innehåller polygoner med ett klassfält som kan tolkas till klass 1-4
  - Hotspot-raster finns i outputs/rasters/{AOI_NAME}_hotspot_class.tif

Kör:
  python scripts/python/validate_against_gpkg.py
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import rasterio
from rasterio.transform import rowcol

sys.path.insert(0, str(Path(__file__).parent))
from config import AOI_NAME, EPSG_SWEREF, FIGURES_DIR, RASTERS_DIR, REPO_DIR

# Windows: undvik krock mellan system-PROJ och pyproj (trasig proj.db).
os.environ.setdefault("PYPROJ_USE_PROJ_DATA_PACKAGES", "1")


def _crs_equivalent(a, b) -> bool:
    """True om GDF redan ligger i samma EPSG-kod som *b* (int eller CRS-liknande)."""
    if a is None or b is None:
        return False
    if isinstance(b, int):
        try:
            return a.to_epsg() == b
        except Exception:
            return False
    if str(a) == str(b):
        return True
    try:
        ea = a.to_epsg()
        eb = b.to_epsg()
        return ea is not None and eb is not None and ea == eb
    except Exception:
        return False


def _parse_class(v: object) -> int | None:
    if v is None:
        return None
    s = str(v).strip()
    m = re.search(r"\b([1-4])\b", s)
    if not m:
        return None
    return int(m.group(1))


def _sample_raster_near_point(src: rasterio.DatasetReader, x: float, y: float, window: int = 3) -> int | None:
    try:
        row, col = rowcol(src.transform, x, y)
        half = window // 2
        data = src.read(1)
        r0, r1 = max(0, row - half), min(src.height, row + half + 1)
        c0, c1 = max(0, col - half), min(src.width, col + half + 1)
        patch = data[r0:r1, c0:c1]
        nonzero = patch[(patch != src.nodata) & (patch > 0)]
        if nonzero.size > 0:
            return int(np.max(nonzero))
    except Exception:
        return None
    return None


def _plot(results: list[dict], out_path: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    exact = sum(r["match"] for r in results)
    total = len(results)
    fig.suptitle(
        f"Validering mot GPKG-NVI ({AOI_NAME})\n"
        f"Träffandel: {exact}/{total} objekt",
        fontsize=12,
        fontweight="bold",
    )

    labels = ["Klass 1\n(Mycket högt)", "Klass 2\n(Högt)", "Klass 3\n(Påtagligt)", "Klass 4\n(Visst)"]
    cm = np.zeros((4, 4), dtype=int)
    for r in results:
        ei = min(max(r["exp_cls"] - 1, 0), 3)
        pi = min(max(r["pipe_cls"] - 1, 0), 3)
        cm[ei, pi] += 1
    ax = axes[0]
    im = ax.imshow(cm, cmap="Blues", vmin=0)
    ax.set_xticks(range(4))
    ax.set_yticks(range(4))
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel("Pipeline-klass")
    ax.set_ylabel("Förväntad klass (GPKG)")
    ax.set_title("Confusion matrix (antal objekt)", fontsize=10)
    for i in range(4):
        for j in range(4):
            ax.text(
                j,
                i,
                cm[i, j],
                ha="center",
                va="center",
                fontsize=12,
                fontweight="bold",
                color="white" if cm[i, j] > max(1, cm.max() * 0.6) else "black",
            )

    ax2 = axes[1]
    colors = {True: "#2ca02c", False: "#d62728"}
    for r in results:
        ax2.scatter(
            r["exp_cls"],
            r["pipe_cls"],
            s=max(20, r["area_m2"] / 250),
            alpha=0.6,
            color=colors[r["match"]],
            edgecolors="grey",
            linewidths=0.4,
        )
    ax2.set_xticks([1, 2, 3, 4])
    ax2.set_yticks([1, 2, 3, 4])
    ax2.set_xticklabels(["1 (Mycket högt)", "2 (Högt)", "3 (Påtagligt)", "4 (Visst)"], fontsize=8)
    ax2.set_yticklabels(["1 (Mycket högt)", "2 (Högt)", "3 (Påtagligt)", "4 (Visst)"], fontsize=8)
    ax2.set_xlabel("Förväntad klass (GPKG)")
    ax2.set_ylabel("Pipeline-klass")
    ax2.set_title("Klassöverensstämmelse per objekt\n(cirkelstorlek ≈ area)", fontsize=10)
    ax2.plot([0.5, 4.5], [0.5, 4.5], "k--", lw=1, alpha=0.4, label="Perfekt träff")
    ax2.legend(
        handles=[
            mpatches.Patch(color="#2ca02c", label="Korrekt klass"),
            mpatches.Patch(color="#d62728", label="Felklassad"),
        ],
        fontsize=9,
        loc="upper left",
    )
    ax2.set_xlim(0.5, 4.5)
    ax2.set_ylim(0.5, 4.5)

    plt.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()


def _load_nv_gpkg() -> tuple[Path, str | None, gpd.GeoDataFrame] | None:
    gpkg_dir = REPO_DIR / "data" / "raw" / "gpkg"
    gpkg_files = sorted(gpkg_dir.glob("*.gpkg"))
    if not gpkg_files:
        return None
    gpkg_path = gpkg_files[0]
    layers = gpd.list_layers(gpkg_path)
    layer = layers.iloc[0]["name"] if len(layers) else None
    gdf = gpd.read_file(gpkg_path, layer=layer)
    if "nvklass" not in gdf.columns:
        return None
    gdf = gdf[gdf.geometry.notna() & (~gdf.geometry.is_empty)].copy()
    gdf["exp_cls"] = gdf["nvklass"].map(_parse_class)
    gdf = gdf[gdf["exp_cls"].notna()].copy()
    gdf["exp_cls"] = gdf["exp_cls"].astype(int)
    gdf["area_m2"] = gdf.geometry.area
    return gpkg_path, layer, gdf


def evaluate_hotspot_vs_gpkg(hotspot_path: Path) -> dict | None:
    """
    Returnerar mätvärden + resultatlista. None om GPKG eller raster saknas / inga objekt.
    """
    if not hotspot_path.exists():
        return None
    loaded = _load_nv_gpkg()
    if loaded is None:
        return None
    gpkg_path, layer, gdf = loaded

    with rasterio.open(hotspot_path) as src:
        if gdf.crs is None:
            return None
        if not _crs_equivalent(gdf.crs, EPSG_SWEREF):
            gdf = gdf.to_crs(epsg=EPSG_SWEREF)
        cent = gdf.geometry.centroid
        gdf = gdf.copy()
        gdf["pipe_cls"] = [
            _sample_raster_near_point(src, float(pt.x), float(pt.y), window=3) for pt in cent
        ]

    results: list[dict] = []
    for _, r in gdf.iterrows():
        pipe_raw = r["pipe_cls"]
        if pipe_raw is None or (isinstance(pipe_raw, float) and np.isnan(pipe_raw)):
            continue
        if int(pipe_raw) == 0:
            continue
        exp_cls = int(r["exp_cls"])
        pipe_cls = int(pipe_raw)
        results.append(
            {
                "exp_cls": exp_cls,
                "pipe_cls": pipe_cls,
                "area_m2": float(r["area_m2"]),
                "match": exp_cls == pipe_cls,
                "diff": pipe_cls - exp_cls,
            }
        )

    if not results:
        return None

    total = len(results)
    exact = sum(r["match"] for r in results)
    near = sum(abs(r["diff"]) <= 1 for r in results)
    a_tot = sum(r["area_m2"] for r in results)
    a_hit = sum(r["area_m2"] for r in results if r["match"])

    return {
        "gpkg_name": gpkg_path.name,
        "layer": layer,
        "total": total,
        "exact": exact,
        "near": near,
        "exact_pct": 100.0 * exact / total,
        "near_pct": 100.0 * near / total,
        "area_weighted_pct": 100.0 * a_hit / a_tot,
        "over": sum(r["diff"] > 0 for r in results),
        "under": sum(r["diff"] < 0 for r in results),
        "results": results,
    }


def print_metrics(m: dict, raster_label: str) -> None:
    print(f"Raster: {raster_label}")
    print(f"GPKG:   {m['gpkg_name']}")
    print(f"Lager:  {m['layer']}")
    print(f"Objekt: {m['total']}")
    print(f"Exakt träff:      {m['exact']}/{m['total']} = {m['exact_pct']:.1f}%")
    print(f"Nära träff (±1):  {m['near']}/{m['total']} = {m['near_pct']:.1f}%")
    print(f"Areal-viktad:     {m['area_weighted_pct']:.1f}%")
    print(f"Överskattade:     {m['over']}")
    print(f"Underskattade:    {m['under']}")


def run(hotspot_path: Path | None = None, write_figure: bool = True) -> None:
    path = hotspot_path or (RASTERS_DIR / f"{AOI_NAME}_hotspot_class.tif")
    if not path.exists():
        sys.exit(f"[FEL] Saknar {path}")

    m = evaluate_hotspot_vs_gpkg(path)
    if m is None:
        sys.exit("[FEL] GPKG eller fältet nvklass saknas, eller inga jämförbara objekt.")

    print("\n" + "=" * 60)
    print("Validering mot GPKG-NVI")
    print("=" * 60)
    print_metrics(m, path.name)

    if write_figure:
        out = FIGURES_DIR / f"{AOI_NAME}_validation_gpkg.png"
        _plot(m["results"], out)
        print(f"Figur: {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validera hotspot mot GPKG-NVI")
    parser.add_argument(
        "--hotspot",
        type=Path,
        default=None,
        help="Sökväg till hotspot_class.tif (standard: outputs/rasters/{AOI}_hotspot_class.tif)",
    )
    parser.add_argument(
        "--no-figure",
        action="store_true",
        help="Skriv endast tabell (ingen PNG)",
    )
    a = parser.parse_args()
    run(hotspot_path=a.hotspot, write_figure=not a.no_figure)
