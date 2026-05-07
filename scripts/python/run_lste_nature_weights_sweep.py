"""
run_lste_nature_weights_sweep.py
Sweep av naturbonus-vikter.

--preset kungsbacka ger snabbare körning på mindre AOI (config.py).
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

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


def _resolve_python(args_python: str | None) -> str:
    if args_python:
        return args_python
    venv_py = REPO_DIR / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable


def main() -> None:
    parser = argparse.ArgumentParser(description="Svep för naturbonus-scenarier.")
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
        "--python",
        default=None,
        help="Valfri python-tolk (default: .venv/Scripts/python.exe eller aktuell interpreter).",
    )
    parser.add_argument(
        "--out-prefix",
        default=None,
        help="Prefix för CSV i outputs/validation/ (standard: <preset>_nature_weights_sweep).",
    )
    args = parser.parse_args()

    hotspot_path, gpkg_path, class_field = resolve_validation_paths(args.preset, args.gpkg)
    out_prefix = args.out_prefix or f"{args.preset}_nature_weights_sweep"

    scenarios = [
        ("baseline_no_nature", {"NATURE_LAYER_BONUSES": "0"}),
        (
            "current_defaults",
            {
                "NATURE_LAYER_BONUSES": "1",
                "NATURE_W_STRUCTURE_NYCKELBIOTOPER": "0.05",
                "NATURE_W_STRUCTURE_NATURKULTUR": "0.03",
                "NATURE_W_STRUCTURE_SUMPSKOG": "0.02",
                "NATURE_W_CONT_NATURVARDSAVTAL": "0.05",
                "NATURE_W_MOISTURE_SUMPSKOG": "0.00",
            },
        ),
        (
            "strong_structure_moisture",
            {
                "NATURE_LAYER_BONUSES": "1",
                "NATURE_W_STRUCTURE_NYCKELBIOTOPER": "0.10",
                "NATURE_W_STRUCTURE_NATURKULTUR": "0.06",
                "NATURE_W_STRUCTURE_SUMPSKOG": "0.05",
                "NATURE_W_CONT_NATURVARDSAVTAL": "0.05",
                "NATURE_W_MOISTURE_SUMPSKOG": "0.08",
            },
        ),
    ]

    rows: list[dict[str, str]] = []
    py = _resolve_python(args.python)

    for name, overrides in scenarios:
        env = subprocess_env_from_preset(
            args.preset, {"CONTINUITY_AGE_BLEND": "0.18", **overrides}
        )

        print(f"\n=== Scenario: {name} ===")
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
        rows.append(
            {
                "scenario": name,
                "exact_pct": "" if exact is None else f"{exact:.1f}",
                "near_pct": "" if near is None else f"{near:.1f}",
                "area_weighted_pct": "" if area is None else f"{area:.1f}",
            }
        )
        print(f"  exact={rows[-1]['exact_pct']} near={rows[-1]['near_pct']} area={rows[-1]['area_weighted_pct']}")

    out_dir = OUTPUTS_DIR / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d")
    out_csv = out_dir / f"{out_prefix}_{stamp}.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["scenario", "exact_pct", "near_pct", "area_weighted_pct"])
        w.writeheader()
        w.writerows(rows)
    print(f"\nSaved: {out_csv}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[FEL] {e}")
        sys.exit(1)
