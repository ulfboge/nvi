"""
diagnose_lste_sensitivity.py
Baseline vs stress: jämför NVI-score och hotspot-klasser.

--preset kungsbacka = snabbare iteration på mindre AOI (config.py).
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import rasterio

from config import OUTPUTS_DIR, REPO_DIR
from sweep_test_presets import (
    PRESET_CHOICES,
    get_preset,
    hotspot_tif_for_preset,
    nvi_score_tif_for_preset,
    subprocess_env_from_preset,
)


def _run(cmd: list[str], env: dict[str, str]) -> None:
    proc = subprocess.run(
        cmd,
        cwd=REPO_DIR,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{proc.stdout}\n{proc.stderr}")


def _resolve_python(py_override: str | None) -> str:
    if py_override:
        return py_override
    venv_py = REPO_DIR / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable


def _read_raster(path: Path) -> np.ndarray:
    with rasterio.open(path) as src:
        arr = src.read(1).astype(float)
        nodata = src.nodata
    if nodata is not None:
        arr[arr == nodata] = np.nan
    return arr


def _summarize_score_diff(a: np.ndarray, b: np.ndarray) -> dict[str, float]:
    valid = np.isfinite(a) & np.isfinite(b)
    if not np.any(valid):
        return {k: float("nan") for k in ["mae", "max_abs", "changed_pct", "p95_abs"]}
    diff = np.abs(a[valid] - b[valid])
    return {
        "mae": float(np.mean(diff)),
        "max_abs": float(np.max(diff)),
        "p95_abs": float(np.percentile(diff, 95)),
        "changed_pct": float(np.mean(diff > 1e-6) * 100.0),
    }


def _summarize_class_diff(a: np.ndarray, b: np.ndarray) -> dict[str, float]:
    va = np.isfinite(a) & (a > 0)
    vb = np.isfinite(b) & (b > 0)
    valid = va & vb
    if not np.any(valid):
        return {k: float("nan") for k in ["changed_pct", "mean_abs_step", "max_abs_step"]}
    da = a[valid].astype(int)
    db = b[valid].astype(int)
    step = np.abs(da - db)
    return {
        "changed_pct": float(np.mean(step > 0) * 100.0),
        "mean_abs_step": float(np.mean(step)),
        "max_abs_step": float(np.max(step)),
    }


def _class_hist(arr: np.ndarray) -> dict[str, int]:
    out: dict[str, int] = {}
    for k in [1, 2, 3, 4]:
        out[f"class_{k}"] = int(np.sum(arr == k))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnos för modellkänslighet (baseline vs stress).")
    parser.add_argument(
        "--preset",
        choices=PRESET_CHOICES,
        default="lste",
        help="AOI-preset: lste eller kungsbacka (mindre).",
    )
    parser.add_argument("--python", default=None, help="Valfri python-tolk.")
    parser.add_argument(
        "--out-prefix",
        default=None,
        help="Prefix för CSV i outputs/validation/ (standard: <preset>_sensitivity_diagnosis).",
    )
    args = parser.parse_args()
    out_prefix = args.out_prefix or f"{args.preset}_sensitivity_diagnosis"
    aoi_stem = str(get_preset(args.preset)["hotspot_aoi_name"])

    py = _resolve_python(args.python)
    stamp = dt.datetime.now().strftime("%Y%m%d")
    out_dir = OUTPUTS_DIR / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)

    scenarios = [
        (
            "baseline",
            {
                "CONTINUITY_AGE_BLEND": "0.00",
                "NATURE_LAYER_BONUSES": "0",
            },
        ),
        (
            "stress",
            {
                "CONTINUITY_AGE_BLEND": "0.50",
                "NATURE_LAYER_BONUSES": "1",
                "NATURE_W_STRUCTURE_NYCKELBIOTOPER": "0.20",
                "NATURE_W_STRUCTURE_NATURKULTUR": "0.15",
                "NATURE_W_STRUCTURE_SUMPSKOG": "0.15",
                "NATURE_W_CONT_NATURVARDSAVTAL": "0.20",
                "NATURE_W_MOISTURE_SUMPSKOG": "0.20",
            },
        ),
    ]

    scenario_outputs: dict[str, dict[str, Path]] = {}

    for name, overrides in scenarios:
        env = subprocess_env_from_preset(args.preset, dict(overrides))
        print(f"\n=== Scenario: {name} ===")
        _run([py, "scripts/python/compute_indices.py"], env)
        _run([py, "scripts/python/hotspot_model.py"], env)

        score_src = nvi_score_tif_for_preset(args.preset)
        class_src = hotspot_tif_for_preset(args.preset)
        score_dst = out_dir / f"{aoi_stem}_nvi_score_{name}_{stamp}.tif"
        class_dst = out_dir / f"{aoi_stem}_hotspot_class_{name}_{stamp}.tif"
        shutil.copy2(score_src, score_dst)
        shutil.copy2(class_src, class_dst)
        scenario_outputs[name] = {"score": score_dst, "cls": class_dst}
        print(f"  sparade: {score_dst.name}, {class_dst.name}")

    base_score = _read_raster(scenario_outputs["baseline"]["score"])
    stress_score = _read_raster(scenario_outputs["stress"]["score"])
    base_cls = _read_raster(scenario_outputs["baseline"]["cls"])
    stress_cls = _read_raster(scenario_outputs["stress"]["cls"])

    score_stats = _summarize_score_diff(base_score, stress_score)
    class_stats = _summarize_class_diff(base_cls, stress_cls)
    base_hist = _class_hist(base_cls)
    stress_hist = _class_hist(stress_cls)

    out_csv = out_dir / f"{out_prefix}_{stamp}.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["metric", "value"])
        w.writerow(["score_mae_abs", f"{score_stats['mae']:.6f}"])
        w.writerow(["score_p95_abs", f"{score_stats['p95_abs']:.6f}"])
        w.writerow(["score_max_abs", f"{score_stats['max_abs']:.6f}"])
        w.writerow(["score_changed_pct", f"{score_stats['changed_pct']:.2f}"])
        w.writerow(["class_changed_pct", f"{class_stats['changed_pct']:.2f}"])
        w.writerow(["class_mean_abs_step", f"{class_stats['mean_abs_step']:.6f}"])
        w.writerow(["class_max_abs_step", f"{class_stats['max_abs_step']:.0f}"])
        for k in [1, 2, 3, 4]:
            w.writerow([f"baseline_class_{k}_px", base_hist[f"class_{k}"]])
            w.writerow([f"stress_class_{k}_px", stress_hist[f"class_{k}"]])

    print("\n=== Diagnos ===")
    print(f"score MAE(abs):         {score_stats['mae']:.6f}")
    print(f"score P95(abs):         {score_stats['p95_abs']:.6f}")
    print(f"score max(abs):         {score_stats['max_abs']:.6f}")
    print(f"score changed pixels:   {score_stats['changed_pct']:.2f}%")
    print(f"class changed pixels:   {class_stats['changed_pct']:.2f}%")
    print(f"class mean abs step:    {class_stats['mean_abs_step']:.6f}")
    print(f"class max abs step:     {class_stats['max_abs_step']:.0f}")
    print(f"\nSaved: {out_csv}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[FEL] {exc}")
        sys.exit(1)
