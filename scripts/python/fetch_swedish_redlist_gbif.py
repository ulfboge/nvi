"""
Hämtar svenska rödlistan för pipeline (CSV till species_overlay_a.py). Ingen nyckel.

Standard (2025): ResearchData — Rodlistade_arter_2025.csv
  DOI https://doi.org/10.5878/2x1z-jm10
  Kolumner: Vetenskapligt_namn, Kategori (semicolon-CSV)

Alternativ (2020): GBIF DwC-arkiv (taxon + distribution)
  https://www.gbif.se/ipt/archive.do?r=swedishredlist2020

Skriver data/raw/arter/rodlista/rodlista.csv (+ rodlista_version.txt).

Kör:
  python scripts/python/fetch_swedish_redlist_gbif.py
  python scripts/python/fetch_swedish_redlist_gbif.py --edition 2020
"""

from __future__ import annotations

import argparse
import io
import sys
import zipfile
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).parent))
from config import ARTER_RODLISTA_DIR

USER_AGENT = "Mozilla/5.0 (compatible; nvi-pipeline/1.0; +https://github.com/ulfboge/nvi)"

# Rödlista 2025 (ResearchData / SND)
RESEARCHDATA_2025_CSV = (
    "https://api.researchdata.se/dataset/2026-63/1/file/data"
    "?filePath=Rodlistade_arter_2025.csv"
)
RESEARCHDATA_2025_DOI = "10.5878/2x1z-jm10"

# Rödlista 2020 (GBIF Sweden IPT)
DWC_ARCHIVE_URL_2020 = "https://www.gbif.se/ipt/archive.do?r=swedishredlist2020"
GBIF_DATASET_KEY_2020 = "23c0a6c4-f1f4-4577-ac5c-98787c1a2d0c"

_CODE_PRIORITY: dict[str, int] = {
    "EX": 6,
    "EW": 6,
    "RE": 5,
    "CR": 4,
    "EN": 3,
    "VU": 2,
    "NT": 1,
    "LC": 0,
    "DD": -1,
    "NE": -1,
}


def _http_get(url: str, timeout: int = 300) -> requests.Response:
    return requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})


def load_redlist_2025_csv(content: bytes) -> pd.DataFrame:
    df = pd.read_csv(
        io.BytesIO(content),
        sep=";",
        dtype=str,
        keep_default_na=False,
        na_values=[""],
        encoding="utf-8",
    )
    sp_col, cat_col = "Vetenskapligt_namn", "Kategori"
    if sp_col not in df.columns or cat_col not in df.columns:
        raise SystemExit(
            f"Oväntat format i Rodlistade_arter_2025.csv. Saknar {sp_col!r} eller {cat_col!r}. "
            f"Har: {list(df.columns)[:20]} …"
        )
    tax_col = "Taxonkategori_rödlistan"
    if tax_col in df.columns:
        df = df[df[tax_col].str.strip().str.lower() == "arter"]
    df = df.rename(columns={sp_col: "scientific_name", cat_col: "redlist_category"})
    df["scientific_name"] = df["scientific_name"].str.strip()
    df["redlist_category"] = df["redlist_category"].str.strip().str.upper()
    df = df[df["scientific_name"] != ""]
    df = df[df["redlist_category"] != ""]
    df = df[~df["redlist_category"].isin(["N/A", "NA", "—", "-"])]
    df["_p"] = df["redlist_category"].map(lambda c: _CODE_PRIORITY.get(str(c), -99))
    df = df.sort_values("_p").drop_duplicates(subset=["scientific_name"], keep="last")
    return df.drop(columns=["_p"])[["scientific_name", "redlist_category"]]


def load_redlist_2020_dwc(zip_bytes: bytes) -> pd.DataFrame:
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    tax = pd.read_csv(
        zf.open("taxon.txt"),
        sep="\t",
        dtype=str,
        keep_default_na=False,
        na_values=[""],
    )
    dist = pd.read_csv(
        zf.open("distribution.txt"),
        sep="\t",
        dtype=str,
        keep_default_na=False,
        na_values=[""],
    )
    m = dist.merge(
        tax[["id", "scientificName", "taxonomicStatus", "taxonRank"]],
        on="id",
        how="inner",
    )
    m = m[m["taxonomicStatus"].str.lower() == "accepted"]
    m = m[m["taxonRank"].str.lower() == "species"]
    if "threatStatus" not in m.columns:
        raise SystemExit("distribution.txt saknar kolumnen threatStatus — oväntat DwC-format.")
    m = m[m["threatStatus"].str.strip() != ""]
    m["_p"] = m["threatStatus"].str.upper().map(lambda c: _CODE_PRIORITY.get(c, -99))
    m = m.sort_values("_p").drop_duplicates(subset=["scientificName"], keep="last")
    out = m[["scientificName", "threatStatus"]].rename(
        columns={"scientificName": "scientific_name", "threatStatus": "redlist_category"}
    )
    out["redlist_category"] = out["redlist_category"].str.strip().str.upper()
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Hämta svenska rödlistan (2025 ResearchData eller 2020 GBIF DwC)")
    ap.add_argument(
        "--edition",
        choices=("2025", "2020"),
        default="2025",
        help="2025 = Rodlistade_arter_2025.csv (ResearchData); 2020 = GBIF IPT DwC-arkiv",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="CSV (standard: data/raw/arter/rodlista/rodlista.csv)",
    )
    args = ap.parse_args()
    out = args.out or (ARTER_RODLISTA_DIR / "rodlista.csv")
    out.parent.mkdir(parents=True, exist_ok=True)

    if args.edition == "2025":
        print("[ResearchData] Laddar Rodlistade_arter_2025.csv (Rödlista 2025) …")
        r = _http_get(RESEARCHDATA_2025_CSV, timeout=600)
        r.raise_for_status()
        df = load_redlist_2025_csv(r.content)
        ver_text = (
            "Källa: SLU Artdatabanken / ResearchData — Rödlistade arter i Sverige 2025 "
            "(med ekologiska variabler), fil Rodlistade_arter_2025.csv\n"
            f"DOI https://doi.org/{RESEARCHDATA_2025_DOI}\n"
            f"API {RESEARCHDATA_2025_CSV.split('?')[0]}?filePath=Rodlistade_arter_2025.csv\n"
            "Licens: CC0 1.0 (enligt dataportalen)\n"
        )
    else:
        print("[GBIF IPT] Laddar DwC-arkiv (The Swedish Red List 2020) …")
        r = _http_get(DWC_ARCHIVE_URL_2020, timeout=300)
        r.raise_for_status()
        df = load_redlist_2020_dwc(r.content)
        ver_text = (
            "Källa: GBIF DwC-arkiv The Swedish Red List 2020\n"
            f"datasetKey={GBIF_DATASET_KEY_2020}\n"
            f"archive={DWC_ARCHIVE_URL_2020}\n"
            "https://www.gbif.org/dataset/23c0a6c4-f1f4-4577-ac5c-98787c1a2d0c\n"
            "Citera GBIF: https://www.gbif.org/citation-guidelines\n"
        )

    df.to_csv(out, index=False, encoding="utf-8")
    print(f"  [ok] {len(df)} arter -> {out}")

    ver = out.parent / "rodlista_version.txt"
    ver.write_text(ver_text, encoding="utf-8")
    print(f"  [ok] {ver}")


if __name__ == "__main__":
    main()
