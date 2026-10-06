#puts the clean data into PostgreSQL
from __future__ import annotations

import io
import logging
import time

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

from etl.config import SQL_DIR, PostgresSettings

log = logging.getLogger("load")


# --------------------------------------------------------------------------- connection / schema
def get_connection(settings: PostgresSettings | None = None):
    settings = settings or PostgresSettings()
    conn = psycopg2.connect(**settings.connect_kwargs())
    log.info("Connected to PostgreSQL %s@%s:%s/%s", settings.user, settings.host, settings.port, settings.dbname)
    return conn


def apply_sql_file(conn, filename: str) -> None:
    path = SQL_DIR / filename
    if not path.exists():
        log.warning("SQL file %s not found, skipping", path.name)
        return
    with conn.cursor() as cur:
        cur.execute(path.read_text())
    conn.commit()
    log.info("Applied %s", path.name)


# --------------------------------------------------------------------------- dimensions
def _upsert_countries(cur, countries: pd.DataFrame) -> dict[str, int]:
    execute_values(
        cur,
        "INSERT INTO retail.dim_country (country_name) VALUES %s ON CONFLICT (country_name) DO NOTHING",
        [(c,) for c in countries["country_name"]],
    )
    cur.execute("SELECT country_name, country_id FROM retail.dim_country")
    return dict(cur.fetchall())


def _upsert_products(cur, products: pd.DataFrame) -> None:
    rows = list(products[["stock_code", "description", "is_non_product"]].itertuples(index=False, name=None))
    execute_values(
        cur,
        """
        INSERT INTO retail.dim_product (stock_code, description, is_non_product) VALUES %s
        ON CONFLICT (stock_code) DO UPDATE
           SET description = EXCLUDED.description,
               is_non_product = EXCLUDED.is_non_product,
               updated_at = now()
         WHERE retail.dim_product.description IS DISTINCT FROM EXCLUDED.description
            OR retail.dim_product.is_non_product IS DISTINCT FROM EXCLUDED.is_non_product
        """,
        rows,
        page_size=5000,
    )


def _upsert_customers(cur, customers: pd.DataFrame, country_ids: dict[str, int]) -> None:
    rows = [
        (int(r.customer_id), country_ids[r.country_name], r.first_purchase_at.to_pydatetime(), r.last_purchase_at.to_pydatetime())
        for r in customers.itertuples(index=False)
    ]
    execute_values(
        cur,
        """
        INSERT INTO retail.dim_customer (customer_id, country_id, first_purchase_at, last_purchase_at) VALUES %s
        ON CONFLICT (customer_id) DO UPDATE
           SET country_id        = EXCLUDED.country_id,
               first_purchase_at = LEAST(retail.dim_customer.first_purchase_at, EXCLUDED.first_purchase_at),
               last_purchase_at  = GREATEST(retail.dim_customer.last_purchase_at, EXCLUDED.last_purchase_at),
               updated_at        = now()
        """,
        rows,
        page_size=5000,
    )


# --------------------------------------------------------------------------- fact
_FACT_COLUMNS = [
    "invoice_no", "line_no", "invoice_ts", "stock_code", "customer_id",
    "country_id", "quantity", "unit_price", "is_cancellation", "etl_run_id",
]


def _load_fact(cur, clean: pd.DataFrame, country_ids: dict[str, int], run_id: str) -> int:
    fact = clean.copy()
    fact["country_id"] = fact["country"].map(country_ids)
    fact["etl_run_id"] = run_id
    fact["invoice_ts"] = fact["invoice_ts"].dt.strftime("%Y-%m-%d %H:%M:%S")
    fact = fact[_FACT_COLUMNS]

    cur.execute(
        """
        CREATE TEMP TABLE stg_fact_sales (
            invoice_no VARCHAR(10), line_no SMALLINT, invoice_ts TIMESTAMP, stock_code VARCHAR(20),
            customer_id INTEGER, country_id SMALLINT, quantity INTEGER, unit_price NUMERIC(10,3),
            is_cancellation BOOLEAN, etl_run_id VARCHAR(40)
        ) ON COMMIT DROP
        """
    )

    buffer = io.StringIO()
    fact.to_csv(buffer, index=False, header=False, na_rep="")
    buffer.seek(0)
    started = time.perf_counter()
    cur.copy_expert("COPY stg_fact_sales FROM STDIN WITH (FORMAT csv, NULL '')", buffer)
    log.info("COPY %s rows into staging in %.1fs", f"{len(fact):,}", time.perf_counter() - started)

    cur.execute(
        f"""
        INSERT INTO retail.fact_sales ({", ".join(_FACT_COLUMNS)})
        SELECT {", ".join(_FACT_COLUMNS)} FROM stg_fact_sales
        ON CONFLICT (invoice_no, line_no) DO UPDATE
           SET invoice_ts      = EXCLUDED.invoice_ts,
               stock_code      = EXCLUDED.stock_code,
               customer_id     = EXCLUDED.customer_id,
               country_id      = EXCLUDED.country_id,
               quantity        = EXCLUDED.quantity,
               unit_price      = EXCLUDED.unit_price,
               is_cancellation = EXCLUDED.is_cancellation,
               etl_run_id      = EXCLUDED.etl_run_id,
               loaded_at       = now()
        """
    )
    return cur.rowcount


# --------------------------------------------------------------------------- public entry point
def load_all(conn, clean: pd.DataFrame, dims: dict[str, pd.DataFrame], run_id: str) -> int:
    """Load dimensions and the fact table in a single transaction. Returns fact rows upserted."""
    started = time.perf_counter()
    try:
        with conn.cursor() as cur:
            country_ids = _upsert_countries(cur, dims["countries"])
            log.info("dim_country   : %s countries", len(country_ids))

            _upsert_products(cur, dims["products"])
            log.info("dim_product   : %s products upserted", f"{len(dims['products']):,}")

            _upsert_customers(cur, dims["customers"], country_ids)
            log.info("dim_customer  : %s customers upserted", f"{len(dims['customers']):,}")

            loaded = _load_fact(cur, clean, country_ids, run_id)
            log.info("fact_sales    : %s rows upserted", f"{loaded:,}")

        conn.commit()
    except Exception:
        conn.rollback()
        log.exception("Load failed, transaction rolled back - no partial data was written")
        raise
    log.info("Load committed in %.1fs", time.perf_counter() - started)
    return loaded


def post_load_maintenance(conn) -> None:
    """Refresh the reporting materialized view (if it exists) and update planner statistics."""
    old_autocommit = conn.autocommit
    conn.autocommit = True  # VACUUM cannot run inside a transaction
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('retail.mv_monthly_revenue')")
            if cur.fetchone()[0]:
                cur.execute("REFRESH MATERIALIZED VIEW retail.mv_monthly_revenue")
                log.info("Refreshed materialized view retail.mv_monthly_revenue")
            # ANALYZE keeps the query planner's statistics accurate; VACUUM updates the
            # visibility map so covering indexes can be used for Index Only Scans.
            cur.execute("VACUUM (ANALYZE) retail.fact_sales")
            cur.execute("ANALYZE retail.dim_product, retail.dim_customer, retail.dim_country")
            log.info("VACUUM ANALYZE done")
    finally:
        conn.autocommit = old_autocommit
