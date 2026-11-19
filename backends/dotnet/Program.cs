using System.Text.Json;
using System.Text.Json.Serialization;
using Dapper;
using Npgsql;

const string UserCols = """
    id, email, username, first_name, last_name, phone, date_of_birth::text AS date_of_birth, gender,
    address_line1, address_line2, city, state, postal_code, country::text AS country, is_active, is_verified,
    loyalty_points, iso(created_at) AS created_at, iso(updated_at) AS updated_at
    """;
const string ProductCols = """
    id, sku, name, description, category, subcategory, brand, price_cents, currency::text AS currency,
    stock_quantity, weight_grams, length_mm, width_mm, height_mm, color, rating_x10, review_count, is_active,
    iso(created_at) AS created_at, iso(updated_at) AS updated_at
    """;
const string OrderCols = """
    id, user_id, order_number, status, subtotal_cents, tax_cents, shipping_cents, discount_cents,
    total_cents, currency::text AS currency, payment_method, payment_status, shipping_address, shipping_city,
    shipping_postal_code, shipping_country::text AS shipping_country, tracking_number, notes,
    iso(placed_at) AS placed_at, iso(updated_at) AS updated_at
    """;
const string ItemCols = """
    id, order_id, product_id, quantity, unit_price_cents, discount_cents, line_total_cents,
    iso(created_at) AS created_at
    """;

DefaultTypeMap.MatchNamesWithUnderscores = true;

// DATABASE_URL is postgres://user:pass@host:port/db; Npgsql wants key=value.
var url = new Uri(Environment.GetEnvironmentVariable("DATABASE_URL")!);
var pool = Environment.GetEnvironmentVariable("POOL_SIZE") ?? "20";
var creds = url.UserInfo.Split(':');
var ds = NpgsqlDataSource.Create(
    $"Host={url.Host};Port={url.Port};Database={url.AbsolutePath.TrimStart('/')};Username={creds[0]};Password={creds[1]};" +
    $"Minimum Pool Size={pool};Maximum Pool Size={pool};No Reset On Close=true");

var builder = WebApplication.CreateSlimBuilder(args);
builder.Logging.ClearProviders();
builder.WebHost.UseUrls("http://0.0.0.0:8080");
builder.Services.ConfigureHttpJsonOptions(o => o.SerializerOptions.PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower);
var app = builder.Build();

app.MapGet("/health", () => "ok");

// ---------------------------------------------------------------- users
app.MapGet("/users", (HttpRequest r) => ListOf<User>(r, $"SELECT {UserCols} FROM (SELECT * FROM users ORDER BY id LIMIT @limit OFFSET @offset) users ORDER BY id"));
app.MapGet("/users/{id}", (string id) => One<User>($"SELECT {UserCols} FROM users WHERE id = @id", new { id = Id(id) }));
app.MapPost("/users", (HttpRequest r) => Write<User>(r, 201, """
    INSERT INTO users (email, username, password_hash, first_name, last_name, phone, date_of_birth, gender,
      address_line1, address_line2, city, state, postal_code, country)
    SELECT email, username, 'not-a-real-hash', first_name, last_name, phone, date_of_birth, gender,
      address_line1, address_line2, city, state, postal_code, COALESCE(country, 'US')
    FROM jsonb_to_record(@body::jsonb) AS x(email text, username text, first_name text, last_name text, phone text,
      date_of_birth date, gender text, address_line1 text, address_line2 text, city text, state text,
      postal_code text, country text)
    RETURNING
    """ + " " + UserCols, 0));
app.MapPut("/users/{id}", (HttpRequest r, string id) => Write<User>(r, 200, """
    UPDATE users SET
      first_name = COALESCE(@body::jsonb->>'first_name', first_name), last_name = COALESCE(@body::jsonb->>'last_name', last_name),
      phone = COALESCE(@body::jsonb->>'phone', phone), address_line1 = COALESCE(@body::jsonb->>'address_line1', address_line1),
      city = COALESCE(@body::jsonb->>'city', city), state = COALESCE(@body::jsonb->>'state', state),
      postal_code = COALESCE(@body::jsonb->>'postal_code', postal_code),
      is_active = COALESCE((@body::jsonb->>'is_active')::boolean, is_active),
      loyalty_points = COALESCE((@body::jsonb->>'loyalty_points')::int, loyalty_points), updated_at = now()
    WHERE id = @id RETURNING
    """ + " " + UserCols, Id(id)));
