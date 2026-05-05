# NVI-pipelinen: vad skripten gör och vad som produceras

Det här dokumentet är ett **internt metodstöd** – det publiceras inte på GitHub Pages. Det beskriver logiken i Python-flödet från rådata till hotspot-raster och showcase-bilder.

---

## 1. Översikt

Pipelinen gör följande i ordning:

1. **`download_data.py`** – hämtar (eller förbereder för manuell) geodata inom **AOI** (area of interest), definierad i `config.py`.
2. **`compute_indices.py`** – klipper raster till AOI, bygger **tre delindex** (struktur, kontinuitet, fukt), normaliserar dem till ungefär **0–1**, viktar ihop dem till **NVI-poäng** (0–1).
3. **`hotspot_model.py`** – delar in NVI-poäng i **fyrklassig** hotspot-klassning enligt SS 199000:2023 (**klass 1 = Mycket högt … klass 4 = Visst**), sparar klassraster och en diagnostisk PNG i `outputs/`.
4. **`generate_showcase.py`** – bygger **publika figurer** till `docs/assets/` för sidan (kräver att steg 2–3 körts, samt nätverk för översiktskarta). Kan även skriva **hotspot + skyddad natur** om WFS-data finns (se §3 och §6).

**Viktter** (summa 1,0) i `config.py`:

| Delindex     | Vikt  | Idé |
|-------------|-------|-----|
| Struktur    | 0,40  | Skoglig komplexitet / höjd / lämpliga trädslag |
| Kontinuitet | 0,40  | Låg avverkningspåverkan = mer “ostörd” skog |
| Fukt        | 0,20  | Topografisk blötmarksbenägenhet (+ ev. NMD-våtmark) |

---

## 2. Konfiguration (`scripts/python/config.py`)

- **`AOI_NAME`** – filprefix för alla utdata (t.ex. `fiby_urskog`).
- **`AOI_BBOX`** – rektangel i WGS84 (min/max lon/lat). All klippning mot raster sker mot denna box (omräknad till SWEREF99 TM för svenska data).
- **`WEIGHTS`** – vikterna ovan.
- **Sökvägar** – NMD: `data/raw/nmd/` (junction till `E:\nmd` vanligt). **Stora SLU-GIS-lager** (Skogskarta 2018, kol 2023): standard **`E:/slu_gis/`** om enheten finns (`slu_forest_map_2018/`, `carbon_2023/`); överstyr med **`SLU_GIS_LARGE_ROOT`** i `.env`. Övriga nedladdningar under `data/raw/...`, processade raster (`data/processed/`), figurer (`outputs/figures/`), slutliga klassraster (`outputs/rasters/`). Torvkarta: `data/raw/slu_gis/peat_1_0/` (junction till t.ex. `E:\slu_gis\peat_1_0`).
- **`LANTMATERIET_API_KEY`** – läses från `.env` för Lantmäteriet STAC.

Byter du område: uppdatera **`AOI_BBOX`** och **`AOI_NAME`**, kör om hela kedjan.

---

## 3. `download_data.py` – data in i projektet

**Syfte:** Få in de filer som `compute_indices.py` letar efter, klippta eller färdiga att klippas till AOI.

**Ungefärligt innehåll:**

- **NMD 2023** (Naturvårdsverket) – zip med basskikt (obligatoriskt för NMD-vägen), valfritt trädslag och objekthöjd. Stort (~tior GB totalt); kräver `--nmd-confirm`.
- **Skogsstyrelsen** – avverkningsanmälningar via ArcGIS REST, rasteriseras till AOI-relaterade GeoTIFF under `data/raw/skogsstyrelsen/`.
- **Lantmäteriet** – GSD-höjddata (lidar-DEM) via STAC/API till `data/raw/lantmateriet/` (API-nyckel).
- **SLU Skogliga Grunddata** – om du manuellt lagt filer enligt strukturen i `data/raw/slu/` används de i strukturindex med **högst prioritet**.
- **Skyddad natur (valfritt)** – flagga `--protected-sites` eller enbart `--protected-sites-only`: hämtar **INSPIRE Protected Sites** (`ps:ProtectedSite`) från Naturvårdsverkets **WFS** inom `AOI_BBOX` utökad med marginal (`--protected-expand-deg`, standard 0,05°). Använder **WFS 1.1.0** + bbox i **EPSG:4258** (servern returnerar då träffar). Sparar `data/raw/naturvardsverket/skyddad_natur/protected_sites_<AOI>.gpkg` (SWEREF99 TM) + metadata. **Inga NVI-index** använder detta; det är kontext för GIS/showcase.
- **SLU Skogskarta 2018 (valfritt, stort)** – `--slu-forest-map-confirm` hämtar `Ek_andel.tif` och `Bok_andel.tif` till `SLU_FOREST_MAP_DIR` (under `SLU_GIS_LARGE_ROOT`, se §2). Valfritt `--slu-forest-map-include-ovrlov` för `OvrLov_andel.tif`. `--slu-forest-map-only` = endast detta steg (kräver confirm).
- **SLU kol 2023 (valfritt, zip)** – `--slu-carbon-confirm` hämtar `Stock_SOC.zip` eller `Stock_DOM.zip` (`--slu-carbon-product`), packar upp `.tif` till `SLU_CARBON_DIR`. `--slu-carbon-only` = endast detta steg. `--slu-gis-overwrite` skriver om befintliga filer.

