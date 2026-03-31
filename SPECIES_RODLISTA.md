# Artdata och Rödlistan – plan för utökning av NVI-pipelinen

Internt dokument (samma som `PIPELINE_METOD.md`: **inte** menat som GitHub Pages-innehåll). Beskriver ett **stegvis** sätt att ta in arter och rödliststatus utan att blanda ihop dem med de fysiskt grundade indexen.

---

## 1. Rekommenderad ordning (tre faser)

### Fas A – Överlagring (**implementerad**)

**Syfte:** Se var **kända fynd** och **rödlistade arter** sammanfaller med befintlig `hotspot_class` / `nvi_score` – utan att ändra kärnpipelinen.

**Skript:** `scripts/python/species_overlay_a.py`

1. Lägg (eller exportera) data lokalt, t.ex.  
   `data/raw/arter/observations/` och `data/raw/arter/rodlista/` (mapparna skapas vid körning; innehållet versionshanteras normalt **inte** i git).
2. **Rödlista:** CSV med minst två kolumner – vetenskapligt namn och kategori. Standardkolumnnamn: `scientific_name`, `redlist_category`. Koder och svenska namn som `VU`, `Sårbar`, `Akut hotad`, `LC`, `Livskraftig` m.fl. känns igen (se `_THREAT_RANK` i skriptet).
3. **Observationer:** GeoPackage / GeoJSON / Shapefile **eller** CSV med lon/lat. CSV: kolumner `decimalLongitude` + `decimalLatitude` (GBIF-liknande) hittas automatiskt; annars ange `--obs-lon-col` / `--obs-lat-col`.
4. Kör:

```bash
python scripts/python/species_overlay_a.py \
  --obs data/raw/arter/observations/dina_fynd.gpkg \
  --rodlista data/raw/arter/rodlista/rodlista.csv
```

**Utdata** (under `outputs/species/`, återskapas vid varje körning):

| Fil | Innehåll |
|-----|----------|
| `{AOI}_observations_rodlista.gpkg` | Alla punkter inom AOI + buffer, med rödlistfält (`_cat_raw`, `_threat_rank`, `_rodlista_match` m.m.) |
| `{AOI}_species_obs_count.tif` | Antal observationer per pixel (samma grid som `structure_index` om den finns, annars 10 m-grid över AOI) |
| `{AOI}_species_threat_obs_count.tif` | Antal observationer per pixel där arten har **hotnivå ≥ VU** (standard `--min-threat-rank 2`; höj till 3 för EN+). |

**Inställningar i `config.py`:** `SPECIES_AOI_BUFFER_M` (standard 5000 m), `SPECIES_OUTPUT_DIR`.

**Miniexempel** (ingen känslig data, för att testa kedjan):

```bash
python scripts/python/species_overlay_a.py \
  --obs examples/species/obs_minimal.csv \
  --rodlista examples/species/rodlista_minimal.csv
```

5. **QGIS:** lägg `hotspot_class.tif` / `nvi_score.tif` som botten, öppna GPKG + de två art-rasterna, jämför visuellt.

**Leverans:** Kartlager + raster; dokumentera datakälla, datum och rödlisteversion i t.ex. `data/raw/arter/rodlista/rodlista_version.txt`. `compute_indices.py` ändras inte.

---

### Fas B – Eget delindex `species_relevance` (valfritt)

**Syfte:** En **normaliserad 0–1-raster** som *kompletterar* NVI, med **lägre vikt** än struktur/kontinuitet så osäker eller gles artdata inte dominerar.

**Förslag:**

- Bygg raster från aggregerade fynd (t.ex. antal arter, max rödlistvikt, kernel density).
- Vikta arter med **numerisk skala** från Rödlistan (exempel – du kalibrerar själv):  
  CR > EN > VU > NT > LC; “Data Deficient” hanteras separat eller exkluderas.
- **Resampla** till exakt samma **shape** som `structure_index` (samma som idag för NVI-poäng).
- Ny formel (idé):  
  `nvi_score_extended = w_s*struktur + w_c*kontinuitet + w_m*fukt + w_a*species_relevance`  
  med t.ex. `w_a` = 0,05–0,15 och **omfördela** från övriga vikter så summan = 1.

