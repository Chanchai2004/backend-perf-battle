"""Turns results/ into docs/charts/*.png and the tables between the RESULTS markers in README.md.

usage (from repo root): python report/generate.py
Missing languages/scenarios are skipped, so partial runs still produce a report.
"""
import csv
import json
import math
import re
from pathlib import Path
from statistics import mean

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RESULTS, CHARTS, README = ROOT / "results", ROOT / "docs" / "charts", ROOT / "README.md"

# Fixed language order = fixed color slot (validated categorical palette, light surface).
LANGS = {
    "go": ("Go · Gin", "#2a78d6"),
    "rust": ("Rust · Axum", "#eb6834"),
    "dotnet": (".NET 8 · Minimal API", "#1baf7a"),
    "node": ("Node 22 · Fastify", "#eda100"),
    "python": ("Python · FastAPI", "#e87ba4"),
    "cpp": ("C++ · Drogon", "#008300"),
}
LOAD = ["light", "medium", "heavy", "spike"]
ENDPOINT_TESTS = {"ep_users": "GET /users", "ep_orders_include": "GET /orders?include=user,items", "ep_post_orders": "POST /orders"}
MIX_ENDPOINTS = ["product_get", "user_get", "users_list", "products_list", "orders_include", "user_orders", "search", "order_create"]
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e7e6e2"
MARKER_START, MARKER_END = "<!-- RESULTS:START -->", "<!-- RESULTS:END -->"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "text.color": INK, "xtick.color": INK2, "ytick.color": INK2,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
    "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
    "font.size": 10, "axes.titlesize": 12, "axes.titleweight": "bold", "legend.frameon": False,
})


# ---------------------------------------------------------------- loading

def mib(s: str) -> float:
    m = re.match(r"([\d.]+)\s*([KMG]i?B|B)", s.strip())
    if not m:
        return math.nan
    v, unit = float(m.group(1)), m.group(2)
    return v * {"B": 1 / 2**20, "KiB": 1 / 1024, "kB": 1 / 1024, "MiB": 1, "MB": 1, "GiB": 1024, "GB": 1024}.get(unit, 1)


def load_run(d: Path):
    s = json.loads((d / "summary.json").read_text())["metrics"]
    dur = s["http_req_duration"]["values"]
    run = {
        "rps": s["http_reqs"]["values"]["rate"],
        "reqs": s["http_reqs"]["values"]["count"],
        "p50": dur["med"], "p90": dur["p(90)"], "p95": dur["p(95)"], "p99": dur["p(99)"], "max": dur["max"],
        "err": s.get("http_req_failed", {}).get("values", {}).get("rate", 0.0),
        "endpoints": {}, "timeline": [],
    }
    for ep in MIX_ENDPOINTS:
        k = f"http_req_duration{{ep:{ep}}}"
        if k in s and s[k]["values"].get("p(95)") is not None and s.get(f"http_reqs{{ep:{ep}}}", {}).get("values", {}).get("count"):
            run["endpoints"][ep] = s[k]["values"]["p(95)"]
    w = 0
    while f"http_reqs{{w:{w}}}" in s:
        cnt = s[f"http_reqs{{w:{w}}}"]["values"].get("count", 0)
        p95 = s.get(f"http_req_duration{{w:{w}}}", {}).get("values", {}).get("p(95)")
        if cnt:
            run["timeline"].append((w * 5, cnt / 5, p95))
        w += 1

    app_cpu, app_mem, pg_cpu = [], [], []
    if (d / "stats.csv").exists():
        for r in csv.DictReader(open(d / "stats.csv")):
            try:
                cpu = float(r["cpu_pct"])
            except ValueError:
                continue
            if "postgres" in r["container"]:
                pg_cpu.append(cpu)
            else:
                app_cpu.append(cpu)
                app_mem.append(mib(r["mem_usage"].split("/")[0]))
    nz = lambda xs: xs or [math.nan]  # noqa: E731
    run.update(cpu_avg=mean(nz(app_cpu)), cpu_max=max(nz(app_cpu)), mem_avg=mean(nz(app_mem)), mem_max=max(nz(app_mem)),
               pg_cpu=mean(nz(pg_cpu)))

    db = []
    if (d / "db.csv").exists():
        db = [(int(r["ts"]), int(r["active"]), int(r["total"])) for r in csv.DictReader(open(d / "db.csv")) if r["active"].isdigit()]
    t0 = db[0][0] if db else 0
    run["db"] = [(t - t0, a, tot) for t, a, tot in db]
    run.update(db_active_avg=mean([a for _, a, _ in db] or [math.nan]), db_active_max=max([a for _, a, _ in db] or [0]),
               db_total_max=max([t for _, _, t in db] or [0]))
    run["rps_per_core"] = run["rps"] / (run["cpu_avg"] / 100) if run["cpu_avg"] and not math.isnan(run["cpu_avg"]) else math.nan
    return run