Skriptet transformerar **AOI** till **SWEREF99 TM** där det behövs för att beställa/hämta data som levereras i nationellt grid.

**Resultat:** Filer på disk – inga sammanslagna index ännu. Utan relevanta filer hoppar `compute_indices.py` till fallback-kedjor eller avslutar med fel/meddelande.

### 3b. `download_nature_layers.py` – naturlager för valfria bonusar

**Syfte:** hämta AOI-klippta lager som kan ge försiktiga bonusar i struktur/kontinuitet.

- Nyckelbiotoper (Skogsstyrelsen)
- Naturkultur / objekt med naturvärden (Skogsstyrelsen)
- Sumpskog (Skogsstyrelsen)
- Naturvårdsavtal (om lager hittas i naturkulturtjänsten)

**Körning:**

```bash
python scripts/python/download_nature_layers.py
python scripts/python/download_nature_layers.py --overwrite
```

**Utdata:** `data/raw/nature_layers/` med både GPKG och binära 10 m-raster (`*_10m.tif`) för snabb inläsning i `compute_indices.py`.

---

## 4. `compute_indices.py` – under huven per delindex

All bearbetning sker **inom AOI**. Raster **maskas** (klipps) med Shapely-box i rätt CRS. Värden **normaliseras** oftast med percentil 2–98 till intervallet **\[0, 1\]** (`normalize`), så extrema outliers dämpas.

### 4.1 Strukturindex (prioritetsordning)

1. **SLU** – om `*biomassa*.tif` finns: biomassa normaliseras; ev. höjdskikt viktas in (60 % / 40 %). **Stannar här** om data finns.
2. **GEE-export** – om fil matchar `*Delindex*.tif` i `data/raw/gee_exports/`: band 1 tolkas som NDVI-struktur, normaliseras. **Stannar här** om data finns.
3. **NMD** – om basskikt-filen finns:
   - Skogsklasser 1–3 → skogsmask.
   - **Textur / kärnyta:** avståndstransform mot skogskant (interiör) + fler regler (sekundärlöv, gran, LiDAR, nyckelbiotop, NNK, SLU VOL, lav) — se källkod `build_structure_index()`.
   - **SLU Skogskarta 2018 (valfritt):** om `Ek_andel.tif` / `Bok_andel.tif` / `OvrLov_andel.tif` finns i `SLU_FOREST_MAP_DIR` klipps de till AOI; medel av normaliserade andelar adderas som **~8 %** bonus på struktur (komplement till NMD-trädslag).
   - **Naturlager (valfritt):** om `NATURE_LAYER_BONUSES=1` och raster finns i `data/raw/nature_layers/` adderas små bonusar:
     - Nyckelbiotoper +0.05
     - Naturkultur +0.03
     - Sumpskog +0.02
4. **Hansen Global Forest Change** – `treecover2000` i `data/raw/hansen/`: täckning + textur, normaliserat. Sista utväg innan fel.

**Metodidé:** Högt strukturindex ≈ mer skogsmässig “kvalitet” i bred bemärkelse (biomassa/höjd eller NMD-proxy för gammal/komplex skog med ädellöv och höjd).

### 4.2 Kontinuitetsindex

1. **Skogsstyrelsen GPKG med datum** – tidsavklingad störning via `Inkomdatum`:
   - Störningsvikt = `exp(−ålder_år / 15)` (halvtid 15 år).
   - Nyligen avverkad (0 år) → vikt ≈ 1,0; 15 år gammal → vikt ≈ 0,37; 30 år → vikt ≈ 0,14.
   - `continuity = 1 − störningsvikt`, sedan **mjuk kant** kring störda parceller.
   - Faller tillbaka på binärt raster om GPKG saknas.