app.MapDelete("/users/{id}", (string id) => Delete("DELETE FROM users WHERE id = @id", Id(id)));
app.MapGet("/users/{id}/orders", async (HttpRequest r, string id) =>
{
    var (limit, offset) = Page(r);
    return await Guard(async () =>
    {
        await using var c = await ds.OpenConnectionAsync();
        var orders = (await c.QueryAsync<Order>(
            $"SELECT {OrderCols} FROM orders WHERE user_id = @id ORDER BY placed_at DESC, id DESC LIMIT @limit OFFSET @offset",
            new { id = Id(id), limit, offset })).AsList();
        await AttachItems(c, orders);
        return Results.Ok(new { data = orders, limit, offset });
    });
});

// ---------------------------------------------------------------- products
app.MapGet("/products", (HttpRequest r) => ListOf<Product>(r, $"SELECT {ProductCols} FROM (SELECT * FROM products ORDER BY id LIMIT @limit OFFSET @offset) products ORDER BY id"));
app.MapGet("/products/search", (HttpRequest r) =>
{
    var q = r.Query["q"].ToString().Trim();
    if (q.Length == 0) return Task.FromResult(Err(400, "q is required"));
    var category = r.Query["category"].ToString();
    return ListOf<Product>(r, $"""
        SELECT {ProductCols} FROM (SELECT *, ts_rank(search_vector, plainto_tsquery('english', @q)) AS rank FROM products
        WHERE search_vector @@ plainto_tsquery('english', @q)
          AND (@category::text IS NULL OR category = @category)
          AND (@min_price::bigint IS NULL OR price_cents >= @min_price)
          AND (@max_price::bigint IS NULL OR price_cents <= @max_price)
          AND (NOT @in_stock::boolean OR stock_quantity > 0)
        ORDER BY rank DESC, id
        LIMIT @limit OFFSET @offset) products ORDER BY rank DESC, id
        """, new Dictionary<string, object?>
    {
        ["q"] = q,
        ["category"] = category.Length > 0 ? category : null,
        ["min_price"] = long.TryParse(r.Query["min_price"], out var mn) ? mn : null,
        ["max_price"] = long.TryParse(r.Query["max_price"], out var mx) ? mx : null,
        ["in_stock"] = r.Query["in_stock"] == "true",
    });
});
app.MapGet("/products/{id}", (string id) => One<Product>($"SELECT {ProductCols} FROM products WHERE id = @id", new { id = Id(id) }));
app.MapPost("/products", (HttpRequest r) => Write<Product>(r, 201, """
    INSERT INTO products (sku, name, description, category, subcategory, brand, price_cents, cost_cents, currency,
      stock_quantity, weight_grams, length_mm, width_mm, height_mm, color)
    SELECT sku, name, COALESCE(description, ''), category, subcategory, brand, price_cents, COALESCE(cost_cents, 0),
      COALESCE(currency, 'USD'), COALESCE(stock_quantity, 0), weight_grams, length_mm, width_mm, height_mm, color
    FROM jsonb_to_record(@body::jsonb) AS x(sku text, name text, description text, category text, subcategory text,
      brand text, price_cents bigint, cost_cents bigint, currency text, stock_quantity int, weight_grams int,
      length_mm int, width_mm int, height_mm int, color text)
    RETURNING
    """ + " " + ProductCols, 0));
