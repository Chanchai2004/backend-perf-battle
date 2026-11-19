// Fastify + node-postgres. 2 cluster workers (one per CPU), each with half of POOL_SIZE.
import cluster from 'node:cluster';
import Fastify from 'fastify';
import pg from 'pg';

const WORKERS = 2;

if (cluster.isPrimary) {
  for (let i = 0; i < WORKERS; i++) cluster.fork();
  cluster.on('exit', () => process.exit(1));
} else {
  start();
}

const USER_COLS = `id, email, username, first_name, last_name, phone, date_of_birth::text AS date_of_birth, gender,
address_line1, address_line2, city, state, postal_code, country::text AS country, is_active, is_verified,
loyalty_points, iso(created_at) AS created_at, iso(updated_at) AS updated_at`;
const PRODUCT_COLS = `id, sku, name, description, category, subcategory, brand, price_cents, currency::text AS currency,
stock_quantity, weight_grams, length_mm, width_mm, height_mm, color, rating_x10, review_count, is_active,
iso(created_at) AS created_at, iso(updated_at) AS updated_at`;
const ORDER_COLS = `id, user_id, order_number, status, subtotal_cents, tax_cents, shipping_cents, discount_cents,
total_cents, currency::text AS currency, payment_method, payment_status, shipping_address, shipping_city,
shipping_postal_code, shipping_country::text AS shipping_country, tracking_number, notes,
iso(placed_at) AS placed_at, iso(updated_at) AS updated_at`;
const ITEM_COLS = `id, order_id, product_id, quantity, unit_price_cents, discount_cents, line_total_cents,
iso(created_at) AS created_at`;

