"""
validate_against_report.py
Jämför pipeline-klassning mot Länsstyrelsens NVI-rapport (2022:42, Djupedal).

Rapport-NVI och pipeline använder nu samma numrering (SS 199000:2023):
  Klass 1 = Mycket högt naturvärde
  Klass 2 = Högt naturvärde
  Klass 3 = Påtagligt naturvärde
  Klass 4 = Visst naturvärde

Jämförelsen är direkt – ingen omvändning av klassnummer behövs.
Tolerans ±1 klass räknas som "nära träff".

Kör:
  python scripts/python/validate_against_report.py
"""

import sys
import argparse
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import AOI_NAME, RASTERS_DIR, FIGURES_DIR, EPSG_SWEREF

try:
    import rasterio
    from rasterio.transform import rowcol
    import geopandas as gpd
    from shapely.geometry import Point
except ImportError as e:
    sys.exit(f"[FEL] Saknar paket: {e} – kör: pip install -r requirements.txt")


# ── Rapportdata (extraherat från bilaga 2, SWEREF99TM EPSG:3006) ──────────────
# Koordinaterna i rapporten (X/Y) är centroider för varje objekt.
# Rapporten anger SWEREF99_12_00 men värdena (X~318-319k, Y~6411-6413k)
# matchar SWEREF99TM (EPSG:3006) för Göteborg-området.

REPORT_OBJECTS = [
    # (obj_nr, nvi_klass, area_m2, easting, northing, biotop)
    (1,  4,   2995, 318415, 6411773, "Granskog"),
    (2,  2, 183557, 318616, 6412681, "Näringsfattig ekskog"),
    (3,  3,  18955, 318394, 6411991, "Västlig taiga"),
    (4,  4,  12930, 318518, 6411918, "Granskog"),
    (5,  3,  27731, 318451, 6412174, "Ekskog/Triviallövskog"),
    (6,  4,   5005, 318524, 6412063, "Granskog"),
    (7,  2,  14351, 318575, 6412182, "Näringsrik ekskog"),
    (8,  2,   8186, 318692, 6411896, "Trädklädd betesmark"),
    (9,  3,    290, 318685, 6412089, "Hässle"),
    (10, 4,   2840, 318702, 6412110, "Björksly"),
    (11, 3,    990, 318672, 6412140, "Mindre vattendrag"),
    (12, 3,    603, 318656, 6412152, "Grävd damm"),
    (13, 2,  11165, 318662, 6412285, "Trädklädd betesmark"),
    (14, 2,   5541, 318625, 6412433, "Trädklädd betesmark"),
    (15, 3,   1557, 318666, 6412506, "Trädklädd betesmark"),
    (16, 3,  56385, 318501, 6412567, "Blandskog"),
    (17, 4,   3567, 318470, 6412648, "Granskog"),
    (18, 2,  19521, 318667, 6412762, "Näringsrik ekskog"),
    (19, 3,   5448, 318653, 6413068, "Västlig taiga"),
    (20, 4,   7291, 318586, 6413065, "Björktallskog"),
    (21, 2,  85388, 319050, 6412167, "Näringsfattig ekskog"),
    (22, 3,   7544, 318996, 6412031, "Sekundär lövskog"),
    (23, 3,  13062, 319045, 6412255, "Sekundär lövskog"),
    (24, 3,     57, 318961, 6412283, "Grävd damm"),
    (25, 3,  72581, 319185, 6412709, "Sekundär lövskog"),
    (26, 3,    152, 319216, 6412705, "Grävd damm"),
    (27, 3,   1311, 319080, 6412908, "Öppen kultiverad betesmark"),
    (28, 2,  24399, 319235, 6412931, "Västlig taiga"),
    (29, 4,   6587, 319232, 6412822, "Granskog"),
    (30, 3,  81802, 319631, 6412709, "Blandskog"),
    (31, 2,  12785, 319539, 6412777, "Tallskog/Lövskogsrest"),
    (32, 4,   6271, 319584, 6412725, "Granskog"),
    (33, 4,   4583, 319382, 6412622, "Granskog"),
    (34, 4,   9543, 319328, 6412671, "Triviallövskog"),
    (35, 4,   6978, 319264, 6412655, "Tallskog"),
    (36, 2,  22281, 319770, 6412568, "Tallsumpskog"),
    (37, 2,  44405, 319650, 6412408, "Tallskog"),
    (38, 4,  12294, 319598, 6412223, "Granskog"),
    (39, 3,  23659, 319556, 6412350, "Tallsumpskog"),
    (40, 3,    126, 319501, 6412440, "Fältspatbrott"),
    (41, 4,    564, 319422, 6412394, "Granskog"),
    (42, 4,  14346, 319251, 6412398, "Sekundär lövskog"),
    (43, 4,   1293, 319310, 6412162, "Tallskog"),
    (44, 3, 248557, 319332, 6412229, "Tallskog/Sekundärlövskog"),
    (45, 2,   5123, 319391, 6411978, "Anlagt småvatten"),
]


