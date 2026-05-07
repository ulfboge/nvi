"""
run_lste_midrange_tuning.py
Mellanspann mellan baseline och stress for LstE.
Korer scenarioer, validerar mot GPKG och jamfor klassdiff mot baseline.

Exempel (smal körning, mindre AOI):
  python scripts/python/run_lste_midrange_tuning.py --preset kungsbacka --only baseline,mid_2
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import rasterio

from config import OUTPUTS_DIR, REPO_DIR
from sweep_test_presets import PRESET_CHOICES, resolve_validation_paths, subprocess_env_from_preset


def _run(cmd: list[str], env: dict[str, str]) -> str:
    p = subprocess.run(
        cmd,
        cwd=REPO_DIR,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if p.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{p.stdout}\n{p.stderr}")
    return p.stdout + "\n" + p.stderr


def _extract_metrics(text: str) -> tuple[float | None, float | None, float | None]:
    eq_vals = re.findall(r"=\s*([0-9]+(?:\.[0-9]+)?)%", text)
    exact = float(eq_vals[0]) if len(eq_vals) >= 1 else None
    near = float(eq_vals[1]) if len(eq_vals) >= 2 else None
    m_area = re.search(r"Areal[^\n:]*:\s*([0-9]+(?:\.[0-9]+)?)%", text, flags=re.IGNORECASE)
    area = float(m_area.group(1)) if m_area else None
    return exact, near, area


def _read_class(path: Path) -> np.ndarray:
    with rasterio.open(path) as src:
        arr = src.read(1).astype(np.int16)
    return arr


def _class_diff_stats(base: np.ndarray, cur: np.ndarray) -> tuple[float, float]:
    valid = (base > 0) & (cur > 0)
    if not np.any(valid):
        return float("nan"), float("nan")
    step = np.abs(base[valid] - cur[valid])
    return float(np.mean(step > 0) * 100.0), float(np.mean(step))


def main() -> None:
    parser = argparse.ArgumentParser(description="Mellanspann mellan baseline och tyngre naturbonuser.")
    parser.add_argument(
        "--preset",
        choices=PRESET_CHOICES,
        default="lste",
        help="AOI-preset: lste eller kungsbacka (mindre).",
    )
    parser.add_argument(
        "--gpkg",
        type=Path,
        default=None,
        help="Valfri referens-GPKG (annars preset-standard).",
    )
    parser.add_argument(
        "--only",
        default=None,
        help='Kommaseparerade scenario-namn, t.ex. "baseline,mid_2"',
    )
    args = parser.parse_args()

    hotspot_path, gpkg_path, class_field = resolve_validation_paths(args.preset, args.gpkg)

    scenarios = [
        (
            "baseline",
            {
                "CONTINUITY_AGE_BLEND": "0.18",
                "NATURE_LAYER_BONUSES": "0",
            },
        ),
        (
            "mid_1",
            {
                "CONTINUITY_AGE_BLEND": "0.24",
                "NATURE_LAYER_BONUSES": "1",
                "NATURE_W_STRUCTURE_NYCKELBIOTOPER": "0.08",
                "NATURE_W_STRUCTURE_NATURKULTUR": "0.05",
                "NATURE_W_STRUCTURE_SUMPSKOG": "0.04",
                "NATURE_W_CONT_NATURVARDSAVTAL": "0.08",
                "NATURE_W_MOISTURE_SUMPSKOG": "0.05",
            },
        ),
        (
            "mid_2",
            {
                "CONTINUITY_AGE_BLEND": "0.30",
                "NATURE_LAYER_BONUSES": "1",
                "NATURE_W_STRUCTURE_NYCKELBIOTOPER": "0.10",
                "NATURE_W_STRUCTURE_NATURKULTUR": "0.07",
                "NATURE_W_STRUCTURE_SUMPSKOG": "0.06",
                "NATURE_W_CONT_NATURVARDSAVTAL": "0.10",
                "NATURE_W_MOISTURE_SUMPSKOG": "0.08",
            },
        ),
        (
            "mid_3",
            {
                "CONTINUITY_AGE_BLEND": "0.35",
                "NATURE_LAYER_BONUSES": "1",
                "NATURE_W_STRUCTURE_NYCKELBIOTOPER": "0.12",
                "NATURE_W_STRUCTURE_NATURKULTUR": "0.09",
                "NATURE_W_STRUCTURE_SUMPSKOG": "0.08",
                "NATURE_W_CONT_NATURVARDSAVTAL": "0.12",
                "NATURE_W_MOISTURE_SUMPSKOG": "0.10",
            },
        ),
    ]

    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        scenarios = [(n, o) for n, o in scenarios if n in wanted]
        missing = wanted - {n for n, _ in scenarios}
        if missing:
            sys.exit(f"[FEL] Okända scenario: {sorted(missing)}. Tillåtna: baseline, mid_1, mid_2, mid_3")
        if not scenarios:
            sys.exit("[FEL] Inga scenario matchade --only.")

    py = str(REPO_DIR / ".venv" / "Scripts" / "python.exe")
    if not Path(py).exists():
        py = sys.executable

    out_dir = OUTPUTS_DIR / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d")
    rows: list[dict[str, str]] = []
    baseline_cls: np.ndarray | None = None

    for name, overrides in scenarios:
        env = subprocess_env_from_preset(args.preset, dict(overrides))
        print(f"\n=== Scenario: {name} ===", flush=True)
        _run([py, "scripts/python/compute_indices.py"], env)
        _run([py, "scripts/python/hotspot_model.py"], env)
        out = _run(
            [
                py,
                "scripts/python/validate_against_gpkg.py",
                "--hotspot",
                str(hotspot_path),
                "--gpkg",
                str(gpkg_path),
                "--class-field",
                class_field,
                "--no-figure",
            ],
            env,
        )
        exact, near, area = _extract_metrics(out)
        cur_cls = _read_class(hotspot_path)
        if baseline_cls is None:
            baseline_cls = cur_cls.copy()
        changed_pct, mean_step = _class_diff_stats(baseline_cls, cur_cls)
        rows.append(
            {
                "scenario": name,
                "exact_pct": "" if exact is None else f"{exact:.1f}",
                "near_pct": "" if near is None else f"{near:.1f}",
                "area_weighted_pct": "" if area is None else f"{area:.1f}",
                "class_changed_pct_vs_baseline": f"{changed_pct:.2f}",
                "class_mean_step_vs_baseline": f"{mean_step:.4f}",
            }
        )
        print(
            "  "
            + f"exact={rows[-1]['exact_pct']} near={rows[-1]['near_pct']} area={rows[-1]['area_weighted_pct']} "
            + f"class_changed={rows[-1]['class_changed_pct_vs_baseline']}%",
            flush=True,
        )

    out_csv = out_dir / f"{args.preset}_midrange_tuning_{stamp}.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "scenario",
                "exact_pct",
                "near_pct",
                "area_weighted_pct",
                "class_changed_pct_vs_baseline",
                "class_mean_step_vs_baseline",
            ],
        )
        w.writeheader()
        w.writerows(rows)
    print(f"\nSaved: {out_csv}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[FEL] {e}")
        sys.exit(1)