// bigint columns: ids and cents are far below 2^53, so plain numbers are exact.
pg.types.setTypeParser(20, Number);

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function start() {
  const db = new pg.Pool({
    connectionString: process.env.DATABASE_URL,
    max: Math.floor(Number(process.env.POOL_SIZE || 20) / WORKERS),
  });
  // named prepared statements, like the other drivers do implicitly
  let n = 0;
  const names = new Map();
  const q = (text, values) => {
    let name = names.get(text);
    if (!name) names.set(text, (name = `q${n++}`));
    return db.query({ name, text, values });
  };

  const app = Fastify({ logger: false });
  // keep the raw body: mutations pass it to SQL as jsonb, POST /orders also reads it
  app.addContentTypeParser('*', { parseAs: 'string' }, (_req, body, done) => done(null, body));
  app.removeContentTypeParser('application/json');
  app.addContentTypeParser('application/json', { parseAs: 'string' }, (_req, body, done) => done(null, body));

  app.setErrorHandler((err, _req, reply) => {
    const code = err.code || '';
    if (err instanceof HttpError) return reply.code(err.status).send({ error: err.message });
    if (code === '23505' || code === '23503') return reply.code(409).send({ error: 'conflict' });
    if (code.startsWith('22') || code === '23502' || code === '23514') return reply.code(400).send({ error: 'invalid input' });
    return reply.code(500).send({ error: 'internal error' });
  });

  const page = (query) => {
    const l = parseInt(query.limit, 10);
    const o = parseInt(query.offset, 10);
    return [Number.isNaN(l) ? 20 : Math.min(Math.max(l, 1), 100), Number.isNaN(o) || o < 0 ? 0 : o];
  };
  const id = (s) => (/^-?\d+$/.test(s) ? s : '0'); // non-numeric -> 0 -> 404
  const jsonObject = (raw) => {
    try {
      const v = JSON.parse(raw);
      if (v && typeof v === 'object' && !Array.isArray(v)) return v;
    } catch {}
    throw new HttpError(400, 'body must be a JSON object');
  };
  const list = async (req, sql, extra = []) => {
    const [limit, offset] = page(req.query);
    const { rows } = await q(sql, [...extra, limit, offset]);
    return { data: rows, limit, offset };
  };
  const one = (reply, rows, status = 200) =>
    rows.length ? reply.code(status).send(rows[0]) : reply.code(404).send({ error: 'not found' });
  const write = async (req, reply, status, sql, withId) => {
    jsonObject(req.body);
    const { rows } = await q(sql, withId ? [id(req.params.id), req.body] : [req.body]);
    return one(reply, rows, status);
  };
  const del = async (req, reply, sql) => {
    const { rowCount } = await q(sql, [id(req.params.id)]);
    return rowCount ? reply.code(204).send() : reply.code(404).send({ error: 'not found' });
  };

  // Loads all items for the given orders in one query and groups them by order_id.
  const attachItems = async (orders) => {
    if (!orders.length) return;
    const { rows } = await q(`SELECT ${ITEM_COLS} FROM order_items WHERE order_id = ANY($1::bigint[]) ORDER BY order_id, id`,
      [orders.map((o) => o.id)]);
    const byOrder = new Map();
    for (const it of rows) {
      const arr = byOrder.get(it.order_id);
      arr ? arr.push(it) : byOrder.set(it.order_id, [it]);
    }
    for (const o of orders) o.items = byOrder.get(o.id) || [];
  };

  app.get('/health', async () => 'ok');

  // -------------------------------------------------------------- users
  app.get('/users', (req) => list(req, `SELECT ${USER_COLS} FROM (SELECT * FROM users ORDER BY id LIMIT $1 OFFSET $2) users ORDER BY id`));
  app.get('/users/:id', async (req, reply) =>
    one(reply, (await q(`SELECT ${USER_COLS} FROM users WHERE id = $1`, [id(req.params.id)])).rows));
  app.post('/users', (req, reply) => write(req, reply, 201, `INSERT INTO users (email, username, password_hash, first_name, last_name, phone, date_of_birth, gender,
  address_line1, address_line2, city, state, postal_code, country)
SELECT email, username, 'not-a-real-hash', first_name, last_name, phone, date_of_birth, gender,
  address_line1, address_line2, city, state, postal_code, COALESCE(country, 'US')
FROM jsonb_to_record($1::jsonb) AS x(email text, username text, first_name text, last_name text, phone text,
  date_of_birth date, gender text, address_line1 text, address_line2 text, city text, state text,
  postal_code text, country text)
RETURNING ${USER_COLS}`));
  app.put('/users/:id', (req, reply) => write(req, reply, 200, `UPDATE users SET
  first_name = COALESCE($2::jsonb->>'first_name', first_name), last_name = COALESCE($2::jsonb->>'last_name', last_name),
  phone = COALESCE($2::jsonb->>'phone', phone), address_line1 = COALESCE($2::jsonb->>'address_line1', address_line1),
  city = COALESCE($2::jsonb->>'city', city), state = COALESCE($2::jsonb->>'state', state),
  postal_code = COALESCE($2::jsonb->>'postal_code', postal_code),
  is_active = COALESCE(($2::jsonb->>'is_active')::boolean, is_active),
  loyalty_points = COALESCE(($2::jsonb->>'loyalty_points')::int, loyalty_points), updated_at = now()
WHERE id = $1 RETURNING ${USER_COLS}`, true));
  app.delete('/users/:id', (req, reply) => del(req, reply, 'DELETE FROM users WHERE id = $1'));
  app.get('/users/:id/orders', async (req) => {
    const res = await list(req, `SELECT ${ORDER_COLS} FROM orders WHERE user_id = $1 ORDER BY placed_at DESC, id DESC LIMIT $2 OFFSET $3`,
      [id(req.params.id)]);
    await attachItems(res.data);
    return res;
  });

  // -------------------------------------------------------------- products
  app.get('/products', (req) => list(req, `SELECT ${PRODUCT_COLS} FROM (SELECT * FROM products ORDER BY id LIMIT $1 OFFSET $2) products ORDER BY id`));
  app.get('/products/search', async (req) => {
    const text = (req.query.q || '').trim();
    if (!text) throw new HttpError(400, 'q is required');
    const int = (v) => (/^-?\d+$/.test(v || '') ? v : null);
    return list(req, `SELECT ${PRODUCT_COLS} FROM (SELECT *, ts_rank(search_vector, plainto_tsquery('english', $1)) AS rank FROM products
WHERE search_vector @@ plainto_tsquery('english', $1)
  AND ($2::text IS NULL OR category = $2)
  AND ($3::bigint IS NULL OR price_cents >= $3)
  AND ($4::bigint IS NULL OR price_cents <= $4)
  AND (NOT $5::boolean OR stock_quantity > 0)
ORDER BY rank DESC, id
LIMIT $6 OFFSET $7) products ORDER BY rank DESC, id`, [text, req.query.category || null, int(req.query.min_price), int(req.query.max_price),
      req.query.in_stock === 'true']);
  });
  app.get('/products/:id', async (req, reply) =>
    one(reply, (await q(`SELECT ${PRODUCT_COLS} FROM products WHERE id = $1`, [id(req.params.id)])).rows));
  app.post('/products', (req, reply) => write(req, reply, 201, `INSERT INTO products (sku, name, description, category, subcategory, brand, price_cents, cost_cents, currency,
  stock_quantity, weight_grams, length_mm, width_mm, height_mm, color)
SELECT sku, name, COALESCE(description, ''), category, subcategory, brand, price_cents, COALESCE(cost_cents, 0),
  COALESCE(currency, 'USD'), COALESCE(stock_quantity, 0), weight_grams, length_mm, width_mm, height_mm, color
FROM jsonb_to_record($1::jsonb) AS x(sku text, name text, description text, category text, subcategory text,
  brand text, price_cents bigint, cost_cents bigint, currency text, stock_quantity int, weight_grams int,
  length_mm int, width_mm int, height_mm int, color text)
RETURNING ${PRODUCT_COLS}`));
  app.put('/products/:id', (req, reply) => write(req, reply, 200, `UPDATE products SET
  name = COALESCE($2::jsonb->>'name', name), description = COALESCE($2::jsonb->>'description', description),
  category = COALESCE($2::jsonb->>'category', category),
  price_cents = COALESCE(($2::jsonb->>'price_cents')::bigint, price_cents),
  stock_quantity = COALESCE(($2::jsonb->>'stock_quantity')::int, stock_quantity),
  is_active = COALESCE(($2::jsonb->>'is_active')::boolean, is_active), updated_at = now()
WHERE id = $1 RETURNING ${PRODUCT_COLS}`, true));
  app.delete('/products/:id', (req, reply) => del(req, reply, 'DELETE FROM products WHERE id = $1'));

  // -------------------------------------------------------------- orders
  app.get('/orders', async (req) => {
    const include = `,${req.query.include || ''},`;
    const res = await list(req, `SELECT ${ORDER_COLS} FROM (SELECT * FROM orders ORDER BY id DESC LIMIT $1 OFFSET $2) orders ORDER BY id DESC`);
    if (include.includes(',user,') && res.data.length) {
      const { rows } = await q(`SELECT ${USER_COLS} FROM users WHERE id = ANY($1::bigint[])`, [res.data.map((o) => o.user_id)]);
      const byId = new Map(rows.map((u) => [u.id, u]));
      for (const o of res.data) o.user = byId.get(o.user_id);
    }
    if (include.includes(',items,')) await attachItems(res.data);
    return res;
  });
  app.get('/orders/:id', async (req, reply) => {
    const { rows } = await q(`SELECT ${ORDER_COLS} FROM orders WHERE id = $1`, [id(req.params.id)]);
    await attachItems(rows);
    return one(reply, rows);
  });
  app.post('/orders', async (req, reply) => {
    const body = jsonObject(req.body);
    const items = body.items;
    const isInt = Number.isInteger;
    if (!isInt(body.user_id) || body.user_id <= 0 || !Array.isArray(items) || items.length < 1 || items.length > 50 ||
        items.some((i) => !i || !isInt(i.product_id) || i.product_id <= 0 || !isInt(i.quantity) || i.quantity < 1 || i.quantity > 1000)) {
      throw new HttpError(400, 'user_id and 1-50 valid items are required');
    }
    const pids = items.map((i) => i.product_id);
    const qtys = items.map((i) => i.quantity);

    const c = await db.connect();
    try {
      await c.query('BEGIN');
      const prices = new Map((await c.query({ name: 'prices', text: 'SELECT id, price_cents FROM products WHERE id = ANY($1::bigint[])', values: [pids] }))
        .rows.map((r) => [r.id, r.price_cents]));
      const units = pids.map((p) => prices.get(p));
      if (units.includes(undefined)) throw new HttpError(400, 'unknown product');
      const subtotal = units.reduce((s, u, i) => s + u * qtys[i], 0);
      const tax = Math.floor((subtotal * 7) / 100);
      const shipping = subtotal >= 5000 ? 0 : 499;
      let order;
      try {
        ({ rows: [order] } = await c.query({ name: 'new_order', text: `INSERT INTO orders (user_id, subtotal_cents, tax_cents, shipping_cents, total_cents, payment_method,
  shipping_address, shipping_city, shipping_postal_code, shipping_country, notes)
VALUES ($1, $2, $3, $4, $5, COALESCE($6::jsonb->>'payment_method', 'card'), $6::jsonb->>'shipping_address',
  $6::jsonb->>'shipping_city', $6::jsonb->>'shipping_postal_code',
  COALESCE($6::jsonb->>'shipping_country', 'US'), $6::jsonb->>'notes')
RETURNING ${ORDER_COLS}`, values: [body.user_id, subtotal, tax, shipping, subtotal + tax + shipping, req.body] }));
      } catch (e) {
        throw e.code === '23503' ? new HttpError(400, 'unknown user') : e;
      }
      order.items = (await c.query({ name: 'new_items', text: `INSERT INTO order_items (order_id, product_id, quantity, unit_price_cents, line_total_cents)
SELECT $1, t.p, t.q, t.u, t.u * t.q FROM unnest($2::bigint[], $3::int[], $4::bigint[]) AS t(p, q, u)
RETURNING ${ITEM_COLS}`, values: [order.id, pids, qtys, units] })).rows;
      await c.query('COMMIT');
      return reply.code(201).send(order);
    } catch (e) {
      await c.query('ROLLBACK');
      throw e;
    } finally {
      c.release();
    }
  });
  app.put('/orders/:id', (req, reply) => write(req, reply, 200, `UPDATE orders SET
  status = COALESCE($2::jsonb->>'status', status), payment_status = COALESCE($2::jsonb->>'payment_status', payment_status),
  tracking_number = COALESCE($2::jsonb->>'tracking_number', tracking_number),
  notes = COALESCE($2::jsonb->>'notes', notes), updated_at = now()
WHERE id = $1 RETURNING ${ORDER_COLS}`, true));
  app.delete('/orders/:id', (req, reply) => del(req, reply, 'DELETE FROM orders WHERE id = $1'));

  app.listen({ host: '0.0.0.0', port: 8080 });
}
