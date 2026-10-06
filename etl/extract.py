# reads the raw CSV file,
from __future__ import annotations

import io
import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger("extract")


RAW_ENCODING = "ISO-8859-1"

EXPECTED_COLUMNS = [
    "InvoiceNo",
    "StockCode",
    "Description",
    "Quantity",
    "InvoiceDate",
    "UnitPrice",
    "CustomerID",
    "Country",
]


def _read_csv(source) -> pd.DataFrame:
    df = pd.read_csv(
        source,
        dtype=str,
        encoding=RAW_ENCODING,
        keep_default_na=False,  # keep "" as "", the transform step decides what is missing
        na_values=[],
    )
    df.columns = [c.strip() for c in df.columns]

    missing = [c for c in EXPECTED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Raw file is missing expected columns: {missing}. Found: {list(df.columns)}")

    df = df[EXPECTED_COLUMNS].copy()
    # Row number in the original file (header = line 1) so every rejected
    # record can be traced back to the exact line in the source file.
    df.insert(0, "source_row", range(2, len(df) + 2))
    return df


def extract_from_file(path: Path) -> pd.DataFrame:
    log.info("Reading raw file %s", path)
    df = _read_csv(path)
    log.info("Extracted %s rows x %s columns", f"{len(df):,}", len(df.columns) - 1)
    return df


def extract_from_bytes(payload: bytes, source_name: str) -> pd.DataFrame:
    log.info("Reading raw data from %s (%s bytes)", source_name, f"{len(payload):,}")
    df = _read_csv(io.BytesIO(payload))
    log.info("Extracted %s rows x %s columns", f"{len(df):,}", len(df.columns) - 1)
    return df
