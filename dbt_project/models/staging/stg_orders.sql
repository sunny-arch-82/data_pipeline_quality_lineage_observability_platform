-- Staging model: orders
-- Reads from raw source and applies basic cleaning
SELECT
    id          AS order_id,
    customer_id,
    LOWER(status) AS status,
    amount,
    created_at,
    updated_at
FROM {{ source('raw', 'orders') }}