def load_all():
    data = {}
    for lang in LANGS:
        for d in sorted((RESULTS / lang).glob("*")) if (RESULTS / lang).exists() else []:
            if (d / "summary.json").exists():
                data.setdefault(lang, {})[d.name] = load_run(d)
    return data


# ---------------------------------------------------------------- charts

def langs_with(data, scenario):
    return [l for l in LANGS if scenario in data.get(l, {})]


def grouped_bars(ax, groups, langs, value, fmt="{:,.0f}", label_top=True):
    n = len(langs)
    width = 0.8 / max(n, 1)
    for i, lang in enumerate(langs):
        xs, ys = [], []
        for g, (key, _) in enumerate(groups):
            v = value(lang, key)
            if v is not None and not math.isnan(v):
                xs.append(g - 0.4 + width * (i + 0.5))
                ys.append(v)
        bars = ax.bar(xs, ys, width * 0.92, color=LANGS[lang][1], label=LANGS[lang][0], edgecolor=SURFACE, linewidth=1)
        if label_top:
            for b, y in zip(bars, ys):
                ax.annotate(fmt.format(y), (b.get_x() + b.get_width() / 2, y), xytext=(0, 2), textcoords="offset points",
                            ha="center", va="bottom", fontsize=7, color=INK2, rotation=90 if n > 4 else 0)
    ax.set_xticks(range(len(groups)), [lbl for _, lbl in groups])
    ax.tick_params(axis="x", length=0)


def legend(fig, langs):
    handles = [plt.Rectangle((0, 0), 1, 1, color=LANGS[l][1]) for l in langs]
    fig.legend(handles, [LANGS[l][0] for l in langs], loc="lower center", ncol=min(len(langs), 6), fontsize=9,
               bbox_to_anchor=(0.5, -0.01))


def save(fig, name):
    CHARTS.mkdir(parents=True, exist_ok=True)
    fig.savefig(CHARTS / name, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return f"docs/charts/{name}"


def chart_throughput(data):
    langs = [l for l in LANGS if any(s in data.get(l, {}) for s in LOAD)]
    fig, ax = plt.subplots(figsize=(11, 4.8))
    grouped_bars(ax, [(s, s.capitalize()) for s in LOAD], langs, lambda l, s: data[l].get(s, {}).get("rps"))
    ax.set_title("Throughput by load scenario (requests/second, higher is better)", loc="left")
    ax.set_ylabel("req/s")
    ax.margins(y=0.15)
    legend(fig, langs)
    fig.subplots_adjust(bottom=0.18)
    return save(fig, "throughput.png")


def chart_latency(data, scenario="heavy"):
    langs = langs_with(data, scenario)
    fig, ax = plt.subplots(figsize=(11, 4.8))
    grouped_bars(ax, [("p50", "p50"), ("p90", "p90"), ("p95", "p95"), ("p99", "p99")], langs,
                 lambda l, p: data[l][scenario][p], fmt="{:,.0f}")
    ax.set_yscale("log")
    ax.set_title(f"Latency percentiles, {scenario} load (ms, log scale, lower is better)", loc="left")
    ax.set_ylabel("ms")
    legend(fig, langs)
    fig.subplots_adjust(bottom=0.18)
    return save(fig, f"latency_{scenario}.png")


def chart_resources(data, scenario="heavy"):
    langs = langs_with(data, scenario)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.6))
    grouped_bars(a1, [("cpu_avg", "CPU avg"), ("cpu_max", "CPU max")], langs, lambda l, k: data[l][scenario][k])
    a1.axhline(200, color=INK2, linewidth=1, linestyle="--")
    a1.annotate("limit: 2 cores", (1.45, 200), xytext=(0, 3), textcoords="offset points", ha="right", fontsize=8, color=INK2)
    a1.set_title("Backend CPU (% of one core)", loc="left")
    grouped_bars(a2, [("mem_avg", "Memory avg"), ("mem_max", "Memory max")], langs, lambda l, k: data[l][scenario][k])
    a2.set_title("Backend memory (MiB, lower is better)", loc="left")
    for a in (a1, a2):
        a.margins(y=0.15)
    fig.suptitle(f"Resource usage, {scenario} load", x=0.01, ha="left", fontweight="bold")
    legend(fig, langs)
    fig.subplots_adjust(bottom=0.2)
    return save(fig, f"resources_{scenario}.png")


