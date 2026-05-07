# Återupptagningsnot – 2026-05-06

Dagens spår: **LSTE-spåret** (kalibrering, naturvikter, känslighetsdiagnos, midrange).

## Status

Inga commits gjorda – allt ligger som lokala ändringar / otrackade filer.

### Klart i dag

- Parametriserade svepskripten (CLI, dagens datum i CSV-namn, valbar Python-tolk):
  - `scripts/python/run_lste_calibration_sweep.py`
  - `scripts/python/run_lste_nature_weights_sweep.py`
- Båda kördes på LstE → samma resultat för alla testade värden:
  - `outputs/validation/lste_calibration_sweep_20260506.csv` (alla rader: exact 33.4 %, near 85.1 %, area 26.1 %)
  - `outputs/validation/lste_nature_weights_sweep_20260506.csv` (samma siffror för alla scenarier)
- Nytt: `scripts/python/diagnose_lste_sensitivity.py` (baseline vs stress).
  - Kördes på LstE: `outputs/validation/lste_sensitivity_diagnosis_20260506.csv`.
  - Tydlig signal: `score_changed_pct=48.57`, `class_changed_pct=37.78`,
    `score_mae_abs=0.097`, `class_max_abs_step=1`.
  - Tolkning: modellen reagerar – tidigare svep var för smala.
- Nytt: `scripts/python/run_lste_midrange_tuning.py` (baseline + mid_1/2/3).
  - Inte slutkört. Tre bakgrundskörningar avslutades med
    `exit_code=4294967295` innan första scenarioutskrift.
  - Skriptet kör utan fel via `--help` och hade också
    fått `--only` för smalare körningar.
- Nytt: `scripts/python/sweep_test_presets.py` – gemensam preset-modul
  (`lste`, `kungsbacka`) som rensar tidigare AOI-overrides och pekar ut
  rätt referens-GPKG.
- Alla fyra skript ovan har nu `--preset {lste,kungsbacka}` och `--gpkg`.
  - Kungsbacka använder `AOI_NAME`/`AOI_BBOX` från `config.py`
    (`kungsbacka_vastra`) och referens-GPKG
    `data/raw/gpkg/Naturvärdesinventering västra Kungsbacka kommun.gpkg`.

### Filer som väntar på commit

`scripts/python/`:
- nya: `diagnose_lste_sensitivity.py`, `discover_nvi_gpkg_candidates.py`,
  `extract_atom_download_links.py`, `ingest_gpkg_from_zip.py`,
  `inspect_geodata_zip.py`, `normalize_candidate_gpkg.py`,
  `run_lste_calibration_sweep.py`, `run_lste_nature_weights_sweep.py`,
  `run_lste_midrange_tuning.py`, `sweep_test_presets.py`
- modifierade: `compute_indices.py`, `config.py`, `hotspot_model.py`,
  `validate_against_gpkg.py`, `validate_against_report.py`
- arkivanteckningar: `docs/_archive/*_20260505.md` + denna fil
- root: `README.md`

## Nästa steg när vi tar upp igen

Förslag i ordning:

1. Smal körning på mindre AOI:
   `python scripts/python/run_lste_midrange_tuning.py --preset kungsbacka --only baseline,mid_2`
2. Vid behov: bygg/uppdatera naturlager för `kungsbacka_vastra`
   (rastren i `data/raw/nature_layers/*_kungsbacka_vastra_10m.tif`).
3. Granska referens-GPKG: klassfördelning, geometriöverlapp,
   därefter stratifierad validering per delområde/biotop.
4. Om midrange-skriptet faller igen: skriv stdout till logfil och
   prefixa med `python -u` (unbuffered) – då fångar vi var det stannar.

## Snabbreferens – körningar

```bash
# Kalibrering (CONTINUITY_AGE_BLEND-svep)
python scripts/python/run_lste_calibration_sweep.py --preset kungsbacka

# Naturvikter
python scripts/python/run_lste_nature_weights_sweep.py --preset kungsbacka

# Baseline vs stress (modellkänslighet)
python scripts/python/diagnose_lste_sensitivity.py --preset kungsbacka

# Midrange (smal körning)
python scripts/python/run_lste_midrange_tuning.py --preset kungsbacka --only baseline,mid_2
```