2. **GEE Delindex** – om minst 5 band: NDVI-standardavvikelse m.m. omvandlas till en stabilitetsproxy (lägre variabilitet → högre kontinuitet).
3. Annars **konstant 0,5** (neutralt) med varning.
4. **Naturlager (valfritt)**: om `NATURE_LAYER_BONUSES=1` och naturvårdsavtal-raster finns adderas +0.05 i kontinuitetsindex.

**Metodidé:** Högt index ≈ ytor som sällan/aldrig störts, med äldre störningar viktade lägre.

**Begränsning:** Skogsstyrelsen registrerar bara formella avverkningsanmälningar. Oregistrerade planteringar och hyggen utan anmälan ger falskt hög kontinuitet för granplanteringar.

### 4.3 Fuktindex

1. **Lantmäteriet DEM** – gradienter → **TWI** (Topographic Wetness Index, förenklad flödesackumulation / lutning). Normaliseras till \[0,1\]. Om NMD basskikt finns: **våtmarksklass** blandas in (70 % TWI + 30 % våtmarksmask).
2. **Valfritt SLU torv** – kontinuerlig/kategorisk torvkarta blandas in i fuktindex (se kod).
3. **Valfritt SLU kol 2023** – om `.tif` från uppackad `Stock_SOC`/`Stock_DOM` finns i `SLU_CARBON_DIR`: normaliserad **~12 %** blend mot fuktindex.
4. **Bara NMD** – om ingen DEM: våtmark + liten offset som fuktproxy.
5. **Copernicus DEM** i `data/raw/dem/` – TWI med grovare upplösning (30 m cell antaget).
6. Annars **konstant 0,5** med varning.

**Metodidé:** Högt index ≈ topografiskt fuktigare lägen (och ev. kartlagd våtmark).

**TWI-beräkning:** Försöker D8-flödesackumulation via **pysheds** (korrekt upslope-area). Faller tillbaka på enkel 3×3-gradientmetod om pysheds saknas eller är inkompatibelt med installerad NumPy-version.

### 4.4 NVI-poäng och utdata från steget

- **NaN/hål** fylls med **median** per skikt (minimerar konstiga hål i summan).
- **`nvi_score = 0,4×struktur + 0,4×kontinuitet + 0,2×fukt`**

**Filer skrivna till `data/processed/`** (samma geometri, SWEREF99 TM, float32, nodata −9999):

| Fil | Innehåll |
|-----|----------|
| `{AOI}_structure_index.tif` | Struktur 0–1 |
| `{AOI}_continuity_index.tif` | Kontinuitet 0–1 |
| `{AOI}_moisture_index.tif` | Fukt 0–1 |
| `{AOI}_nvi_score.tif` | Viktat medel 0–1 |

---

## 5. `hotspot_model.py` – från poäng till klasser

**Indata:** `{AOI}_nvi_score.tif`.

**Klassificering (percentilbaserad, SS 199000:2023 4-klasschema):**

- Bara pixlar med **poäng > 0** räknas in i percentilberäkningen.
- **p25**, **p75** och **p93** beräknas på dessa värden.
- Absoluta golv används för att motverka att lågvärdiga ytor klassas upp enbart av sin relativa rank.
- Regel (kumulativt högre naturvärde vid högre poäng):
  - Poäng > 0              → klass **4** – Visst naturvärde.
  - Poäng > p25 (min 0,38) → klass **3** – Påtagligt naturvärde.
  - Poäng > p75 (min 0,72) → klass **2** – Högt naturvärde.
  - Poäng > p93 (min 0,85) → klass **1** – Mycket högt naturvärde.
- Pixlar med poäng 0 blir **0** (nodata för klasser).

**Validering mot kommunal GPKG (västra Kungsbacka, 335 objekt):**

| Mätning | Värde |
|---------|-------|
| Exakt klassträff | 41,2 % (138/335) |
| Nära träff (±1 klass) | **85,1 %** (285/335) – viktigaste praktiska mätning |
| Areal-viktad träff | 36,1 % |
| Överskattade objekt | 173 |
| Underskattade objekt | 24 |

Rapportvalidering för Djupedal finns kvar som separat skript (`validate_against_report.py`), men utfallet beror på att AOI/raster verkligen täcker rapportens koordinater.

