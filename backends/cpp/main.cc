// Drogon + its async PostgreSQL client (one shared pool of POOL_SIZE connections).
#include <drogon/drogon.h>

#include <cstdlib>
#include <optional>
#include <regex>
#include <unordered_map>

using namespace drogon;
using drogon::orm::Result;
using drogon::orm::Row;

static const std::string USER_COLS = R"(id, email, username, first_name, last_name, phone, date_of_birth::text AS date_of_birth, gender,
address_line1, address_line2, city, state, postal_code, country::text AS country, is_active, is_verified,
loyalty_points, iso(created_at) AS created_at, iso(updated_at) AS updated_at)";
static const std::string PRODUCT_COLS = R"(id, sku, name, description, category, subcategory, brand, price_cents, currency::text AS currency,
stock_quantity, weight_grams, length_mm, width_mm, height_mm, color, rating_x10, review_count, is_active,
iso(created_at) AS created_at, iso(updated_at) AS updated_at)";
static const std::string ORDER_COLS = R"(id, user_id, order_number, status, subtotal_cents, tax_cents, shipping_cents, discount_cents,
total_cents, currency::text AS currency, payment_method, payment_status, shipping_address, shipping_city,
shipping_postal_code, shipping_country::text AS shipping_country, tracking_number, notes,
iso(placed_at) AS placed_at, iso(updated_at) AS updated_at)";
static const std::string ITEM_COLS = R"(id, order_id, product_id, quantity, unit_price_cents, discount_cents, line_total_cents,
iso(created_at) AS created_at)";

// Column layout of each SELECT list above, in order: name + JSON kind.
enum Kind { I, S, B };
struct Col { const char *name; Kind kind; };
using Cols = std::vector<Col>;
static const Cols USER = {{"id", I}, {"email", S}, {"username", S}, {"first_name", S}, {"last_name", S}, {"phone", S},
    {"date_of_birth", S}, {"gender", S}, {"address_line1", S}, {"address_line2", S}, {"city", S}, {"state", S},
    {"postal_code", S}, {"country", S}, {"is_active", B}, {"is_verified", B}, {"loyalty_points", I},
    {"created_at", S}, {"updated_at", S}};
static const Cols PRODUCT = {{"id", I}, {"sku", S}, {"name", S}, {"description", S}, {"category", S},
    {"subcategory", S}, {"brand", S}, {"price_cents", I}, {"currency", S}, {"stock_quantity", I},
    {"weight_grams", I}, {"length_mm", I}, {"width_mm", I}, {"height_mm", I}, {"color", S}, {"rating_x10", I},
    {"review_count", I}, {"is_active", B}, {"created_at", S}, {"updated_at", S}};
static const Cols ORDER = {{"id", I}, {"user_id", I}, {"order_number", S}, {"status", S}, {"subtotal_cents", I},
    {"tax_cents", I}, {"shipping_cents", I}, {"discount_cents", I}, {"total_cents", I}, {"currency", S},
    {"payment_method", S}, {"payment_status", S}, {"shipping_address", S}, {"shipping_city", S},
    {"shipping_postal_code", S}, {"shipping_country", S}, {"tracking_number", S}, {"notes", S},
    {"placed_at", S}, {"updated_at", S}};
static const Cols ITEM = {{"id", I}, {"order_id", I}, {"product_id", I}, {"quantity", I},
    {"unit_price_cents", I}, {"discount_cents", I}, {"line_total_cents", I}, {"created_at", S}};

struct HttpErr : std::runtime_error {
    int status;
    HttpErr(int s, const char *m) : std::runtime_error(m), status(s) {}
};

// Responses are written straight into a string: jsoncpp's map-based DOM cost ~2x throughput.
// Results come back in Postgres text format, so integers are copied as-is and bools are "t"/"f".
static void str(std::string &o, const char *s) {
    o += '"';
    for (; *s; ++s) {
        unsigned char c = *s;
        if (c == '"' || c == '\\') { o += '\\'; o += c; }
        else if (c < 0x20) { char b[8]; std::snprintf(b, sizeof b, "\\u%04x", c); o += b; }
        else o += c;
    }
    o += '"';
}