def chart_efficiency(data, scenario="heavy"):
    langs = langs_with(data, scenario)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.6))
    for ax, key, title, fmt in (
        (a1, lambda r: r["rps_per_core"], "Requests/s per CPU core used", "{:,.0f}"),
        (a2, lambda r: r["rps"] / r["mem_max"] * 100 if r["mem_max"] else math.nan, "Requests/s per 100 MiB of peak memory", "{:,.0f}"),
    ):
        vals = [key(data[l][scenario]) for l in langs]
        bars = ax.bar(range(len(langs)), vals, 0.6, color=[LANGS[l][1] for l in langs], edgecolor=SURFACE)
        for b, v in zip(bars, vals):
            if not math.isnan(v):
                ax.annotate(fmt.format(v), (b.get_x() + b.get_width() / 2, v), xytext=(0, 2), textcoords="offset points",
                            ha="center", fontsize=8, color=INK2)
        ax.set_xticks(range(len(langs)), [LANGS[l][0].split(" · ")[0] for l in langs])
        ax.tick_params(axis="x", length=0)
        ax.set_title(title, loc="left")
        ax.margins(y=0.15)
    fig.suptitle(f"Efficiency, {scenario} load (higher is better)", x=0.01, ha="left", fontweight="bold")
    return save(fig, f"efficiency_{scenario}.png")


def chart_timeline(data, scenario="spike"):
    langs = langs_with(data, scenario)
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    for l in langs:
        tl = data[l][scenario]["timeline"]
        a1.plot([t for t, _, _ in tl], [r for _, r, _ in tl], color=LANGS[l][1], linewidth=2, label=LANGS[l][0])
        a2.plot([t for t, _, p in tl if p is not None], [p for _, _, p in tl if p is not None], color=LANGS[l][1], linewidth=2)
    for a in (a1, a2):
        a.axvspan(30, 50, color=GRID, alpha=0.6, linewidth=0)
    a1.annotate("50 → 800 VUs", (40, a1.get_ylim()[1]), xytext=(0, -12), textcoords="offset points", ha="center", fontsize=8, color=INK2)
    a1.set_title(f"{scenario.capitalize()} test: throughput over time (req/s)", loc="left")
    a2.set_title("p95 latency over time (ms, log scale)", loc="left")
    a2.set_yscale("log")
    a2.set_xlabel("seconds since start (5 s windows)")
    legend(fig, langs)
    fig.subplots_adjust(bottom=0.12, hspace=0.3)
    return save(fig, f"timeline_{scenario}.png")


def chart_db(data, scenario="heavy"):
    langs = [l for l in langs_with(data, scenario) if data[l][scenario]["db"]]
    fig, ax = plt.subplots(figsize=(11, 4.2))
    for l in langs:
        db = data[l][scenario]["db"]
        ax.plot([t for t, _, _ in db], [a for _, a, _ in db], color=LANGS[l][1], linewidth=2, label=LANGS[l][0])
    ax.axhline(20, color=INK2, linewidth=1, linestyle="--")
    ax.annotate("pool size: 20", (0, 20), xytext=(2, 3), textcoords="offset points", fontsize=8, color=INK2)
    ax.set_ylim(0, 22)
    ax.set_title(f"Busy DB connections over time, {scenario} load (state = active)", loc="left")
    ax.set_xlabel("seconds since start")
    legend(fig, langs)
    fig.subplots_adjust(bottom=0.2)
    return save(fig, f"db_connections_{scenario}.png")


