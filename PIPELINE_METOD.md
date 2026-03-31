# NVI-pipelinen: vad skripten gör och vad som produceras

Det här dokumentet är ett **internt metodstöd** – det publiceras inte på GitHub Pages. Det beskriver logiken i Python-flödet från rådata till hotspot-raster och showcase-bilder.

---

## 1. Översikt

Pipelinen gör följande i ordning:

1. **`download_data.py`** – hämtar (eller förbereder för manuell) geodata inom **AOI** (area of interest), definierad i `config.py`.
2. **`compute_indices.py`** – klipper raster till AOI, bygger **tre delindex** (struktur, kontinuitet, fukt), normaliserar dem till ungefär **0–1**, viktar ihop dem till **NVI-poäng** (0–1).
3. **`hotspot_model.py`** – delar in NVI-poäng i **tre prioritetsklasser** med percentiler, sparar klassraster och en diagnostisk PNG i `outputs/`.
4. **`generate_showcase.py`** – bygger **publika figurer** till `docs/assets/` för sidan (kräver att steg 2–3 körts, samt nätverk för översiktskarta).

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
- **Sökvägar** – var NMD ligger (ofta extern disk, t.ex. `E:/nmd`), var nedladdningar hamnar (`data/raw/...`), processade raster (`data/processed/`), figurer (`outputs/figures/`), slutliga klassraster (`outputs/rasters/`).
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

Skriptet transformerar **AOI** till **SWEREF99 TM** där det behövs för att beställa/hämta data som levereras i nationellt grid.

**Resultat:** Filer på disk – inga sammanslagna index ännu. Utan relevanta filer hoppar `compute_indices.py` till fallback-kedjor eller avslutar med fel/meddelande.

---

## 4. `compute_indices.py` – under huven per delindex

All bearbetning sker **inom AOI**. Raster **maskas** (klipps) med Shapely-box i rätt CRS. Värden **normaliseras** oftast med percentil 2–98 till intervallet **\[0, 1\]** (`normalize`), så extrema outliers dämpas.

### 4.1 Strukturindex (prioritetsordning)

1. **SLU** – om `*biomassa*.tif` finns: biomassa normaliseras; ev. höjdskikt viktas in (60 % / 40 %). **Stannar här** om data finns.
2. **GEE-export** – om fil matchar `*Delindex*.tif` i `data/raw/gee_exports/`: band 1 tolkas som NDVI-struktur, normaliseras. **Stannar här** om data finns.
3. **NMD** – om basskikt-filen finns:
   - Skogsklasser 1–3 → skogsmask.
   - **Lokal heterogenitet:** glidande standardavvikelse (7×7) på skogsmasken → proxy för “fragmentering/kant”.
   - Bas: `0,5 × skog + 0,2 × norm(textur)` (klippt till \[0,1\] i slutet).
   - **Trädslag:** ädellövskikt (adel/bok/ekovradel) ger **+0,20 × täckning**; trivial löv **+0,08**.
   - **Objekthöjd:** normaliserad höjd ger **+0,10 × värde**.
4. **Hansen Global Forest Change** – `treecover2000` i `data/raw/hansen/`: täckning + textur, normaliserat. Sista utväg innan fel.

**Metodidé:** Högt strukturindex ≈ mer skogsmässig “kvalitet” i bred bemärkelse (biomassa/höjd eller NMD-proxy för gammal/komplex skog med ädellöv och höjd).

### 4.2 Kontinuitetsindex

1. **Skogsstyrelsen** – raster där **1 = avverkad/störd**, **0 = ostört**:
   - `continuity = 1 − störning`, sedan **mjuk kant** kring störning (uniform filter): pixlar nära avverkning får sänkt kontinuitet (×0,6 där bufferten är tydlig).
2. **GEE Delindex** – om minst 5 band: NDVI-standardavvikelse m.m. omvandlas till en stabilitetsproxy (lägre variabilitet → högre kontinuitet).
3. Annars **konstant 0,5** (neutralt) med varning.

