--creates a pre-calculated summary table of revenue per month.
CREATE MATERIALIZED VIEW IF NOT EXISTS retail.mv_monthly_revenue AS
SELECT
    date_trunc('month', invoice_date)::date                          AS month,
    SUM(line_total) FILTER (WHERE NOT is_cancellation)                AS gross_sales,
    SUM(line_total) FILTER (WHERE is_cancellation)                    AS cancellations,
    SUM(line_total)                                                   AS net_revenue,
    COUNT(DISTINCT invoice_no) FILTER (WHERE NOT is_cancellation)     AS orders,
    COUNT(DISTINCT customer_id)                                       AS active_customers
FROM retail.fact_sales
GROUP BY 1
WITH DATA;

-- One row per month. The pipeline uses a plain REFRESH (fast for 13 rows); this unique
-- index would also allow REFRESH ... CONCURRENTLY if readers must never be blocked.
CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_monthly_revenue_month
    ON retail.mv_monthly_revenue (month);
