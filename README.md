# NVI – geodatadriven naturvärdesinventering

Reproducerbar pipeline som kombinerar **svenska öppna geodata** (NMD, Lantmäteriet, Skogsstyrelsen) till en prioriterad hotspot-karta för riktad fältinventering. Metod och exempel visas på GitHub Pages.

**Live-sida:** [ulfboge.github.io/nvi](https://ulfboge.github.io/nvi/)

Tillkommit i repot (förutom kärnpipelinen): **SLU SOS**-hämtning av observationer (API-nyckel), **svensk rödlista** som CSV (ResearchData 2025 / valfritt GBIF 2020), **fas A** artöverlagring mot hotspot, **skyddad natur** via Naturvårdsverkets WFS till valfri showcase-figur. Se [PIPELINE_METOD.md](PIPELINE_METOD.md) och [SPECIES_RODLISTA.md](SPECIES_RODLISTA.md).

## Disclaimer

Detta repo tillhandahålls utan support eller garanti för korrekthet eller lämplighet i ditt projekt. Du kör kod och hämtar data på egen risk och ansvarar själv för att följa respektive dataleverantörs villkor.

## Snabbstart

```bash
pip install -r requirements.txt
python scripts/python/download_data.py --nmd-confirm
python scripts/python/compute_indices.py
python scripts/python/hotspot_model.py
python scripts/python/generate_showcase.py
```

**Valfritt — skyddad natur till showcase:** efter att `hotspot_class.tif` finns, kör `python scripts/python/download_data.py --protected-sites-only` och sedan `generate_showcase.py` igen (producerar `docs/assets/hotspot_protected_context.png`).

**Valfritt — artdata:** `fetch_public_observations.py` (GBIF och/eller SOS med `SOS_API_BASE` + `SOS_SUBSCRIPTION_KEY` i `.env`), `fetch_swedish_redlist_gbif.py` för rödlista-CSV, `species_overlay_a.py` mot `outputs/species/`. Detaljer i [SPECIES_RODLISTA.md](SPECIES_RODLISTA.md).

Se [CLAUDE.md](CLAUDE.md) för konfiguration, datavägar och valfria fallback-källor.

## Licens

Kod i detta repo licensieras under [MIT](LICENSE). **Data** från Naturvårdsverket, Lantmäteriet, Skogsstyrelsen, Artdatabanken/SLU m.fl. omfattas av respektive leverantörs licenser – de ingår inte i MIT-licensen.
