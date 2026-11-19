package main

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"strconv"
	"strings"

	"github.com/gin-gonic/gin"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

const userCols = `id, email, username, first_name, last_name, phone, date_of_birth::text AS date_of_birth, gender,
address_line1, address_line2, city, state, postal_code, country::text AS country, is_active, is_verified,
loyalty_points, iso(created_at) AS created_at, iso(updated_at) AS updated_at`

const productCols = `id, sku, name, description, category, subcategory, brand, price_cents, currency::text AS currency,
stock_quantity, weight_grams, length_mm, width_mm, height_mm, color, rating_x10, review_count, is_active,
iso(created_at) AS created_at, iso(updated_at) AS updated_at`

const orderCols = `id, user_id, order_number, status, subtotal_cents, tax_cents, shipping_cents, discount_cents,
total_cents, currency::text AS currency, payment_method, payment_status, shipping_address, shipping_city,
shipping_postal_code, shipping_country::text AS shipping_country, tracking_number, notes,
iso(placed_at) AS placed_at, iso(updated_at) AS updated_at`

const itemCols = `id, order_id, product_id, quantity, unit_price_cents, discount_cents, line_total_cents,
iso(created_at) AS created_at`

type User struct {
	ID            int64   `json:"id" db:"id"`
	Email         string  `json:"email" db:"email"`
	Username      string  `json:"username" db:"username"`
	FirstName     string  `json:"first_name" db:"first_name"`
	LastName      string  `json:"last_name" db:"last_name"`
	Phone         *string `json:"phone" db:"phone"`
	DateOfBirth   *string `json:"date_of_birth" db:"date_of_birth"`
	Gender        *string `json:"gender" db:"gender"`
	AddressLine1  *string `json:"address_line1" db:"address_line1"`
	AddressLine2  *string `json:"address_line2" db:"address_line2"`
	City          *string `json:"city" db:"city"`
	State         *string `json:"state" db:"state"`
	PostalCode    *string `json:"postal_code" db:"postal_code"`
	Country       string  `json:"country" db:"country"`
	IsActive      bool    `json:"is_active" db:"is_active"`
	IsVerified    bool    `json:"is_verified" db:"is_verified"`
	LoyaltyPoints int32   `json:"loyalty_points" db:"loyalty_points"`
	CreatedAt     string  `json:"created_at" db:"created_at"`
	UpdatedAt     string  `json:"updated_at" db:"updated_at"`
}

type Product struct {
	ID            int64   `json:"id" db:"id"`
	Sku           string  `json:"sku" db:"sku"`
	Name          string  `json:"name" db:"name"`
	Description   string  `json:"description" db:"description"`
	Category      string  `json:"category" db:"category"`
	Subcategory   *string `json:"subcategory" db:"subcategory"`
	Brand         *string `json:"brand" db:"brand"`
	PriceCents    int64   `json:"price_cents" db:"price_cents"`
	Currency      string  `json:"currency" db:"currency"`
	StockQuantity int32   `json:"stock_quantity" db:"stock_quantity"`
	WeightGrams   *int32  `json:"weight_grams" db:"weight_grams"`
	LengthMm      *int32  `json:"length_mm" db:"length_mm"`
	WidthMm       *int32  `json:"width_mm" db:"width_mm"`
	HeightMm      *int32  `json:"height_mm" db:"height_mm"`
	Color         *string `json:"color" db:"color"`
	RatingX10     int16   `json:"rating_x10" db:"rating_x10"`
	ReviewCount   int32   `json:"review_count" db:"review_count"`
	IsActive      bool    `json:"is_active" db:"is_active"`
	CreatedAt     string  `json:"created_at" db:"created_at"`
	UpdatedAt     string  `json:"updated_at" db:"updated_at"`
}

type Item struct {
	ID             int64  `json:"id" db:"id"`
	OrderID        int64  `json:"order_id" db:"order_id"`
	ProductID      int64  `json:"product_id" db:"product_id"`
	Quantity       int32  `json:"quantity" db:"quantity"`
	UnitPriceCents int64  `json:"unit_price_cents" db:"unit_price_cents"`
	DiscountCents  int64  `json:"discount_cents" db:"discount_cents"`
	LineTotalCents int64  `json:"line_total_cents" db:"line_total_cents"`
	CreatedAt      string `json:"created_at" db:"created_at"`
}

