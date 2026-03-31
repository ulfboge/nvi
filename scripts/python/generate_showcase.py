"""
generate_showcase.py
Genererar showcase-figurer för GitHub Pages.

  * hotspot_showcase.png      – hotspot + delindex (som tidigare)
  * method_diagram.png        – processkarta
  * hotspot_protected_context.png – samma hotspot som underlag med formellt skydd
    (Naturvårdsverket INSPIRE WFS) som kontext; kräver
    data/raw/naturvardsverket/skyddad_natur/protected_sites_<AOI>.gpkg
    (t.ex. download_data.py --protected-sites-only)

Kör: python scripts/python/generate_showcase.py
"""

import sys
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.patches as mpatches
import matplotlib.gridspec as gridspec
import matplotlib.patheffects as pe
from pathlib import Path

from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).parent))
from config import AOI_NAME, PROC_DIR, PROTECTED_SITES_DIR, RASTERS_DIR

try:
    import geopandas as gpd
except ImportError:
    gpd = None  # type: ignore

DOCS_ASSETS = Path(__file__).resolve().parents[2] / "docs" / "assets"
DOCS_ASSETS.mkdir(parents=True, exist_ok=True)

try:
    import rasterio
    from rasterio.warp import transform_bounds
except ImportError:
    sys.exit("Saknar rasterio – kor: pip install rasterio")


def _hotspot_class_path() -> Path:
    p = RASTERS_DIR / f"{AOI_NAME}_hotspot_class.tif"
    if p.exists():
        return p
    return PROC_DIR / f"{AOI_NAME}_hotspot_class.tif"


def load(suffix: str) -> np.ndarray:
    p = RASTERS_DIR / f"{AOI_NAME}_{suffix}.tif"
    if not p.exists():
        p = PROC_DIR / f"{AOI_NAME}_{suffix}.tif"
    with rasterio.open(p) as src:
        arr = src.read(1).astype(float)
        nd  = src.nodata
    if nd is not None:
        arr[arr == nd] = np.nan
    return arr


_WGS84_TO_WEBM = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)


