"""
fetch_public_observations.py
Hämtar artobservationer för AOI (config.py) från öppna API:er.

  GBIF (standard, ingen nyckel)
    - GET https://api.gbif.org/v1/occurrence/search
    - Inkluderar bl.a. dataset som härrör från Artportalen (datasetName "Artportalen")
    - Skyddade/koordinatförsedda poster följer GBIF:s och datavärdarnas regler
    - Använd User-Agent enligt GBIF:s rekommendation

  SLU SOS (valfritt)
    - Kräver gratis produktprenumeration + bas-URL + nyckel från
      https://api-portal.artdatabanken.se/ (header Ocp-Apim-Subscription-Key)
    - Sätt miljövariabler SOS_API_BASE och SOS_SUBSCRIPTION_KEY
    - Sökningen använder output.fieldSet Extended så kingdom–genus (och species-epitet) fylls i
    - Om nyckel saknas hoppas SOS över (GBIF räcker ofta)

Kör:
  python scripts/python/fetch_public_observations.py
  python scripts/python/fetch_public_observations.py --year-from 2015 --max-records 5000
  python scripts/python/fetch_public_observations.py --source gbif
  python scripts/python/fetch_public_observations.py --source sos
  python scripts/python/fetch_public_observations.py --source both

SOS: sätt SOS_API_BASE och SOS_SUBSCRIPTION_KEY i .env (config.py laddar .env automatiskt).

Sedan:
  python scripts/python/species_overlay_a.py --obs data/raw/arter/observations/gbif_<AOI>.gpkg --rodlista ...
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).parent))
from config import AOI_BBOX, AOI_NAME, ARTER_OBS_DIR

GBIF_OCCURRENCE_SEARCH = "https://api.gbif.org/v1/occurrence/search"
GBIF_PAGE_LIMIT = 300  # GBIF max per request
USER_AGENT = "nvi-pipeline/1.0 (+https://github.com/ulfboge/nvi)"

# SOS: POST body + query skip/take (se biodiversitydata-se/SOS ObservationsController)
SOS_SEARCH_PATH = "/Observations/Search"
SOS_TAKE_MAX = 1000  # max take per anrop enligt API
# Varje begäran måste uppfylla skip + take <= detta (konfigurerbart i SOS; typiskt 10 000)
SOS_SKIP_PLUS_TAKE_MAX = 10_000


def aoi_polygon_wkt() -> str:
    """WGS84, lon lat — stängd ring."""
    lo, la_min = AOI_BBOX["min_lon"], AOI_BBOX["min_lat"]
    hi, la_max = AOI_BBOX["max_lon"], AOI_BBOX["max_lat"]
    return (
        f"POLYGON (({lo} {la_min}, {hi} {la_min}, {hi} {la_max}, {lo} {la_max}, {lo} {la_min}))"
    )


def _sos_bbox_filter() -> dict:
    """SOS boundingBox: topLeft = NW, bottomRight = SE (WGS84)."""
    lo, la_min = AOI_BBOX["min_lon"], AOI_BBOX["min_lat"]
    hi, la_max = AOI_BBOX["max_lon"], AOI_BBOX["max_lat"]
    return {
        "geographics": {
            "boundingBox": {
                "topLeft": {"latitude": la_max, "longitude": lo},
                "bottomRight": {"latitude": la_min, "longitude": hi},
            }
        }
    }


def _sos_search_body(
    *,
    year_from: int | None,
    year_to: int | None,
) -> dict:
    body: dict = _sos_bbox_filter()
    # Extended: rik taxonomi (kingdom … genus) enligt SLU FieldSets.md; Minimum lämnar dessa tomma.
    body["output"] = {"fieldSet": "Extended"}
    if year_from is not None or year_to is not None:
        y0 = year_from if year_from is not None else 1500
        y1 = year_to if year_to is not None else 2100
        body["date"] = {
            "startDate": f"{y0}-01-01",
            "endDate": f"{y1}-12-31",
            "dateFilterType": "OverlappingStartDateAndEndDate",
        }
    return body


def _dig(d: object, *keys: str) -> object | None:
    cur: object = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _sos_taxon_text(tax: dict | None, key: str) -> str | None:
    """SOS taxonfält som sträng eller { id, value } (vokabulär)."""
    if not isinstance(tax, dict):
        return None
    v = tax.get(key)
    if v is None:
        return None
    if isinstance(v, dict) and "value" in v:
        s = str(v.get("value", "")).strip()
        return s or None
    s = str(v).strip()
    return s or None


def _infer_specific_epithet(scientific_name: str | None, genus: str | None) -> str | None:
    """Art-epitet från vetenskapligt namn när API inte skickar separat species-fält (före auktor-komma)."""
    if not scientific_name:
        return None
    head = str(scientific_name).split(",", 1)[0].strip()
    parts = head.split()
    if len(parts) < 2:
        return None
    if genus:
        if parts[0].lower() == genus.strip().lower():
            return parts[1]
    return parts[1]


def fetch_sos(
    base_url: str,
    subscription_key: str,
    *,
    year_from: int | None,
    year_to: int | None,
    max_records: int,
    sleep_s: float,
) -> tuple[list[dict], int]:
    """
    POST …/Observations/Search med skip/take i query.
    Returnerar (records, total_count). Enkel paging stannar vid API-gräns skip+take<=SOS_SKIP_PLUS_TAKE_MAX.
    """
    base = base_url.strip().rstrip("/")
    url_base = base + SOS_SEARCH_PATH
    headers = {
        "Ocp-Apim-Subscription-Key": subscription_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
        "X-Requesting-System": "nvi-pipeline",
    }
    body = _sos_search_body(year_from=year_from, year_to=year_to)
    all_rows: list[dict] = []
    skip = 0
    total_reported = 0

    while len(all_rows) < max_records:
        take = min(SOS_TAKE_MAX, max_records - len(all_rows))
        if take <= 0:
            break
        if skip + take > SOS_SKIP_PLUS_TAKE_MAX:
            take = SOS_SKIP_PLUS_TAKE_MAX - skip
            if take <= 0:
                break
        params = {"skip": skip, "take": take}
        url = url_base + "?" + urllib.parse.urlencode(params)
        for attempt in range(5):
            try:
                r = requests.post(url, headers=headers, json=body, timeout=180)
                if r.status_code == 429:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                if r.status_code == 401:
                    raise SystemExit(
                        "SOS 401 — kontrollera SOS_SUBSCRIPTION_KEY (prenumerationsnyckel i portalen)."
                    )
                if r.status_code == 404:
                    raise SystemExit(
                        "SOS 404 — kontrollera SOS_API_BASE (ska peka på API-roten, t.ex. …/sos/v1 utan /Observations/Search)."
                    )
                r.raise_for_status()
                break
            except requests.RequestException as e:
                if attempt == 4:
                    raise SystemExit(f"SOS-fel efter retries: {e}") from e
                time.sleep(1.5 * (attempt + 1))
        data = r.json()
        total_reported = int(data.get("totalCount") or data.get("TotalCount") or 0)
        batch = data.get("records") or data.get("Records") or []
        if not batch:
            break
        need = max_records - len(all_rows)
        all_rows.extend(batch[:need])
        if len(batch) < take or len(all_rows) >= max_records:
            break
        skip += take
        if skip >= SOS_SKIP_PLUS_TAKE_MAX:
            break
        time.sleep(sleep_s)

    return all_rows, total_reported


def sos_records_to_geodataframe(records: list[dict]) -> gpd.GeoDataFrame:
    rows = []
    for o in records:
        if not isinstance(o, dict):
            continue
        loc = _dig(o, "location")
        lat = _dig(loc, "decimalLatitude") if isinstance(loc, dict) else None
        lon = _dig(loc, "decimalLongitude") if isinstance(loc, dict) else None
        if lat is None or lon is None:
            continue
        tax = _dig(o, "taxon")
        tax_d = tax if isinstance(tax, dict) else None
        sci = _sos_taxon_text(tax_d, "scientificName") if tax_d else None
        genus = _sos_taxon_text(tax_d, "genus") if tax_d else None
        species_ep = None
        if tax_d:
            for k in ("species", "specificEpithet", "specific_epithet"):
                if tax_d.get(k) is not None:
                    species_ep = _sos_taxon_text(tax_d, k)
                    break
        if not species_ep:
            species_ep = _infer_specific_epithet(sci, genus)
        ev = _dig(o, "event")
        ed = None
        if isinstance(ev, dict):
            ed = ev.get("startDate") or ev.get("plainStartDate")
        occ = _dig(o, "occurrence")
        occ_id = _dig(occ, "occurrenceId") if isinstance(occ, dict) else None
        bor = _dig(o, "basisOfRecord")
        if isinstance(bor, dict):
            bor = bor.get("value")
        rows.append(
            {
                "decimalLongitude": float(lon),
                "decimalLatitude": float(lat),
                "scientific_name": sci,
                "eventDate": ed,
                "kingdom": _sos_taxon_text(tax_d, "kingdom"),
                "phylum": _sos_taxon_text(tax_d, "phylum"),
                "class": _sos_taxon_text(tax_d, "class"),
                "order": _sos_taxon_text(tax_d, "order"),
                "family": _sos_taxon_text(tax_d, "family"),
                "genus": genus,
                "species": species_ep,
                "datasetName": o.get("datasetName"),
                "basisOfRecord": bor,
                "occurrenceID": occ_id,
                "gbifID": "",
                "license": None,
                "country": "SE",
                "source": "SOS",
            }
        )
    if not rows:
        sys.exit("Inga poster med koordinater i SOS-svaret.")
    df = pd.DataFrame(rows)
    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["decimalLongitude"], df["decimalLatitude"]),
        crs="EPSG:4326",
    )
    return gdf


def fetch_gbif(
    *,
    year_from: int | None,
    year_to: int | None,
    country: str,
    max_records: int,
    sleep_s: float,
) -> tuple[list[dict], int]:
    """Returnerar (records, total_count_enligt_gbif)."""
    geom = aoi_polygon_wkt()
    params_base: dict = {
        "geometry": geom,
        "country": country,
        "limit": GBIF_PAGE_LIMIT,
    }
    if year_from is not None or year_to is not None:
        y0 = year_from if year_from is not None else 1500
        y1 = year_to if year_to is not None else 2100
        params_base["year"] = f"{y0},{y1}"

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    all_rows: list[dict] = []
    offset = 0
    total_reported = 0

    while len(all_rows) < max_records:
        params = {**params_base, "offset": offset}
        url = GBIF_OCCURRENCE_SEARCH + "?" + urllib.parse.urlencode(params)
        for attempt in range(5):
            try:
                r = requests.get(url, headers=headers, timeout=120)
                if r.status_code == 429:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                r.raise_for_status()
                break
            except requests.RequestException as e:
                if attempt == 4:
                    raise SystemExit(f"GBIF-fel efter retries: {e}") from e
                time.sleep(1.5 * (attempt + 1))
        data = r.json()
        total_reported = int(data.get("count", 0))
        batch = data.get("results", [])
        if not batch:
            break
        need = max_records - len(all_rows)
        all_rows.extend(batch[:need])
        if data.get("endOfRecords") or len(batch) < GBIF_PAGE_LIMIT:
            break
        offset += GBIF_PAGE_LIMIT
        time.sleep(sleep_s)

    return all_rows, total_reported


def gbif_results_to_geodataframe(records: list[dict]) -> gpd.GeoDataFrame:
    rows = []
    for o in records:
        lat, lon = o.get("decimalLatitude"), o.get("decimalLongitude")
        if lat is None or lon is None:
            continue
        ed = o.get("eventDate")
        if not ed and o.get("year") is not None:
            y = int(o["year"])
            m = int(o["month"]) if o.get("month") is not None else 1
            d = int(o["day"]) if o.get("day") is not None else 1
            ed = f"{y}-{m:02d}-{d:02d}"
        rows.append(
            {
                "decimalLongitude": float(lon),
                "decimalLatitude": float(lat),
                "scientific_name": o.get("scientificName") or o.get("acceptedScientificName"),
                "eventDate": ed,
                "kingdom": o.get("kingdom"),
                "phylum": o.get("phylum"),
                "class": o.get("class"),
                "order": o.get("order"),
                "family": o.get("family"),
                "genus": o.get("genus"),
                "species": o.get("species"),
                "datasetName": o.get("datasetName"),
                "basisOfRecord": o.get("basisOfRecord"),
                "occurrenceID": o.get("occurrenceID"),
                "gbifID": str(o.get("key", "")),
                "license": o.get("license"),
                "country": o.get("country"),
            }
        )
    if not rows:
        sys.exit("Inga poster med koordinater i GBIF-svaret.")
    df = pd.DataFrame(rows)
    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["decimalLongitude"], df["decimalLatitude"]),
        crs="EPSG:4326",
    )
    return gdf


def write_metadata(
    path: Path,
    source: str,
    fetched: int,
    total_matching: int,
    extra_lines: list[str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"Källa: {source}",
        f"AOI: {AOI_NAME}",
        f"Bbox WGS84: {AOI_BBOX}",
        f"Hämtad: {datetime.now(timezone.utc).isoformat()}Z",
        f"Poster sparade: {fetched}",
        f"Totalt enligt API (samma filter): {total_matching}",
    ]
    if extra_lines:
        lines.extend(extra_lines)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Hämta observationer för AOI (GBIF och/eller SLU SOS)")
    ap.add_argument(
        "--source",
        choices=["gbif", "sos", "both"],
        default="gbif",
        help="gbif | sos | both (both kräver SOS-nycklar för SOS-delen)",
    )
    ap.add_argument("--year-from", type=int, default=None)
    ap.add_argument("--year-to", type=int, default=None)
    ap.add_argument("--country", default="SE", help="GBIF landsfilter (ISO 3166-1 alpha-2)")
    ap.add_argument(
        "--max-records",
        type=int,
        default=25_000,
        help="Max antal poster per källa (SOS: enkel paging max ca 10 000 p.g.a. skip+take-gräns)",
    )
    ap.add_argument(
        "--sleep",
        type=float,
        default=0.35,
        help="Paus mellan sidor/requests (GBIF och SOS)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="GBIF GeoPackage (standard: data/raw/arter/observations/gbif_<AOI>.gpkg)",
    )
    ap.add_argument(
        "--out-sos",
        type=Path,
        default=None,
        help="SOS GeoPackage (standard: data/raw/arter/observations/sos_<AOI>.gpkg)",
    )
    args = ap.parse_args()

    ARTER_OBS_DIR.mkdir(parents=True, exist_ok=True)
    out_gbif = args.out or (ARTER_OBS_DIR / f"gbif_{AOI_NAME}.gpkg")
    out_sos = args.out_sos or (ARTER_OBS_DIR / f"sos_{AOI_NAME}.gpkg")

    if args.source in ("gbif", "both"):
        print("\n[GBIF] Hämtar occurrence/search inom AOI-polygon …")
        records, total = fetch_gbif(
            year_from=args.year_from,
            year_to=args.year_to,
            country=args.country,
            max_records=args.max_records,
            sleep_s=args.sleep,
        )
        print(f"  GBIF uppger {total} träffar; sparar {len(records)} (max {args.max_records}).")
        if total > len(records):
            print(
                "  [info] Färre poster sparade än totala träffar — höj --max-records eller "
                "använd GBIF Download API för bulk."
            )

        gdf = gbif_results_to_geodataframe(records)
        gdf.to_file(out_gbif, driver="GPKG", layer="occurrences")
        print(f"  [ok] {out_gbif}")

        meta = ARTER_OBS_DIR / f"gbif_{AOI_NAME}_metadata.txt"
        write_metadata(
            meta,
            "GBIF occurrence/search",
            len(gdf),
            total,
            extra_lines=["", "Citera GBIF: https://www.gbif.org/citation-guidelines"],
        )
        print(f"  [ok] {meta}")

    sos_written = False
    if args.source in ("sos", "both"):
        base = os.environ.get("SOS_API_BASE", "").strip().rstrip("/")
        key = os.environ.get("SOS_SUBSCRIPTION_KEY", "").strip()
        if not base or not key:
            if args.source == "sos":
                sys.exit(
                    "SOS kräver SOS_API_BASE och SOS_SUBSCRIPTION_KEY i miljö eller .env — "
                    "se https://api-portal.artdatabanken.se/"
                )
            print(
                "\n[SOS] Hoppar över — sätt SOS_API_BASE och SOS_SUBSCRIPTION_KEY i .env "
                "(profil → prenumerationer i utvecklarportalen)."
            )
        else:
            print("\n[SOS] Hämtar Observations/Search inom AOI-bounding box …")
            sos_records, sos_total = fetch_sos(
                base,
                key,
                year_from=args.year_from,
                year_to=args.year_to,
                max_records=args.max_records,
                sleep_s=args.sleep,
            )
            print(
                f"  SOS uppger {sos_total} träffar; sparar {len(sos_records)} "
                f"(max {args.max_records}, paging take<={SOS_TAKE_MAX})."
            )
            if sos_total > len(sos_records):
                print(
                    f"  [info] Färre poster än totala träffar. "
                    f"Enkel skip/take-paging kan max nå {SOS_SKIP_PLUS_TAKE_MAX} poster; "
                    "för mer använd Export/SearchByCursor i API:et."
                )
            gdf_s = sos_records_to_geodataframe(sos_records)
            gdf_s.to_file(out_sos, driver="GPKG", layer="occurrences")
            print(f"  [ok] {out_sos}")
            meta_s = ARTER_OBS_DIR / f"sos_{AOI_NAME}_metadata.txt"
            write_metadata(
                meta_s,
                "SLU SOS POST /Observations/Search",
                len(gdf_s),
                sos_total,
                extra_lines=["", "Portal: https://api-portal.artdatabanken.se/"],
            )
            print(f"  [ok] {meta_s}")
            sos_written = True

    print("\n[klar] Koppla till rödlista m.m. med species_overlay_a.py --obs <gpkg> --rodlista …")
    if args.source in ("gbif", "both"):
        print(f"  GBIF: {out_gbif}")
    if sos_written:
        print(f"  SOS:  {out_sos}")


if __name__ == "__main__":
    main()
