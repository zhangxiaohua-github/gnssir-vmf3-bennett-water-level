#!/usr/bin/env python3
"""Compare VMF3- and Bennett-corrected GNSS-IR heights in NAVD88 with a gauge."""
from __future__ import annotations
import argparse
from pathlib import Path
import os
import warnings
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", message="Input line .* contained no data")
HERE = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("station", nargs="?", default="islk")
    p.add_argument("year", nargs="?", type=int, default=2024)
    p.add_argument("start_doy", nargs="?", type=int, default=183)
    p.add_argument("end_doy", nargs="?", type=int, default=205)
    p.add_argument("--refl-code", type=Path,
        default=Path(os.environ["REFL_CODE"]) if os.environ.get("REFL_CODE") else None)
    p.add_argument("--data-dir", type=Path, default=HERE / "intermediate_files")
    p.add_argument("--results-dir", type=Path, default=HERE / "results")
    return p.parse_args()


def load_navd(root: Path, station: str, year: int, doy: int) -> np.ndarray:
    path = root / "gnssir_surface_NAVD88" / f"{station}_{year}_{doy:03d}_gnssir_surface_NAVD88.txt"
    data = np.loadtxt(path, comments="%", dtype=str, ndmin=2)
    if data.shape[1] != 21:
        raise ValueError(f"expected 21 columns in {path}; regenerate the NAVD88 result")
    return data


def apc_navd88(root: Path, station: str, year: int, doy: int) -> float:
    data = load_navd(root, station, year, doy)
    values = data[:, 19].astype(float) + data[:, 11].astype(float)
    if np.ptp(values) > 2e-4:
        raise ValueError(f"inconsistent APC NAVD88 height for DOY {doy}")
    return float(np.median(values))


def load_vmf3(root: Path, station: str, year: int, doys: range) -> pd.DataFrame:
    frames = []
    for doy in doys:
        data = load_navd(root, station, year, doy)
        frames.append(pd.DataFrame({"time_utc": pd.to_datetime(data[:, 0], utc=True),
            "surface_navd88_m": data[:, 20].astype(float), "doy": doy,
            "satellite": data[:, 2].astype(int), "frequency": "GPS L1", "method": "VMF3"}))
    return pd.concat(frames, ignore_index=True).sort_values("time_utc")


def load_bennett(root: Path, refl_code: Path, station: str, year: int, doys: range) -> pd.DataFrame:
    rows = []; origin = pd.Timestamp(f"{year}-01-01", tz="UTC")
    result_dir = refl_code / str(year) / "results" / station
    for doy in doys:
        apc = apc_navd88(root, station, year, doy)
        raw = np.loadtxt(result_dir / f"{doy:03d}.txt", comments="%", ndmin=2)
        for row in raw:
            if int(row[10]) != 1: continue
            rows.append({"time_utc": origin + pd.Timedelta(days=doy-1, hours=float(row[4])),
                "surface_navd88_m": apc-float(row[2]), "doy": doy,
                "satellite": int(row[3]), "frequency": "GPS L1", "method": "Bennett"})
    if not rows: raise ValueError("no GPS L1 Bennett results found")
    return pd.DataFrame(rows).sort_values("time_utc")


def load_gauge(root: Path, station: str, year: int, doys: range) -> pd.DataFrame:
    frames = []
    for doy in doys:
        path = root/"water_gauge_NAVD88"/f"{station}_{year}_{doy:03d}_water_gauge_NAVD88.txt"
        data = pd.read_csv(path, comment="%", sep=r"\s+", header=None)
        frames.append(pd.DataFrame({"time_utc": pd.to_datetime(data.iloc[:,1], utc=True),
                                    "gauge_navd88_m": data.iloc[:,5].astype(float)}))
    return pd.concat(frames, ignore_index=True).drop_duplicates("time_utc").sort_values("time_utc")