def _add_overview_inset(ax_parent, tif_path: Path) -> None:
    """AOI-outline på ljus webbkarta (CartoDB Positron, OSM-data). Kräver nätverk."""
    if not tif_path.exists():
        return
    try:
        import contextily as ctx
    except ImportError:
        print("  [info] Installera contextily för översiktskarta: pip install contextily")
        return

    with rasterio.open(tif_path) as src:
        b = src.bounds
        crs = src.crs

    # AOI i Web Mercator (röd ram)
    left, bottom, right, top = transform_bounds(
        crs, "EPSG:3857", b.left, b.bottom, b.right, b.top
    )
    w, h = right - left, top - bottom

    # Expanderat utsnitt i WGS84 så Uppsala–Enköping–Sala m.m. ryms (tydligare läge)
    lon_w, lat_s, lon_e, lat_n = transform_bounds(
        crs, "EPSG:4326", b.left, b.bottom, b.right, b.top
    )
    clon = (lon_w + lon_e) / 2.0
    clat = (lat_s + lat_n) / 2.0
    half_lon_aoi = (lon_e - lon_w) / 2.0
    half_lat_aoi = (lat_n - lat_s) / 2.0
    half_lon = max(half_lon_aoi * 7.0, 0.62)
    half_lat = max(half_lat_aoi * 7.0, 0.48)
    exp_w = clon - half_lon
    exp_e = clon + half_lon
    exp_s = clat - half_lat
    exp_n = clat + half_lat

    vx0, vy0 = _WGS84_TO_WEBM.transform(exp_w, exp_s)
    vx1, vy1 = _WGS84_TO_WEBM.transform(exp_e, exp_n)
    xlim = (min(vx0, vx1), max(vx0, vx1))
    ylim = (min(vy0, vy1), max(vy0, vy1))

    ax_in = ax_parent.inset_axes([0.02, 0.56, 0.34, 0.40])
    ax_in.set_xlim(xlim[0], xlim[1])
    ax_in.set_ylim(ylim[0], ylim[1])
    ax_in.set_aspect("equal", adjustable="box")

    try:
        ctx.add_basemap(
            ax_in,
            crs="EPSG:3857",
            source=ctx.providers.CartoDB.Positron,
            zoom="auto",
        )
    except Exception as exc:
        print(f"  [varning] Översiktskarta utelämnad ({exc})")
        ax_in.remove()
        return

    ax_in.add_patch(
        mpatches.Rectangle(
            (left, bottom),
            w,
            h,
            fill=False,
            edgecolor="#c1121f",
            linewidth=2.4,
            zorder=15,
        )
    )

    # Orter i Uppland / Mälardalen (WGS84) – ungefärliga centrum
    places = [
        ("Uppsala", 17.6389, 59.8586),
        ("Enköping", 17.0778, 59.6353),
        ("Sala", 16.6066, 59.9201),
        ("Örsundsbro", 17.2990, 59.7300),
        ("Knutby", 18.1680, 59.9180),
        ("Morgongåva", 17.1390, 59.8720),
    ]
    xl0, xl1 = ax_in.get_xlim()
    yl0, yl1 = ax_in.get_ylim()
    xspan = xl1 - xl0
    yspan = yl1 - yl0
    for name, plon, plat in places:
        px, py = _WGS84_TO_WEBM.transform(plon, plat)
        if not (xl0 - 0.02 * xspan <= px <= xl1 + 0.02 * xspan and
                yl0 - 0.02 * yspan <= py <= yl1 + 0.02 * yspan):
            continue
        txt = ax_in.text(
            px,
            py,
            name,
            fontsize=7.2,
            fontweight="bold",
            color="#1b4332",
            ha="center",
            va="center",
            zorder=12,
            clip_on=True,
        )
        txt.set_path_effects(
            [pe.withStroke(linewidth=2.5, foreground="white", alpha=0.95)]
        )

    ax_in.set_xticks([])
    ax_in.set_yticks([])
    for s in ax_in.spines.values():
        s.set_edgecolor("#1b4332")
        s.set_linewidth(1.0)
    ax_in.text(
        0.5,
        -0.12,
        "Läge: Fiby urskog (AOI) · Uppland",
        transform=ax_in.transAxes,
        ha="center",
        fontsize=8,
        color="#1b4332",
        fontweight="bold",
    )


