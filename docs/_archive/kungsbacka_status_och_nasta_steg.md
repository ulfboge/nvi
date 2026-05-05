# Kungsbacka – status och nästa steg

Detta dokument sammanfattar vad som har gjorts i Kungsbacka-körningen och vad som ska göras i nästa session.

## Genomfört

- AOI satt till Kungsbacka i `scripts/python/config.py`:
  - `AOI_NAME = "kungsbacka_vastra"`
- Extern NVI läst från:
  - `data/raw/gpkg/Naturvärdesinventering västra Kungsbacka kommun.gpkg`
  - lager `kungsbacka_kommun_vstra`
  - klassfält `nvklass`
- Nytt valideringsskript tillagt:
  - `scripts/python/validate_against_gpkg.py`
- Pipeline körd och jämförd mot GPKG (baseline och förbättrad variant).

## Kodförändringar

- `scripts/python/compute_indices.py`
  - robust hantering när raster saknar AOI-overlap (skip i stället för krasch),
  - stöd för SLU skogsålder 2025 i kontinuitetsindex (blend),
  - stöd för SLU lavindikator i strukturindex (bonus),
  - stöd för SLU torvkarta i fuktindex (blend).
- `scripts/python/config.py`
  - nya katalogvariabler för SLU GIS-lager,
  - torvkarta pekas mot `E:`:
    - `SLU_PEAT_DIR = Path("E:/slu_gis/peat_1_0")`

## Nedladdade data

- `data/raw/slu_gis/skogsalder_2025/SPRUCE_AGE_2025.tif`
- `data/raw/slu_gis/lichenindicator_2025/SLU_lavindikatorkartan_2025_kategorisk.tif`
- `E:/slu_gis/peat_1_0/ClassifiedPeatMap.tif`

## Resultat (mot GPKG-NVI)

### Baseline
- Exakt träff: `38.5%`
- Nära träff (±1): `85.7%`
- Areal-viktad träff: `33.5%`

### Efter skogsålder-komponent
- Exakt träff: `45.4%`
- Nära träff (±1): `86.6%`
- Areal-viktad träff: `38.7%`

## Viktigt om artdata

I nuvarande implementation läggs artbonus på `nvi_score` (före skogsmask):

- `+0.10` vid minst 1 hotat fynd i pixel
- `+0.15` vid minst 3 hotade fynd i pixel

Det kan ge tydlig påverkan på klassningen och bör kalibreras.

## Nästa session – plan

1. Kör om med torvkartan aktiv:
   - `python scripts/python/compute_indices.py`
   - `python scripts/python/hotspot_model.py`
   - `python scripts/python/validate_against_gpkg.py`
2. Inkludera artdata i samma AOI och gör A/B-jämförelse:
   - utan artdata,
   - med nuvarande artbonus,
   - med mildare artbonus (t.ex. `0.05/0.10`).
3. Jämför alla varianter med samma mått:
   - exakt träff,
   - ±1 träff,
   - areal-viktad träff,
   - över-/underskattade objekt.
4. Dokumentera vald modellversion (baseline, +skogsålder, +torv, +artdata).

## Relevanta output-filer

- `outputs/rasters/kungsbacka_vastra_hotspot_class.tif`
- `outputs/figures/kungsbacka_vastra_hotspot_map.png`
- `outputs/figures/kungsbacka_vastra_validation_gpkg.png`