// Appends {"col":value,... without the closing brace, so callers can add nested fields.
static void open(std::string &o, const Row &r, const Cols &cols) {
    o += '{';
    for (Row::SizeType i = 0; i < cols.size(); ++i) {
        if (i) o += ',';
        o += '"'; o += cols[i].name; o += "\":";
        auto f = r[i];
        if (f.isNull()) o += "null";
        else if (cols[i].kind == I) o += f.c_str();
        else if (cols[i].kind == B) o += f.c_str()[0] == 't' ? "true" : "false";
        else str(o, f.c_str());
    }
}

static std::string obj(const Row &r, const Cols &cols) { std::string o; open(o, r, cols); return o += '}'; }

static std::string rows(const Result &rs, const Cols &cols) {  // comma-joined objects
    std::string o;
    o.reserve(rs.size() * 512);
    for (const auto &r : rs) { if (!o.empty()) o += ','; open(o, r, cols); o += '}'; }
    return o;
}

static HttpResponsePtr json(std::string body, int status = 200) {
    auto resp = HttpResponse::newHttpResponse();
    resp->setStatusCode(static_cast<HttpStatusCode>(status));
    resp->setContentTypeCode(CT_APPLICATION_JSON);
    resp->setBody(std::move(body));
    return resp;
}

static HttpResponsePtr err(int status, const char *msg) {
    return json(std::string("{\"error\":\"") + msg + "\"}", status);
}

static std::optional<int64_t> toInt(const std::string &s) {
    size_t start = !s.empty() && s[0] == '-';
    if (s.size() == start || s.find_first_not_of("0123456789", start) != std::string::npos) return std::nullopt;
    try { return std::stoll(s); } catch (...) { return std::nullopt; }
}

static int64_t pid(const std::string &s) { return toInt(s).value_or(0); }  // non-numeric -> 0 -> 404

static std::pair<int64_t, int64_t> page(const HttpRequestPtr &req) {
    auto limit = toInt(req->getParameter("limit")).value_or(20);
    auto offset = toInt(req->getParameter("offset")).value_or(0);
    return {std::clamp<int64_t>(limit, 1, 100), std::max<int64_t>(offset, 0)};
}

static HttpResponsePtr listed(const std::string &data, int64_t limit, int64_t offset) {
    return json("{\"data\":[" + data + "],\"limit\":" + std::to_string(limit) + ",\"offset\":" + std::to_string(offset) + "}");
}

static Json::Value parseObject(const HttpRequestPtr &req) {
    Json::Value v;
    std::string errs;
    auto body = req->body();
    std::unique_ptr<Json::CharReader> reader(Json::CharReaderBuilder().newCharReader());
    if (!reader->parse(body.data(), body.data() + body.size(), &v, &errs) || !v.isObject())
        throw HttpErr(400, "body must be a JSON object");
    return v;
}

static std::string pgArray(const std::vector<int64_t> &xs) {
    std::string s = "{";
    for (size_t i = 0; i < xs.size(); ++i) s += (i ? "," : "") + std::to_string(xs[i]);
    return s + "}";
}

static orm::DbClientPtr db() { return app().getDbClient(); }

// Drogon 1.9's PgConnection reports every error as orm::Failure(PQerrorMessage) with no
// SQLSTATE, so the HTTP status is derived from Postgres' (English) message text.
// ponytail: message matching breaks if lc_messages changes; switch to sqlState() once Drogon exposes it.
static int statusFor(const orm::DrogonDbException &e) {
    std::string_view m = e.base().what();
    auto has = [&](const char *s) { return m.find(s) != std::string_view::npos; };
    if (has("duplicate key value") || has("violates foreign key constraint")) return 409;
    if (has("violates not-null constraint") || has("violates check constraint") || has("invalid input syntax") ||
        has("out of range") || has("invalid input value")) return 400;
    return 500;
}

// Runs a statement and turns SQL errors into HttpErr (the global handler maps those to responses).
template <typename... Args>
static Task<Result> sql(std::string q, Args... args) {
    try {
        co_return co_await db()->execSqlCoro(q, args...);
    } catch (const orm::DrogonDbException &e) {
        int st = statusFor(e);
        throw HttpErr(st, st == 409 ? "conflict" : st == 400 ? "invalid input" : "internal error");
    }
}

static HttpResponsePtr one(const Result &rs, const Cols &cols, int status = 200) {
    return rs.empty() ? err(404, "not found") : json(obj(rs[0], cols), status);
}

