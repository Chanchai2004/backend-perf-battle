"""FastAPI + asyncpg. Run with 2 uvicorn workers (one per CPU), each with half of POOL_SIZE."""
import os
import re
from contextlib import asynccontextmanager

import asyncpg
import orjson
from fastapi import FastAPI, Request
from fastapi.responses import ORJSONResponse, Response
from pydantic import BaseModel, Field, ValidationError

USER_COLS = """id, email, username, first_name, last_name, phone, date_of_birth::text AS date_of_birth, gender,
address_line1, address_line2, city, state, postal_code, country::text AS country, is_active, is_verified,
loyalty_points, iso(created_at) AS created_at, iso(updated_at) AS updated_at"""
PRODUCT_COLS = """id, sku, name, description, category, subcategory, brand, price_cents, currency::text AS currency,
stock_quantity, weight_grams, length_mm, width_mm, height_mm, color, rating_x10, review_count, is_active,
iso(created_at) AS created_at, iso(updated_at) AS updated_at"""
ORDER_COLS = """id, user_id, order_number, status, subtotal_cents, tax_cents, shipping_cents, discount_cents,
total_cents, currency::text AS currency, payment_method, payment_status, shipping_address, shipping_city,
shipping_postal_code, shipping_country::text AS shipping_country, tracking_number, notes,
iso(placed_at) AS placed_at, iso(updated_at) AS updated_at"""
ITEM_COLS = """id, order_id, product_id, quantity, unit_price_cents, discount_cents, line_total_cents,
iso(created_at) AS created_at"""

WORKERS = 2
INT = re.compile(r"-?\d+")
db: asyncpg.Pool


@asynccontextmanager
async def lifespan(_app):
    global db
    size = int(os.environ.get("POOL_SIZE", "20")) // WORKERS
    db = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=size, max_size=size)
    yield
    await db.close()


app = FastAPI(lifespan=lifespan, default_response_class=ORJSONResponse, docs_url=None, redoc_url=None, openapi_url=None)


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


@app.exception_handler(HttpError)
async def http_error(_req, e: HttpError):
    return ORJSONResponse({"error": e.message}, status_code=e.status)


@app.exception_handler(asyncpg.PostgresError)
async def pg_error(_req, e: asyncpg.PostgresError):
    code = e.sqlstate or ""
    if code in ("23505", "23503"):
        return ORJSONResponse({"error": "conflict"}, status_code=409)
    if code.startswith("22") or code in ("23502", "23514"):
        return ORJSONResponse({"error": "invalid input"}, status_code=400)
    return ORJSONResponse({"error": "internal error"}, status_code=500)


NOT_FOUND = HttpError(404, "not found")


def page(req: Request) -> tuple[int, int]:
    q = req.query_params
    limit = int(q["limit"]) if INT.fullmatch(q.get("limit", "")) else 20
    offset = int(q["offset"]) if INT.fullmatch(q.get("offset", "")) else 0
    return min(max(limit, 1), 100), max(offset, 0)


def pid(s: str) -> int:
    return int(s) if INT.fullmatch(s) else 0  # non-numeric -> 0 -> 404


async def json_object(req: Request) -> str:
    raw = await req.body()
    try:
        if isinstance(orjson.loads(raw), dict):
            return raw.decode()
    except orjson.JSONDecodeError:
        pass
    raise HttpError(400, "body must be a JSON object")


async def listed(req: Request, sql: str, *args):
    limit, offset = page(req)
    rows = await db.fetch(sql, *args, limit, offset)
    return {"data": [dict(r) for r in rows], "limit": limit, "offset": offset}


def one(row, status=200):
    if row is None:
        raise NOT_FOUND
    return ORJSONResponse(dict(row), status_code=status)


async def write(req: Request, status: int, sql: str, *args):
    body = await json_object(req)
    return one(await db.fetchrow(sql, *args, body), status)


async def delete(sql: str, id_: str):
    if await db.execute(sql, pid(id_)) == "DELETE 0":
        raise NOT_FOUND
    return Response(status_code=204)


async def attach_items(conn, orders: list[dict]):
    """Loads all items for the given orders in one query and groups them by order_id."""
    if not orders:
        return
    rows = await conn.fetch(f"SELECT {ITEM_COLS} FROM order_items WHERE order_id = ANY($1::bigint[]) ORDER BY order_id, id",
                            [o["id"] for o in orders])
    by_order: dict[int, list] = {}
    for r in rows:
        by_order.setdefault(r["order_id"], []).append(dict(r))
    for o in orders:
        o["items"] = by_order.get(o["id"], [])


@app.get("/health")
async def health():
    return Response("ok")


# ---------------------------------------------------------------- users

