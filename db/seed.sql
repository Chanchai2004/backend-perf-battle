-- Deterministic seed: same rows on every run (single-threaded + fixed random seed).
SET max_parallel_workers_per_gather = 0;
SELECT setseed(0.42);

CREATE FUNCTION pick(a TEXT[]) RETURNS TEXT LANGUAGE SQL VOLATILE AS
$$ SELECT a[1 + floor(random() * cardinality(a))::int] $$;

-- ---------------------------------------------------------------- users: 30,000
INSERT INTO users (email, username, password_hash, first_name, last_name, phone, date_of_birth, gender,
                   address_line1, address_line2, city, state, postal_code, country,
                   is_active, is_verified, loyalty_points, created_at, updated_at)
SELECT 'user' || g || '@example.com',
       'user' || g,
       'not-a-real-hash',
       pick('{James,Mary,John,Patricia,Robert,Jennifer,Michael,Linda,William,Elizabeth,David,Barbara,Richard,Susan,Joseph,Jessica,Thomas,Sarah,Daniel,Karen,Somchai,Malee,Arthit,Ploy}'),
       pick('{Smith,Johnson,Williams,Brown,Jones,Garcia,Miller,Davis,Rodriguez,Martinez,Hernandez,Lopez,Wilson,Anderson,Taylor,Moore,Jackson,Martin,Lee,Thompson,Saetang,Wongsa}'),
       '+1-555-' || lpad((floor(random() * 10000))::int::text, 4, '0'),
       DATE '1960-01-01' + floor(random() * 16000)::int,
       pick('{male,female,other}'),
       (1 + floor(random() * 9999))::int || ' ' || pick('{Main,Oak,Pine,Maple,Cedar,Elm,Lake,Hill,Park,River}') || ' St',
       CASE WHEN random() < 0.2 THEN 'Apt ' || (1 + floor(random() * 500))::int END,
       pick('{New York,Los Angeles,Chicago,Houston,Phoenix,Seattle,Denver,Boston,Austin,Portland,Miami,Atlanta}'),
       pick('{NY,CA,IL,TX,AZ,WA,CO,MA,OR,FL,GA}'),
       lpad((floor(random() * 99999))::int::text, 5, '0'),
       'US',
       random() < 0.95,
       random() < 0.7,
       floor(random() * 5000)::int,
       ts, ts + (random() * INTERVAL '200 days')
FROM (SELECT g, TIMESTAMPTZ '2022-01-01 00:00:00+00' + random() * INTERVAL '900 days' AS ts
      FROM generate_series(1, 30000) g) s;

-- ---------------------------------------------------------------- products: 15,000
-- name = adjective + material + noun, so k6 search terms always hit.
INSERT INTO products (sku, name, description, category, subcategory, brand, price_cents, cost_cents, currency,
                      stock_quantity, weight_grams, length_mm, width_mm, height_mm, color,
                      rating_x10, review_count, is_active, created_at, updated_at)
SELECT 'SKU-' || lpad(g::text, 6, '0'),
       adj || ' ' || mat || ' ' || noun,
       'A ' || lower(adj) || ' ' || lower(noun) || ' made of ' || lower(mat) || '. ' ||
       pick('{Perfect for everyday use.,Built to last.,Customer favorite.,Limited edition.,Great gift idea.,Designed for professionals.}'),
       cat,
       noun,
       pick('{Acme,Globex,Initech,Umbrella,Stark,Wayne,Wonka,Hooli,Vandelay,Soylent}'),
       price, (price * (0.4 + random() * 0.3))::bigint, 'USD',
       CASE WHEN random() < 0.1 THEN 0 ELSE floor(random() * 1000)::int END,
       (50 + floor(random() * 20000))::int,
       (10 + floor(random() * 2000))::int, (10 + floor(random() * 2000))::int, (10 + floor(random() * 2000))::int,
       pick('{red,blue,green,black,white,silver,gold,gray,orange,purple}'),
       (10 + floor(random() * 41))::smallint,
       floor(random() * 3000)::int,
       random() < 0.97,
       ts, ts + (random() * INTERVAL '100 days')