def chart_endpoints(data):
    langs = [l for l in LANGS if any(s in data.get(l, {}) for s in ENDPOINT_TESTS)]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 8))
    groups = list(ENDPOINT_TESTS.items())
    grouped_bars(a1, groups, langs, lambda l, s: data[l].get(s, {}).get("rps"))
    a1.set_title("Single-endpoint tests at 100 VUs: throughput (req/s, higher is better)", loc="left")
    grouped_bars(a2, groups, langs, lambda l, s: data[l].get(s, {}).get("p99"))
    a2.set_title("p99 latency (ms, lower is better)", loc="left")
    for a in (a1, a2):
        a.margins(y=0.18)
    legend(fig, langs)
    fig.subplots_adjust(bottom=0.1, hspace=0.35)
    return save(fig, "endpoints.png")


# ---------------------------------------------------------------- tables

def num(v, fmt="{:,.0f}"):
    return "–" if v is None or (isinstance(v, float) and math.isnan(v)) else fmt.format(v)


def table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] + ["--:"] * (len(header) - 1)) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def bold_best(values, lower_is_better):
    """Format cells and bold the best one."""
    clean = [v for v in values if v is not None and not math.isnan(v)]
    best = (min if lower_is_better else max)(clean) if clean else None
    return lambda v, fmt: (f"**{num(v, fmt)}**" if v == best and best is not None else num(v, fmt))


def matrix(data, scenarios, key, fmt, lower):
    rows = []
    for s, label in scenarios:
        vals = [data.get(l, {}).get(s, {}).get(key) for l in LANGS]
        b = bold_best(vals, lower)
        rows.append([label] + [b(v, fmt) for v in vals])
    return table(["Scenario"] + [LANGS[l][0] for l in LANGS], rows)


def medals(data):
    cats = [
        ("Throughput (heavy RPS)", "heavy", "rps", False, "{:,.0f} req/s"),
        ("Tail latency (heavy p99)", "heavy", "p99", True, "{:,.1f} ms"),
        ("Memory (heavy peak)", "heavy", "mem_max", True, "{:,.0f} MiB"),
        ("CPU efficiency (req/s per core)", "heavy", "rps_per_core", False, "{:,.0f}"),
        ("Spike p99", "spike", "p99", True, "{:,.1f} ms"),
        ("Heavy join (GET /orders?include)", "ep_orders_include", "rps", False, "{:,.0f} req/s"),
        ("Writes (POST /orders)", "ep_post_orders", "rps", False, "{:,.0f} req/s"),
    ]
    rows = []
    for title, s, k, lower, fmt in cats:
        ranked = sorted(((data[l][s][k], l) for l in LANGS if s in data.get(l, {}) and not math.isnan(data[l][s][k])),
                        reverse=not lower)
        cells = [f"{m} {LANGS[l][0]} ({fmt.format(v)})" for m, (v, l) in zip("🥇🥈🥉", ranked)]
        rows.append([title] + cells + ["–"] * (3 - len(cells)))
    return table(["Category", "🥇", "🥈", "🥉"], rows).replace("|--:|--:|--:|", "|---|---|---|")