type Order struct {
	ID                 int64   `json:"id" db:"id"`
	UserID             int64   `json:"user_id" db:"user_id"`
	OrderNumber        string  `json:"order_number" db:"order_number"`
	Status             string  `json:"status" db:"status"`
	SubtotalCents      int64   `json:"subtotal_cents" db:"subtotal_cents"`
	TaxCents           int64   `json:"tax_cents" db:"tax_cents"`
	ShippingCents      int64   `json:"shipping_cents" db:"shipping_cents"`
	DiscountCents      int64   `json:"discount_cents" db:"discount_cents"`
	TotalCents         int64   `json:"total_cents" db:"total_cents"`
	Currency           string  `json:"currency" db:"currency"`
	PaymentMethod      string  `json:"payment_method" db:"payment_method"`
	PaymentStatus      string  `json:"payment_status" db:"payment_status"`
	ShippingAddress    *string `json:"shipping_address" db:"shipping_address"`
	ShippingCity       *string `json:"shipping_city" db:"shipping_city"`
	ShippingPostalCode *string `json:"shipping_postal_code" db:"shipping_postal_code"`
	ShippingCountry    string  `json:"shipping_country" db:"shipping_country"`
	TrackingNumber     *string `json:"tracking_number" db:"tracking_number"`
	Notes              *string `json:"notes" db:"notes"`
	PlacedAt           string  `json:"placed_at" db:"placed_at"`
	UpdatedAt          string  `json:"updated_at" db:"updated_at"`
	User               *User   `json:"user,omitempty" db:"-"`
	Items              *[]Item `json:"items,omitempty" db:"-"`
}

var db *pgxpool.Pool

