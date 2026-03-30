# NVI – geodatadriven naturvärdesinventering

Reproducerbar pipeline som kombinerar **svenska öppna geodata** (NMD, Lantmäteriet, Skogsstyrelsen) till en prioriterad hotspot-karta för riktad fältinventering. Metod och exempel visas på GitHub Pages.

**Live-sida:** [ulfboge.github.io/nvi](https://ulfboge.github.io/nvi/)

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

Se [CLAUDE.md](CLAUDE.md) för konfiguration, datavägar och valfria fallback-källor.

## Licens

Kod i detta repo licensieras under [MIT](LICENSE). **Data** från Naturvårdsverket, Lantmäteriet, Skogsstyrelsen m.fl. omfattas av respektive leverantörs licenser – de ingår inte i MIT-licensen.