def build_section(data):
    if not data:
        return "_No results yet. Run `./bench/run-all.sh`, then this section fills itself in._"
    meta = json.loads((RESULTS / "meta.json").read_text()) if (RESULTS / "meta.json").exists() else {}
    parts = []
    if meta:
        parts.append(f"> Measured {meta.get('date', '?')} on **{meta.get('cpu', '?')}**, Docker {meta.get('docker', '?')}.\n"
                     "> Every backend capped at 2 CPUs / 1 GB, PostgreSQL at 4 CPUs / 2 GB, 20 DB connections per backend.")
    parts.append("### Winners\n\n" + medals(data))

    loads = [(s, s.capitalize()) for s in LOAD]
    if any(s in d for d in data.values() for s in LOAD):
        parts.append(f"### Throughput\n\n![Throughput]({chart_throughput(data)})\n\n**Requests/second** (bold = best)\n\n"
                     + matrix(data, loads, "rps", "{:,.0f}", False))
        parts.append("### Latency\n\n"
                     + (f"![Latency]({chart_latency(data)})\n\n" if langs_with(data, "heavy") else "")
                     + "**p50 (ms)**\n\n" + matrix(data, loads, "p50", "{:,.1f}", True)
                     + "\n\n**p95 (ms)**\n\n" + matrix(data, loads, "p95", "{:,.1f}", True)
                     + "\n\n**p99 (ms)**\n\n" + matrix(data, loads, "p99", "{:,.1f}", True)
                     + "\n\n**Error rate (%)**\n\n" + matrix(
                         {l: {s: {"err": r["err"] * 100} for s, r in d.items()} for l, d in data.items()},
                         loads, "err", "{:,.2f}", True))

    if langs_with(data, "heavy"):
        rows = []
        for l in langs_with(data, "heavy"):
            r = data[l]["heavy"]
            rows.append([LANGS[l][0], num(r["cpu_avg"]), num(r["cpu_max"]), num(r["mem_avg"]), num(r["mem_max"]),
                         num(r["rps_per_core"]), num(r["db_active_avg"], "{:,.1f}"), num(r["db_active_max"]),
                         num(r["db_total_max"]), num(r["pg_cpu"])])
        parts.append(f"### Resources (heavy load, 500 VUs)\n\n![Resources]({chart_resources(data)})\n\n"
                     f"![Efficiency]({chart_efficiency(data)})\n\n"
                     + table(["Backend", "CPU avg %", "CPU max %", "Mem avg MiB", "Mem max MiB", "req/s per core",
                              "DB active avg", "DB active max", "DB conns", "Postgres CPU avg %"], rows)
                     + "\n\nCPU % is of one core (200 % = the full 2-CPU limit). *DB active* counts connections "
                       "running a query at the sampling instant; *DB conns* is the pool size Postgres saw.")
        parts.append(f"### Database connection usage\n\n![DB connections]({chart_db(data)})")

        eps = [ep for ep in MIX_ENDPOINTS if any(ep in data[l]["heavy"]["endpoints"] for l in langs_with(data, "heavy"))]
        rows = []
        for ep in eps:
            vals = [data.get(l, {}).get("heavy", {}).get("endpoints", {}).get(ep) for l in LANGS]
            b = bold_best(vals, True)
            rows.append([f"`{ep}`"] + [b(v, "{:,.1f}") for v in vals])
        parts.append("### p95 per endpoint inside the heavy mix (ms)\n\n" + table(["Endpoint"] + [LANGS[l][0] for l in LANGS], rows))

    if langs_with(data, "spike"):
        parts.append(f"### Spike test (50 → 800 VUs in 20 s)\n\n![Spike timeline]({chart_timeline(data)})")

    if any(s in d for d in data.values() for s in ENDPOINT_TESTS):
        eps = list(ENDPOINT_TESTS.items())
        parts.append(f"### Single-endpoint tests (100 VUs, 60 s)\n\n![Endpoints]({chart_endpoints(data)})\n\n"
                     "**Requests/second**\n\n" + matrix(data, eps, "rps", "{:,.0f}", False).replace("| Scenario", "| Endpoint")
                     + "\n\n**p99 (ms)**\n\n" + matrix(data, eps, "p99", "{:,.1f}", True).replace("| Scenario", "| Endpoint"))
    return "\n\n".join(parts)


def main():
    section = build_section(load_all())
    text = README.read_text(encoding="utf-8")
    start, end = text.index(MARKER_START) + len(MARKER_START), text.index(MARKER_END)
    README.write_text(text[:start] + "\n\n" + section + "\n\n" + text[end:], encoding="utf-8")
    print(f"README updated, charts in {CHARTS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