def report_to_pipeline_class(nvi_klass: int) -> int:
    """Direkt mappning – rapport och pipeline använder nu samma numrering (SS 199000:2023).

      Rapport klass 1 (Mycket högt) → pipeline klass 1
      Rapport klass 2 (Högt)        → pipeline klass 2
      Rapport klass 3 (Påtagligt)   → pipeline klass 3
      Rapport klass 4 (Visst)       → pipeline klass 4
    """
    return nvi_klass   # direkt, ingen omvändning


def sample_raster_at_point(src, easting: float, northing: float, window: int = 3):
    """Samplar rastervärde i ett fönster runt centroiden, returnerar max icke-noll värde.
    Centroiden kan hamna på en stig/gata-pixel – fönstret fångar omkringliggande skog."""
    try:
        row, col = rowcol(src.transform, easting, northing)
        half = window // 2
        data = src.read(1)
        r0, r1 = max(0, row - half), min(src.height, row + half + 1)
        c0, c1 = max(0, col - half), min(src.width,  col + half + 1)
        patch = data[r0:r1, c0:c1]
        nonzero = patch[(patch != src.nodata) & (patch > 0)]
        if nonzero.size > 0:
            return int(np.max(nonzero))
    except Exception:
        pass
    return None


def run(hotspot_path: Path | None = None):
    if hotspot_path is None:
        hotspot_path = RASTERS_DIR / f"{AOI_NAME}_hotspot_class.tif"
    if not hotspot_path.exists():
        sys.exit(f"[FEL] Saknar {hotspot_path.name} – kör hotspot_model.py först.")

    print("\n" + "=" * 60)
    print("Validering mot Länsstyrelsens NVI-rapport 2022:42")
    print("=" * 60)

    results = []
    outside = []

    run_label = hotspot_path.stem.replace("_hotspot_class", "")

    with rasterio.open(hotspot_path) as src:
        for obj_nr, nvi_klass, area_m2, east, north, biotop in REPORT_OBJECTS:
            pipe_cls = sample_raster_at_point(src, east, north)
            exp_cls  = report_to_pipeline_class(nvi_klass)
            if pipe_cls is None or pipe_cls == 0:
                outside.append(obj_nr)
                continue
            results.append({
                "obj_nr":    obj_nr,
                "nvi_klass": nvi_klass,
                "exp_cls":   exp_cls,
                "pipe_cls":  pipe_cls,
                "area_m2":   area_m2,
                "biotop":    biotop,
                "match":     pipe_cls == exp_cls,
                "diff":      pipe_cls - exp_cls,
            })

    print(f"\n  Samplade: {len(results)} objekt  |  Utanför/maskerade: {len(outside)}")
    if outside:
        print(f"  Objekt utanför AOI eller maskerade: {outside}")

    if not results:
        print("\n  [info] Inga rapportobjekt överlappar aktuell hotspot-raster/AOI.")
        print("  [tips] Kontrollera AOI_NAME, rasterfil och att koordinatsystemet är SWEREF99 TM (EPSG:3006).")
        return

    # ── Sammanfattning ──
    n_match = sum(r["match"] for r in results)
    n_total = len(results)
    print(f"\n  Träffandel (exakt klass): {n_match}/{n_total} = {100*n_match/n_total:.0f}%")

    # Areal-viktad träff
    total_area = sum(r["area_m2"] for r in results)
    match_area = sum(r["area_m2"] for r in results if r["match"])
    print(f"  Areal-viktad träff:       {match_area/total_area*100:.0f}%"
          f"  ({match_area/10000:.1f} ha av {total_area/10000:.1f} ha)")

    # Tolerans ±1
    near  = [r for r in results if abs(r["diff"]) <= 1]
    print(f"  Nära träff (±1 klass):    {len(near)}/{n_total} = {100*len(near)/n_total:.0f}%")

    # Avvikelser
    over  = [r for r in results if r["diff"] > 0]
    under = [r for r in results if r["diff"] < 0]
    print(f"\n  Överskattade (pipeline > rapport): {len(over)} objekt")
    print(f"  Underskattade (pipeline < rapport): {len(under)} objekt")

    # Per rapport-klass
    print("\n  Träff per rapport-klass:")
    for k, label in [
        (1, "Klass 1 – Mycket högt"),
        (2, "Klass 2 – Högt"),
        (3, "Klass 3 – Påtagligt"),
        (4, "Klass 4 – Visst"),
    ]:
        sub = [r for r in results if r["nvi_klass"] == k]
        if not sub:
            continue
        hits = sum(r["match"] for r in sub)
        a_tot = sum(r["area_m2"] for r in sub)
        a_hit = sum(r["area_m2"] for r in sub if r["match"])
        print(f"    {label:<30} {hits}/{len(sub)} objekt  "
              f"({100*a_hit/a_tot:.0f}% areal)")

    # ── Detaljlista ──
    print("\n  Detaljresultat:")
    print(f"  {chr(39)}Obj{chr(39):>3}  {chr(39)}Rapp{chr(39):>4}  {chr(39)}Exp{chr(39):>3}  {chr(39)}Pipe{chr(39):>4}  Av  Areal(ha)  Biotop")
    print("  " + "-" * 65)
    for r in sorted(results, key=lambda x: x["obj_nr"]):
        flag = "OK" if r["match"] else ("+" if r["diff"] > 0 else "-")
        print(f"  {r['obj_nr']:>3}  {r['nvi_klass']:>4}  {r['exp_cls']:>3}  "
              f"{r['pipe_cls']:>4}  {flag:>2}  "
              f"{r['area_m2']/10000:>8.2f}  {r['biotop'][:30]}")

    # ── Figur ──
    _plot_validation(results, outside, run_label=run_label)
    print("\n[klar]")