static HttpResponsePtr deleted(const Result &rs) {
    if (rs.affectedRows() == 0) return err(404, "not found");
    auto resp = HttpResponse::newHttpResponse();
    resp->setStatusCode(k204NoContent);
    return resp;
}

// Comma-joined order objects, optionally with "user" and "items" loaded in one query each.
static Task<std::string> ordersJson(const Result &rs, bool withUser, bool withItems) {
    std::string out;
    if (rs.empty()) co_return out;
    std::unordered_map<int64_t, std::string> users, items;  // user id -> object, order id -> joined items
    if (withUser) {
        std::vector<int64_t> ids;
        for (const auto &r : rs) ids.push_back(r[1].as<int64_t>());
        auto us = co_await sql("SELECT " + USER_COLS + " FROM users WHERE id = ANY($1::bigint[])", pgArray(ids));
        for (const auto &u : us) users.emplace(u[0].as<int64_t>(), obj(u, USER));
    }
    if (withItems) {
        std::vector<int64_t> ids;
        for (const auto &r : rs) ids.push_back(r[0].as<int64_t>());
        auto its = co_await sql(
            "SELECT " + ITEM_COLS + " FROM order_items WHERE order_id = ANY($1::bigint[]) ORDER BY order_id, id", pgArray(ids));
        for (const auto &it : its) {
            auto &s = items[it[1].as<int64_t>()];
            if (!s.empty()) s += ',';
            open(s, it, ITEM);
            s += '}';
        }
    }
    out.reserve(rs.size() * (withItems ? 2048 : 640));
    for (const auto &r : rs) {
        if (!out.empty()) out += ',';
        open(out, r, ORDER);
        if (withUser) {
            auto u = users.find(r[1].as<int64_t>());
            out += ",\"user\":";
            out += u == users.end() ? "null" : u->second;
        }
        if (withItems) {
            auto i = items.find(r[0].as<int64_t>());
            out += ",\"items\":[";
            if (i != items.end()) out += i->second;
            out += ']';
        }
        out += '}';
    }
    co_return out;
}

static Task<HttpResponsePtr> createOrder(HttpRequestPtr req) {
    auto body = parseObject(req);
    auto rawBody = std::string(req->body());
    const auto &items = body["items"];
    auto isId = [](const Json::Value &v) { return v.isInt64() && v.asInt64() > 0; };
    bool ok = isId(body["user_id"]) && items.isArray() && !items.empty() && items.size() <= 50;
    std::vector<int64_t> pids, qtys;
    for (const auto &it : ok ? items : Json::Value(Json::arrayValue)) {
        if (!it.isObject() || !isId(it["product_id"]) || !it["quantity"].isInt64() ||
            it["quantity"].asInt64() < 1 || it["quantity"].asInt64() > 1000) { ok = false; break; }
        pids.push_back(it["product_id"].asInt64());
        qtys.push_back(it["quantity"].asInt64());
    }
    if (!ok) co_return err(400, "user_id and 1-50 valid items are required");

    auto tx = co_await db()->newTransactionCoro();
    auto priceRows = co_await tx->execSqlCoro("SELECT id, price_cents FROM products WHERE id = ANY($1::bigint[])", pgArray(pids));
    std::unordered_map<int64_t, int64_t> prices;
    for (const auto &r : priceRows) prices[r[0].as<int64_t>()] = r[1].as<int64_t>();
    std::vector<int64_t> units;
    int64_t subtotal = 0;
    for (size_t i = 0; i < pids.size(); ++i) {
        auto p = prices.find(pids[i]);
        if (p == prices.end()) {
            tx->rollback();
            co_return err(400, "unknown product");
        }
        units.push_back(p->second);
        subtotal += p->second * qtys[i];
    }
    int64_t tax = subtotal * 7 / 100, shipping = subtotal >= 5000 ? 0 : 499;
    Result orderRows(nullptr);
    try {
        orderRows = co_await tx->execSqlCoro(R"(INSERT INTO orders (user_id, subtotal_cents, tax_cents, shipping_cents, total_cents, payment_method,
  shipping_address, shipping_city, shipping_postal_code, shipping_country, notes)
VALUES ($1, $2, $3, $4, $5, COALESCE($6::jsonb->>'payment_method', 'card'), $6::jsonb->>'shipping_address',
  $6::jsonb->>'shipping_city', $6::jsonb->>'shipping_postal_code',
  COALESCE($6::jsonb->>'shipping_country', 'US'), $6::jsonb->>'notes')
RETURNING )" + ORDER_COLS, body["user_id"].asInt64(), subtotal, tax, shipping, subtotal + tax + shipping, rawBody);
    } catch (const orm::DrogonDbException &e) {
        if (std::string_view(e.base().what()).find("violates foreign key constraint") != std::string_view::npos)
            co_return err(400, "unknown user");
        throw;
    }
    auto itemRows = co_await tx->execSqlCoro(R"(INSERT INTO order_items (order_id, product_id, quantity, unit_price_cents, line_total_cents)
SELECT $1, t.p, t.q, t.u, t.u * t.q FROM unnest($2::bigint[], $3::int[], $4::bigint[]) AS t(p, q, u)
RETURNING )" + ITEM_COLS, orderRows[0][0].as<int64_t>(), pgArray(pids), pgArray(qtys), pgArray(units));
    std::string order;
    open(order, orderRows[0], ORDER);
    co_return json(order + ",\"items\":[" + rows(itemRows, ITEM) + "]}", 201);  // tx commits when it goes out of scope
}