def make_hotspot_figure() -> None:
    """Stor showcase-figur: hotspot-karta + tre delindex + översiktskarta."""

    cls       = np.nan_to_num(load("hotspot_class"))
    score     = np.nan_to_num(load("nvi_score"))
    structure = np.nan_to_num(load("structure_index"))
    cont      = np.nan_to_num(load("continuity_index"))
    moisture  = np.nan_to_num(load("moisture_index"))

    # Rubrik ovanför rutnätet; datakällor längst ned (undviker överlapp med karttitlar)
    fig = plt.figure(figsize=(18, 11.5), facecolor="#f5f7f5")
    fig.suptitle(
        "Geodatadriven naturvärdesinventering  |  Testområde: Fiby urskog, Uppland",
        fontsize=14,
        fontweight="bold",
        color="#1b4332",
        y=0.97,
    )

    gs = gridspec.GridSpec(
        2,
        4,
        figure=fig,
        left=0.02,
        right=0.98,
        top=0.89,
        bottom=0.11,
        wspace=0.04,
        hspace=0.20,
    )

    # ── Hotspot-karta (stor, vänster) ─────────────────────────────────────────
    ax_main = fig.add_subplot(gs[:, :2])

    cmap_cls = mcolors.ListedColormap(["#4575b4", "#fee090", "#d73027"])
    norm_cls = mcolors.BoundaryNorm([0.5, 1.5, 2.5, 3.5], 3)

    ax_main.imshow(
        np.where(cls > 0, cls, np.nan),
        cmap=cmap_cls,
        norm=norm_cls,
        interpolation="nearest",
        aspect="equal",
    )
    ax_main.set_title(
        "Hotspot-klassifikation\n(nationell geodata)",
        fontsize=12,
        fontweight="bold",
        pad=8,
        color="#1b4332",
    )
    ax_main.axis("off")

    _add_overview_inset(ax_main, _hotspot_class_path())

    legend_patches = [
        mpatches.Patch(color="#d73027", label="Klass 3 – Hotspot (intensiv inventering)"),
        mpatches.Patch(color="#fee090", label="Klass 2 – Mellanklass (stickprov)"),
        mpatches.Patch(color="#4575b4", label="Klass 1 – Låg prioritet (snabb verif.)"),
    ]
    ax_main.legend(
        handles=legend_patches,
        loc="lower left",
        fontsize=10,
        framealpha=0.92,
        edgecolor="#ccc",
        title="NVI-prioritet",
        title_fontsize=10,
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
        im = ax.imshow(
            arr, cmap=cmap, vmin=vmin, vmax=vmax,
            interpolation="bilinear", aspect="equal",
        )
        ax.set_title(title, fontsize=10, fontweight="bold", pad=5, color="#1b4332")
        ax.axis("off")
        cb = plt.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
        cb.ax.tick_params(labelsize=8)

    fig.text(
        0.5,
        0.028,
        "Datakällor: NMD 2023 (Naturvårdsverket)  ·  GSD-Höjddata 1 m (Lantmäteriet)  ·  "
        "Avverkningsanmälningar (Skogsstyrelsen)",
        ha="center",
        fontsize=9,
        color="#555",
        style="italic",
    )

    out = DOCS_ASSETS / "hotspot_showcase.png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"[ok] {out}")
    plt.close()


def _relative_luminance_srgb(hex_color: str) -> float:
    """WCAG relative luminance 0–1; högre värde = ljusare bakgrund."""
    h = hex_color.strip().lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))

    def linearize(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    R, G, B = linearize(r), linearize(g), linearize(b)
    return 0.2126 * R + 0.7152 * G + 0.0722 * B


def make_method_diagram() -> None:
    """Enkel processkarta – 6 steg som pil-diagram."""

    steps = [
        ("1", "Geodata-screening", "NMD 2023 · Lidar DTM\nSkogsstyrelsen",  "#2d6a4f"),
        ("2", "Hotspot-modell",    "Struktur + Kontinuitet\n+ Fukt (viktat)", "#40916c"),
        ("3", "Sampling-design",   "Stratifiering\nHotspot / Mellan / Låg",  "#52b788"),
        ("4", "Fältinventering",   "Riktad NVI\nSignalarter · Substrat",     "#74c69d"),
        ("5", "Kvantifiering",     "Naturvärdesindex\nf(struktur+kont.+art)", "#95d5b2"),
        ("6", "Reproducerbarhet",  "Python · QGIS\nNytt område = ny kör", "#b7e4c7"),
    ]

    # Ljus bakgrund → mörk text (vit text på de ljusaste gröna var oläsbar).
    _lum_thresh = 0.43

    fig, ax = plt.subplots(figsize=(14, 3.2), facecolor="#f5f7f5")
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 1)
    ax.axis("off")

    step_w = 14 / len(steps)
    for i, (num, title, desc, color) in enumerate(steps):
        x = i * step_w + step_w * 0.1
        w = step_w * 0.75

        lum = _relative_luminance_srgb(color)
        dark_text = lum > _lum_thresh
        fg = "#0d2818" if dark_text else "#ffffff"
        fg_muted = "#1b4332" if dark_text else "#ffffff"
        desc_alpha = 0.92 if dark_text else 0.9
        edge = "#1b4332" if dark_text else "#ffffff"

        # Box
        rect = mpatches.FancyBboxPatch(
            (x, 0.12), w, 0.76,
            boxstyle="round,pad=0.02",
            facecolor=color, edgecolor=edge, linewidth=2,
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
                fontsize=8, color=fg, fontweight="bold", zorder=3)
        ax.text(x + w/2, 0.52, title, ha="center", va="center",
                fontsize=9, color=fg, fontweight="bold", zorder=3)
        ax.text(x + w/2, 0.28, desc, ha="center", va="center",
                fontsize=7, color=fg_muted, alpha=desc_alpha, zorder=3,
                linespacing=1.4)

    fig.text(
        0.5, 0.97,
        "Metodöversikt: nationell geodata → riktad NVI",
        ha="center", fontsize=11, fontweight="bold", color="#1b4332"
    )

    out = DOCS_ASSETS / "method_diagram.png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"[ok] {out}")
    plt.close()