def _plot_validation(results, outside, run_label: str):
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle(
        f"Validering mot NVI-rapport 2022:42 – Djupedal\n"
        f"Träffandel: {sum(r['match'] for r in results)}/{len(results)} objekt",
        fontsize=12, fontweight="bold"
    )

    # ── Confusion matrix (objekt-antal) ──
    ax = axes[0]
    labels = ["Klass 1\n(Mycket högt)", "Klass 2\n(Högt)", "Klass 3\n(Påtagligt)", "Klass 4\n(Visst)"]
    cm = np.zeros((4, 4), dtype=int)
    for r in results:
        ei = min(max(r["exp_cls"] - 1, 0), 3)
        pi = min(max(r["pipe_cls"] - 1, 0), 3)
        cm[ei, pi] += 1
    im = ax.imshow(cm, cmap="Blues", vmin=0)
    ax.set_xticks(range(4)); ax.set_xticklabels(labels, fontsize=8)
    ax.set_yticks(range(4)); ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel("Pipeline-klass", fontsize=10)
    ax.set_ylabel("Förväntad klass (rapport)", fontsize=10)
    ax.set_title("Confusion matrix (antal objekt)\nSS 199000:2023", fontsize=10)
    for i in range(4):
        for j in range(4):
            ax.text(j, i, cm[i, j], ha="center", va="center",
                    fontsize=12, fontweight="bold",
                    color="white" if cm[i, j] > cm.max() * 0.6 else "black")

    # ── Scatter: förväntad vs faktisk klass, storlekskodad ──
    ax2 = axes[1]
    colors = {True: "#2ca02c", False: "#d62728"}
    for r in results:
        ax2.scatter(r["exp_cls"], r["pipe_cls"],
                    s=max(20, r["area_m2"] / 500),
                    alpha=0.6,
                    color=colors[r["match"]],
                    edgecolors="grey", linewidths=0.4)
    ax2.set_xticks([1, 2, 3, 4])
    ax2.set_yticks([1, 2, 3, 4])
    ax2.set_xticklabels(["1 (Mycket högt)", "2 (Högt)", "3 (Påtagligt)", "4 (Visst)"], fontsize=8)
    ax2.set_yticklabels(["1 (Mycket högt)", "2 (Högt)", "3 (Påtagligt)", "4 (Visst)"], fontsize=8)
    ax2.set_xlabel("Förväntad klass (rapport → SS 199000:2023)", fontsize=10)
    ax2.set_ylabel("Pipeline-klass", fontsize=10)
    ax2.set_title("Klassöverensstämmelse per objekt\n(cirkelstorlek ≈ areal)", fontsize=10)
    ax2.plot([0.5, 4.5], [0.5, 4.5], "k--", lw=1, alpha=0.4, label="Perfekt träff")
    ax2.legend(handles=[
        mpatches.Patch(color="#2ca02c", label="Korrekt klass"),
        mpatches.Patch(color="#d62728", label="Felklassad"),
    ], fontsize=9, loc="upper left")
    ax2.set_xlim(0.5, 4.5); ax2.set_ylim(0.5, 4.5)

    plt.tight_layout()
    out = FIGURES_DIR / f"{run_label}_validation_report.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"\n  Figur sparad: {out.name}")
    plt.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validera hotspot-raster mot Djupedal-rapport")
    parser.add_argument(
        "--hotspot",
        type=Path,
        default=None,
        help="Valfri sökväg till hotspot_class.tif (annars outputs/rasters/<AOI>_hotspot_class.tif)",
    )
    args = parser.parse_args()
    run(hotspot_path=args.hotspot)
