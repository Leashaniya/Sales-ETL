--holds the business questions' answer from the database

-- name: q1_top_products_nov_2011
-- Q1. Top 10 products by revenue in November 2011 (the busiest month, pre-Christmas)
--     Optimization: partial covering index idx_fact_sales_sales_by_date
--     -> reads only November's index entries (Index Only Scan) instead of all 534k rows.
SELECT p.stock_code,
       p.description,
       SUM(f.quantity)               AS units_sold,
       ROUND(SUM(f.line_total), 2)   AS revenue
FROM retail.fact_sales f
JOIN retail.dim_product p ON p.stock_code = f.stock_code
WHERE NOT f.is_cancellation
  AND NOT p.is_non_product
  AND f.invoice_date >= DATE '2011-11-01'
  AND f.invoice_date <  DATE '2011-12-01'
GROUP BY p.stock_code, p.description
ORDER BY revenue DESC
LIMIT 10;


-- name: q2_monthly_growth
-- Q2. Monthly net revenue and month-over-month growth (window function LAG)
--     This reads the WHOLE table, so an index cannot help (see Q2b).
--     Note: December 2011 only contains data up to 9 Dec, so its growth is negative.
WITH monthly AS (
    SELECT date_trunc('month', invoice_date)::date AS month,
           SUM(line_total)                          AS net_revenue,
           COUNT(DISTINCT invoice_no) FILTER (WHERE NOT is_cancellation) AS orders
    FROM retail.fact_sales
    GROUP BY 1
)
SELECT month,
       ROUND(net_revenue, 2) AS net_revenue,
       orders,
       ROUND(100.0 * (net_revenue - LAG(net_revenue) OVER (ORDER BY month))
                   / NULLIF(LAG(net_revenue) OVER (ORDER BY month), 0), 1) AS mom_growth_pct
FROM monthly
ORDER BY month;


-- name: q2b_monthly_growth_from_mv
-- Q2b. Same answer from the materialized view (13 pre-computed rows instead of 534k)
SELECT month,
       ROUND(net_revenue, 2) AS net_revenue,
       orders,
       ROUND(100.0 * (net_revenue - LAG(net_revenue) OVER (ORDER BY month))
                   / NULLIF(LAG(net_revenue) OVER (ORDER BY month), 0), 1) AS mom_growth_pct
FROM retail.mv_monthly_revenue
ORDER BY month;


-- name: q3_aov_by_country_q4_2011
-- Q3. Average order value (AOV) by country for Q4 2011 (Oct-Dec)
--     The dataset has no rating column, so "average order value by country" replaces
--     the brief's "average rating by country" example.
--     Optimization: same covering index (date range + country_id, invoice_no, line_total included).
WITH orders AS (
    SELECT f.invoice_no,
           f.country_id,
           SUM(f.line_total) AS order_value
    FROM retail.fact_sales f
    WHERE NOT f.is_cancellation
      AND f.invoice_date >= DATE '2011-10-01'
      AND f.invoice_date <  DATE '2012-01-01'
    GROUP BY f.invoice_no, f.country_id
)
SELECT c.country_name,
       COUNT(*)                       AS orders,
       ROUND(SUM(o.order_value), 2)   AS revenue,
       ROUND(AVG(o.order_value), 2)   AS avg_order_value
FROM orders o
JOIN retail.dim_country c ON c.country_id = o.country_id
GROUP BY c.country_name
HAVING COUNT(*) >= 5
ORDER BY revenue DESC
LIMIT 15;


-- name: q4_customer_purchase_history
-- Q4. Purchase history of one customer (customer 14646 = biggest spender)
--     Typical "customer 360" lookup for a CRM screen or API.
--     Optimization: idx_fact_sales_customer_ts (customer_id, invoice_ts DESC)
--     -> jumps straight to this customer's ~2k rows, already sorted, instead of scanning 534k rows.
SELECT f.invoice_no,
       f.invoice_ts,
       COUNT(*)                     AS lines,
       SUM(f.quantity)              AS units,
       ROUND(SUM(f.line_total), 2)  AS invoice_total,
       BOOL_OR(f.is_cancellation)   AS is_cancellation
FROM retail.fact_sales f
WHERE f.customer_id = 14646
GROUP BY f.invoice_no, f.invoice_ts
ORDER BY f.invoice_ts DESC;


-- name: q5_repeat_customer_rate
-- Q5 (bonus). What share of customers came back and bought again in a later month?
SELECT COUNT(*)                                                         AS customers,
       COUNT(*) FILTER (WHERE active_months > 1)                        AS repeat_customers,
       ROUND(100.0 * COUNT(*) FILTER (WHERE active_months > 1) / COUNT(*), 1) AS repeat_rate_pct
FROM (
    SELECT customer_id, COUNT(DISTINCT date_trunc('month', invoice_date)) AS active_months
    FROM retail.fact_sales
    WHERE customer_id IS NOT NULL AND NOT is_cancellation
    GROUP BY customer_id
) t;