@app.get("/users")
async def list_users(req: Request):
    return await listed(req, f"SELECT {USER_COLS} FROM (SELECT * FROM users ORDER BY id LIMIT $1 OFFSET $2) users ORDER BY id")


@app.get("/users/{id_}")
async def get_user(id_: str):
    return one(await db.fetchrow(f"SELECT {USER_COLS} FROM users WHERE id = $1", pid(id_)))


@app.post("/users")
async def create_user(req: Request):
    return await write(req, 201, f"""INSERT INTO users (email, username, password_hash, first_name, last_name, phone, date_of_birth, gender,
  address_line1, address_line2, city, state, postal_code, country)
SELECT email, username, 'not-a-real-hash', first_name, last_name, phone, date_of_birth, gender,
  address_line1, address_line2, city, state, postal_code, COALESCE(country, 'US')
FROM jsonb_to_record($1::jsonb) AS x(email text, username text, first_name text, last_name text, phone text,
  date_of_birth date, gender text, address_line1 text, address_line2 text, city text, state text,
  postal_code text, country text)
RETURNING {USER_COLS}""")


@app.put("/users/{id_}")
async def update_user(req: Request, id_: str):
    return await write(req, 200, f"""UPDATE users SET
  first_name = COALESCE($2::jsonb->>'first_name', first_name), last_name = COALESCE($2::jsonb->>'last_name', last_name),
  phone = COALESCE($2::jsonb->>'phone', phone), address_line1 = COALESCE($2::jsonb->>'address_line1', address_line1),
  city = COALESCE($2::jsonb->>'city', city), state = COALESCE($2::jsonb->>'state', state),
  postal_code = COALESCE($2::jsonb->>'postal_code', postal_code),
  is_active = COALESCE(($2::jsonb->>'is_active')::boolean, is_active),
  loyalty_points = COALESCE(($2::jsonb->>'loyalty_points')::int, loyalty_points), updated_at = now()
WHERE id = $1 RETURNING {USER_COLS}""", pid(id_))


@app.delete("/users/{id_}")
async def delete_user(id_: str):
    return await delete("DELETE FROM users WHERE id = $1", id_)


@app.get("/users/{id_}/orders")
async def user_orders(req: Request, id_: str):
    res = await listed(req, f"SELECT {ORDER_COLS} FROM orders WHERE user_id = $1 ORDER BY placed_at DESC, id DESC LIMIT $2 OFFSET $3",
                       pid(id_))
    await attach_items(db, res["data"])
    return res


# ---------------------------------------------------------------- products

@app.get("/products")
async def list_products(req: Request):
    return await listed(req, f"SELECT {PRODUCT_COLS} FROM (SELECT * FROM products ORDER BY id LIMIT $1 OFFSET $2) products ORDER BY id")


@app.get("/products/search")
async def search_products(req: Request):
    q = req.query_params
    text = q.get("q", "").strip()
    if not text:
        raise HttpError(400, "q is required")

    def num(k):
        return int(q[k]) if INT.fullmatch(q.get(k, "")) else None

    return await listed(req, f"""SELECT {PRODUCT_COLS} FROM (SELECT *, ts_rank(search_vector, plainto_tsquery('english', $1)) AS rank FROM products
WHERE search_vector @@ plainto_tsquery('english', $1)
  AND ($2::text IS NULL OR category = $2)
  AND ($3::bigint IS NULL OR price_cents >= $3)
  AND ($4::bigint IS NULL OR price_cents <= $4)
  AND (NOT $5::boolean OR stock_quantity > 0)
ORDER BY rank DESC, id
LIMIT $6 OFFSET $7) products ORDER BY rank DESC, id""", text, q.get("category") or None, num("min_price"), num("max_price"), q.get("in_stock") == "true")


@app.get("/products/{id_}")
async def get_product(id_: str):
    return one(await db.fetchrow(f"SELECT {PRODUCT_COLS} FROM products WHERE id = $1", pid(id_)))


@app.post("/products")
async def create_product(req: Request):
    return await write(req, 201, f"""INSERT INTO products (sku, name, description, category, subcategory, brand, price_cents, cost_cents, currency,
  stock_quantity, weight_grams, length_mm, width_mm, height_mm, color)
SELECT sku, name, COALESCE(description, ''), category, subcategory, brand, price_cents, COALESCE(cost_cents, 0),
  COALESCE(currency, 'USD'), COALESCE(stock_quantity, 0), weight_grams, length_mm, width_mm, height_mm, color
FROM jsonb_to_record($1::jsonb) AS x(sku text, name text, description text, category text, subcategory text,
  brand text, price_cents bigint, cost_cents bigint, currency text, stock_quantity int, weight_grams int,
  length_mm int, width_mm int, height_mm int, color text)
RETURNING {PRODUCT_COLS}""")