def make_hotspot_protected_context_figure() -> None:
    """
    Hotspot-raster med overlay av skyddade områden (informationslager — ingår ej i NVI-viktning).
    """
    if gpd is None:
        print("[skip] hotspot_protected_context.png — saknar geopandas")
        return

    gpkg = PROTECTED_SITES_DIR / f"protected_sites_{AOI_NAME}.gpkg"
    tif_path = _hotspot_class_path()
    if not tif_path.exists():
        print("[skip] hotspot_protected_context.png — saknar hotspot_class.tif")
        return
    if not gpkg.exists():
        print(
            f"[skip] hotspot_protected_context.png — saknar {gpkg.name}\n"
            "       Kor: python scripts/python/download_data.py --protected-sites-only"
        )
        return

    with rasterio.open(tif_path) as src:
        cls = src.read(1).astype(float)
        nd = src.nodata
        if nd is not None:
            cls[cls == nd] = np.nan
        h, w = cls.shape
        transform = src.transform
        crs = src.crs
        from rasterio.transform import array_bounds

        left, bottom, right, top = array_bounds(h, w, transform)

    gdf = gpd.read_file(gpkg)
    if gdf.crs is None:
        print("[skip] hotspot_protected_context.png — GPKG saknar CRS")
        return
    gdf = gdf.to_crs(crs)

    fig, ax = plt.subplots(figsize=(12, 10), facecolor="#f5f7f5")
    cmap_cls = mcolors.ListedColormap(["#4575b4", "#fee090", "#d73027"])
    norm_cls = mcolors.BoundaryNorm([0.5, 1.5, 2.5, 3.5], 3)
    ax.imshow(
        np.where(cls > 0, cls, np.nan),
        cmap=cmap_cls,
        norm=norm_cls,
        interpolation="nearest",
        aspect="equal",
        extent=(left, right, bottom, top),
        origin="upper",
    )
    gdf.plot(
        ax=ax,
        facecolor=(0, 0, 0, 0),
        edgecolor="#ffffff",
        linewidth=1.8,
        zorder=5,
    )
    ax.set_xlim(left, right)
    ax.set_ylim(bottom, top)
    ax.set_aspect("equal", adjustable="box")
    ax.set_title(
        "NVI-prioritet och formellt skyddad natur (kontext)\n"
        f"{AOI_NAME.replace('_', ' ').title()} — skyddade ytor enligt Naturvårdsverket (INSPIRE)",
        fontsize=13,
        fontweight="bold",
        color="#1b4332",
        pad=12,
    )
    ax.set_xlabel(f"Östning (m) · {crs}", fontsize=9, color="#555")
    ax.set_ylabel("Northing (m)", fontsize=9, color="#555")

    leg = [
        mpatches.Patch(color="#d73027", label="Klass 3 – Hotspot"),
        mpatches.Patch(color="#fee090", label="Klass 2 – Mellan"),
        mpatches.Patch(color="#4575b4", label="Klass 1 – Låg"),
        mpatches.Patch(
            facecolor="none",
            edgecolor="#ffffff",
            linewidth=2,
            label="Formellt skydd (WFS ps:ProtectedSite)",
        ),
    ]
    ax.legend(handles=leg, loc="lower left", fontsize=9, framealpha=0.95, edgecolor="#ccc")

    fig.text(
        0.5,
        0.02,
        "Skyddade polygoner ingår inte i NVI-modellens viktning — de visas för tolkning "
        "(t.ex. var hög prioritet möter eller ligger utanför reservat). "
        "Källa skydd: Naturvårdsverket INSPIRE Protected Sites (WFS).",
        ha="center",
        fontsize=9,
        color="#555",
        style="italic",
    )

    out = DOCS_ASSETS / "hotspot_protected_context.png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"[ok] {out}")
    plt.close()


if __name__ == "__main__":
    print("Genererar showcase-figurer ...")
    make_hotspot_figure()
    make_method_diagram()
    make_hotspot_protected_context_figure()
    print("Klart – se docs/assets/")
