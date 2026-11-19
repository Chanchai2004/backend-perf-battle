use axum::{
    body::Bytes,
    extract::{Path, Query, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::get,
    Json, Router,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sqlx::{postgres::PgPoolOptions, FromRow, PgPool};
use std::collections::HashMap;

const USER_COLS: &str = "id, email, username, first_name, last_name, phone, date_of_birth::text AS date_of_birth, gender,
address_line1, address_line2, city, state, postal_code, country::text AS country, is_active, is_verified,
loyalty_points, iso(created_at) AS created_at, iso(updated_at) AS updated_at";
const PRODUCT_COLS: &str = "id, sku, name, description, category, subcategory, brand, price_cents, currency::text AS currency,
stock_quantity, weight_grams, length_mm, width_mm, height_mm, color, rating_x10, review_count, is_active,
iso(created_at) AS created_at, iso(updated_at) AS updated_at";
const ORDER_COLS: &str = "id, user_id, order_number, status, subtotal_cents, tax_cents, shipping_cents, discount_cents,
total_cents, currency::text AS currency, payment_method, payment_status, shipping_address, shipping_city,
shipping_postal_code, shipping_country::text AS shipping_country, tracking_number, notes,
iso(placed_at) AS placed_at, iso(updated_at) AS updated_at";
const ITEM_COLS: &str = "id, order_id, product_id, quantity, unit_price_cents, discount_cents, line_total_cents,
iso(created_at) AS created_at";

#[derive(Serialize, FromRow, Clone)]
struct User {
    id: i64,
    email: String,
    username: String,
    first_name: String,
    last_name: String,
    phone: Option<String>,
    date_of_birth: Option<String>,
    gender: Option<String>,
    address_line1: Option<String>,
    address_line2: Option<String>,
    city: Option<String>,
    state: Option<String>,
    postal_code: Option<String>,
    country: String,
    is_active: bool,
    is_verified: bool,
    loyalty_points: i32,
    created_at: String,
    updated_at: String,
}

#[derive(Serialize, FromRow)]
struct Product {
    id: i64,
    sku: String,
    name: String,
    description: String,
    category: String,
    subcategory: Option<String>,
    brand: Option<String>,
    price_cents: i64,
    currency: String,
    stock_quantity: i32,
    weight_grams: Option<i32>,
    length_mm: Option<i32>,
    width_mm: Option<i32>,
    height_mm: Option<i32>,
    color: Option<String>,
    rating_x10: i16,
    review_count: i32,
    is_active: bool,
    created_at: String,
    updated_at: String,
}

#[derive(Serialize, FromRow)]
struct Item {
    id: i64,
    order_id: i64,
    product_id: i64,
    quantity: i32,
    unit_price_cents: i64,
    discount_cents: i64,
    line_total_cents: i64,
    created_at: String,
}

#[derive(Serialize, FromRow)]
struct Order {
    id: i64,
    user_id: i64,
    order_number: String,
    status: String,
    subtotal_cents: i64,
    tax_cents: i64,
    shipping_cents: i64,
    discount_cents: i64,
    total_cents: i64,
    currency: String,
    payment_method: String,
    payment_status: String,
    shipping_address: Option<String>,
    shipping_city: Option<String>,
    shipping_postal_code: Option<String>,
    shipping_country: String,
    tracking_number: Option<String>,
    notes: Option<String>,
    placed_at: String,
    updated_at: String,
    #[sqlx(skip)]
    #[serde(skip_serializing_if = "Option::is_none")]
    user: Option<User>,
    #[sqlx(skip)]
    #[serde(skip_serializing_if = "Option::is_none")]
    items: Option<Vec<Item>>,
}

type Res = Result<Response, ApiError>;
type Params = Query<HashMap<String, String>>;

#[derive(Clone, Copy)]
struct ApiError(StatusCode, &'static str);

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.0, Json(json!({ "error": self.1 }))).into_response()
    }
}

impl From<sqlx::Error> for ApiError {
    fn from(e: sqlx::Error) -> Self {
        let code = e.as_database_error().and_then(|d| d.code()).unwrap_or_default();
        match code.as_ref() {
            "23505" | "23503" => ApiError(StatusCode::CONFLICT, "conflict"),
            c if c.starts_with("22") || c == "23502" || c == "23514" => ApiError(StatusCode::BAD_REQUEST, "invalid input"),
            _ => ApiError(StatusCode::INTERNAL_SERVER_ERROR, "internal error"),
        }
    }
}

