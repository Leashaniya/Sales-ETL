--creates indexes to make specific queries faster.
CREATE INDEX IF NOT EXISTS idx_fact_sales_sales_by_date
    ON retail.fact_sales (invoice_date)
    INCLUDE (stock_code, country_id, invoice_no, quantity, line_total)
    WHERE NOT is_cancellation;

-- (2) Customer history lookups  ->  Q4
--     Composite (customer_id, invoice_ts) supports both the filter and the ORDER BY.
CREATE INDEX IF NOT EXISTS idx_fact_sales_customer_ts
    ON retail.fact_sales (customer_id, invoice_ts DESC)
    WHERE customer_id IS NOT NULL;