app.MapPut("/products/{id}", (HttpRequest r, string id) => Write<Product>(r, 200, """
    UPDATE products SET
      name = COALESCE(@body::jsonb->>'name', name), description = COALESCE(@body::jsonb->>'description', description),
      category = COALESCE(@body::jsonb->>'category', category),
      price_cents = COALESCE((@body::jsonb->>'price_cents')::bigint, price_cents),
      stock_quantity = COALESCE((@body::jsonb->>'stock_quantity')::int, stock_quantity),
      is_active = COALESCE((@body::jsonb->>'is_active')::boolean, is_active), updated_at = now()
    WHERE id = @id RETURNING
    """ + " " + ProductCols, Id(id)));
app.MapDelete("/products/{id}", (string id) => Delete("DELETE FROM products WHERE id = @id", Id(id)));

// ---------------------------------------------------------------- orders
app.MapGet("/orders", async (HttpRequest r) =>
{
    var (limit, offset) = Page(r);
    var include = "," + r.Query["include"] + ",";
    return await Guard(async () =>
    {
        await using var c = await ds.OpenConnectionAsync();
        var orders = (await c.QueryAsync<Order>($"SELECT {OrderCols} FROM (SELECT * FROM orders ORDER BY id DESC LIMIT @limit OFFSET @offset) orders ORDER BY id DESC",
            new { limit, offset })).AsList();
        if (include.Contains(",user,") && orders.Count > 0)
        {
            var users = (await c.QueryAsync<User>($"SELECT {UserCols} FROM users WHERE id = ANY(@ids::bigint[])",
                new { ids = orders.Select(o => o.UserId).ToArray() })).ToDictionary(u => u.Id);
            foreach (var o in orders) o.User = users.GetValueOrDefault(o.UserId);
        }
        if (include.Contains(",items,")) await AttachItems(c, orders);
        return Results.Ok(new { data = orders, limit, offset });
    });
});
app.MapGet("/orders/{id}", (string id) => Guard(async () =>
{
    await using var c = await ds.OpenConnectionAsync();
    var order = await c.QueryFirstOrDefaultAsync<Order>($"SELECT {OrderCols} FROM orders WHERE id = @id", new { id = Id(id) });
    if (order is null) return Err(404, "not found");
    await AttachItems(c, [order]);
    return Results.Ok(order);
}));
app.MapPost("/orders", async (HttpRequest r) =>
{
    var raw = await ReadObject(r);
    if (raw is null) return Err(400, "body must be a JSON object");
    NewOrder? input;
    try { input = JsonSerializer.Deserialize<NewOrder>(raw, NewOrderJson); }
    catch (JsonException) { input = null; }
    if (input is null || input.UserId <= 0 || input.Items is not { Length: > 0 and <= 50 } ||
        input.Items.Any(i => i.ProductId <= 0 || i.Quantity < 1 || i.Quantity > 1000))
        return Err(400, "user_id and 1-50 valid items are required");
    var pids = input.Items.Select(i => i.ProductId).ToArray();
    var qtys = input.Items.Select(i => i.Quantity).ToArray();

    return await Guard(async () =>
    {
        await using var c = await ds.OpenConnectionAsync();
        await using var tx = await c.BeginTransactionAsync();
        var prices = (await c.QueryAsync<(long Id, long Price)>("SELECT id, price_cents FROM products WHERE id = ANY(@pids::bigint[])",
            new { pids }, tx)).ToDictionary(p => p.Id, p => p.Price);
        var units = new long[pids.Length];
        long subtotal = 0;
        for (var i = 0; i < pids.Length; i++)
        {
            if (!prices.TryGetValue(pids[i], out units[i])) return Err(400, "unknown product");
            subtotal += units[i] * qtys[i];
        }
        long tax = subtotal * 7 / 100, shipping = subtotal >= 5000 ? 0 : 499;
        Order order;
        try
        {
            order = await c.QuerySingleAsync<Order>($"""
                INSERT INTO orders (user_id, subtotal_cents, tax_cents, shipping_cents, total_cents, payment_method,
                  shipping_address, shipping_city, shipping_postal_code, shipping_country, notes)
                VALUES (@user_id, @subtotal, @tax, @shipping, @total, COALESCE(@body::jsonb->>'payment_method', 'card'),
                  @body::jsonb->>'shipping_address', @body::jsonb->>'shipping_city', @body::jsonb->>'shipping_postal_code',
                  COALESCE(@body::jsonb->>'shipping_country', 'US'), @body::jsonb->>'notes')
                RETURNING {OrderCols}
                """, new { user_id = input.UserId, subtotal, tax, shipping, total = subtotal + tax + shipping, body = raw }, tx);
        }
        catch (PostgresException e) when (e.SqlState == "23503") { return Err(400, "unknown user"); }
        order.Items = (await c.QueryAsync<Item>($"""
            INSERT INTO order_items (order_id, product_id, quantity, unit_price_cents, line_total_cents)
            SELECT @order_id, t.p, t.q, t.u, t.u * t.q FROM unnest(@pids::bigint[], @qtys::int[], @units::bigint[]) AS t(p, q, u)
            RETURNING {ItemCols}
            """, new { order_id = order.Id, pids, qtys, units }, tx)).AsList();
        await tx.CommitAsync();
        return Results.Json(order, statusCode: 201);
    });
});
app.MapPut("/orders/{id}", (HttpRequest r, string id) => Write<Order>(r, 200, """
    UPDATE orders SET
      status = COALESCE(@body::jsonb->>'status', status), payment_status = COALESCE(@body::jsonb->>'payment_status', payment_status),
      tracking_number = COALESCE(@body::jsonb->>'tracking_number', tracking_number),
      notes = COALESCE(@body::jsonb->>'notes', notes), updated_at = now()
    WHERE id = @id RETURNING
    """ + " " + OrderCols, Id(id)));
