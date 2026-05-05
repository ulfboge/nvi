# Benchmark: naturlager (Kungsbacka västra)

Datum: 2026-05-05  
AOI: `kungsbacka_vastra`

## Upplägg

1. Baslinje: `NATURE_LAYER_BONUSES=0`
2. Förstärkt: `NATURE_LAYER_BONUSES=1` + `download_nature_layers.py`
3. Validering: `validate_against_gpkg.py`
4. A/B-jämförelse: `compare_hotspot_runs.py --ref outputs/rasters/kungsbacka_vastra_hotspot_class_baseline_ref.tif`

## Hämtade naturlager

- Nyckelbiotoper: 10 objekt
- Naturkultur: 0 objekt
- Sumpskog: 3 objekt
- Naturvårdsavtal: 0 objekt

## Resultat

| Mätning | Baslinje | Förstärkt | Delta |
|---|---:|---:|---:|
| Exakt träff | 39.1% | 39.1% | +0.0 pp |
| Nära träff (±1) | 84.8% | 84.8% | +0.0 pp |
| Areal-viktad träff | 34.0% | 34.0% | +0.0 pp |
| Överskattade objekt | 176 | 176 | 0 |
| Underskattade objekt | 28 | 28 | 0 |

Pixelöverensstämmelse mellan raster: 99.91% (klass > 0 i båda).

## Tolkning

I detta AOI gav första versionen av naturlager-bonus ingen mätbar förbättring i GPKG-valideringen.
Sannolik orsak är liten faktisk täckning i de nya lagren (0 naturkultur/0 naturvårdsavtal i AOI) samt konservativa vikter.
