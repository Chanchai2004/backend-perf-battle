"""Contract test: every backend must answer exactly like the reference backend.

usage: python bench/contract.py <base_url> <out.json> [reference.json]
Run against a freshly reset DB. Reads are compared byte-for-byte (after JSON parse),
writes are compared with volatile fields (ids, timestamps, order numbers) stripped.
Exit code 1 on any mismatch with the reference.
"""
import json
import sys
import urllib.error
import urllib.request

VOLATILE = {"id", "order_id", "created_at", "updated_at", "placed_at", "order_number"}
BASE = sys.argv[1].rstrip("/")


def call(method, path, body=None):
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    try:
        parsed = json.loads(raw) if raw else None
    except ValueError:
        parsed = raw.decode(errors="replace")
    return status, parsed


def strip(v):
    if isinstance(v, dict):
        return {k: strip(x) for k, x in v.items() if k not in VOLATILE}
    if isinstance(v, list):
        return [strip(x) for x in v]
    return v


results = {}


def case(name, method, path, body=None, compare="full"):
    """compare: full | strip (drop volatile fields) | status (ignore body)."""
    status, parsed = call(method, path, body)
    if compare == "status":
        parsed = None
    elif compare == "strip":
        parsed = strip(parsed)
    results[name] = {"status": status, "body": parsed}


def new_id(method, path, body):
    status, parsed = call(method, path, body)
    return status, (parsed or {}).get("id") if isinstance(parsed, dict) else None


# ---------------------------------------------------------------- reads
case("users list", "GET", "/users?limit=3&offset=5")
case("users limit clamp", "GET", "/users?limit=500")
case("users bad paging", "GET", "/users?limit=abc&offset=-4")
case("user get", "GET", "/users/123")
case("user 404", "GET", "/users/999999", compare="status")
case("user orders", "GET", "/users/1/orders?limit=3")
case("user orders empty", "GET", "/users/999999/orders")
case("products list", "GET", "/products?limit=3&offset=7")
case("product get", "GET", "/products/77")
case("product 404", "GET", "/products/999999", compare="status")
case("search", "GET", "/products/search?q=steel%20chair&limit=5")
case("search filters", "GET", "/products/search?q=lamp&category=Home&min_price=1000&max_price=50000&in_stock=true&limit=5")
case("search no q", "GET", "/products/search", compare="status")
case("orders list", "GET", "/orders?limit=2")
case("orders include all", "GET", "/orders?include=user,items&limit=3&offset=10")
case("orders include items", "GET", "/orders?include=items&limit=2")
case("orders include user", "GET", "/orders?include=user&limit=2")
case("order get", "GET", "/orders/42")
case("order 404", "GET", "/orders/999999", compare="status")

# ---------------------------------------------------------------- users writes
user = {"email": "contract@example.com", "username": "contract", "first_name": "Con", "last_name": "Tract",
        "city": "Bangkok", "date_of_birth": "1990-02-03"}
case("user create", "POST", "/users", user, compare="strip")
case("user create dup", "POST", "/users", user, compare="status")
case("user create missing", "POST", "/users", {"email": "x@example.com"}, compare="status")
case("user create not json", "POST", "/users", b"nope", compare="status")
case("user create array", "POST", "/users", b"[1,2]", compare="status")
_, uid = new_id("POST", "/users", {**user, "email": "c2@example.com", "username": "c2"})
case("user update", "PUT", f"/users/{uid}", {"city": "Chiang Mai", "loyalty_points": 7}, compare="strip")
case("user update bad type", "PUT", f"/users/{uid}", {"loyalty_points": "lots"}, compare="status")
case("user update 404", "PUT", "/users/999999", {"city": "x"}, compare="status")
case("user delete referenced", "DELETE", "/users/1", compare="status")
case("user delete", "DELETE", f"/users/{uid}", compare="status")
case("user deleted 404", "GET", f"/users/{uid}", compare="status")

# ---------------------------------------------------------------- products writes
product = {"sku": "SKU-CONTRACT", "name": "Contract Steel Mug", "category": "Kitchen", "price_cents": 1299,
           "stock_quantity": 5, "color": "red"}
case("product create", "POST", "/products", product, compare="strip")
case("product create dup", "POST", "/products", product, compare="status")
case("product create missing", "POST", "/products", {"name": "x"}, compare="status")
_, pid = new_id("POST", "/products", {**product, "sku": "SKU-C2"})
case("product update", "PUT", f"/products/{pid}", {"price_cents": 999, "is_active": False}, compare="strip")
case("product delete referenced", "DELETE", "/products/1", compare="status")
case("product delete", "DELETE", f"/products/{pid}", compare="status")

# ---------------------------------------------------------------- orders writes
order = {"user_id": 5, "items": [{"product_id": 10, "quantity": 2}, {"product_id": 20, "quantity": 1}],
         "payment_method": "paypal", "notes": "contract"}
case("order create", "POST", "/orders", order, compare="strip")
case("order create unknown product", "POST", "/orders", {"user_id": 5, "items": [{"product_id": 999999, "quantity": 1}]}, compare="status")
case("order create unknown user", "POST", "/orders", {"user_id": 999999, "items": [{"product_id": 10, "quantity": 1}]}, compare="status")
case("order create no items", "POST", "/orders", {"user_id": 5, "items": []}, compare="status")
case("order create bad qty", "POST", "/orders", {"user_id": 5, "items": [{"product_id": 10, "quantity": 0}]}, compare="status")
_, oid = new_id("POST", "/orders", order)
case("order update", "PUT", f"/orders/{oid}", {"status": "paid", "tracking_number": "TRK1"}, compare="strip")
case("order update bad status", "PUT", f"/orders/{oid}", {"status": "bogus"}, compare="status")
case("order delete", "DELETE", f"/orders/{oid}", compare="status")
case("order deleted 404", "GET", f"/orders/{oid}", compare="status")

# ---------------------------------------------------------------- self-checks (independent of reference)
problems = []
oc = results["order create"]
if oc["status"] == 201:
    p10, p20 = call("GET", "/products/10")[1]["price_cents"], call("GET", "/products/20")[1]["price_cents"]
    sub = p10 * 2 + p20
    ship = 0 if sub >= 5000 else 499
    want = {"subtotal_cents": sub, "tax_cents": sub * 7 // 100, "shipping_cents": ship,
            "total_cents": sub + sub * 7 // 100 + ship}
    got = {k: oc["body"].get(k) for k in want}
    if got != want:
        problems.append(f"order totals: want {want} got {got}")
if len((results["users limit clamp"]["body"] or {}).get("data", [])) != 100:
    problems.append("limit=500 must clamp to 100")

with open(sys.argv[2], "w") as f:
    json.dump(results, f, indent=1, sort_keys=True)

if len(sys.argv) > 3:
    ref = json.load(open(sys.argv[3]))
    for name, want in ref.items():
        if results.get(name) != want:
            got = json.dumps(results.get(name), sort_keys=True)[:300]
            problems.append(f"{name}:\n    want {json.dumps(want, sort_keys=True)[:300]}\n    got  {got}")

for p in problems:
    print("MISMATCH", p)
print(f"{BASE}: {len(results)} cases, {len(problems)} problems")
sys.exit(1 if problems else 0)