app.MapDelete("/orders/{id}", (string id) => Delete("DELETE FROM orders WHERE id = @id", Id(id)));

app.Run();

// ---------------------------------------------------------------- helpers

// Loads all items for the given orders in one query and groups them by order_id.
async Task AttachItems(NpgsqlConnection c, List<Order> orders)
{
    if (orders.Count == 0) return;
    var items = (await c.QueryAsync<Item>($"SELECT {ItemCols} FROM order_items WHERE order_id = ANY(@ids::bigint[]) ORDER BY order_id, id",
        new { ids = orders.Select(o => o.Id).ToArray() })).ToLookup(i => i.OrderId);
    foreach (var o in orders) o.Items = items[o.Id].ToList();
}

Task<IResult> ListOf<T>(HttpRequest r, string sql, Dictionary<string, object?>? extra = null)
{
    var (limit, offset) = Page(r);
    var args = new DynamicParameters(extra ?? new Dictionary<string, object?>());
    args.Add("limit", limit);
    args.Add("offset", offset);
    return Guard(async () =>
    {
        await using var c = await ds.OpenConnectionAsync();
        return Results.Ok(new { data = await c.QueryAsync<T>(sql, args), limit, offset });
    });
}

Task<IResult> One<T>(string sql, object args) => Guard(async () =>
{
    await using var c = await ds.OpenConnectionAsync();
    var row = await c.QueryFirstOrDefaultAsync<T>(sql, args);
    return row is null ? Err(404, "not found") : Results.Ok(row);
});

async Task<IResult> Write<T>(HttpRequest r, int status, string sql, long id)
{
    var body = await ReadObject(r);
    if (body is null) return Err(400, "body must be a JSON object");
    return await Guard(async () =>
    {
        await using var c = await ds.OpenConnectionAsync();
        var row = await c.QueryFirstOrDefaultAsync<T>(sql, new { id, body });
        return row is null ? Err(404, "not found") : Results.Json(row, statusCode: status);
    });
}

Task<IResult> Delete(string sql, long id) => Guard(async () =>
{
    await using var c = await ds.OpenConnectionAsync();
    return await c.ExecuteAsync(sql, new { id }) == 0 ? Err(404, "not found") : Results.NoContent();
});

async Task<IResult> Guard(Func<Task<IResult>> f)
{
    try { return await f(); }
    catch (PostgresException e) when (e.SqlState is "23505" or "23503") { return Err(409, "conflict"); }
    catch (PostgresException e) when (e.SqlState.StartsWith("22") || e.SqlState is "23502" or "23514") { return Err(400, "invalid input"); }
}

