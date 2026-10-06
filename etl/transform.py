#Tidy, standardize, fill gaps, remove duplicates, reject bad rows with a reason, and prove that no row was lost.
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

log = logging.getLogger("transform")

RAW_COLUMNS = ["InvoiceNo", "StockCode", "Description", "Quantity", "InvoiceDate", "UnitPrice", "CustomerID", "Country"]

RENAME = {
    "InvoiceNo": "invoice_no",
    "StockCode": "stock_code",
    "Description": "description",
    "Quantity": "quantity_raw",
    "InvoiceDate": "invoice_date_raw",
    "UnitPrice": "unit_price_raw",
    "CustomerID": "customer_id_raw",
    "Country": "country_raw",
}

# Date format used by the source system, e.g. "12/1/2010 8:26" (month/day/year)
SOURCE_DATE_FORMAT = "%m/%d/%Y %H:%M"

# Country names in the source are inconsistent (codes, old names, placeholders)
COUNTRY_MAP = {
    "EIRE": "Ireland",
    "RSA": "South Africa",
    "USA": "United States",
    "UNITED STATES": "United States",
    "UK": "United Kingdom",
    "U.K.": "United Kingdom",
    "UNSPECIFIED": "Unknown",
}

# Stock codes that are fees / services / adjustments, not physical products.
# They are kept (they are real money) but flagged so product analytics can exclude them.
NON_PRODUCT_CODES = {
    "POST", "DOT", "M", "D", "C2", "CRUK", "PADS", "S", "B",
    "BANK CHARGES", "AMAZONFEE", "ADJUST", "ADJUST2", "TEST001", "TEST002",
}

INVOICE_PATTERN = re.compile(r"^C?\d{6}$")  # 536365 (sale) or C536379 (cancellation)

DEDUP_KEY = ["invoice_no", "stock_code", "description", "quantity", "invoice_ts", "unit_price", "customer_id", "country"]


@dataclass
class TransformResult:
    clean: pd.DataFrame
    rejected: pd.DataFrame
    stats: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- helpers
def _normalize_text(s: pd.Series) -> pd.Series:
    """Trim, collapse inner whitespace, and turn empty strings into missing."""
    s = s.astype("object").where(s.notna(), None)
    s = s.map(lambda v: re.sub(r"\s+", " ", v).strip() if isinstance(v, str) else v)
    return s.map(lambda v: None if v in ("", None) else v)


def _standardize_country(value):
    if value is None:
        return "Unknown"
    key = value.upper()
    if key in COUNTRY_MAP:
        return COUNTRY_MAP[key]
    return value.title() if value.isupper() or value.islower() else value


def _is_non_product(code) -> bool:
    if code is None:
        return False
    return code in NON_PRODUCT_CODES or code.startswith("GIFT_")


def _parse_dates(raw: pd.Series) -> pd.Series:
    """Parse the source format strictly; fall back to a flexible parser for anything else."""
    parsed = pd.to_datetime(raw, format=SOURCE_DATE_FORMAT, errors="coerce")
    retry = parsed.isna() & raw.notna()
    if retry.any():
        parsed.loc[retry] = pd.to_datetime(raw[retry], format="mixed", errors="coerce", dayfirst=False)
    return parsed


