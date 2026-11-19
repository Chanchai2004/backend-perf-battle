-- Canonical SQL. Every backend uses these statements verbatim (placeholder syntax aside:
-- Dapper uses @p names). If you change one here, change it in all six backends.
--
-- Lists and search page in a subquery first, so the outer SELECT formats only the returned rows
-- (otherwise Postgres projects every row skipped by OFFSET / every search match before the sort).
--
-- Column lists
--   USER_COLS    = id, email, username, first_name, last_name, phone, date_of_birth::text AS date_of_birth, gender,
--                  address_line1, address_line2, city, state, postal_code, country::text AS country, is_active,
--                  is_verified, loyalty_points, iso(created_at) AS created_at, iso(updated_at) AS updated_at
--   PRODUCT_COLS = id, sku, name, description, category, subcategory, brand, price_cents, currency::text AS currency,
--                  stock_quantity, weight_grams, length_mm, width_mm, height_mm, color, rating_x10, review_count,
--                  is_active, iso(created_at) AS created_at, iso(updated_at) AS updated_at
--   ORDER_COLS   = id, user_id, order_number, status, subtotal_cents, tax_cents, shipping_cents, discount_cents,
--                  total_cents, currency::text AS currency, payment_method, payment_status, shipping_address,
--                  shipping_city, shipping_postal_code, shipping_country::text AS shipping_country, tracking_number,
--                  notes, iso(placed_at) AS placed_at, iso(updated_at) AS updated_at
--   ITEM_COLS    = id, order_id, product_id, quantity, unit_price_cents, discount_cents, line_total_cents,
--                  iso(created_at) AS created_at

-- users -------------------------------------------------------------------------------------------
-- list
SELECT USER_COLS FROM (SELECT * FROM users ORDER BY id LIMIT $1 OFFSET $2) users ORDER BY id;
-- get
SELECT USER_COLS FROM users WHERE id = $1;
-- create ($1 = request body as JSON text)
INSERT INTO users (email, username, password_hash, first_name, last_name, phone, date_of_birth, gender,
                   address_line1, address_line2, city, state, postal_code, country)
SELECT email, username, 'not-a-real-hash', first_name, last_name, phone, date_of_birth, gender,
       address_line1, address_line2, city, state, postal_code, COALESCE(country, 'US')
FROM jsonb_to_record($1::jsonb) AS x(email text, username text, first_name text, last_name text, phone text,
     date_of_birth date, gender text, address_line1 text, address_line2 text, city text, state text,
     postal_code text, country text)
RETURNING USER_COLS;
-- update (fields absent from the body are kept)
UPDATE users SET
  first_name     = COALESCE($2::jsonb->>'first_name', first_name),
  last_name      = COALESCE($2::jsonb->>'last_name', last_name),
  phone          = COALESCE($2::jsonb->>'phone', phone),
  address_line1  = COALESCE($2::jsonb->>'address_line1', address_line1),
  city           = COALESCE($2::jsonb->>'city', city),
  state          = COALESCE($2::jsonb->>'state', state),
  postal_code    = COALESCE($2::jsonb->>'postal_code', postal_code),
  is_active      = COALESCE(($2::jsonb->>'is_active')::boolean, is_active),
  loyalty_points = COALESCE(($2::jsonb->>'loyalty_points')::int, loyalty_points),
  updated_at     = now()
WHERE id = $1 RETURNING USER_COLS;
-- delete
DELETE FROM users WHERE id = $1;
-- a user's orders (items fetched with the items query below)
SELECT ORDER_COLS FROM orders WHERE user_id = $1 ORDER BY placed_at DESC, id DESC LIMIT $2 OFFSET $3;

-- products ----------------------------------------------------------------------------------------
SELECT PRODUCT_COLS FROM (SELECT * FROM products ORDER BY id LIMIT $1 OFFSET $2) products ORDER BY id;
SELECT PRODUCT_COLS FROM products WHERE id = $1;
INSERT INTO products (sku, name, description, category, subcategory, brand, price_cents, cost_cents, currency,
                      stock_quantity, weight_grams, length_mm, width_mm, height_mm, color)
