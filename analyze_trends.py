#!/usr/bin/env python3
"""
Fuel shortage trend analysis for French gas stations.

Reads JSON snapshots from raw/ directory (stations_YYYY-MM-DD_HHhMM.json)
and produces:
  - Console summary with day-over-day, 7-day, and 30-day trends
  - HTML report with interactive Plotly charts (requires internet for CDN)
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FUEL_TYPES = ["gazole", "sp95", "sp98", "e10", "e85", "gplc"]
FUEL_LABELS = {
    "gazole": "Gazole",
    "sp95": "SP95",
    "sp98": "SP98",
    "e10": "E10",
    "e85": "E85",
    "gplc": "GPLc",
}
FUEL_COLORS = {
    "gazole": "#1f77b4",
    "sp95": "#ff7f0e",
    "sp98": "#2ca02c",
    "e10": "#d62728",
    "e85": "#9467bd",
    "gplc": "#8c564b",
}

RAW_DIR = Path(__file__).parent / "raw"
REPORTS_DIR = Path(__file__).parent / "reports"

FILENAME_RE = re.compile(r"^stations_(\d{4}-\d{2}-\d{2}_\d{2}h\d{2})\.json$")
DATETIME_FMT = "%Y-%m-%d_%Hh%M"
DATETIME_DISPLAY = "%Y-%m-%d %H:%M"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def discover_snapshots(raw_dir: Path) -> list[tuple[datetime, Path]]:
    """Return sorted list of (datetime, filepath) from raw/ directory."""
    snapshots = []
    for f in raw_dir.iterdir():
        m = FILENAME_RE.match(f.name)
        if m and f.is_file():
            try:
                dt = datetime.strptime(m.group(1), DATETIME_FMT)
            except ValueError:
                continue
            snapshots.append((dt, f))
    snapshots.sort(key=lambda x: x[0])
    return snapshots


def compute_metrics(dt: datetime, stations: list[dict]) -> dict:
    """Compute all metrics for a single snapshot."""
    total = len(stations)

    # --- Global temporary rupture count ---
    temp_rupture_stations = sum(
        1 for s in stations if s.get("rupture_temporaire") and len(s["rupture_temporaire"]) > 0
    )

    # --- Per fuel ---
    fuel_data = {}
    for fuel in FUEL_TYPES:
        fuel_obj_list = [s.get(fuel) for s in stations if s.get(fuel) is not None]
        offers = len(fuel_obj_list)
        temp = sum(1 for f in fuel_obj_list if f.get("rupture_type") == "temporaire")
        defin = sum(1 for f in fuel_obj_list if f.get("rupture_type") == "definitive")
        prices = [f["prix"] for f in fuel_obj_list if "prix" in f and f["prix"] is not None]
        avg_price = sum(prices) / len(prices) if prices else None

        fuel_data[fuel] = {
            "offers": offers,
            "rupture_temp": temp,
            "rupture_def": defin,
            "rupture_temp_pct": (temp / offers * 100) if offers > 0 else 0,
            "avg_price": avg_price,
        }

    # --- Per region ---
    region_data = {}
    for s in stations:
        region = s.get("region", "Inconnu")
        if region not in region_data:
            region_data[region] = {"total": 0, "rupture_temp": 0}
        region_data[region]["total"] += 1
        if s.get("rupture_temporaire") and len(s["rupture_temporaire"]) > 0:
            region_data[region]["rupture_temp"] += 1

    for region in region_data:
        t = region_data[region]["total"]
        r = region_data[region]["rupture_temp"]
        region_data[region]["rupture_temp_pct"] = (r / t * 100) if t > 0 else 0

    # --- Per department ---
    dept_data = {}
    for s in stations:
        dept = s.get("departement", "Inconnu")
        if dept not in dept_data:
            dept_data[dept] = {"total": 0, "rupture_temp": 0}
        dept_data[dept]["total"] += 1
        if s.get("rupture_temporaire") and len(s["rupture_temporaire"]) > 0:
            dept_data[dept]["rupture_temp"] += 1

    for dept in dept_data:
        t = dept_data[dept]["total"]
        r = dept_data[dept]["rupture_temp"]
        dept_data[dept]["rupture_temp_pct"] = (r / t * 100) if t > 0 else 0

    return {
        "datetime": dt,
        "total_stations": total,
        "rupture_temp_stations": temp_rupture_stations,
        "rupture_temp_pct": (temp_rupture_stations / total * 100) if total > 0 else 0,
        "fuel": fuel_data,
        "region": region_data,
        "dept": dept_data,
    }


def load_all_snapshots(raw_dir: Path) -> list[dict]:
    """Load and compute metrics for all snapshots in raw/."""
    snapshots = discover_snapshots(raw_dir)
    if not snapshots:
        print(f"ERROR: No stations_YYYY-MM-DD_HHhMM.json files found in {raw_dir}")
        sys.exit(1)

    all_metrics = []
    for dt, filepath in snapshots:
        print(f"  Loading {filepath.name}...", end=" ", flush=True)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"ERROR: Failed to parse {filepath.name}: {e}")
            sys.exit(1)
        stations = data.get("stations", [])
        metrics = compute_metrics(dt, stations)
        all_metrics.append(metrics)
        print(f"{metrics['total_stations']} stations, {metrics['rupture_temp_stations']} ruptures temp.")

    return all_metrics


# ---------------------------------------------------------------------------
# Trend computation with pandas
# ---------------------------------------------------------------------------


def build_dataframes(all_metrics: list[dict]) -> dict[str, pd.DataFrame]:
    """Build pandas DataFrames from metrics for trend analysis."""

    # --- Global (one row per snapshot) ---
    global_rows = []
    for m in all_metrics:
        row = {
            "datetime": pd.Timestamp(m["datetime"]),
            "total_stations": m["total_stations"],
            "rupture_temp": m["rupture_temp_stations"],
            "rupture_temp_pct": m["rupture_temp_pct"],
        }
        for fuel in FUEL_TYPES:
            row[f"{fuel}_temp"] = m["fuel"][fuel]["rupture_temp"]
            row[f"{fuel}_def"] = m["fuel"][fuel]["rupture_def"]
            row[f"{fuel}_pct"] = m["fuel"][fuel]["rupture_temp_pct"]
            row[f"{fuel}_price"] = m["fuel"][fuel]["avg_price"]
            row[f"{fuel}_offers"] = m["fuel"][fuel]["offers"]
        global_rows.append(row)

    df_global = pd.DataFrame(global_rows).set_index("datetime").sort_index()

    # Add rolling averages
    for col in ["rupture_temp", "rupture_temp_pct"] + [f"{f}_temp" for f in FUEL_TYPES] + [f"{f}_pct" for f in FUEL_TYPES]:
        if len(df_global) >= 7:
            df_global[f"{col}_ma7"] = df_global[col].rolling(7, min_periods=1).mean()
        if len(df_global) >= 30:
            df_global[f"{col}_ma30"] = df_global[col].rolling(30, min_periods=1).mean()

    # --- Region per snapshot ---
    region_rows = []
    for m in all_metrics:
        for region, rdata in m["region"].items():
            region_rows.append({
                "datetime": pd.Timestamp(m["datetime"]),
                "region": region,
                "total": rdata["total"],
                "rupture_temp": rdata["rupture_temp"],
                "rupture_temp_pct": rdata["rupture_temp_pct"],
            })
    df_region = pd.DataFrame(region_rows)

    # --- Department per snapshot ---
    dept_rows = []
    for m in all_metrics:
        for dept, ddata in m["dept"].items():
            dept_rows.append({
                "datetime": pd.Timestamp(m["datetime"]),
                "dept": dept,
                "total": ddata["total"],
                "rupture_temp": ddata["rupture_temp"],
                "rupture_temp_pct": ddata["rupture_temp_pct"],
            })
    df_dept = pd.DataFrame(dept_rows)

    return {"global": df_global, "region": df_region, "dept": df_dept}


def compute_delta(series: pd.Series, days: int) -> float | None:
    """Compute delta between last value and value at -N calendar days."""
    target_date = series.index[-1] - pd.Timedelta(days=days)
    if target_date < series.index[0]:
        return None
    ref = series.asof(target_date)
    if pd.isna(ref):
        return None
    return series.iloc[-1] - ref


# ---------------------------------------------------------------------------
# Console report
# ---------------------------------------------------------------------------


def trend_arrow(delta: float | None) -> str:
    if delta is None:
        return "N/A"
    if delta > 0:
        return f"+{delta:.0f} \u2b06"
    if delta < 0:
        return f"{delta:.0f} \u2b07"
    return "0 ="


def trend_label(delta: float | None) -> str:
    if delta is None:
        return ""
    if delta > 50:
        return "S'AGGRAVE"
    if delta > 0:
        return "s'aggrave"
    if delta < -50:
        return "S'AMELIORE"
    if delta < 0:
        return "s'ameliore"
    return "stable"


def print_console_report(dfs: dict[str, pd.DataFrame]):
    """Print structured console report."""
    df = dfs["global"]
    df_region = dfs["region"]

    latest = df.iloc[-1]
    dt_latest = df.index[-1].strftime(DATETIME_DISPLAY)
    dt_first = df.index[0].strftime(DATETIME_DISPLAY)
    n_snapshots = len(df)

    d1 = compute_delta(df["rupture_temp"], 1)
    d7 = compute_delta(df["rupture_temp"], 7)
    d30 = compute_delta(df["rupture_temp"], 30)

    d1_pct = compute_delta(df["rupture_temp_pct"], 1)
    d7_pct = compute_delta(df["rupture_temp_pct"], 7)
    d30_pct = compute_delta(df["rupture_temp_pct"], 30)

    print()
    print("=" * 65)
    print("  RAPPORT PENURIE CARBURANT — FRANCE")
    print("=" * 65)
    print(f"  Periode : {dt_first} -> {dt_latest} ({n_snapshots} snapshots)")
    print(f"  Dernier snapshot : {dt_latest}")
    print()

    # --- Current situation ---
    print("-" * 65)
    print("  SITUATION ACTUELLE")
    print("-" * 65)
    print(f"  Stations totales         : {latest['total_stations']:.0f}")
    print(f"  Ruptures temporaires     : {latest['rupture_temp']:.0f} ({latest['rupture_temp_pct']:.1f}%)")
    print()
    if d1 is not None:
        print(f"  Tendance J-1  : {trend_arrow(d1)} stations ({trend_arrow(d1_pct)} pts)  {trend_label(d1)}")
    if d7 is not None:
        print(f"  Tendance 7j   : {trend_arrow(d7)} stations ({trend_arrow(d7_pct)} pts)  {trend_label(d7)}")
    if d30 is not None:
        print(f"  Tendance 30j  : {trend_arrow(d30)} stations ({trend_arrow(d30_pct)} pts)  {trend_label(d30)}")
    print()

    # --- Per fuel ---
    print("-" * 65)
    print("  PAR CARBURANT")
    print("-" * 65)
    header = f"  {'Carburant':<10} {'Ruptures':>10} {'%':>7} {'J-1':>8} {'7j':>8} {'30j':>8}  {'Tendance':<12}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    for fuel in FUEL_TYPES:
        col = f"{fuel}_temp"
        val = latest[col]
        pct = latest[f"{fuel}_pct"]
        fd1 = compute_delta(df[col], 1)
        fd7 = compute_delta(df[col], 7)
        fd30 = compute_delta(df[col], 30)
        label = FUEL_LABELS[fuel]
        print(f"  {label:<10} {val:>10.0f} {pct:>6.1f}% {trend_arrow(fd1):>8} {trend_arrow(fd7):>8} {trend_arrow(fd30):>8}  {trend_label(fd1):<12}")

    print()

    # --- Per fuel: prices ---
    print("-" * 65)
    print("  PRIX MOYENS (EUR/L)")
    print("-" * 65)
    price_header = f"  {'Carburant':<10} {'Prix':>8} {'J-1':>10} {'7j':>10} {'30j':>10}"
    print(price_header)
    print("  " + "-" * (len(price_header) - 2))

    for fuel in FUEL_TYPES:
        col = f"{fuel}_price"
        price = latest[col]
        if pd.isna(price):
            print(f"  {FUEL_LABELS[fuel]:<10} {'N/A':>8}")
            continue
        pd1 = compute_delta(df[col], 1)
        pd7 = compute_delta(df[col], 7)
        pd30 = compute_delta(df[col], 30)

        def fmt_price_delta(d):
            if d is None:
                return "N/A"
            return f"{d:+.3f}"

        print(f"  {FUEL_LABELS[fuel]:<10} {price:>8.3f} {fmt_price_delta(pd1):>10} {fmt_price_delta(pd7):>10} {fmt_price_delta(pd30):>10}")

    print()

    # --- Top regions ---
    print("-" * 65)
    print("  TOP 10 REGIONS (dernier snapshot)")
    print("-" * 65)

    latest_dt = df.index[-1]
    region_latest = df_region[df_region["datetime"] == latest_dt].copy()
    region_latest = region_latest.sort_values("rupture_temp", ascending=False).head(10)

    region_header = f"  {'Region':<30} {'Ruptures':>10} {'%':>7} {'/ Total':>10}"
    print(region_header)
    print("  " + "-" * (len(region_header) - 2))

    for _, row in region_latest.iterrows():
        print(f"  {row['region']:<30} {row['rupture_temp']:>10.0f} {row['rupture_temp_pct']:>6.1f}% {row['total']:>10.0f}")

    print()

    # --- Auto diagnostic ---
    print("-" * 65)
    print("  DIAGNOSTIC")
    print("-" * 65)

    if d1 is not None:
        if d1 > 100:
            print("  [ALERTE] Forte degradation en 24h. La situation empire rapidement.")
        elif d1 > 0:
            print("  [ATTENTION] Legere degradation en 24h.")
        elif d1 < -100:
            print("  [POSITIF] Nette amelioration en 24h.")
        elif d1 < 0:
            print("  [POSITIF] Legere amelioration en 24h.")
        else:
            print("  [STABLE] Situation inchangee en 24h.")

    # Find most impacted fuel
    worst_fuel = max(FUEL_TYPES, key=lambda f: latest[f"{f}_temp"])
    worst_val = latest[f"{worst_fuel}_temp"]
    worst_pct = latest[f"{worst_fuel}_pct"]
    print(f"  Carburant le plus touche : {FUEL_LABELS[worst_fuel]} ({worst_val:.0f} ruptures, {worst_pct:.1f}%)")

    # Overall trend
    if d7 is not None:
        if d7 > 0:
            print(f"  Tendance 7j : la penurie S'AGGRAVE ({d7:+.0f} stations)")
        elif d7 < 0:
            print(f"  Tendance 7j : la penurie S'AMELIORE ({d7:+.0f} stations)")
        else:
            print("  Tendance 7j : STABLE")

    print()
    print("=" * 65)


# ---------------------------------------------------------------------------
# HTML report generation
# ---------------------------------------------------------------------------


def generate_html_report(dfs: dict[str, pd.DataFrame], output_path: Path):
    """Generate HTML report with Plotly charts."""
    df = dfs["global"]
    df_region = dfs["region"]

    timestamps = df.index.strftime(DATETIME_DISPLAY).tolist()
    latest = df.iloc[-1]
    dt_latest = df.index[-1].strftime(DATETIME_DISPLAY)
    dt_latest_file = df.index[-1].strftime(DATETIME_FMT)
    n_snapshots = len(df)

    d1 = compute_delta(df["rupture_temp"], 1)
    d7 = compute_delta(df["rupture_temp"], 7)
    d30 = compute_delta(df["rupture_temp"], 30)

    # ---- Chart 1: Global evolution ----
    fig1 = go.Figure()
    fig1.add_trace(go.Scatter(
        x=timestamps, y=df["rupture_temp"].tolist(),
        mode="lines+markers", name="Ruptures temporaires",
        line=dict(color="#d62728", width=3),
        marker=dict(size=6),
    ))
    if "rupture_temp_ma7" in df.columns:
        fig1.add_trace(go.Scatter(
            x=timestamps, y=df["rupture_temp_ma7"].tolist(),
            mode="lines", name="Moyenne 7j",
            line=dict(color="#ff7f0e", width=2, dash="dash"),
        ))
    if "rupture_temp_ma30" in df.columns:
        fig1.add_trace(go.Scatter(
            x=timestamps, y=df["rupture_temp_ma30"].tolist(),
            mode="lines", name="Moyenne 30j",
            line=dict(color="#1f77b4", width=2, dash="dot"),
        ))
    fig1.update_layout(
        title="Evolution des stations en rupture temporaire",
        xaxis_title="Date", yaxis_title="Nombre de stations",
        template="plotly_white", height=450,
    )

    # ---- Chart 2: Per fuel evolution ----
    fig2 = go.Figure()
    for fuel in FUEL_TYPES:
        fig2.add_trace(go.Scatter(
            x=timestamps, y=df[f"{fuel}_temp"].tolist(),
            mode="lines+markers", name=FUEL_LABELS[fuel],
            line=dict(color=FUEL_COLORS[fuel], width=2),
            marker=dict(size=4),
        ))
    fig2.update_layout(
        title="Ruptures temporaires par carburant",
        xaxis_title="Date", yaxis_title="Nombre de ruptures",
        template="plotly_white", height=450,
    )

    # ---- Chart 3: Per fuel % evolution ----
    fig3 = go.Figure()
    for fuel in FUEL_TYPES:
        fig3.add_trace(go.Scatter(
            x=timestamps, y=df[f"{fuel}_pct"].tolist(),
            mode="lines+markers", name=FUEL_LABELS[fuel],
            line=dict(color=FUEL_COLORS[fuel], width=2),
            marker=dict(size=4),
        ))
    fig3.update_layout(
        title="% de ruptures temporaires par carburant",
        xaxis_title="Date", yaxis_title="% des stations proposant ce carburant",
        template="plotly_white", height=450,
    )

    # ---- Chart 4: Top 15 regions bar chart (latest) ----
    latest_dt = df.index[-1]
    region_latest = df_region[df_region["datetime"] == latest_dt].copy()
    region_latest = region_latest.sort_values("rupture_temp", ascending=True).tail(15)

    fig4 = go.Figure()
    fig4.add_trace(go.Bar(
        y=region_latest["region"].tolist(),
        x=region_latest["rupture_temp"].tolist(),
        orientation="h",
        marker_color="#d62728",
        text=[f"{v:.0f} ({p:.1f}%)" for v, p in zip(region_latest["rupture_temp"], region_latest["rupture_temp_pct"])],
        textposition="outside",
    ))
    fig4.update_layout(
        title=f"Top 15 regions — Ruptures temporaires ({dt_latest})",
        xaxis_title="Nombre de stations", yaxis_title="",
        template="plotly_white", height=500,
        margin=dict(l=200),
    )

    # ---- Chart 5: Region heatmap evolution ----
    fig5_html = ""
    if n_snapshots > 1:
        top_regions = (
            df_region[df_region["datetime"] == latest_dt]
            .sort_values("rupture_temp", ascending=False)
            .head(12)["region"]
            .tolist()
        )
        heatmap_data = df_region[df_region["region"].isin(top_regions)].pivot_table(
            index="region", columns="datetime", values="rupture_temp_pct", aggfunc="first"
        )
        heatmap_data = heatmap_data.reindex(top_regions)

        fig5 = go.Figure(data=go.Heatmap(
            z=heatmap_data.values.tolist(),
            x=[d.strftime(DATETIME_DISPLAY) for d in heatmap_data.columns],
            y=heatmap_data.index.tolist(),
            colorscale="YlOrRd",
            colorbar_title="% rupture",
        ))
        fig5.update_layout(
            title="Heatmap — % ruptures temporaires par region et snapshot",
            template="plotly_white", height=450,
        )
        fig5_html = fig5.to_html(full_html=False, include_plotlyjs=False)

    # ---- Chart 6: Average prices ----
    fig6 = go.Figure()
    for fuel in FUEL_TYPES:
        prices = df[f"{fuel}_price"].tolist()
        if any(p is not None and not pd.isna(p) for p in prices):
            fig6.add_trace(go.Scatter(
                x=timestamps, y=prices,
                mode="lines+markers", name=FUEL_LABELS[fuel],
                line=dict(color=FUEL_COLORS[fuel], width=2),
                marker=dict(size=4),
            ))
    fig6.update_layout(
        title="Evolution des prix moyens par carburant (EUR/L)",
        xaxis_title="Date", yaxis_title="Prix moyen (EUR/L)",
        template="plotly_white", height=450,
    )

    # ---- Data table ----
    table_rows = ""
    for i, (dt, row) in enumerate(df.iterrows()):
        bg = "#f9f9f9" if i % 2 == 0 else "#ffffff"
        fuels_cells = ""
        for fuel in FUEL_TYPES:
            val = row[f"{fuel}_temp"]
            pct = row[f"{fuel}_pct"]
            fuels_cells += f"<td>{val:.0f} ({pct:.1f}%)</td>"
        table_rows += f"""<tr style="background:{bg}">
            <td>{dt.strftime(DATETIME_DISPLAY)}</td>
            <td>{row['total_stations']:.0f}</td>
            <td><strong>{row['rupture_temp']:.0f}</strong></td>
            <td>{row['rupture_temp_pct']:.1f}%</td>
            {fuels_cells}
        </tr>"""

    # ---- KPI cards ----
    def kpi_delta(d, suffix=""):
        if d is None:
            return '<span style="color:#888">N/A</span>'
        color = "#d62728" if d > 0 else "#2ca02c" if d < 0 else "#888"
        arrow = "\u2b06" if d > 0 else "\u2b07" if d < 0 else "="
        return f'<span style="color:{color}">{d:+.0f}{suffix} {arrow}</span>'

    # ---- Assemble HTML ----
    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Rapport Penurie Carburant — {dt_latest}</title>
    <script src="https://cdn.plot.ly/plotly-2.35.0.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f0f2f5; color: #1a1a2e; }}
        .container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
        h1 {{ text-align: center; margin: 30px 0 10px; font-size: 28px; color: #1a1a2e; }}
        .subtitle {{ text-align: center; color: #666; margin-bottom: 30px; font-size: 14px; }}
        .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 30px; }}
        .kpi-card {{ background: white; border-radius: 12px; padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
        .kpi-card .label {{ font-size: 13px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }}
        .kpi-card .value {{ font-size: 32px; font-weight: 700; margin: 8px 0; }}
        .kpi-card .delta {{ font-size: 14px; }}
        .chart-section {{ background: white; border-radius: 12px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
        .chart-section h2 {{ font-size: 18px; margin-bottom: 15px; color: #1a1a2e; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
        th {{ background: #1a1a2e; color: white; padding: 10px 8px; text-align: left; position: sticky; top: 0; }}
        td {{ padding: 8px; border-bottom: 1px solid #eee; }}
        .table-wrap {{ max-height: 500px; overflow-y: auto; }}
        .footer {{ text-align: center; color: #999; font-size: 12px; margin: 30px 0; }}
    </style>
</head>
<body>
<div class="container">
    <h1>Rapport Penurie Carburant</h1>
    <p class="subtitle">Periode : {df.index[0].strftime(DATETIME_DISPLAY)} &rarr; {dt_latest} &mdash; {n_snapshots} snapshot(s)</p>

    <!-- KPI Cards -->
    <div class="kpi-grid">
        <div class="kpi-card">
            <div class="label">Stations en rupture temp.</div>
            <div class="value" style="color:#d62728">{latest['rupture_temp']:.0f}</div>
            <div class="delta">sur {latest['total_stations']:.0f} stations ({latest['rupture_temp_pct']:.1f}%)</div>
        </div>
        <div class="kpi-card">
            <div class="label">Tendance J-1</div>
            <div class="value">{kpi_delta(d1)}</div>
            <div class="delta">stations</div>
        </div>
        <div class="kpi-card">
            <div class="label">Tendance 7 jours</div>
            <div class="value">{kpi_delta(d7)}</div>
            <div class="delta">stations</div>
        </div>
        <div class="kpi-card">
            <div class="label">Tendance 30 jours</div>
            <div class="value">{kpi_delta(d30)}</div>
            <div class="delta">stations</div>
        </div>
    </div>

    <!-- Chart 1: Global evolution -->
    <div class="chart-section">
        {fig1.to_html(full_html=False, include_plotlyjs=False)}
    </div>

    <!-- Chart 2: Per fuel -->
    <div class="chart-section">
        {fig2.to_html(full_html=False, include_plotlyjs=False)}
    </div>

    <!-- Chart 3: Per fuel % -->
    <div class="chart-section">
        {fig3.to_html(full_html=False, include_plotlyjs=False)}
    </div>

    <!-- Chart 4: Top regions -->
    <div class="chart-section">
        {fig4.to_html(full_html=False, include_plotlyjs=False)}
    </div>

    <!-- Chart 5: Region heatmap -->
    {"<div class='chart-section'>" + fig5_html + "</div>" if fig5_html else ""}

    <!-- Chart 6: Prices -->
    <div class="chart-section">
        {fig6.to_html(full_html=False, include_plotlyjs=False)}
    </div>

    <!-- Data table -->
    <div class="chart-section">
        <h2>Donnees detaillees</h2>
        <div class="table-wrap">
            <table>
                <thead>
                    <tr>
                        <th>Date</th>
                        <th>Stations</th>
                        <th>Ruptures temp.</th>
                        <th>%</th>
                        {"".join(f"<th>{FUEL_LABELS[f]}</th>" for f in FUEL_TYPES)}
                    </tr>
                </thead>
                <tbody>
                    {table_rows}
                </tbody>
            </table>
        </div>
    </div>

    <p class="footer">Genere le {datetime.now().strftime('%Y-%m-%d %H:%M')} &mdash; Source: data.economie.gouv.fr</p>
</div>
</body>
</html>"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"  HTML report saved to: {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    raw_dir = RAW_DIR
    if len(sys.argv) > 1:
        raw_dir = Path(sys.argv[1])

    if not raw_dir.exists():
        print(f"ERROR: Directory {raw_dir} does not exist.")
        print("Usage: python analyze_trends.py [raw_directory]")
        sys.exit(1)

    print(f"\n[1/4] Loading snapshots from {raw_dir}...")
    all_metrics = load_all_snapshots(raw_dir)

    print(f"\n[2/4] Building DataFrames & computing trends...")
    dfs = build_dataframes(all_metrics)

    print(f"\n[3/4] Console report:")
    print_console_report(dfs)

    dt_latest_file = dfs["global"].index[-1].strftime(DATETIME_FMT)
    output_html = REPORTS_DIR / f"rapport_{dt_latest_file}.html"

    print(f"\n[4/4] Generating HTML report...")
    generate_html_report(dfs, output_html)

    print("\nDone.")


if __name__ == "__main__":
    main()