func main() {
	cfg, err := pgxpool.ParseConfig(os.Getenv("DATABASE_URL"))
	if err != nil {
		panic(err)
	}
	size, _ := strconv.Atoi(os.Getenv("POOL_SIZE"))
	cfg.MaxConns, cfg.MinConns = int32(size), int32(size)
	if db, err = pgxpool.NewWithConfig(context.Background(), cfg); err != nil {
		panic(err)
	}

	gin.SetMode(gin.ReleaseMode)
	r := gin.New()
	r.GET("/health", func(c *gin.Context) { c.String(200, "ok") })

	r.GET("/users", func(c *gin.Context) {
		limit, offset := page(c)
		list[User](c, `SELECT `+userCols+` FROM (SELECT * FROM users ORDER BY id LIMIT $1 OFFSET $2) users ORDER BY id`, limit, offset)
	})
	r.GET("/users/:id", func(c *gin.Context) { one[User](c, `SELECT `+userCols+` FROM users WHERE id = $1`, id(c)) })
	r.POST("/users", func(c *gin.Context) {
		if body, ok := jsonObject(c); ok {
			write[User](c, 201, `INSERT INTO users (email, username, password_hash, first_name, last_name, phone, date_of_birth, gender,
  address_line1, address_line2, city, state, postal_code, country)
SELECT email, username, 'not-a-real-hash', first_name, last_name, phone, date_of_birth, gender,
  address_line1, address_line2, city, state, postal_code, COALESCE(country, 'US')
FROM jsonb_to_record($1::jsonb) AS x(email text, username text, first_name text, last_name text, phone text,
  date_of_birth date, gender text, address_line1 text, address_line2 text, city text, state text,
  postal_code text, country text)
RETURNING `+userCols, body)
		}
	})
	r.PUT("/users/:id", func(c *gin.Context) {
		if body, ok := jsonObject(c); ok {
			write[User](c, 200, `UPDATE users SET
  first_name = COALESCE($2::jsonb->>'first_name', first_name), last_name = COALESCE($2::jsonb->>'last_name', last_name),
  phone = COALESCE($2::jsonb->>'phone', phone), address_line1 = COALESCE($2::jsonb->>'address_line1', address_line1),
  city = COALESCE($2::jsonb->>'city', city), state = COALESCE($2::jsonb->>'state', state),
  postal_code = COALESCE($2::jsonb->>'postal_code', postal_code),
  is_active = COALESCE(($2::jsonb->>'is_active')::boolean, is_active),
  loyalty_points = COALESCE(($2::jsonb->>'loyalty_points')::int, loyalty_points), updated_at = now()
WHERE id = $1 RETURNING `+userCols, id(c), body)
		}
	})
	r.DELETE("/users/:id", func(c *gin.Context) { del(c, `DELETE FROM users WHERE id = $1`) })
	r.GET("/users/:id/orders", func(c *gin.Context) {
		limit, offset := page(c)
		orders, err := query[Order](c, `SELECT `+orderCols+` FROM orders WHERE user_id = $1 ORDER BY placed_at DESC, id DESC LIMIT $2 OFFSET $3`, id(c), limit, offset)
		if err == nil {
			err = attachItems(c, orders)
		}
		if err != nil {
			fail(c, err)
			return
		}
		c.JSON(200, gin.H{"data": orders, "limit": limit, "offset": offset})
	})

	r.GET("/products", func(c *gin.Context) {
		limit, offset := page(c)
		list[Product](c, `SELECT `+productCols+` FROM (SELECT * FROM products ORDER BY id LIMIT $1 OFFSET $2) products ORDER BY id`, limit, offset)
	})
	r.GET("/products/search", func(c *gin.Context) {
		q := strings.TrimSpace(c.Query("q"))
		if q == "" {
			c.JSON(400, gin.H{"error": "q is required"})
			return
		}
		limit, offset := page(c)
		list[Product](c, `SELECT `+productCols+` FROM (SELECT *, ts_rank(search_vector, plainto_tsquery('english', $1)) AS rank FROM products
WHERE search_vector @@ plainto_tsquery('english', $1)
  AND ($2::text IS NULL OR category = $2)
  AND ($3::bigint IS NULL OR price_cents >= $3)
  AND ($4::bigint IS NULL OR price_cents <= $4)
  AND (NOT $5::boolean OR stock_quantity > 0)
ORDER BY rank DESC, id
LIMIT $6 OFFSET $7) products ORDER BY rank DESC, id`, q, optStr(c, "category"), optInt(c, "min_price"), optInt(c, "max_price"),
			c.Query("in_stock") == "true", limit, offset)
	})
	r.GET("/products/:id", func(c *gin.Context) {
		one[Product](c, `SELECT `+productCols+` FROM products WHERE id = $1`, id(c))
	})
	r.POST("/products", func(c *gin.Context) {
		if body, ok := jsonObject(c); ok {
			write[Product](c, 201, `INSERT INTO products (sku, name, description, category, subcategory, brand, price_cents, cost_cents, currency,
  stock_quantity, weight_grams, length_mm, width_mm, height_mm, color)
SELECT sku, name, COALESCE(description, ''), category, subcategory, brand, price_cents, COALESCE(cost_cents, 0),
  COALESCE(currency, 'USD'), COALESCE(stock_quantity, 0), weight_grams, length_mm, width_mm, height_mm, color
FROM jsonb_to_record($1::jsonb) AS x(sku text, name text, description text, category text, subcategory text,
  brand text, price_cents bigint, cost_cents bigint, currency text, stock_quantity int, weight_grams int,
  length_mm int, width_mm int, height_mm int, color text)
RETURNING `+productCols, body)
		}
	})
	r.PUT("/products/:id", func(c *gin.Context) {
		if body, ok := jsonObject(c); ok {
			write[Product](c, 200, `UPDATE products SET
  name = COALESCE($2::jsonb->>'name', name), description = COALESCE($2::jsonb->>'description', description),
  category = COALESCE($2::jsonb->>'category', category),
  price_cents = COALESCE(($2::jsonb->>'price_cents')::bigint, price_cents),
  stock_quantity = COALESCE(($2::jsonb->>'stock_quantity')::int, stock_quantity),
  is_active = COALESCE(($2::jsonb->>'is_active')::boolean, is_active), updated_at = now()
WHERE id = $1 RETURNING `+productCols, id(c), body)
		}
	})
	r.DELETE("/products/:id", func(c *gin.Context) { del(c, `DELETE FROM products WHERE id = $1`) })

	r.GET("/orders", listOrders)
	r.GET("/orders/:id", func(c *gin.Context) {
		orders, err := query[Order](c, `SELECT `+orderCols+` FROM orders WHERE id = $1`, id(c))
		if err == nil && len(orders) == 1 {
			err = attachItems(c, orders)
		}
		switch {
		case err != nil:
			fail(c, err)
		case len(orders) == 0:
			c.JSON(404, gin.H{"error": "not found"})
		default:
			c.JSON(200, orders[0])
		}
	})
	r.POST("/orders", createOrder)
	r.PUT("/orders/:id", func(c *gin.Context) {
		if body, ok := jsonObject(c); ok {
			write[Order](c, 200, `UPDATE orders SET
  status = COALESCE($2::jsonb->>'status', status), payment_status = COALESCE($2::jsonb->>'payment_status', payment_status),
  tracking_number = COALESCE($2::jsonb->>'tracking_number', tracking_number),
  notes = COALESCE($2::jsonb->>'notes', notes), updated_at = now()
WHERE id = $1 RETURNING `+orderCols, id(c), body)
		}
	})
	r.DELETE("/orders/:id", func(c *gin.Context) { del(c, `DELETE FROM orders WHERE id = $1`) })

	if err := r.Run(":8080"); err != nil {
		panic(err)
	}
}