FROM (SELECT g,
             pick('{Ergonomic,Rustic,Sleek,Intelligent,Gorgeous,Incredible,Practical,Fantastic,Handcrafted,Refined,Modern,Vintage}') AS adj,
             pick('{Steel,Wooden,Cotton,Plastic,Granite,Rubber,Leather,Bamboo,Concrete,Glass,Silk,Bronze}') AS mat,
             pick('{Chair,Table,Lamp,Keyboard,Mouse,Shirt,Shoes,Watch,Backpack,Bottle,Speaker,Wallet,Desk,Pillow,Jacket,Headphones,Mug,Knife,Clock,Camera}') AS noun,
             pick('{Home,Electronics,Fashion,Sports,Office,Kitchen,Outdoors,Toys}') AS cat,
             (199 + floor(random() * 99800))::bigint AS price,
             TIMESTAMPTZ '2021-06-01 00:00:00+00' + random() * INTERVAL '900 days' AS ts
      FROM generate_series(1, 15000) g) s;

-- ---------------------------------------------------------------- orders: 80,000
-- user_id skewed (power 2) so some users have many orders, like real shops.
INSERT INTO orders (user_id, order_number, status, shipping_cents, discount_cents, currency,
                    payment_method, payment_status, shipping_address, shipping_city, shipping_postal_code,
                    shipping_country, tracking_number, notes, placed_at, updated_at)
SELECT uid,
       'ORD-' || lpad(g::text, 8, '0'),
       st,
       CASE WHEN random() < 0.5 THEN 0 ELSE 499 END,
       CASE WHEN random() < 0.1 THEN 500 ELSE 0 END,
       'USD',
       pick('{card,paypal,bank_transfer,cod}'),
       CASE WHEN st IN ('pending', 'cancelled') THEN 'unpaid' ELSE 'paid' END,
       (1 + floor(random() * 9999))::int || ' ' || pick('{Main,Oak,Pine,Maple,Cedar,Elm}') || ' St',
       pick('{New York,Los Angeles,Chicago,Houston,Phoenix,Seattle,Denver,Boston}'),
       lpad((floor(random() * 99999))::int::text, 5, '0'),
       'US',
       CASE WHEN st IN ('shipped', 'delivered') THEN 'TRK' || lpad((floor(random() * 1e9))::bigint::text, 10, '0') END,
       CASE WHEN random() < 0.1 THEN pick('{Leave at door,Call before delivery,Gift wrap please,Fragile}') END,
       ts, ts + (random() * INTERVAL '10 days')
FROM (SELECT g,
             (1 + floor(power(random(), 2) * 30000))::bigint AS uid,
             pick('{pending,paid,shipped,delivered,delivered,delivered,cancelled}') AS st,
             TIMESTAMPTZ '2023-01-01 00:00:00+00' + random() * INTERVAL '700 days' AS ts
      FROM generate_series(1, 80000) g) s;

-- ---------------------------------------------------------------- order_items: 1-5 per order (~240,000)
INSERT INTO order_items (order_id, product_id, quantity, unit_price_cents, discount_cents, line_total_cents, created_at)
SELECT x.order_id, p.id, x.q, p.price_cents, 0, p.price_cents * x.q, x.placed_at
FROM (SELECT o.id AS order_id, o.placed_at,
             (1 + floor(random() * 15000))::bigint AS pid,
             (1 + floor(random() * 3))::int AS q
      FROM (SELECT id, placed_at, (1 + floor(random() * 5))::int AS n FROM orders) o
      CROSS JOIN LATERAL generate_series(1, o.n)) x
JOIN products p ON p.id = x.pid
ORDER BY x.order_id;

-- Order totals derived from the real items: total = subtotal + 7% tax + shipping - discount.
-- The 5.00 discount only applies to orders of 50.00+, so totals never go negative.
UPDATE orders o
SET subtotal_cents = s.sub,
    tax_cents      = s.sub * 7 / 100,
    discount_cents = CASE WHEN s.sub >= 5000 THEN o.discount_cents ELSE 0 END,
    total_cents    = s.sub + s.sub * 7 / 100 + o.shipping_cents
                     - CASE WHEN s.sub >= 5000 THEN o.discount_cents ELSE 0 END
FROM (SELECT order_id, sum(line_total_cents) AS sub FROM order_items GROUP BY order_id) s
WHERE s.order_id = o.id;

DROP FUNCTION pick(TEXT[]);
VACUUM ANALYZE;