const NOT_FOUND: ApiError = ApiError(StatusCode::NOT_FOUND, "not found");

fn page(q: &HashMap<String, String>) -> (i64, i64) {
    let limit = q.get("limit").and_then(|v| v.parse::<i64>().ok()).unwrap_or(20).clamp(1, 100);
    let offset = q.get("offset").and_then(|v| v.parse::<i64>().ok()).filter(|v| *v >= 0).unwrap_or(0);
    (limit, offset)
}

fn listed<T: Serialize>(data: Vec<T>, limit: i64, offset: i64) -> Response {
    Json(json!({ "data": data, "limit": limit, "offset": offset })).into_response()
}

fn json_object(body: &Bytes) -> Result<String, ApiError> {
    match serde_json::from_slice::<Value>(body) {
        Ok(Value::Object(_)) => Ok(String::from_utf8_lossy(body).into_owned()),
        _ => Err(ApiError(StatusCode::BAD_REQUEST, "body must be a JSON object")),
    }
}

fn one<T: Serialize>(row: Option<T>, status: StatusCode) -> Res {
    row.map(|r| (status, Json(r)).into_response()).ok_or(NOT_FOUND)
}

fn deleted(rows: u64) -> Res {
    if rows == 0 { Err(NOT_FOUND) } else { Ok(StatusCode::NO_CONTENT.into_response()) }
}

// path ids: non-numeric -> 0 -> 404, same as every other backend
fn pid(s: &str) -> i64 {
    s.parse().unwrap_or(0)
}

// ---------------------------------------------------------------- users

async fn list_users(State(db): State<PgPool>, Query(q): Params) -> Res {
    let (limit, offset) = page(&q);
    let rows: Vec<User> = sqlx::query_as(&format!("SELECT {USER_COLS} FROM (SELECT * FROM users ORDER BY id LIMIT $1 OFFSET $2) users ORDER BY id"))
        .bind(limit).bind(offset).fetch_all(&db).await?;
    Ok(listed(rows, limit, offset))
}

async fn get_user(State(db): State<PgPool>, Path(id): Path<String>) -> Res {
    let row: Option<User> = sqlx::query_as(&format!("SELECT {USER_COLS} FROM users WHERE id = $1"))
        .bind(pid(&id)).fetch_optional(&db).await?;
    one(row, StatusCode::OK)
}