func listOrders(c *gin.Context) {
	limit, offset := page(c)
	include := "," + c.Query("include") + ","
	orders, err := query[Order](c, `SELECT `+orderCols+` FROM (SELECT * FROM orders ORDER BY id DESC LIMIT $1 OFFSET $2) orders ORDER BY id DESC`, limit, offset)
	if err == nil && strings.Contains(include, ",user,") && len(orders) > 0 {
		ids := make([]int64, len(orders))
		for i := range orders {
			ids[i] = orders[i].UserID
		}
		var users []User
		if users, err = query[User](c, `SELECT `+userCols+` FROM users WHERE id = ANY($1::bigint[])`, ids); err == nil {
			byID := make(map[int64]*User, len(users))
			for i := range users {
				byID[users[i].ID] = &users[i]
			}
			for i := range orders {
				orders[i].User = byID[orders[i].UserID]
			}
		}
	}
	if err == nil && strings.Contains(include, ",items,") {
		err = attachItems(c, orders)
	}
	if err != nil {
		fail(c, err)
		return
	}
	c.JSON(200, gin.H{"data": orders, "limit": limit, "offset": offset})
}

// attachItems loads all items for the given orders in one query and groups them by order_id.
func attachItems(c *gin.Context, orders []Order) error {
	if len(orders) == 0 {
		return nil
	}
	ids := make([]int64, len(orders))
	for i := range orders {
		ids[i] = orders[i].ID
	}
	items, err := query[Item](c, `SELECT `+itemCols+` FROM order_items WHERE order_id = ANY($1::bigint[]) ORDER BY order_id, id`, ids)
	if err != nil {
		return err
	}
	byOrder := make(map[int64][]Item, len(orders))
	for _, it := range items {
		byOrder[it.OrderID] = append(byOrder[it.OrderID], it)
	}
	for i := range orders {
		list := byOrder[orders[i].ID]
		if list == nil {
			list = []Item{}
		}
		orders[i].Items = &list
	}
	return nil
}

type newOrder struct {
	UserID int64 `json:"user_id"`
	Items  []struct {
		ProductID int64 `json:"product_id"`
		Quantity  int32 `json:"quantity"`
	} `json:"items"`
}

func createOrder(c *gin.Context) {
	raw, ok := jsonObject(c)
	if !ok {
		return
	}
	var in newOrder
	if err := json.Unmarshal([]byte(raw), &in); err != nil || in.UserID <= 0 || len(in.Items) == 0 || len(in.Items) > 50 {
		c.JSON(400, gin.H{"error": "user_id and 1-50 items are required"})
		return
	}
	pids := make([]int64, len(in.Items))
	for i, it := range in.Items {
		if it.ProductID <= 0 || it.Quantity < 1 || it.Quantity > 1000 {
			c.JSON(400, gin.H{"error": "invalid item"})
			return
		}
		pids[i] = it.ProductID
	}

	tx, err := db.Begin(c)
	if err != nil {
		fail(c, err)
		return
	}
	defer tx.Rollback(context.Background())

	rows, _ := tx.Query(c, `SELECT id, price_cents FROM products WHERE id = ANY($1::bigint[])`, pids)
	prices := map[int64]int64{}
	var pid, price int64
	if _, err = pgx.ForEachRow(rows, []any{&pid, &price}, func() error { prices[pid] = price; return nil }); err != nil {
		fail(c, err)
		return
	}
	qtys, units := make([]int32, len(in.Items)), make([]int64, len(in.Items))
	var subtotal int64
	for i, it := range in.Items {
		p, found := prices[it.ProductID]
		if !found {
			c.JSON(400, gin.H{"error": "unknown product"})
			return
		}
		qtys[i], units[i] = it.Quantity, p
		subtotal += p * int64(it.Quantity)
	}
	tax, shipping := subtotal*7/100, int64(499)
	if subtotal >= 5000 {
		shipping = 0
	}

	rows, _ = tx.Query(c, `INSERT INTO orders (user_id, subtotal_cents, tax_cents, shipping_cents, total_cents, payment_method,
  shipping_address, shipping_city, shipping_postal_code, shipping_country, notes)
VALUES ($1, $2, $3, $4, $5, COALESCE($6::jsonb->>'payment_method', 'card'), $6::jsonb->>'shipping_address',
  $6::jsonb->>'shipping_city', $6::jsonb->>'shipping_postal_code',
  COALESCE($6::jsonb->>'shipping_country', 'US'), $6::jsonb->>'notes')
RETURNING `+orderCols, in.UserID, subtotal, tax, shipping, subtotal+tax+shipping, raw)
	order, err := pgx.CollectExactlyOneRow(rows, pgx.RowToStructByName[Order])
	if err != nil {
		if pgCode(err) == "23503" {
			c.JSON(400, gin.H{"error": "unknown user"})
		} else {
			fail(c, err)
		}
		return
	}
	rows, _ = tx.Query(c, `INSERT INTO order_items (order_id, product_id, quantity, unit_price_cents, line_total_cents)
SELECT $1, t.p, t.q, t.u, t.u * t.q FROM unnest($2::bigint[], $3::int[], $4::bigint[]) AS t(p, q, u)
RETURNING `+itemCols, order.ID, pids, qtys, units)
	items, err := pgx.CollectRows(rows, pgx.RowToStructByName[Item])
	if err == nil {
		err = tx.Commit(c)
	}
	if err != nil {
		fail(c, err)
		return
	}
	order.Items = &items
	c.JSON(201, order)
}