int main() {
    // DATABASE_URL = postgres://user:pass@host:port/db
    std::smatch m;
    std::string url = std::getenv("DATABASE_URL");
    std::regex_match(url, m, std::regex(R"(postgres://([^:]+):([^@]+)@([^:/]+):(\d+)/(.+))"));
    int pool = std::atoi(std::getenv("POOL_SIZE") ? std::getenv("POOL_SIZE") : "20");
    const int threads = 2;

    Json::Value cfg;
    cfg["listeners"][0]["address"] = "0.0.0.0";
    cfg["listeners"][0]["port"] = 8080;
    cfg["app"]["threads_num"] = threads;
    cfg["app"]["log"]["log_level"] = "WARN";
    auto &dbc = cfg["db_clients"][0];
    dbc["rdbms"] = "postgresql";
    dbc["user"] = m[1].str();
    dbc["passwd"] = m[2].str();
    dbc["host"] = m[3].str();
    dbc["port"] = std::stoi(m[4].str());
    dbc["dbname"] = m[5].str();
    // Normal (not fast) client: exactly POOL_SIZE connections. The fast client opens N per event
    // loop, and Drogon runs one more loop than threads_num, so it ended up with 30.
    dbc["is_fast"] = false;
    dbc["number_of_connections"] = pool;
    app().loadConfigJson(cfg);

    app().setExceptionHandler([](const std::exception &e, const HttpRequestPtr &, std::function<void(const HttpResponsePtr &)> &&cb) {
        if (auto h = dynamic_cast<const HttpErr *>(&e)) return cb(err(h->status, h->what()));
        cb(err(500, "internal error"));
    });

    app().registerHandler("/health", [](HttpRequestPtr) -> Task<HttpResponsePtr> {
        auto r = HttpResponse::newHttpResponse();
        r->setBody("ok");
        co_return r;
    }, {Get});

    // ------------------------------------------------------------ users
    app().registerHandler("/users", [](HttpRequestPtr req) -> Task<HttpResponsePtr> {
        auto [limit, offset] = page(req);
        auto rs = co_await sql("SELECT " + USER_COLS + " FROM (SELECT * FROM users ORDER BY id LIMIT $1 OFFSET $2) users ORDER BY id", limit, offset);
        co_return listed(rows(rs, USER), limit, offset);
    }, {Get});
    app().registerHandler("/users/{id}", [](HttpRequestPtr, std::string id) -> Task<HttpResponsePtr> {
        co_return one(co_await sql("SELECT " + USER_COLS + " FROM users WHERE id = $1", pid(id)), USER);
    }, {Get});
    app().registerHandler("/users", [](HttpRequestPtr req) -> Task<HttpResponsePtr> {
        parseObject(req);
        co_return one(co_await sql(R"(INSERT INTO users (email, username, password_hash, first_name, last_name, phone, date_of_birth, gender,
  address_line1, address_line2, city, state, postal_code, country)
SELECT email, username, 'not-a-real-hash', first_name, last_name, phone, date_of_birth, gender,
  address_line1, address_line2, city, state, postal_code, COALESCE(country, 'US')
FROM jsonb_to_record($1::jsonb) AS x(email text, username text, first_name text, last_name text, phone text,
  date_of_birth date, gender text, address_line1 text, address_line2 text, city text, state text,
  postal_code text, country text)
RETURNING )" + USER_COLS, std::string(req->body())), USER, 201);
    }, {Post});
    app().registerHandler("/users/{id}", [](HttpRequestPtr req, std::string id) -> Task<HttpResponsePtr> {
        parseObject(req);
        co_return one(co_await sql(R"(UPDATE users SET
  first_name = COALESCE($2::jsonb->>'first_name', first_name), last_name = COALESCE($2::jsonb->>'last_name', last_name),
  phone = COALESCE($2::jsonb->>'phone', phone), address_line1 = COALESCE($2::jsonb->>'address_line1', address_line1),
  city = COALESCE($2::jsonb->>'city', city), state = COALESCE($2::jsonb->>'state', state),
  postal_code = COALESCE($2::jsonb->>'postal_code', postal_code),
  is_active = COALESCE(($2::jsonb->>'is_active')::boolean, is_active),
  loyalty_points = COALESCE(($2::jsonb->>'loyalty_points')::int, loyalty_points), updated_at = now()
WHERE id = $1 RETURNING )" + USER_COLS, pid(id), std::string(req->body())), USER);
    }, {Put});
    app().registerHandler("/users/{id}", [](HttpRequestPtr, std::string id) -> Task<HttpResponsePtr> {
        co_return deleted(co_await sql("DELETE FROM users WHERE id = $1", pid(id)));
    }, {Delete});
    app().registerHandler("/users/{id}/orders", [](HttpRequestPtr req, std::string id) -> Task<HttpResponsePtr> {
        auto [limit, offset] = page(req);
        auto rs = co_await sql("SELECT " + ORDER_COLS +
            " FROM orders WHERE user_id = $1 ORDER BY placed_at DESC, id DESC LIMIT $2 OFFSET $3", pid(id), limit, offset);
        co_return listed(co_await ordersJson(rs, false, true), limit, offset);
    }, {Get});

    // ------------------------------------------------------------ products
    app().registerHandler("/products", [](HttpRequestPtr req) -> Task<HttpResponsePtr> {
        auto [limit, offset] = page(req);
        auto rs = co_await sql("SELECT " + PRODUCT_COLS + " FROM (SELECT * FROM products ORDER BY id LIMIT $1 OFFSET $2) products ORDER BY id", limit, offset);
        co_return listed(rows(rs, PRODUCT), limit, offset);
    }, {Get});
    app().registerHandler("/products/search", [](HttpRequestPtr req) -> Task<HttpResponsePtr> {
        auto q = req->getParameter("q");
        q.erase(0, q.find_first_not_of(" \t"));
        q.erase(q.find_last_not_of(" \t") + 1);
        if (q.empty()) co_return err(400, "q is required");
        auto [limit, offset] = page(req);
        auto cat = req->getParameter("category");
        auto rs = co_await sql("SELECT " + PRODUCT_COLS + R"( FROM (SELECT *, ts_rank(search_vector, plainto_tsquery('english', $1)) AS rank FROM products
WHERE search_vector @@ plainto_tsquery('english', $1)
  AND ($2::text IS NULL OR category = $2)
  AND ($3::bigint IS NULL OR price_cents >= $3)
  AND ($4::bigint IS NULL OR price_cents <= $4)
  AND (NOT $5::boolean OR stock_quantity > 0)
ORDER BY rank DESC, id
LIMIT $6 OFFSET $7) products ORDER BY rank DESC, id)", q, cat.empty() ? std::nullopt : std::optional<std::string>(cat),
            toInt(req->getParameter("min_price")), toInt(req->getParameter("max_price")),
            req->getParameter("in_stock") == "true", limit, offset);
        co_return listed(rows(rs, PRODUCT), limit, offset);
    }, {Get});
    app().registerHandler("/products/{id}", [](HttpRequestPtr, std::string id) -> Task<HttpResponsePtr> {
        co_return one(co_await sql("SELECT " + PRODUCT_COLS + " FROM products WHERE id = $1", pid(id)), PRODUCT);
    }, {Get});
    app().registerHandler("/products", [](HttpRequestPtr req) -> Task<HttpResponsePtr> {
        parseObject(req);
        co_return one(co_await sql(R"(INSERT INTO products (sku, name, description, category, subcategory, brand, price_cents, cost_cents, currency,
  stock_quantity, weight_grams, length_mm, width_mm, height_mm, color)
SELECT sku, name, COALESCE(description, ''), category, subcategory, brand, price_cents, COALESCE(cost_cents, 0),
  COALESCE(currency, 'USD'), COALESCE(stock_quantity, 0), weight_grams, length_mm, width_mm, height_mm, color
FROM jsonb_to_record($1::jsonb) AS x(sku text, name text, description text, category text, subcategory text,
  brand text, price_cents bigint, cost_cents bigint, currency text, stock_quantity int, weight_grams int,
  length_mm int, width_mm int, height_mm int, color text)
RETURNING )" + PRODUCT_COLS, std::string(req->body())), PRODUCT, 201);
    }, {Post});
    app().registerHandler("/products/{id}", [](HttpRequestPtr req, std::string id) -> Task<HttpResponsePtr> {
        parseObject(req);
        co_return one(co_await sql(R"(UPDATE products SET
  name = COALESCE($2::jsonb->>'name', name), description = COALESCE($2::jsonb->>'description', description),
  category = COALESCE($2::jsonb->>'category', category),
  price_cents = COALESCE(($2::jsonb->>'price_cents')::bigint, price_cents),
  stock_quantity = COALESCE(($2::jsonb->>'stock_quantity')::int, stock_quantity),
  is_active = COALESCE(($2::jsonb->>'is_active')::boolean, is_active), updated_at = now()
WHERE id = $1 RETURNING )" + PRODUCT_COLS, pid(id), std::string(req->body())), PRODUCT);
    }, {Put});
    app().registerHandler("/products/{id}", [](HttpRequestPtr, std::string id) -> Task<HttpResponsePtr> {
        co_return deleted(co_await sql("DELETE FROM products WHERE id = $1", pid(id)));
    }, {Delete});

    // ------------------------------------------------------------ orders
    app().registerHandler("/orders", [](HttpRequestPtr req) -> Task<HttpResponsePtr> {
        auto [limit, offset] = page(req);
        auto include = "," + req->getParameter("include") + ",";
        auto rs = co_await sql("SELECT " + ORDER_COLS + " FROM (SELECT * FROM orders ORDER BY id DESC LIMIT $1 OFFSET $2) orders ORDER BY id DESC", limit, offset);
        bool user = include.find(",user,") != std::string::npos, items = include.find(",items,") != std::string::npos;
        co_return listed(co_await ordersJson(rs, user, items), limit, offset);
    }, {Get});
    app().registerHandler("/orders/{id}", [](HttpRequestPtr, std::string id) -> Task<HttpResponsePtr> {
        auto rs = co_await sql("SELECT " + ORDER_COLS + " FROM orders WHERE id = $1", pid(id));
        if (rs.empty()) co_return err(404, "not found");
        co_return json(co_await ordersJson(rs, false, true));
    }, {Get});
    app().registerHandler("/orders", &createOrder, {Post});
    app().registerHandler("/orders/{id}", [](HttpRequestPtr req, std::string id) -> Task<HttpResponsePtr> {
        parseObject(req);
        co_return one(co_await sql(R"(UPDATE orders SET
  status = COALESCE($2::jsonb->>'status', status), payment_status = COALESCE($2::jsonb->>'payment_status', payment_status),
  tracking_number = COALESCE($2::jsonb->>'tracking_number', tracking_number),
  notes = COALESCE($2::jsonb->>'notes', notes), updated_at = now()
WHERE id = $1 RETURNING )" + ORDER_COLS, pid(id), std::string(req->body())), ORDER);
    }, {Put});
    app().registerHandler("/orders/{id}", [](HttpRequestPtr, std::string id) -> Task<HttpResponsePtr> {
        co_return deleted(co_await sql("DELETE FROM orders WHERE id = $1", pid(id)));
    }, {Delete});

    app().run();
}
