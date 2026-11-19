-- E-commerce schema shared by every backend. Money is integer cents, rating is x10 (45 = 4.5).

-- Every backend formats timestamps through this, so JSON is byte-identical across languages.
-- STABLE (same as to_char) so the planner inlines it; IMMUTABLE would block inlining (~3x slower).
CREATE FUNCTION iso(t TIMESTAMPTZ) RETURNS TEXT LANGUAGE SQL STABLE PARALLEL SAFE AS
$$ SELECT to_char(t AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') $$;

CREATE TABLE users (
    id              BIGSERIAL PRIMARY KEY,
    email           TEXT        NOT NULL UNIQUE,
    username        TEXT        NOT NULL UNIQUE,
    password_hash   TEXT        NOT NULL,
    first_name      TEXT        NOT NULL,
    last_name       TEXT        NOT NULL,
    phone           TEXT,
    date_of_birth   DATE,
    gender          TEXT,
    address_line1   TEXT,
    address_line2   TEXT,
    city            TEXT,
    state           TEXT,
    postal_code     TEXT,
    country         CHAR(2)     NOT NULL DEFAULT 'US',
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    is_verified     BOOLEAN     NOT NULL DEFAULT FALSE,
    loyalty_points  INTEGER     NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE products (
    id              BIGSERIAL PRIMARY KEY,
    sku             TEXT        NOT NULL UNIQUE,
    name            TEXT        NOT NULL,
    description     TEXT        NOT NULL DEFAULT '',
    category        TEXT        NOT NULL,
    subcategory     TEXT,
    brand           TEXT,
    price_cents     BIGINT      NOT NULL CHECK (price_cents >= 0),
    cost_cents      BIGINT      NOT NULL DEFAULT 0,
    currency        CHAR(3)     NOT NULL DEFAULT 'USD',
    stock_quantity  INTEGER     NOT NULL DEFAULT 0,
    weight_grams    INTEGER,
    length_mm       INTEGER,
    width_mm        INTEGER,
    height_mm       INTEGER,
    color           TEXT,
    rating_x10      SMALLINT    NOT NULL DEFAULT 0,
    review_count    INTEGER     NOT NULL DEFAULT 0,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    search_vector   TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', name || ' ' || description)) STORED,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE orders (
    id                   BIGSERIAL PRIMARY KEY,
    user_id              BIGINT      NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    order_number         TEXT        NOT NULL UNIQUE DEFAULT ('ORD-' || gen_random_uuid()),
    status               TEXT        NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending', 'paid', 'shipped', 'delivered', 'cancelled')),
    subtotal_cents       BIGINT      NOT NULL DEFAULT 0,
    tax_cents            BIGINT      NOT NULL DEFAULT 0,
    shipping_cents       BIGINT      NOT NULL DEFAULT 0,
    discount_cents       BIGINT      NOT NULL DEFAULT 0,
    total_cents          BIGINT      NOT NULL DEFAULT 0,
    currency             CHAR(3)     NOT NULL DEFAULT 'USD',
    payment_method       TEXT        NOT NULL DEFAULT 'card',
    payment_status       TEXT        NOT NULL DEFAULT 'unpaid',
    shipping_address     TEXT,
    shipping_city        TEXT,
    shipping_postal_code TEXT,
    shipping_country     CHAR(2)     NOT NULL DEFAULT 'US',
    tracking_number      TEXT,
    notes                TEXT,
    placed_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE order_items (
    id               BIGSERIAL PRIMARY KEY,
    order_id         BIGINT      NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id       BIGINT      NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    quantity         INTEGER     NOT NULL CHECK (quantity > 0),
    unit_price_cents BIGINT      NOT NULL,
    discount_cents   BIGINT      NOT NULL DEFAULT 0,
    line_total_cents BIGINT      NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX orders_user_placed_idx      ON orders (user_id, placed_at DESC);
CREATE INDEX order_items_order_idx       ON order_items (order_id);
CREATE INDEX order_items_product_idx     ON order_items (product_id);
CREATE INDEX products_search_idx         ON products USING GIN (search_vector);
CREATE INDEX products_category_price_idx ON products (category, price_cents);