// ---------------------------------------------------------------- helpers

func query[T any](c *gin.Context, sql string, args ...any) ([]T, error) {
	rows, _ := db.Query(c, sql, args...)
	return pgx.CollectRows(rows, pgx.RowToStructByName[T])
}

func list[T any](c *gin.Context, sql string, args ...any) {
	data, err := query[T](c, sql, args...)
	if err != nil {
		fail(c, err)
		return
	}
	c.JSON(200, gin.H{"data": data, "limit": args[len(args)-2], "offset": args[len(args)-1]})
}

func one[T any](c *gin.Context, sql string, args ...any) { write[T](c, 200, sql, args...) }

// write runs a single-row statement and answers with that row, 404 when no row matched.
func write[T any](c *gin.Context, status int, sql string, args ...any) {
	rows, _ := db.Query(c, sql, args...)
	row, err := pgx.CollectExactlyOneRow(rows, pgx.RowToStructByName[T])
	if errors.Is(err, pgx.ErrNoRows) {
		c.JSON(404, gin.H{"error": "not found"})
	} else if err != nil {
		fail(c, err)
	} else {
		c.JSON(status, row)
	}
}

func del(c *gin.Context, sql string) {
	tag, err := db.Exec(c, sql, id(c))
	switch {
	case err != nil:
		fail(c, err)
	case tag.RowsAffected() == 0:
		c.JSON(404, gin.H{"error": "not found"})
	default:
		c.Status(204)
	}
}

func pgCode(err error) string {
	var pg *pgconn.PgError
	if errors.As(err, &pg) {
		return pg.Code
	}
	return ""
}

func fail(c *gin.Context, err error) {
	code := pgCode(err)
	switch {
	case code == "23505" || code == "23503":
		c.JSON(409, gin.H{"error": "conflict"})
	case strings.HasPrefix(code, "22") || code == "23502" || code == "23514":
		c.JSON(400, gin.H{"error": "invalid input"})
	default:
		c.JSON(500, gin.H{"error": "internal error"})
	}
}

func jsonObject(c *gin.Context) (string, bool) {
	raw, err := c.GetRawData()
	if err == nil && json.Valid(raw) && strings.HasPrefix(strings.TrimSpace(string(raw)), "{") {
		return string(raw), true
	}
	c.JSON(400, gin.H{"error": "body must be a JSON object"})
	return "", false
}

func id(c *gin.Context) int64 {
	v, _ := strconv.ParseInt(c.Param("id"), 10, 64) // non-numeric -> 0 -> 404
	return v
}

func page(c *gin.Context) (int, int) {
	limit, err := strconv.Atoi(c.Query("limit"))
	if err != nil {
		limit = 20
	}
	offset, err := strconv.Atoi(c.Query("offset"))
	if err != nil || offset < 0 {
		offset = 0
	}
	return min(max(limit, 1), 100), offset
}

func optStr(c *gin.Context, k string) *string {
	if v := c.Query(k); v != "" {
		return &v
	}
	return nil
}

func optInt(c *gin.Context, k string) *int64 {
	if v, err := strconv.ParseInt(c.Query(k), 10, 64); err == nil {
		return &v
	}
	return nil
}