**Krav:** Versionsfil för rödlista + reproducerbart skript; committa **inte** stora råexportfiler om licensen säger nej – använd `.gitignore` och dokumentera hur man återskapar dem.

---

### Fas C – Objekt- / fältnivå (stämmer med din metodtext)

**Syfte:** `Naturvärdesindex = f(struktur, kontinuitet, art)` **efter** att du avgränsat objekt i GIS.

- Zonal statistics: medel/max **nvi_score**, andel hotspot-yta, **artrikedom**, **närvaro av rödlistade arter** inom polygon.
- Rödlistan används som **klassificering av fynden**, inte som pixelgissning över hela skogen.

Detta passar **NVI-metodik** och juridisk försiktighet bäst när artdata är gles eller känslig.

---

## 2. Föreslagen katalogstruktur (innan kod skrivs)

Lägg **rådata** under `data/raw/` så det följer resten av repot:

```text
data/raw/arter/
  observations/     # export från Artportalen, GBIF, egen inventering (.gpkg, .csv)
  rodlista/         # officiell rödlisttabell + metadata (se nedan)
  derived/          # genererade raster/vektor (ofta .gitignore om stort)
```

Skapa en liten **metadatafil** i `rodlista/`:

```text
rodlista_version.txt   # t.ex. "Rödlista 2020" eller Artdatabankens versionsbenämning + nedladdningsdatum
species_columns.md     # vilka kolumner du joinar på (scientificName, taxonId, …)
```

---

## 3. Rödlistan – “senaste versionen”

- **Använd alltid den version Artdatabanken anger som gällande** när du kör analysen, och **spara versionsår + nedladdningsdatum** i repot (textfil räcker).
- När Rödlistan **byts ut** (vart femte år i praxis): kör om artleden, uppdatera `rodlista_version.txt`, notera i commit/rapport.
- **Tolkning:** Rödlistan = **utdöenderisk i Sverige**, inte samma sak som “högt lokalt naturvärde”. Dokumentera att indexet är **artbevarande relevans**, inte NVI-substitut.

---

## 4. Datakällor att välja mellan (kort)

| Källa | Typiskt format | Att tänka på |
|--------|----------------|--------------|
| **Artportalen** | Export CSV/Excel, ev. API | Användarvillkor; **skyddsvärda arter** kan ha **dolda eller förflyttade koordinater** – förvräng aldrig för att “få bättre karta”. |
| **GBIF** | Nedladdning / API | Bra täckning; samma heder kring **sensitive species** och citat av dataset DOI. |
| **Egen fältdata** | GPX, formulär | Starkast lokalt underlag; bäst tillsammans med Fas C. |
| **Artdatabanken – Rödlistan** | Tabell (art ↔ status) | **Officiell** kategori; join mot observationstabellens artfält. |

Välj **en primär observationskälla** först så du inte dubbelräknar samma fynd (Artportalen ↔ GBIF överlappar).

---

## 5. Koppling till befintlig kod

- **`config.py`:** `ARTER_*`, `SPECIES_AOI_BUFFER_M`, `SPECIES_OUTPUT_DIR` (skapas vid import om saknas).
- **Fas A:** `scripts/python/species_overlay_a.py` (klar).
- **Fas B (plan):** ev. `WEIGHTS` utökad + raster i `data/processed/`; valfritt tillägg i `download_data.py` för API mot öppna tabeller.

Fas B–C är ännu **roadmap**; se övriga avsnitt ovan.

---

## 6. Integritet och publicering

- Publicera **inte** exakta koordinater för **känsliga arter** på öppen webb eller i öppna repo utan att följa **Artportalens / leverantörens regler**.
- På **GitHub Pages**-showcasen: beskriv gärna metoden på **övergripande nivå**; detaljkartor med arter kan vara **interna** eller aggregerade till grov skala.

---

## Se även

- [`PIPELINE_METOD.md`](PIPELINE_METOD.md) – nuvarande fjärranalys- och indexflöde utan artlager.
