"""The one command that runs the whole pipeline in order: upload, read, clean, log the rejects, load.
It reports failure clearly so a scheduler can react."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from etl.config import DEFAULT_RAW_FILE, REJECTED_DIR, S3Settings
from etl.extract import extract_from_bytes, extract_from_file
from etl.load import apply_sql_file, get_connection, load_all, post_load_maintenance
from etl.logger import setup_logging
from etl.transform import build_dimensions, transform

log = logging.getLogger("pipeline")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Retail sales ETL: CSV -> S3 -> clean -> PostgreSQL")
    parser.add_argument("--input", type=Path, default=DEFAULT_RAW_FILE, help="Raw CSV file (default: data/raw/data.csv)")
    parser.add_argument("--skip-s3", action="store_true", help="Run locally without AWS S3")
    return parser.parse_args()


def save_rejected(result, run_id: str) -> Path:
    """Write every rejected row (original values + reason) to data/rejected/."""
    REJECTED_DIR.mkdir(parents=True, exist_ok=True)
    rejected_path = REJECTED_DIR / f"rejected_{run_id}.csv"
    result.rejected.to_csv(rejected_path, index=False)
    log.info("Saved %s rejected rows -> data/rejected/%s", f"{len(result.rejected):,}", rejected_path.name)
    return rejected_path


def main() -> int:
    args = parse_args()
    run_id = datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")
    setup_logging()
    started = time.perf_counter()

    log.info("=" * 64)
    log.info("Starting ETL run %s", run_id)
    log.info("=" * 64)

    if not args.input.exists():
        log.error("Input file not found: %s", args.input)
        return 1

    s3 = None
    if not args.skip_s3:
        settings = S3Settings()
        if settings.enabled:
            from etl.s3_storage import S3Storage

            s3 = S3Storage(settings)
            s3.check_access()
        else:
            log.warning("S3_BUCKET_NAME not set in .env -> running without S3 (use --skip-s3 to hide this warning)")

    conn = get_connection()
    stats: dict = {}
    try:
        # Schema + indexes + views are idempotent, so they are applied on every run
        for sql_file in ("01_schema.sql", "02_indexes.sql", "04_materialized_views.sql"):
            apply_sql_file(conn, sql_file)

        # ---- 1 + 2. land raw file in S3, then extract it back from S3 --------------------
        if s3:
            log.info("[1/5] Uploading raw file to S3 (raw zone)")
            raw_key = s3.upload_file(args.input, zone="raw")
            source_uri = s3.uri(raw_key)
            log.info("[2/5] Extracting from %s", source_uri)
            raw_df = extract_from_bytes(s3.download_bytes(raw_key), source_uri)
        else:
            log.info("[1/5] S3 skipped")
            log.info("[2/5] Extracting from local file")
            raw_df = extract_from_file(args.input)

        # ---- 3. transform ----------------------------------------------------------------
        log.info("[3/5] Transforming (clean, standardize, dedupe, validate)")
        result = transform(raw_df)
        dims = build_dimensions(result.clean)
        stats = dict(result.stats)

        # ---- 4. log rejected records ------------------------------------------------------
        log.info("[4/5] Logging rejected records")
        save_rejected(result, run_id)

        # ---- 5. load ---------------------------------------------------------------------
        log.info("[5/5] Loading into PostgreSQL")
        stats["rows_loaded"] = load_all(conn, result.clean, dims, run_id)
        post_load_maintenance(conn)

    except Exception:  # log the full error, then exit with a non-zero code
        log.exception("Pipeline FAILED")
        conn.rollback()
        return 1
    finally:
        conn.close()

    elapsed = time.perf_counter() - started
    log.info("=" * 64)
    log.info("RUN SUMMARY  %s", run_id)
    log.info("  rows extracted     : %s", f"{stats['rows_in']:,}")
    log.info("  duplicates removed : %s", f"{stats['duplicates_removed']:,}")
    log.info("  rows rejected      : %s", f"{stats['rows_rejected']:,}")
    log.info("  rows loaded        : %s", f"{stats['rows_loaded']:,}")
    log.info("  duration           : %.1fs", elapsed)
    log.info("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