def evaluate(data: pd.DataFrame, gauge: pd.DataFrame):
    result = data.copy(); gx = gauge.time_utc.astype("int64").to_numpy()
    result["gauge_interpolated_navd88_m"] = np.interp(result.time_utc.astype("int64"), gx,
                                                       gauge.gauge_navd88_m.to_numpy())
    result["gnss_minus_gauge_m"] = result.surface_navd88_m-result.gauge_interpolated_navd88_m
    result["absolute_error_m"] = result.gnss_minus_gauge_m.abs()
    error = result.gnss_minus_gauge_m.to_numpy()
    return result, {"method": result.method.iloc[0], "frequency": "GPS L1", "n_arcs": len(result),
        "mean_bias_m": np.mean(error), "mae_m": np.mean(np.abs(error)),
        "rmse_m": np.sqrt(np.mean(error**2)),
        "correlation": np.corrcoef(result.surface_navd88_m, result.gauge_interpolated_navd88_m)[0,1]}


def main() -> None:
    a = parse_args(); station = a.station.lower()
    if not 1 <= a.start_doy <= a.end_doy <= 366: raise SystemExit("invalid DOY range")
    if a.refl_code is None: raise SystemExit("REFL_CODE or --refl-code is required")
    doys = range(a.start_doy, a.end_doy+1); root = a.data_dir
    gauge = load_gauge(root, station, a.year, doys)
    vmf3, vmf3_stats = evaluate(load_vmf3(root, station, a.year, doys), gauge)
    bennett, bennett_stats = evaluate(load_bennett(root, a.refl_code, station, a.year, doys), gauge)
    stats = pd.DataFrame([vmf3_stats, bennett_stats])
    fig, ax = plt.subplots(figsize=(14, 7.2), constrained_layout=True)
    ax.plot(gauge.time_utc, gauge.gauge_navd88_m, color="#222222", lw=2.4, label="Water gauge", zorder=2)
    ax.plot(vmf3.time_utc, vmf3.surface_navd88_m, color="#2b7dbc", lw=1.15,
            marker="o", markersize=5.5, alpha=.82, label="GNSS-IR (VMF3, L1)", zorder=3)
    ax.plot(bennett.time_utc, bennett.surface_navd88_m, color="#d95f6f", lw=1.15,
            marker="D", markersize=5.2, alpha=.82, label="GNSS-IR (Bennett, L1)", zorder=4)
    ax.set_title(f"{station.upper()} Surface Height in NAVD88 (DOY {a.start_doy:03d}-{a.end_doy:03d}, {a.year})", fontsize=19, pad=14)
    ax.set_xlabel("UTC date", fontsize=14); ax.set_ylabel("Surface orthometric height (m, NAVD88)", fontsize=14)
    ax.tick_params(axis="both", labelsize=12.5); ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d")); ax.grid(True, color="#c8c8c8", lw=.75, alpha=.55)
    ax.legend(loc="upper right", fontsize=12, frameon=True); ax.margins(x=.01, y=.05)
    span = f"{station}_{a.year}_doy{a.start_doy:03d}_{a.end_doy:03d}"
    figure = a.results_dir/f"{span}_vmf3_bennett_surface_NAVD88_gauge"
    figure.parent.mkdir(parents=True, exist_ok=True); fig.savefig(figure.with_suffix(".png"), dpi=300)
    fig.savefig(figure.with_suffix(".pdf")); plt.close(fig)
    output = a.results_dir; output.mkdir(parents=True, exist_ok=True)
    vmf3.to_csv(output/f"{span}_vmf3_surface_NAVD88.csv", index=False)
    bennett.to_csv(output/f"{span}_bennett_surface_NAVD88.csv", index=False)
    stats.to_csv(output/f"{span}_vmf3_bennett_metrics.csv", index=False)
    print(stats.to_string(index=False)); print(f"Figure: {figure.with_suffix('.png')}")

if __name__ == "__main__": main()
