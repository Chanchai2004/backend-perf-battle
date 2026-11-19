// k6 load test shared by every backend.
//   k6 run -e BASE=http://go:8080 -e SCENARIO=heavy -e OUT=/results/go/heavy/summary.json bench.js
// Closed model, no think time: each VU fires its next request as soon as the last one returns.
import http from 'k6/http';

const BASE = __ENV.BASE || 'http://localhost:8081';
const SCENARIO = __ENV.SCENARIO || 'light';
const WINDOW_S = 5; // timeline resolution

const SCENARIOS = {
  warmup: { executor: 'constant-vus', vus: 20, duration: '15s', exec: 'mix' },
  light: { executor: 'constant-vus', vus: 50, duration: '60s', exec: 'mix' },
  medium: { executor: 'constant-vus', vus: 200, duration: '180s', exec: 'mix' },
  heavy: { executor: 'constant-vus', vus: 500, duration: '180s', exec: 'mix' },
  spike: {
    executor: 'ramping-vus', startVUs: 50, exec: 'mix', gracefulRampDown: '5s',
    stages: [
      { duration: '30s', target: 50 },
      { duration: '20s', target: 800 },
      { duration: '60s', target: 800 },
      { duration: '10s', target: 0 },
    ],
  },
  ep_users: { executor: 'constant-vus', vus: 100, duration: '60s', exec: 'users' },
  ep_orders_include: { executor: 'constant-vus', vus: 100, duration: '60s', exec: 'ordersInclude' },
  ep_post_orders: { executor: 'constant-vus', vus: 100, duration: '60s', exec: 'postOrder' },
};

const ENDPOINTS = ['product_get', 'user_get', 'users_list', 'products_list', 'orders_include',
  'user_orders', 'search', 'order_create'];

function totalSeconds(s) {
  const secs = (d) => parseInt(d, 10);
  return s.stages ? s.stages.reduce((t, st) => t + secs(st.duration), 0) : secs(s.duration);
}

// Always-true thresholds create per-tag sub-metrics, so the summary carries a p95/RPS
// timeline (tag w = 5 s window) and a per-endpoint breakdown without a metrics backend.
const thresholds = {};
const windows = Math.ceil(totalSeconds(SCENARIOS[SCENARIO]) / WINDOW_S) + 2;
for (let w = 0; w < windows; w++) {
  thresholds[`http_req_duration{w:${w}}`] = ['p(95)>=0'];
  thresholds[`http_reqs{w:${w}}`] = ['count>=0'];
  thresholds[`http_req_failed{w:${w}}`] = ['rate>=0'];
}
for (const ep of ENDPOINTS) {
  thresholds[`http_req_duration{ep:${ep}}`] = ['p(95)>=0'];
  thresholds[`http_reqs{ep:${ep}}`] = ['count>=0'];
}

export const options = {
  scenarios: { [SCENARIO]: SCENARIOS[SCENARIO] },
  thresholds,
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  discardResponseBodies: true,
  noVUConnectionReuse: false,
};

export function setup() {
  return { start: Date.now() };
}

const rand = (min, max) => min + Math.floor(Math.random() * (max - min + 1));
const WORDS = ['steel', 'wooden', 'chair', 'lamp', 'keyboard', 'leather', 'bamboo', 'watch', 'ergonomic',
  'vintage', 'glass', 'mug', 'backpack', 'silk', 'camera', 'desk', 'rustic', 'speaker'];
const CATEGORIES = ['Home', 'Electronics', 'Fashion', 'Sports', 'Office', 'Kitchen', 'Outdoors', 'Toys'];
const JSON_HEADERS = { 'Content-Type': 'application/json' };

function tags(data, ep) {
  // name = ep stops k6 from creating one time series per unique URL
  return { tags: { ep, name: ep, w: String(Math.floor((Date.now() - data.start) / 1000 / WINDOW_S)) } };
}

function get(data, ep, path) {
  return http.get(`${BASE}${path}`, tags(data, ep));
}

function search(data) {
  let q = `/products/search?q=${WORDS[rand(0, WORDS.length - 1)]}&limit=20`;
  if (Math.random() < 0.5) q += `&category=${CATEGORIES[rand(0, CATEGORIES.length - 1)]}`;
  if (Math.random() < 0.3) q += `&min_price=${rand(0, 20000)}&max_price=${rand(30000, 100000)}`;
  if (Math.random() < 0.3) q += '&in_stock=true';
  return get(data, 'search', q);
}

export function users(data) {
  get(data, 'users_list', `/users?limit=50&offset=${rand(0, 1000)}`);
}

export function ordersInclude(data) {
  get(data, 'orders_include', `/orders?include=user,items&limit=50&offset=${rand(0, 1000)}`);
}

export function postOrder(data) {
  const items = [];
  for (let i = rand(1, 5); i > 0; i--) items.push({ product_id: rand(1, 15000), quantity: rand(1, 3) });
  const body = JSON.stringify({ user_id: rand(1, 30000), items, payment_method: 'card', notes: 'k6' });
  http.post(`${BASE}/orders`, body, { headers: JSON_HEADERS, ...tags(data, 'order_create') });
}

// Weighted traffic mix (percent): see README "Traffic mix".
export function mix(data) {
  const r = Math.random() * 100;
  if (r < 25) get(data, 'product_get', `/products/${rand(1, 15000)}`);
  else if (r < 40) get(data, 'user_get', `/users/${rand(1, 30000)}`);
  else if (r < 50) get(data, 'users_list', `/users?limit=50&offset=${rand(0, 1000)}`);
  else if (r < 60) get(data, 'products_list', `/products?limit=50&offset=${rand(0, 1000)}`);
  else if (r < 75) ordersInclude(data);
  else if (r < 85) get(data, 'user_orders', `/users/${rand(1, 30000)}/orders`);
  else if (r < 95) search(data);
  else postOrder(data);
}

export function handleSummary(data) {
  const m = data.metrics;
  const d = m.http_req_duration ? m.http_req_duration.values : {};
  const line = `${SCENARIO}: ${Math.round(m.http_reqs ? m.http_reqs.values.rate : 0)} req/s, ` +
    `p50 ${(d.med || 0).toFixed(1)} ms, p95 ${(d['p(95)'] || 0).toFixed(1)} ms, p99 ${(d['p(99)'] || 0).toFixed(1)} ms, ` +
    `errors ${((m.http_req_failed ? m.http_req_failed.values.rate : 0) * 100).toFixed(2)}%\n`;
  const out = { stdout: line };
  if (__ENV.OUT) out[__ENV.OUT] = JSON.stringify(data);
  return out;
}