static IResult Err(int status, string msg) => Results.Json(new { error = msg }, statusCode: status);

static async Task<string?> ReadObject(HttpRequest r)
{
    var raw = await new StreamReader(r.Body).ReadToEndAsync();
    try
    {
        using var doc = JsonDocument.Parse(raw);
        return doc.RootElement.ValueKind == JsonValueKind.Object ? raw : null;
    }
    catch (JsonException) { return null; }
}

static long Id(string s) => long.TryParse(s, out var v) ? v : 0; // non-numeric -> 404

static (int, int) Page(HttpRequest r)
{
    var limit = int.TryParse(r.Query["limit"], out var l) ? Math.Clamp(l, 1, 100) : 20;
    var offset = int.TryParse(r.Query["offset"], out var o) && o >= 0 ? o : 0;
    return (limit, offset);
}

partial class Program
{
    static readonly JsonSerializerOptions NewOrderJson = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
}

record NewItem(long ProductId, int Quantity);
record NewOrder(long UserId, NewItem[]? Items);

class User
{
    public long Id { get; set; }
    public string Email { get; set; } = "";
    public string Username { get; set; } = "";
    public string FirstName { get; set; } = "";
    public string LastName { get; set; } = "";
    public string? Phone { get; set; }
    public string? DateOfBirth { get; set; }
    public string? Gender { get; set; }
    [JsonPropertyName("address_line1")] public string? AddressLine1 { get; set; }
    [JsonPropertyName("address_line2")] public string? AddressLine2 { get; set; }
    public string? City { get; set; }
    public string? State { get; set; }
    public string? PostalCode { get; set; }
    public string Country { get; set; } = "";
    public bool IsActive { get; set; }
    public bool IsVerified { get; set; }
    public int LoyaltyPoints { get; set; }
    public string CreatedAt { get; set; } = "";
    public string UpdatedAt { get; set; } = "";
}

class Product
{
    public long Id { get; set; }
    public string Sku { get; set; } = "";
    public string Name { get; set; } = "";
    public string Description { get; set; } = "";
    public string Category { get; set; } = "";
    public string? Subcategory { get; set; }
    public string? Brand { get; set; }
    public long PriceCents { get; set; }
    public string Currency { get; set; } = "";
    public int StockQuantity { get; set; }
    public int? WeightGrams { get; set; }
    public int? LengthMm { get; set; }
    public int? WidthMm { get; set; }
    public int? HeightMm { get; set; }
    public string? Color { get; set; }
    [JsonPropertyName("rating_x10")] public short RatingX10 { get; set; }
    public int ReviewCount { get; set; }
    public bool IsActive { get; set; }
    public string CreatedAt { get; set; } = "";
    public string UpdatedAt { get; set; } = "";
}

class Item
{
    public long Id { get; set; }
    public long OrderId { get; set; }
    public long ProductId { get; set; }
    public int Quantity { get; set; }
    public long UnitPriceCents { get; set; }
    public long DiscountCents { get; set; }
    public long LineTotalCents { get; set; }
    public string CreatedAt { get; set; } = "";
}

class Order
{
    public long Id { get; set; }
    public long UserId { get; set; }
    public string OrderNumber { get; set; } = "";
    public string Status { get; set; } = "";
    public long SubtotalCents { get; set; }
    public long TaxCents { get; set; }
    public long ShippingCents { get; set; }
    public long DiscountCents { get; set; }
    public long TotalCents { get; set; }
    public string Currency { get; set; } = "";
    public string PaymentMethod { get; set; } = "";
    public string PaymentStatus { get; set; } = "";
    public string? ShippingAddress { get; set; }
    public string? ShippingCity { get; set; }
    public string? ShippingPostalCode { get; set; }
    public string ShippingCountry { get; set; } = "";
    public string? TrackingNumber { get; set; }
    public string? Notes { get; set; }
    public string PlacedAt { get; set; } = "";
    public string UpdatedAt { get; set; } = "";
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public User? User { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public List<Item>? Items { get; set; }
}