SELECT sku, name, COALESCE(description, ''), category, subcategory, brand, price_cents, COALESCE(cost_cents, 0),
       COALESCE(currency, 'USD'), COALESCE(stock_quantity, 0), weight_grams, length_mm, width_mm, height_mm, color
FROM jsonb_to_record($1::jsonb) AS x(sku text, name text, description text, category text, subcategory text,
     brand text, price_cents bigint, cost_cents bigint, currency text, stock_quantity int, weight_grams int,
     length_mm int, width_mm int, height_mm int, color text)
RETURNING PRODUCT_COLS;
UPDATE products SET
  name           = COALESCE($2::jsonb->>'name', name),
  description    = COALESCE($2::jsonb->>'description', description),
  category       = COALESCE($2::jsonb->>'category', category),
  price_cents    = COALESCE(($2::jsonb->>'price_cents')::bigint, price_cents),
  stock_quantity = COALESCE(($2::jsonb->>'stock_quantity')::int, stock_quantity),
  is_active      = COALESCE(($2::jsonb->>'is_active')::boolean, is_active),
  updated_at     = now()
WHERE id = $1 RETURNING PRODUCT_COLS;
DELETE FROM products WHERE id = $1;
-- search: $1 q, $2 category|null, $3 min_price|null, $4 max_price|null, $5 in_stock bool, $6 limit, $7 offset
SELECT PRODUCT_COLS FROM (SELECT *, ts_rank(search_vector, plainto_tsquery('english', $1)) AS rank FROM products
WHERE search_vector @@ plainto_tsquery('english', $1)
  AND ($2::text IS NULL OR category = $2)
  AND ($3::bigint IS NULL OR price_cents >= $3)
  AND ($4::bigint IS NULL OR price_cents <= $4)
  AND (NOT $5::boolean OR stock_quantity > 0)
ORDER BY rank DESC, id
LIMIT $6 OFFSET $7) products ORDER BY rank DESC, id;

-- orders ------------------------------------------------------------------------------------------
SELECT ORDER_COLS FROM (SELECT * FROM orders ORDER BY id DESC LIMIT $1 OFFSET $2) orders ORDER BY id DESC;
SELECT ORDER_COLS FROM orders WHERE id = $1;
-- include=user: one round trip for all users of the page
SELECT USER_COLS FROM users WHERE id = ANY($1::bigint[]);
-- include=items / detail: one round trip for all items of the page
SELECT ITEM_COLS FROM order_items WHERE order_id = ANY($1::bigint[]) ORDER BY order_id, id;
-- create, inside one transaction:
SELECT id, price_cents FROM products WHERE id = ANY($1::bigint[]);
--   backend computes: subtotal = sum(price*qty), tax = subtotal*7/100, shipping = subtotal >= 5000 ? 0 : 499,
--   total = subtotal + tax + shipping. Unknown product -> 400, unknown user (FK 23503) -> 400.
INSERT INTO orders (user_id, subtotal_cents, tax_cents, shipping_cents, total_cents, payment_method,
                    shipping_address, shipping_city, shipping_postal_code, shipping_country, notes)
VALUES ($1, $2, $3, $4, $5, COALESCE($6::jsonb->>'payment_method', 'card'), $6::jsonb->>'shipping_address',
        $6::jsonb->>'shipping_city', $6::jsonb->>'shipping_postal_code',
        COALESCE($6::jsonb->>'shipping_country', 'US'), $6::jsonb->>'notes')
RETURNING ORDER_COLS;
INSERT INTO order_items (order_id, product_id, quantity, unit_price_cents, line_total_cents)
SELECT $1, t.p, t.q, t.u, t.u * t.q FROM unnest($2::bigint[], $3::int[], $4::bigint[]) AS t(p, q, u)
RETURNING ITEM_COLS;
UPDATE orders SET
  status          = COALESCE($2::jsonb->>'status', status),
  payment_status  = COALESCE($2::jsonb->>'payment_status', payment_status),
  tracking_number = COALESCE($2::jsonb->>'tracking_number', tracking_number),
  notes           = COALESCE($2::jsonb->>'notes', notes),
  updated_at      = now()
WHERE id = $1 RETURNING ORDER_COLS;
DELETE FROM orders WHERE id = $1;

-- Error mapping (all backends): SQLSTATE 23505 / 23503 -> 409, class 22 / 23502 / 23514 -> 400.
