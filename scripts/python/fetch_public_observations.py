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
    - Om nyckel saknas hoppas SOS över (GBIF räcker ofta)

Kör:
  python scripts/python/fetch_public_observations.py
  python scripts/python/fetch_public_observations.py --year-from 2015 --max-records 5000
  python scripts/python/fetch_public_observations.py --source gbif

Sedan:
  python scripts/python/species_overlay_a.py --obs data/raw/arter/observations/gbif_<AOI>.gpkg --rodlista ...
"""

from __future__ import annotations

import argparse
import json
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


def aoi_polygon_wkt() -> str:
    """WGS84, lon lat — stängd ring."""
    lo, la_min = AOI_BBOX["min_lon"], AOI_BBOX["min_lat"]
    hi, la_max = AOI_BBOX["max_lon"], AOI_BBOX["max_lat"]
    return (
        f"POLYGON (({lo} {la_min}, {hi} {la_min}, {hi} {la_max}, {lo} {la_max}, {lo} {la_min}))"
    )


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


def write_metadata(path: Path, source: str, fetched: int, total_gbif: int, extra: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"Källa: {source}",
        f"AOI: {AOI_NAME}",
        f"Bbox WGS84: {AOI_BBOX}",
        f"Hämtad: {datetime.now(timezone.utc).isoformat()}Z",
        f"Poster sparade: {fetched}",
        f"GBIF count (samma filter): {total_gbif}",
        "",
        "Citera GBIF: https://www.gbif.org/citation-guidelines",
        extra,
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Hämta observationer för AOI (GBIF + valfritt SOS)")
    ap.add_argument(
        "--source",
        choices=["gbif", "both"],
        default="gbif",
        help="gbif = bara GBIF; both = GBIF + SOS om nycklar finns",
    )
    ap.add_argument("--year-from", type=int, default=None)
    ap.add_argument("--year-to", type=int, default=None)
    ap.add_argument("--country", default="SE", help="GBIF landsfilter (ISO 3166-1 alpha-2)")
    ap.add_argument(
        "--max-records",
        type=int,
        default=25_000,
        help="Max antal poster att hämta (GBIF rekommenderar download-API vid mycket stora volymer)",
    )
    ap.add_argument("--sleep", type=float, default=0.35, help="Paus mellan GBIF-sidor (sek)")
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Utdata GeoPackage (standard: data/raw/arter/observations/gbif_<AOI>.gpkg)",
    )
    args = ap.parse_args()

    ARTER_OBS_DIR.mkdir(parents=True, exist_ok=True)
    out = args.out or (ARTER_OBS_DIR / f"gbif_{AOI_NAME}.gpkg")

    if args.source not in ("gbif", "both"):
        sys.exit("Endast gbif eller both stöds.")

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
    gdf.to_file(out, driver="GPKG", layer="occurrences")
    print(f"  [ok] {out}")

    meta = ARTER_OBS_DIR / f"gbif_{AOI_NAME}_metadata.txt"
    write_metadata(meta, "GBIF occurrence/search", len(gdf), total)
    print(f"  [ok] {meta}")

    if args.source == "both":
        import os

        base = os.environ.get("SOS_API_BASE", "").strip().rstrip("/")
        key = os.environ.get("SOS_SUBSCRIPTION_KEY", "").strip()
        if not base or not key:
            print(
                "\n[SOS] Hoppar över — sätt SOS_API_BASE och SOS_SUBSCRIPTION_KEY "
                "(se https://api-portal.artdatabanken.se/)."
            )
        else:
            print(
                "\n[SOS] Automatisk klient är inte inbyggd här (kräver din bas-URL från portalen). "
                "Använd GBIF-exporten ovan eller POST /Observations/Search enligt "
                "https://github.com/biodiversitydata-se/SOS/blob/master/Docs/Endpoints.md"
            )

    print("\n[klar] Kör species_overlay_a.py --obs", out, "--rodlista …")


if __name__ == "__main__":
    main()
