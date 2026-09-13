-- Marts model: orders
-- Final enriched orders table
SELECT
    order_id,
    customer_id,
    status,
    amount,
    created_at,
    updated_at,
    CURRENT_TIMESTAMP AS dbt_updated_at
FROM {{ ref('stg_orders') }}
WHERE status IS NOT NULL