# --------------------------------------------------------------------------- main
def transform(raw: pd.DataFrame, now: pd.Timestamp | None = None) -> TransformResult:
    now = now or pd.Timestamp.now()
    stats: dict = {"rows_in": int(len(raw))}
    raw_copy = raw[["source_row", *RAW_COLUMNS]].copy()  # untouched values, used for the reject log

    df = raw.rename(columns=RENAME).copy()

    # 1. whitespace / blanks ------------------------------------------------------
    for col in RENAME.values():
        df[col] = _normalize_text(df[col])

    missing_before = {col: int(df[col].isna().sum()) for col in RENAME.values()}
    stats["missing_values_raw"] = missing_before

    # 2. standardize formats --------------------------------------------------------
    df["invoice_no"] = df["invoice_no"].str.upper()
    df["stock_code"] = df["stock_code"].str.upper()
    df["description"] = df["description"].str.upper()

    df["quantity"] = pd.to_numeric(df["quantity_raw"], errors="coerce")
    df["unit_price"] = pd.to_numeric(df["unit_price_raw"], errors="coerce").round(3)
    customer_num = pd.to_numeric(df["customer_id_raw"], errors="coerce")
    df["invoice_ts"] = _parse_dates(df["invoice_date_raw"])

    country_before = df["country_raw"].dropna().unique()
    df["country"] = df["country_raw"].map(_standardize_country)
    stats["countries_renamed"] = {
        c: _standardize_country(c) for c in sorted(country_before) if _standardize_country(c) != c
    }

    # 3. missing values -------------------------------------------------------------
    # 3a. Description: impute from other rows with the same stock code (most frequent text)
    canonical_desc = (
        df.dropna(subset=["stock_code", "description"])
        .groupby("stock_code")["description"]
        .agg(lambda s: s.value_counts().index[0])
    )
    needs_desc = df["description"].isna() & df["stock_code"].notna()
    imputed = df.loc[needs_desc, "stock_code"].map(canonical_desc)
    df.loc[needs_desc, "description"] = imputed
    stats["descriptions_imputed"] = int(imputed.notna().sum())

    # 3b. CustomerID: missing means a guest checkout. These rows are real revenue, so they are
    #     KEPT with customer_id = NULL instead of being dropped (dropping would lose ~25% of sales).
    df["customer_id"] = customer_num.round().astype("Int64")
    stats["guest_rows"] = int(df["customer_id"].isna().sum())

    # 3c. Country: missing -> "Unknown" (handled in _standardize_country)

    # 4. duplicates -----------------------------------------------------------------
    dup_mask = df.duplicated(subset=DEDUP_KEY, keep="first")
    stats["duplicates_removed"] = int(dup_mask.sum())

    # 5. validation -----------------------------------------------------------------
    is_cancel = df["invoice_no"].fillna("").str.startswith("C")
    qty = df["quantity"]
    price = df["unit_price"]

    rules: list[tuple[str, pd.Series]] = [
        ("duplicate_row", dup_mask),
        ("missing_invoice_no", df["invoice_no"].isna()),
        ("invalid_invoice_no_format", df["invoice_no"].notna() & ~df["invoice_no"].fillna("").str.match(INVOICE_PATTERN)),
        ("missing_stock_code", df["stock_code"].isna()),
        ("missing_description", df["description"].isna()),
        ("missing_or_invalid_quantity", qty.isna() | (qty != qty.round()) | (qty == 0)),
        ("negative_quantity_on_sale", ~is_cancel & (qty < 0)),
        ("positive_quantity_on_cancellation", is_cancel & (qty > 0)),
        ("missing_or_invalid_unit_price", price.isna()),
        ("non_positive_unit_price", price.notna() & (price <= 0)),
        ("missing_or_invalid_invoice_date", df["invoice_ts"].isna()),
        ("invoice_date_in_future", df["invoice_ts"].notna() & (df["invoice_ts"] > now)),
        ("invalid_customer_id", df["customer_id_raw"].notna() & customer_num.isna()),
    ]

    reasons = pd.Series("", index=df.index, dtype="object")
    rule_counts = {}
    for name, mask in rules:
        mask = mask.fillna(False).astype(bool)
        rule_counts[name] = int(mask.sum())
        reasons = reasons.where(~mask, reasons + name + ";")
    reasons = reasons.str.rstrip(";")
    stats["rule_violations"] = rule_counts

    reject_mask = reasons != ""

    rejected = raw_copy.loc[reject_mask].copy()
    rejected["reject_reason"] = reasons[reject_mask]
    rejected["primary_reason"] = rejected["reject_reason"].str.split(";").str[0]

    # 6. derive fields on the clean set -----------------------------------------------
    clean = df.loc[~reject_mask].copy()
    clean["is_cancellation"] = clean["invoice_no"].str.startswith("C")
    clean["is_non_product"] = clean["stock_code"].map(_is_non_product)
    clean["quantity"] = clean["quantity"].astype("int64")
    clean = clean.sort_values("source_row")
    clean["line_no"] = clean.groupby("invoice_no").cumcount() + 1
    clean["line_total"] = (clean["quantity"] * clean["unit_price"]).round(3)

    # Data-quality warning (not a reject): one invoice should belong to one customer & country
    per_invoice = clean.groupby("invoice_no").agg(
        n_customers=("customer_id", lambda s: s.nunique(dropna=False)),
        n_countries=("country", "nunique"),
    )
    inconsistent = int(((per_invoice["n_customers"] > 1) | (per_invoice["n_countries"] > 1)).sum())
    stats["invoices_with_inconsistent_header"] = inconsistent
    if inconsistent:
        log.warning("%s invoices have more than one customer/country across their lines", inconsistent)

    clean = clean[
        [
            "invoice_no", "line_no", "invoice_ts", "stock_code", "description", "quantity",
            "unit_price", "line_total", "customer_id", "country", "is_cancellation",
            "is_non_product", "source_row",
        ]
    ].reset_index(drop=True)

    stats["rows_clean"] = int(len(clean))
    stats["rows_rejected"] = int(len(rejected))
    stats["rejected_by_primary_reason"] = rejected["primary_reason"].value_counts().to_dict()
    stats["cancellation_rows"] = int(clean["is_cancellation"].sum())
    stats["distinct_invoices"] = int(clean["invoice_no"].nunique())
    stats["distinct_products"] = int(clean["stock_code"].nunique())
    stats["distinct_customers"] = int(clean["customer_id"].nunique())
    stats["date_range"] = [str(clean["invoice_ts"].min()), str(clean["invoice_ts"].max())] if len(clean) else None

    assert stats["rows_clean"] + stats["rows_rejected"] == stats["rows_in"], "row accounting mismatch"

    log.info(
        "Transform done: %s in -> %s clean, %s rejected (%s duplicates)",
        f"{stats['rows_in']:,}", f"{stats['rows_clean']:,}", f"{stats['rows_rejected']:,}",
        f"{stats['duplicates_removed']:,}",
    )
    for reason, count in stats["rejected_by_primary_reason"].items():
        log.info("   rejected  %-36s %8s", reason, f"{count:,}")

    return TransformResult(clean=clean, rejected=rejected.reset_index(drop=True), stats=stats)


def build_dimensions(clean: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Derive dimension rows from the clean fact lines."""
    products = (
        clean.groupby("stock_code")
        .agg(
            description=("description", lambda s: s.value_counts().index[0]),  # most common spelling
            is_non_product=("is_non_product", "first"),
            n_descriptions=("description", "nunique"),
        )
        .reset_index()
    )

    countries = pd.DataFrame({"country_name": sorted(clean["country"].unique())})

    known = clean.dropna(subset=["customer_id"])
    customers = (
        known.groupby("customer_id")
        .agg(
            country_name=("country", lambda s: s.value_counts().index[0]),
            first_purchase_at=("invoice_ts", "min"),
            last_purchase_at=("invoice_ts", "max"),
        )
        .reset_index()
    )
    customers["customer_id"] = customers["customer_id"].astype("int64")

    return {"products": products, "countries": countries, "customers": customers}
