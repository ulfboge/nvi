# Artdata och Rödlistan – plan för utökning av NVI-pipelinen

Internt dokument (samma som `PIPELINE_METOD.md`: **inte** menat som GitHub Pages-innehåll). Beskriver ett **stegvis** sätt att ta in arter och rödliststatus utan att blanda ihop dem med de fysiskt grundade indexen.

---

## 1. Rekommenderad ordning (tre faser)

### Fas A – Överlagring (minsta insats, störst lärande)

**Syfte:** Se var **kända fynd** och **rödlistade arter** sammanfaller med befintlig `hotspot_class` / `nvi_score` – utan att ändra kärnpipelinen.

1. Exportera observationer för **AOI + rimlig buffer** (t.ex. 5–10 km om du vill fånga närliggande signal) från en källa du får använda i just ditt projekt (se avsnitt 4).
2. Ladda ned **senaste Rödlistan** från [Artdatabanken / SLU](https://www.artdatabanken.se/) som tabell (art ↔ kategori: CR, EN, VU, NT, LC, … + gärna **versionsår**).
3. Koppla observationer → art → **rödlistkategori** (via vetenskapligt namn eller Artdatabankens **Taxon ID** om du har det – minskar stavfel).
4. I **QGIS eller ett litet Python-skript**: punktlager + ev. heatmap / hexagonräkning / rasterisering till samma **SWEREF 99 TM** och ungefär samma upplösning som dina index (t.ex. 10 m eller 100 m – dokumentera valet).
5. **Jämför visuellt eller med enkel logik:** t.ex. “cell är `flag_art` om ≥1 fynd av VU+ inom cell” eller “zonal statistics mot inventeringspolygoner”.

**Leverans:** Kartlager + kort metodnotis (datakälla, datum, rödlisteversion). Ingen ändring av `compute_indices.py` krävs.

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

## 5. Koppling till befintlig kod (när du är redo)

- **`config.py`:** ny konstant t.ex. `ARTER_DIR = RAW_DIR / "arter"` och ev. `WEIGHTS` utökad *om* du implementerar Fas B.
- **`download_data.py`:** valfritt tillägg som hämtar *publika* tabeller (om API tillåts) – ofta enklare att **manuellt** lägga filer i `data/raw/arter/` tills flödet stabiliserats.
- **Nytt skript (förslag):** `scripts/python/build_species_layer.py` som läser `observations/` + `rodlista/` och skriver `data/processed/{AOI}_species_relevance.tif` (eller bara GeoPackage för Fas A).

Inget av ovan är implementerat i repot ännu; det här dokumentet är **roadmap**.

---

## 6. Integritet och publicering

- Publicera **inte** exakta koordinater för **känsliga arter** på öppen webb eller i öppna repo utan att följa **Artportalens / leverantörens regler**.
- På **GitHub Pages**-showcasen: beskriv gärna metoden på **övergripande nivå**; detaljkartor med arter kan vara **interna** eller aggregerade till grov skala.

---

## Se även

- [`PIPELINE_METOD.md`](PIPELINE_METOD.md) – nuvarande fjärranalys- och indexflöde utan artlager.