@app.put("/products/{id_}")
async def update_product(req: Request, id_: str):
    return await write(req, 200, f"""UPDATE products SET
  name = COALESCE($2::jsonb->>'name', name), description = COALESCE($2::jsonb->>'description', description),
  category = COALESCE($2::jsonb->>'category', category),
  price_cents = COALESCE(($2::jsonb->>'price_cents')::bigint, price_cents),
  stock_quantity = COALESCE(($2::jsonb->>'stock_quantity')::int, stock_quantity),
  is_active = COALESCE(($2::jsonb->>'is_active')::boolean, is_active), updated_at = now()
WHERE id = $1 RETURNING {PRODUCT_COLS}""", pid(id_))


@app.delete("/products/{id_}")
async def delete_product(id_: str):
    return await delete("DELETE FROM products WHERE id = $1", id_)


# ---------------------------------------------------------------- orders

@app.get("/orders")
async def list_orders(req: Request):
    include = f",{req.query_params.get('include', '')},"
    res = await listed(req, f"SELECT {ORDER_COLS} FROM (SELECT * FROM orders ORDER BY id DESC LIMIT $1 OFFSET $2) orders ORDER BY id DESC")
    orders = res["data"]
    if ",user," in include and orders:
        rows = await db.fetch(f"SELECT {USER_COLS} FROM users WHERE id = ANY($1::bigint[])", [o["user_id"] for o in orders])
        by_id = {r["id"]: dict(r) for r in rows}
        for o in orders:
            o["user"] = by_id.get(o["user_id"])
    if ",items," in include:
        await attach_items(db, orders)
    return res


@app.get("/orders/{id_}")
async def get_order(id_: str):
    row = await db.fetchrow(f"SELECT {ORDER_COLS} FROM orders WHERE id = $1", pid(id_))
    if row is None:
        raise NOT_FOUND
    order = dict(row)
    await attach_items(db, [order])
    return order


class NewItem(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(ge=1, le=1000)


class NewOrder(BaseModel):
    user_id: int = Field(gt=0)
    items: list[NewItem] = Field(min_length=1, max_length=50)


@app.post("/orders")
async def create_order(req: Request):
    raw = await json_object(req)
    try:
        body = NewOrder.model_validate_json(raw)
    except ValidationError:
        raise HttpError(400, "user_id and 1-50 valid items are required")
    pids = [i.product_id for i in body.items]
    qtys = [i.quantity for i in body.items]

    async with db.acquire() as conn, conn.transaction():
        prices = dict(await conn.fetch("SELECT id, price_cents FROM products WHERE id = ANY($1::bigint[])", pids))
        if any(p not in prices for p in pids):
            raise HttpError(400, "unknown product")
        units = [prices[p] for p in pids]
        subtotal = sum(u * q for u, q in zip(units, qtys))
        tax = subtotal * 7 // 100
        shipping = 0 if subtotal >= 5000 else 499
        try:
            row = await conn.fetchrow(f"""INSERT INTO orders (user_id, subtotal_cents, tax_cents, shipping_cents, total_cents, payment_method,
  shipping_address, shipping_city, shipping_postal_code, shipping_country, notes)
VALUES ($1, $2, $3, $4, $5, COALESCE($6::jsonb->>'payment_method', 'card'), $6::jsonb->>'shipping_address',
  $6::jsonb->>'shipping_city', $6::jsonb->>'shipping_postal_code',
  COALESCE($6::jsonb->>'shipping_country', 'US'), $6::jsonb->>'notes')
RETURNING {ORDER_COLS}""", body.user_id, subtotal, tax, shipping, subtotal + tax + shipping, raw)
        except asyncpg.ForeignKeyViolationError:
            raise HttpError(400, "unknown user")
        order = dict(row)
        order["items"] = [dict(r) for r in await conn.fetch(f"""INSERT INTO order_items (order_id, product_id, quantity, unit_price_cents, line_total_cents)
SELECT $1, t.p, t.q, t.u, t.u * t.q FROM unnest($2::bigint[], $3::int[], $4::bigint[]) AS t(p, q, u)
RETURNING {ITEM_COLS}""", order["id"], pids, qtys, units)]
    return ORJSONResponse(order, status_code=201)


@app.put("/orders/{id_}")
async def update_order(req: Request, id_: str):
    return await write(req, 200, f"""UPDATE orders SET
  status = COALESCE($2::jsonb->>'status', status), payment_status = COALESCE($2::jsonb->>'payment_status', payment_status),
  tracking_number = COALESCE($2::jsonb->>'tracking_number', tracking_number),
  notes = COALESCE($2::jsonb->>'notes', notes), updated_at = now()
WHERE id = $1 RETURNING {ORDER_COLS}""", pid(id_))


@app.delete("/orders/{id_}")
async def delete_order(id_: str):
    return await delete("DELETE FROM orders WHERE id = $1", id_)
