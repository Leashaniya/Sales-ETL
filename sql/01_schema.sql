-- creates the database tables, meaning their structure, not the data. The pipeline runs it automatically on every run.

-- =============================================================================

CREATE SCHEMA IF NOT EXISTS retail;

-- ----------------------------------------------------------------------------- dimensions
CREATE TABLE IF NOT EXISTS retail.dim_country (
    country_id      SMALLSERIAL  PRIMARY KEY,
    country_name    VARCHAR(60)  NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS retail.dim_product (
    stock_code      VARCHAR(20)  PRIMARY KEY,
    description     VARCHAR(255) NOT NULL,
    is_non_product  BOOLEAN      NOT NULL DEFAULT FALSE,  -- postage, fees, manual adjustments
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT chk_product_code_upper CHECK (stock_code = upper(stock_code))
);

CREATE TABLE IF NOT EXISTS retail.dim_customer (
    customer_id       INTEGER      PRIMARY KEY,
    country_id        SMALLINT     NOT NULL REFERENCES retail.dim_country (country_id),
    first_purchase_at TIMESTAMP    NOT NULL,
    last_purchase_at  TIMESTAMP    NOT NULL,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT chk_customer_id_positive CHECK (customer_id > 0),
    CONSTRAINT chk_customer_dates CHECK (last_purchase_at >= first_purchase_at)
);

-- ----------------------------------------------------------------------------- fact
CREATE TABLE IF NOT EXISTS retail.fact_sales (
    invoice_no       VARCHAR(10)   NOT NULL,
    line_no          SMALLINT      NOT NULL,
    invoice_ts       TIMESTAMP     NOT NULL,
    invoice_date     DATE          GENERATED ALWAYS AS (invoice_ts::date) STORED,
    stock_code       VARCHAR(20)   NOT NULL REFERENCES retail.dim_product (stock_code),
    customer_id      INTEGER                REFERENCES retail.dim_customer (customer_id), -- NULL = guest checkout
    country_id       SMALLINT      NOT NULL REFERENCES retail.dim_country (country_id),
    quantity         INTEGER       NOT NULL,
    unit_price       NUMERIC(10,3) NOT NULL,
    line_total       NUMERIC(14,3) GENERATED ALWAYS AS (quantity * unit_price) STORED,
    is_cancellation  BOOLEAN       NOT NULL,
    etl_run_id       VARCHAR(40)   NOT NULL,   -- which pipeline run loaded / last updated the row
    loaded_at        TIMESTAMPTZ   NOT NULL DEFAULT now(),

    -- Composite natural key: an invoice number + the line position inside that invoice
    CONSTRAINT pk_fact_sales PRIMARY KEY (invoice_no, line_no),

    CONSTRAINT chk_line_no_positive    CHECK (line_no > 0),
    CONSTRAINT chk_unit_price_positive CHECK (unit_price > 0),
    CONSTRAINT chk_quantity_not_zero   CHECK (quantity <> 0),
    CONSTRAINT chk_invoice_no_format   CHECK (invoice_no ~ '^C?[0-9]{6}$'),
    -- A sale must have a positive quantity, a cancellation (invoice starting with C) a negative one
    CONSTRAINT chk_cancellation_sign   CHECK (
        (is_cancellation AND quantity < 0 AND invoice_no LIKE 'C%')
        OR (NOT is_cancellation AND quantity > 0 AND invoice_no NOT LIKE 'C%')
    )
);

COMMENT ON TABLE  retail.fact_sales IS 'One row per invoice line. Cancellations are kept as negative lines so net revenue = SUM(line_total).';
COMMENT ON COLUMN retail.fact_sales.customer_id IS 'NULL for guest checkouts (no CustomerID in the source).';