async fn create_user(State(db): State<PgPool>, body: Bytes) -> Res {
    let body = json_object(&body)?;
    let row: Option<User> = sqlx::query_as(&format!(
        "INSERT INTO users (email, username, password_hash, first_name, last_name, phone, date_of_birth, gender,
  address_line1, address_line2, city, state, postal_code, country)
SELECT email, username, 'not-a-real-hash', first_name, last_name, phone, date_of_birth, gender,
  address_line1, address_line2, city, state, postal_code, COALESCE(country, 'US')
FROM jsonb_to_record($1::jsonb) AS x(email text, username text, first_name text, last_name text, phone text,
  date_of_birth date, gender text, address_line1 text, address_line2 text, city text, state text,
  postal_code text, country text)
RETURNING {USER_COLS}"))
        .bind(body).fetch_optional(&db).await?;
    one(row, StatusCode::CREATED)
}

async fn update_user(State(db): State<PgPool>, Path(id): Path<String>, body: Bytes) -> Res {
    let body = json_object(&body)?;
    let row: Option<User> = sqlx::query_as(&format!(
        "UPDATE users SET
  first_name = COALESCE($2::jsonb->>'first_name', first_name), last_name = COALESCE($2::jsonb->>'last_name', last_name),
  phone = COALESCE($2::jsonb->>'phone', phone), address_line1 = COALESCE($2::jsonb->>'address_line1', address_line1),
  city = COALESCE($2::jsonb->>'city', city), state = COALESCE($2::jsonb->>'state', state),
  postal_code = COALESCE($2::jsonb->>'postal_code', postal_code),
  is_active = COALESCE(($2::jsonb->>'is_active')::boolean, is_active),
  loyalty_points = COALESCE(($2::jsonb->>'loyalty_points')::int, loyalty_points), updated_at = now()
WHERE id = $1 RETURNING {USER_COLS}"))
        .bind(pid(&id)).bind(body).fetch_optional(&db).await?;
    one(row, StatusCode::OK)
}

async fn delete_user(State(db): State<PgPool>, Path(id): Path<String>) -> Res {
    deleted(sqlx::query("DELETE FROM users WHERE id = $1").bind(pid(&id)).execute(&db).await?.rows_affected())
}

async fn user_orders(State(db): State<PgPool>, Path(id): Path<String>, Query(q): Params) -> Res {
    let (limit, offset) = page(&q);
    let mut orders: Vec<Order> = sqlx::query_as(&format!(
        "SELECT {ORDER_COLS} FROM orders WHERE user_id = $1 ORDER BY placed_at DESC, id DESC LIMIT $2 OFFSET $3"))
        .bind(pid(&id)).bind(limit).bind(offset).fetch_all(&db).await?;
    attach_items(&db, &mut orders).await?;
    Ok(listed(orders, limit, offset))
}

// ---------------------------------------------------------------- products

async fn list_products(State(db): State<PgPool>, Query(q): Params) -> Res {
    let (limit, offset) = page(&q);
    let rows: Vec<Product> = sqlx::query_as(&format!("SELECT {PRODUCT_COLS} FROM (SELECT * FROM products ORDER BY id LIMIT $1 OFFSET $2) products ORDER BY id"))
        .bind(limit).bind(offset).fetch_all(&db).await?;
    Ok(listed(rows, limit, offset))
}

async fn search_products(State(db): State<PgPool>, Query(q): Params) -> Res {
    let text = q.get("q").map(|s| s.trim()).unwrap_or("");
    if text.is_empty() {
        return Err(ApiError(StatusCode::BAD_REQUEST, "q is required"));
    }
    let (limit, offset) = page(&q);
    let int = |k: &str| q.get(k).and_then(|v| v.parse::<i64>().ok());
    let rows: Vec<Product> = sqlx::query_as(&format!(
        "SELECT {PRODUCT_COLS} FROM (SELECT *, ts_rank(search_vector, plainto_tsquery('english', $1)) AS rank FROM products
WHERE search_vector @@ plainto_tsquery('english', $1)
  AND ($2::text IS NULL OR category = $2)
  AND ($3::bigint IS NULL OR price_cents >= $3)
  AND ($4::bigint IS NULL OR price_cents <= $4)
  AND (NOT $5::boolean OR stock_quantity > 0)
ORDER BY rank DESC, id
LIMIT $6 OFFSET $7) products ORDER BY rank DESC, id"))
        .bind(text)
        .bind(q.get("category").filter(|s| !s.is_empty()))
        .bind(int("min_price"))
        .bind(int("max_price"))
        .bind(q.get("in_stock").map(|s| s == "true").unwrap_or(false))
        .bind(limit).bind(offset)
        .fetch_all(&db).await?;
    Ok(listed(rows, limit, offset))
}

async fn get_product(State(db): State<PgPool>, Path(id): Path<String>) -> Res {
    let row: Option<Product> = sqlx::query_as(&format!("SELECT {PRODUCT_COLS} FROM products WHERE id = $1"))
        .bind(pid(&id)).fetch_optional(&db).await?;
    one(row, StatusCode::OK)
}

async fn create_product(State(db): State<PgPool>, body: Bytes) -> Res {
    let body = json_object(&body)?;
    let row: Option<Product> = sqlx::query_as(&format!(
        "INSERT INTO products (sku, name, description, category, subcategory, brand, price_cents, cost_cents, currency,
  stock_quantity, weight_grams, length_mm, width_mm, height_mm, color)
SELECT sku, name, COALESCE(description, ''), category, subcategory, brand, price_cents, COALESCE(cost_cents, 0),
  COALESCE(currency, 'USD'), COALESCE(stock_quantity, 0), weight_grams, length_mm, width_mm, height_mm, color
FROM jsonb_to_record($1::jsonb) AS x(sku text, name text, description text, category text, subcategory text,
  brand text, price_cents bigint, cost_cents bigint, currency text, stock_quantity int, weight_grams int,
  length_mm int, width_mm int, height_mm int, color text)
RETURNING {PRODUCT_COLS}"))
        .bind(body).fetch_optional(&db).await?;
    one(row, StatusCode::CREATED)
}

async fn update_product(State(db): State<PgPool>, Path(id): Path<String>, body: Bytes) -> Res {
    let body = json_object(&body)?;
    let row: Option<Product> = sqlx::query_as(&format!(
        "UPDATE products SET
  name = COALESCE($2::jsonb->>'name', name), description = COALESCE($2::jsonb->>'description', description),
  category = COALESCE($2::jsonb->>'category', category),
  price_cents = COALESCE(($2::jsonb->>'price_cents')::bigint, price_cents),
  stock_quantity = COALESCE(($2::jsonb->>'stock_quantity')::int, stock_quantity),
  is_active = COALESCE(($2::jsonb->>'is_active')::boolean, is_active), updated_at = now()
WHERE id = $1 RETURNING {PRODUCT_COLS}"))
        .bind(pid(&id)).bind(body).fetch_optional(&db).await?;
    one(row, StatusCode::OK)
}

async fn delete_product(State(db): State<PgPool>, Path(id): Path<String>) -> Res {
    deleted(sqlx::query("DELETE FROM products WHERE id = $1").bind(pid(&id)).execute(&db).await?.rows_affected())
}

// ---------------------------------------------------------------- orders

/// Loads all items for the given orders in one query and groups them by order_id.
async fn attach_items(db: &PgPool, orders: &mut [Order]) -> Result<(), sqlx::Error> {
    if orders.is_empty() {
        return Ok(());
    }
    let ids: Vec<i64> = orders.iter().map(|o| o.id).collect();
    let items: Vec<Item> = sqlx::query_as(&format!(
        "SELECT {ITEM_COLS} FROM order_items WHERE order_id = ANY($1::bigint[]) ORDER BY order_id, id"))
        .bind(&ids).fetch_all(db).await?;
    let mut by_order: HashMap<i64, Vec<Item>> = HashMap::with_capacity(orders.len());
    for it in items {
        by_order.entry(it.order_id).or_default().push(it);
    }
    for o in orders.iter_mut() {
        o.items = Some(by_order.remove(&o.id).unwrap_or_default());
    }
    Ok(())
}

async fn list_orders(State(db): State<PgPool>, Query(q): Params) -> Res {
    let (limit, offset) = page(&q);
    let include = format!(",{},", q.get("include").map(String::as_str).unwrap_or(""));
    let mut orders: Vec<Order> = sqlx::query_as(&format!("SELECT {ORDER_COLS} FROM (SELECT * FROM orders ORDER BY id DESC LIMIT $1 OFFSET $2) orders ORDER BY id DESC"))
        .bind(limit).bind(offset).fetch_all(&db).await?;
    if include.contains(",user,") && !orders.is_empty() {
        let ids: Vec<i64> = orders.iter().map(|o| o.user_id).collect();
        let users: Vec<User> = sqlx::query_as(&format!("SELECT {USER_COLS} FROM users WHERE id = ANY($1::bigint[])"))
            .bind(&ids).fetch_all(&db).await?;
        let by_id: HashMap<i64, User> = users.into_iter().map(|u| (u.id, u)).collect();
        for o in orders.iter_mut() {
            o.user = by_id.get(&o.user_id).cloned(); // a user can own several orders on one page
        }
    }
    if include.contains(",items,") {
        attach_items(&db, &mut orders).await?;
    }
    Ok(listed(orders, limit, offset))
}

async fn get_order(State(db): State<PgPool>, Path(id): Path<String>) -> Res {
    let row: Option<Order> = sqlx::query_as(&format!("SELECT {ORDER_COLS} FROM orders WHERE id = $1"))
        .bind(pid(&id)).fetch_optional(&db).await?;
    let mut orders: Vec<Order> = row.into_iter().collect();
    attach_items(&db, &mut orders).await?;
    one(orders.pop(), StatusCode::OK)
}

#[derive(Deserialize)]
struct NewItem {
    product_id: i64,
    quantity: i32,
}

#[derive(Deserialize)]
struct NewOrder {
    user_id: i64,
    items: Vec<NewItem>,
}

async fn create_order(State(db): State<PgPool>, body: Bytes) -> Res {
    let raw = json_object(&body)?;
    let bad = ApiError(StatusCode::BAD_REQUEST, "user_id and 1-50 valid items are required");
    let input: NewOrder = serde_json::from_str(&raw).map_err(|_| bad)?;
    if input.user_id <= 0 || input.items.is_empty() || input.items.len() > 50
        || input.items.iter().any(|i| i.product_id <= 0 || !(1..=1000).contains(&i.quantity))
    {
        return Err(bad);
    }
    let pids: Vec<i64> = input.items.iter().map(|i| i.product_id).collect();
    let qtys: Vec<i32> = input.items.iter().map(|i| i.quantity).collect();

    let mut tx = db.begin().await?;
    let prices: HashMap<i64, i64> = sqlx::query_as::<_, (i64, i64)>("SELECT id, price_cents FROM products WHERE id = ANY($1::bigint[])")
        .bind(&pids).fetch_all(&mut *tx).await?.into_iter().collect();
    let mut units = Vec::with_capacity(pids.len());
    let mut subtotal = 0i64;
    for it in &input.items {
        let p = *prices.get(&it.product_id).ok_or(ApiError(StatusCode::BAD_REQUEST, "unknown product"))?;
        units.push(p);
        subtotal += p * it.quantity as i64;
    }
    let tax = subtotal * 7 / 100;
    let shipping = if subtotal >= 5000 { 0 } else { 499 };

    let mut order: Order = sqlx::query_as(&format!(
        "INSERT INTO orders (user_id, subtotal_cents, tax_cents, shipping_cents, total_cents, payment_method,
  shipping_address, shipping_city, shipping_postal_code, shipping_country, notes)
VALUES ($1, $2, $3, $4, $5, COALESCE($6::jsonb->>'payment_method', 'card'), $6::jsonb->>'shipping_address',
  $6::jsonb->>'shipping_city', $6::jsonb->>'shipping_postal_code',
  COALESCE($6::jsonb->>'shipping_country', 'US'), $6::jsonb->>'notes')
RETURNING {ORDER_COLS}"))
        .bind(input.user_id).bind(subtotal).bind(tax).bind(shipping).bind(subtotal + tax + shipping).bind(&raw)
        .fetch_one(&mut *tx).await
        .map_err(|e| {
            let fk = e.as_database_error().and_then(|d| d.code()).as_deref() == Some("23503");
            if fk { ApiError(StatusCode::BAD_REQUEST, "unknown user") } else { ApiError::from(e) }
        })?;
    let items: Vec<Item> = sqlx::query_as(&format!(
        "INSERT INTO order_items (order_id, product_id, quantity, unit_price_cents, line_total_cents)
SELECT $1, t.p, t.q, t.u, t.u * t.q FROM unnest($2::bigint[], $3::int[], $4::bigint[]) AS t(p, q, u)
RETURNING {ITEM_COLS}"))
        .bind(order.id).bind(&pids).bind(&qtys).bind(&units)
        .fetch_all(&mut *tx).await?;
    tx.commit().await?;
    order.items = Some(items);
    Ok((StatusCode::CREATED, Json(order)).into_response())
}

async fn update_order(State(db): State<PgPool>, Path(id): Path<String>, body: Bytes) -> Res {
    let body = json_object(&body)?;
    let row: Option<Order> = sqlx::query_as(&format!(
        "UPDATE orders SET
  status = COALESCE($2::jsonb->>'status', status), payment_status = COALESCE($2::jsonb->>'payment_status', payment_status),
  tracking_number = COALESCE($2::jsonb->>'tracking_number', tracking_number),
  notes = COALESCE($2::jsonb->>'notes', notes), updated_at = now()
WHERE id = $1 RETURNING {ORDER_COLS}"))
        .bind(pid(&id)).bind(body).fetch_optional(&db).await?;
    one(row, StatusCode::OK)
}

async fn delete_order(State(db): State<PgPool>, Path(id): Path<String>) -> Res {
    deleted(sqlx::query("DELETE FROM orders WHERE id = $1").bind(pid(&id)).execute(&db).await?.rows_affected())
}

fn main() {
    // 2 worker threads = the container's 2-CPU limit.
    tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()
        .unwrap()
        .block_on(serve());
}

async fn serve() {
    let size: u32 = std::env::var("POOL_SIZE").ok().and_then(|v| v.parse().ok()).unwrap_or(20);
    let db = PgPoolOptions::new()
        .max_connections(size)
        .min_connections(size)
        .connect(&std::env::var("DATABASE_URL").unwrap())
        .await
        .unwrap();
    let app = Router::new()
        .route("/health", get(|| async { "ok" }))
        .route("/users", get(list_users).post(create_user))
        .route("/users/:id", get(get_user).put(update_user).delete(delete_user))
        .route("/users/:id/orders", get(user_orders))
        .route("/products", get(list_products).post(create_product))
        .route("/products/search", get(search_products))
        .route("/products/:id", get(get_product).put(update_product).delete(delete_product))
        .route("/orders", get(list_orders).post(create_order))
        .route("/orders/:id", get(get_order).put(update_order).delete(delete_order))
        .with_state(db);
    let listener = tokio::net::TcpListener::bind("0.0.0.0:8080").await.unwrap();
    axum::serve(listener, app).await.unwrap();
}