**Metodidé:** Högt index ≈ ytor som sällan eller aldrig registrerats som avverkade i datasetet (och inte ligger i “påverkanszon” kring dem).

### 4.3 Fuktindex

1. **Lantmäteriet DEM** – gradienter → **TWI** (Topographic Wetness Index, förenklad flödesackumulation / lutning). Normaliseras till \[0,1\]. Om NMD basskikt finns: **våtmarksklass** blandas in (70 % TWI + 30 % våtmarksmask).
2. **Bara NMD** – om ingen DEM: våtmark + liten offset som fuktproxy.
3. **Copernicus DEM** i `data/raw/dem/` – TWI med grovare upplösning (30 m cell antaget).
4. Annars **konstant 0,5** med varning.

**Metodidé:** Högt index ≈ topografiskt fuktigare lägen (och ev. kartlagd våtmark).

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

**Klassificering (percentilbaserad):**

- Bara pixlar med **poäng > 0** räknas in i percentilberäkningen.
- **p33** och **p67** beräknas på dessa värden.
- Regel (kumulativt högre klass vid högre poäng):
  - Poäng > 0 → minst klass **1** (låg prioritet).
  - Poäng > p33 → klass **2** (mellanklass).
  - Poäng > p67 → klass **3** (hotspot).
- Pixlar med poäng 0 blir **0** (nodata för klasser).

**Arealstatistik** skrivs till terminal (ungefärlig hektar baserat på pixelstorlek från transform, fallback 30 m om något är konstigt).

**Utdata:**

| Var | Fil | Innehåll |
|-----|-----|----------|
| `outputs/rasters/` | `{AOI}_hotspot_class.tif` | Uint8, 0–3, nodata 0 – **det som används vidare** |
| `outputs/figures/` | `{AOI}_hotspot_map.png` | Snabb översikt: klasskarta + NVI-poäng + tre delindex |

---

## 6. `generate_showcase.py` – bilder till webbsidan

**Indata:** Processade index + `outputs/rasters/{AOI}_hotspot_class.tif`.

- Bygger **`docs/assets/hotspot_showcase.png`** – stor klasskarta, fyra paneler (NVI-poäng + tre index), översikts-inset med webbkarta och AOI-ram (kräver **contextily** + nätverk).
- Bygger **`docs/assets/method_diagram.png`** – sexstegs processfigur.

Ändrar du metodtexter eller layout här ska du committa de genererade PNG:erna om sidan ska uppdateras.

---

## 7. Dataflöde (textdiagram)

```
config.py (AOI, vikter, sökvägar)
        │
        ▼
download_data.py  →  data/raw/ …  (+ ev. NMD på extern disk)
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
- **Ingen formell NVI-klass 1–4** enligt handbok – utdata är **tre interna prioritetsklasser** tänkta som stöd för sampling (kan i ett senare steg översättas till er egen klassning).
- **Tolkning av index** är modellbaserad: resultat beror på datakvalitet, upplösning och fallback-val. Percentiltrösklar (**p33/p67**) är **relativa inom AOI**, inte absoluta ekologiska gränser.

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

**Fas A (art + Rödlista, valfritt):** `python scripts/python/species_overlay_a.py --obs … --rodlista …` — se [`SPECIES_RODLISTA.md`](SPECIES_RODLISTA.md).

Om något steg saknar data, läs terminalutskriften – den anger vilken **källa** som användes (SLU, NMD, Skogsstyrelsen, fallback …).

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

## Planerad utökning: artdata och Rödlistan

För en **stegvis roadmap** (överlagring → ev. delindex → objektnivå), mappförslag `data/raw/arter/`, datakällor och versionshantering av Rödlistan, se **[`SPECIES_RODLISTA.md`](SPECIES_RODLISTA.md)** i repots rot.