**Arealstatistik** skrivs till terminal (ungefärlig hektar baserat på pixelstorlek från transform, fallback 30 m om något är konstigt).

**Utdata:**

| Var | Fil | Innehåll |
|-----|-----|----------|
| `outputs/rasters/` | `{AOI}_hotspot_class.tif` | Uint8, 0 = ej skog, **1–4** hotspot-klass – **det som används vidare** |
| `outputs/figures/` | `{AOI}_hotspot_map.png` | Snabb översikt: klasskarta + NVI-poäng + tre delindex |

---

## 6. `generate_showcase.py` – bilder till webbsidan

**Indata:** Processade index + `outputs/rasters/{AOI}_hotspot_class.tif`.

- Bygger **`docs/assets/hotspot_showcase.png`** – stor klasskarta, fyra paneler (NVI-poäng + tre index), översikts-inset med webbkarta och AOI-ram (kräver **contextily** + nätverk).
- Bygger **`docs/assets/method_diagram.png`** – sexstegs processfigur.
- Bygger **`docs/assets/hotspot_protected_context.png`** om **`protected_sites_<AOI>.gpkg`** finns under `data/raw/naturvardsverket/skyddad_natur/` (t.ex. efter `download_data.py --protected-sites-only`). Samma hotspot-raster som underlag med **gula gränser** för formellt skydd; tydlig bildtext i figuren att skydd **inte** viktas in i NVI. Saknas GPKG hoppas detta steg över med meddelande i terminalen.

`docs/index.html` refererar showcase-bilderna; den tredje bilden har egen sektion om skyddad natur som kontext.

Ändrar du metodtexter eller layout här ska du committa de genererade PNG:erna om sidan ska uppdateras.

---

## 7. Dataflöde (textdiagram)

```
config.py (AOI, vikter, sökvägar)
        │
        ▼
download_data.py  →  data/raw/ …  (+ NMD under data/raw/nmd, ev. junction till extern disk)
        │
        ▼
compute_indices.py  →  data/processed/*_index.tif, *_nvi_score.tif
        │
        ▼
hotspot_model.py  →  outputs/rasters/*_hotspot_class.tif
                  →  outputs/figures/*_hotspot_map.png
        │
        ▼
generate_showcase.py  →  docs/assets/*.png
```

---

## 8. Vad pipelinen *inte* gör

- **Ingen fältinventering** – den producerar endast spatial prioritering underlag.
- **Ingen fullständig fältprotokoll-NVI** – utdata är en **geodata-driven fyrklassig** prioritering (SS 199000:2023-orienterad skala i `hotspot_model.py`), inte en ersättning för hela fältmetodiken.
- **Tolkning av index** är modellbaserad: resultat beror på datakvalitet, upplösning och fallback-val. Percentiltrösklar (**p25/p75/p93** m.m.) är **relativa inom AOI**, inte absoluta ekologiska gränser.
- **Skyddade områden, rödlista och observationslager** (GBIF/SOS, `species_overlay_a.py`) ingår **inte** i viktningen av struktur/kontinuitet/fukt. De är **kompletterande lager** för tolkning, rapport och webb-presentation.

**Efter fält:** att jämföra modellen med **fältfynd** (och ev. historiska observationslager med tidsfilter) är ett **separat utvärderingssteg** — se avsnitt 1a–1c i [`SPECIES_RODLISTA.md`](SPECIES_RODLISTA.md). Det ingår normalt **inte** som input till `compute_indices.py` utan tydlig separat metodbeskrivning.

---

## 9. Snabb referens: kommandon

```bash
pip install -r requirements.txt
python scripts/python/download_data.py --nmd-confirm   # om du vill hämta NMD m.m.
python scripts/python/compute_indices.py
python scripts/python/hotspot_model.py
python scripts/python/generate_showcase.py
```

**Öppna observationer:** `python scripts/python/fetch_public_observations.py` — **GBIF** (standard, ingen nyckel) och/eller **SLU SOS** (`--source sos` / `both`) med `SOS_API_BASE` och `SOS_SUBSCRIPTION_KEY` i `.env`. SOS använder `output.fieldSet: Extended` för taxonomi (kingdom–genus m.m.). Sparar `gbif_<AOI>.gpkg` / `sos_<AOI>.gpkg`. Enkel paging begränsar SOS till ca 10 000 poster per körning.

