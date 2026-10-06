#Look at the raw data and list every problem in it, so the cleaning code fixes real issues.
from pathlib import Path

import pandas as pd

RAW_FILE = Path(__file__).resolve().parent.parent / "data" / "raw" / "data.csv"


def section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def main() -> None:
    # Read every column as text so nothing is silently converted
    df = pd.read_csv(RAW_FILE, dtype=str, encoding="ISO-8859-1", keep_default_na=False)
    blank = df.apply(lambda col: col.str.strip() == "")

    section("1. SIZE AND COLUMNS")
    print(f"Rows   : {len(df):,}")
    print(f"Columns: {len(df.columns)} -> {list(df.columns)}")
    print("\nSample rows:")
    print(df.head(5).to_string(index=False))

    section("2. MISSING VALUES (blank cells)")
    missing = blank.sum()
    for col, n in missing.items():
        print(f"{col:<12} {n:>8,}  ({n / len(df):.1%})")

    section("3. DUPLICATES")
    print(f"Exact duplicate rows: {df.duplicated().sum():,}")

    section("4. SUSPICIOUS VALUES")
    qty = pd.to_numeric(df["Quantity"], errors="coerce")
    price = pd.to_numeric(df["UnitPrice"], errors="coerce")
    first_char = df["InvoiceNo"].str[0]
    print(f"Quantity < 0                 : {(qty < 0).sum():,}")
    print(f"UnitPrice = 0                : {(price == 0).sum():,}")
    print(f"UnitPrice < 0                : {(price < 0).sum():,}")
    print(f"Invoices starting with 'C'   : {(first_char == 'C').sum():,}  (cancellations)")
    print(f"Invoices starting with 'A'   : {(first_char == 'A').sum():,}  (bad-debt adjustments)")
    print(f"Quantity range               : {qty.min():,.0f} to {qty.max():,.0f}")
    print(f"UnitPrice range              : {price.min():,.2f} to {price.max():,.2f}")

    section("5. INCONSISTENT FORMATS")
    print("Country values that are codes / placeholders:")
    for name in ["EIRE", "RSA", "USA", "Unspecified", "European Community"]:
        print(f"   {name:<20} {(df['Country'] == name).sum():>6,} rows")

    lower_codes = df.loc[df["StockCode"] != df["StockCode"].str.upper(), "StockCode"]
    print(f"\nStock codes with lowercase letters: {len(lower_codes):,} rows "
          f"(e.g. {', '.join(lower_codes.unique()[:5])})")

    per_code = df[df["Description"].str.strip() != ""].groupby("StockCode")["Description"].nunique()
    print(f"Stock codes with more than one description: {(per_code > 1).sum():,}")

    print(f"\nInvoiceDate is text, e.g. {df['InvoiceDate'].iloc[0]!r} (month/day/year hour:minute)")
    dates = pd.to_datetime(df["InvoiceDate"], format="%m/%d/%Y %H:%M", errors="coerce")
    print(f"Date range: {dates.min()} to {dates.max()}")

    non_numeric_customer = df["CustomerID"].str.strip().ne("") & pd.to_numeric(df["CustomerID"], errors="coerce").isna()
    print(f"CustomerID values that are not numbers: {non_numeric_customer.sum():,}")

    section("SUMMARY")
    print("Problems the ETL must handle:")
    print(f" - {missing['CustomerID']:,} rows with no CustomerID (guest checkouts)")
    print(f" - {missing['Description']:,} rows with no Description")
    print(f" - {df.duplicated().sum():,} exact duplicate rows")
    print(f" - {(qty < 0).sum():,} negative quantities and {(price <= 0).sum():,} zero/negative prices")
    print(" - Country codes (EIRE, RSA, USA, Unspecified) to standardize")
    print(" - Text dates to convert to real timestamps")


if __name__ == "__main__":
    main()