**Rödlista-CSV:** `python scripts/python/fetch_swedish_redlist_gbif.py` — standard **Rödlista 2025** från ResearchData (`Rodlistade_arter_2025.csv`); `--edition 2020` ger GBIF DwC som tidigare. Skriver `data/raw/arter/rodlista/rodlista.csv` + `rodlista_version.txt`.

**Fas A (overlay):** `species_overlay_a.py --obs … --rodlista …` — se [`SPECIES_RODLISTA.md`](SPECIES_RODLISTA.md).

**Skyddad natur:** `python scripts/python/download_data.py --protected-sites-only` (eller `--protected-sites` tillsammans med övriga nedladdningar).

**SLU Skogskarta + kol (stort, ofta på `E:/slu_gis`):**  
`python scripts/python/download_data.py --slu-forest-map-confirm` och/eller `--slu-carbon-confirm` (ev. `--slu-forest-map-only` / `--slu-carbon-only`). Se §3.

**Naturlager (nyckelbiotoper/naturkultur/sumpskog/naturvårdsavtal):**  
`python scripts/python/download_nature_layers.py` (valfritt `--overwrite`).

**Validering mot kommunal GPKG:** `python scripts/python/validate_against_gpkg.py` — valfritt `--hotspot sökväg`, `--no-figure`.

**Jämför två klassraster (A/B):** kopiera referens `…_hotspot_class_ref.tif`, kör om pipelinen, sedan `python scripts/python/compare_hotspot_runs.py --ref sökväg`.

**Benchmark baslinje vs naturlager:**

```bash
# Baslinje utan naturlager
set NATURE_LAYER_BONUSES=0
python scripts/python/compute_indices.py
python scripts/python/hotspot_model.py
copy outputs\\rasters\\kungsbacka_vastra_hotspot_class.tif outputs\\rasters\\kungsbacka_vastra_hotspot_class_ref.tif

# Förstärkt med naturlager
set NATURE_LAYER_BONUSES=1
python scripts/python/download_nature_layers.py
python scripts/python/compute_indices.py
python scripts/python/hotspot_model.py
python scripts/python/compare_hotspot_runs.py --ref outputs\\rasters\\kungsbacka_vastra_hotspot_class_ref.tif
```

Om något steg saknar data, läs terminalutskriften – den anger vilken **källa** som användes (SLU, NMD, Skogsstyrelsen, fallback …).

---

## 10. Senast genomfört (kort intern logg)

- **SLU Skogskarta 2018** (andelar) och **SLU kol 2023** (.tif efter zip) integrerade i `compute_indices.py` (struktur + fukt) när filer finns; kataloger styrs av **`SLU_GIS_LARGE_ROOT`** (standard **`E:/slu_gis`** om `E:` finns).
- **`download_data.py`:** `--slu-forest-map-confirm`, `--slu-carbon-confirm`, tillhörande `*-only`-lägen och `--slu-gis-overwrite`.
- **`validate_against_gpkg.py`:** `--hotspot`, `--no-figure`; återanvändbar utvärdering för jämförelseskript.
- **`compare_hotspot_runs.py`:** GPKG-metrics + pixelöverensstämmelse + delta mellan referens och aktuell raster.
- **`generate_showcase.py`:** CRS-fallback via `AOI_BBOX`, skyddad natur med reprojektion till EPSG:3006, västkust-orter i inset vid behov.
- **`docs/index.html`** m.m. uppdaterade för aktuellt AOI (Kungsbacka-exempel) och valideringsbild/siffror där det passerats in i arbetssessionen.

---

## 11. Nästa fas / fortsatt arbete (nästa gång)

1. **PROJ/pyproj på Windows** – åtgärda `proj.db`-varning så att `contextily`-inset och `pysheds`-TWI fungerar fullt ut (ren venv, uppdaterad `pyproj`/`rasterio`, eller `PROJ_LIB` mot en konsekvent PROJ-installation).
2. **Kalibrering SLU-lager** – efter att Skogskarta + kol laddats ner: kör `compare_hotspot_runs.py` mot sparad referens; justera blend-vikter (8 % / 12 %) eller välj endast `stock_dom` om SOC ger sidoeffekter.
3. **`validate_against_gpkg.py`** – CLI för explicit GPKG-path och lagernamn (istället för första filen/första lagret).
4. **Skogsdatalabbet / fler SLU-lager** – utvärdera om ytterligare öppna raster ska in i samma A/B-mönster innan de läggs i huvudvikten.
5. **PIPELINE_METOD §5 tabell** – uppdatera valideringstal mot vald referens (Djupedal vs Kungsbacka) så tabellen inte står i strid med kod/körning.
6. **Nedladdning** – valfritt: rensa bort nedladdad `.zip` för kol efter lyckad uppackning; valfritt stöd för partiell hämtning endast inom AOI (kräver WCS/COG — större utvecklingsinsats).

---

## Bilaga A – Vad betyder «adel» och «ekovradel» i koden?

I `compute_indices.py` används **fördefinierade filnamnsmönster** mot NMD 2023 **Trädslag** (tilläggsskikt). Namnen kommer från hur **Naturvårdsverket** namnger GeoTIFF:er i nedladdningspaketet – de är alltså **produkt-/kartnamn**, inte generiska svenska ord.

| Mönster i koden | Typisk innebörd i NMD-sammanhang |
|-----------------|-----------------------------------|
| **`*adel*`** | Skikt som beskriver **ädellöv** – träd som i svensk skogsekologi ofta räknas som «ädellöv» (t.ex. ek, bok, ask m.fl. beroende på hur lagret är uppdelat i just din produktversion). |
| **`*bok*`** | **Bok** som eget täckningsskikt (om det finns som fil i din dataleverans). |
| **`*ekovradel*`** | Sammansatt namn i linje med **«ek och övrig ädellöv»** – alltså täckning där **ek** och **övrig ädellöv** hanteras som ett gemensamt skikt i den här produkten. |

**Exakt klassindelning och metadata** finns i Naturvårdsverkets dokumentation för **NMD2023 Trädslag** – använd den som sanning källa om du ska citera vetenskapligt. I pipelinen är syftet enkelt: **högre strukturindex** när pixeln har mer **ädellöv**/**ek**-relaterad täckning enligt dessa raster.

---

## Bilaga B – Hur hänger Google Earth Engine ihop med analysen?

1. **`scripts/gee/01_nvi_screening.js`**  
   Fristående skript som du klistrar in i **GEE Code Editor**. Det är **inte** en del av den vanliga Python-körningen. Det exporterar egna raster (t.ex. Sentinel-baserade) till Drive som du sedan kan lägga i projektet.

2. **`data/raw/gee_exports/` och `compute_indices.py`**  
   Om det finns minst en fil som matchar **`*Delindex*.tif`** används den **före NMD** i **strukturindex** (band **1** tolkas som NDVI-struktur, normaliserat).  
   Om **Skogsstyrelsen** saknas kan samma stack användas för **kontinuitet**: om GeoTIFF har **minst 5 band** används band **4–5** (median- och std-NDVI) till en **stabilitetsproxy** (lägre tidsvariabilitet → högre kontinuitet).

**Sammanfattning:** Python-pipelinen kör **inte** GEE. Den kan **läsa färdigexporterade** GeoTIFF från mappen `gee_exports` om du skapat dem (via GEE-skriptet eller annat).

---

## Bilaga C – Används Lantmäteriets STAC (samma katalog som STAC Browser)?

**Ja, samma datamängd** som du bläddrar i [STAC Browser mot `api.lantmateriet.se/stac-hojd/v1`](https://radiantearth.github.io/stac-browser/#/external/api.lantmateriet.se/stac-hojd/v1/?.language=en) är den katalog som `download_data.py` anropar via **STAC Search API**:

- I koden: `POST https://api.lantmateriet.se/stac-hojd/v1/search` med AOI som **bbox** (WGS84), sedan nedladdning av länkade **COG**-filer till `data/raw/lantmateriet/`.
- **STAC Browser** är bara ett **webb-UI** mot samma API; `compute_indices.py` läser sedan lokala **`.tif`** i `LM_DIR` och bygger **TWI** (fuktindex).

Officiell API-beskrivning: [Lantmäteriet STAC höjd API](https://api.lantmateriet.se/stac-hojd/v1/api.html).

**OBS:** Upplösning i koden/kommentarer anges ofta som **2 m** (GSD-höjdmodell); showcase-text kan säga **1 m** – kontrollera alltid **faktisk produktmetadata** för de brickor du laddar ner.

---

## Artdata, Rödlistan och skyddad natur

**Fas A (överlagring)** och hämtning av observationer/rödlista är **implementerade**; metod, mappar och datakällor finns i **[`SPECIES_RODLISTA.md`](SPECIES_RODLISTA.md)**. Plan för senare faser (ev. `species_relevance`-raster, objektnivå) står kvar i samma dokument.

Skyddad natur (WFS) och showcase-figur `hotspot_protected_context.png` beskrivs i §3 och §6 ovan.
