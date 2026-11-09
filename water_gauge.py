#!/usr/bin/env python3
"""
Download co-located water-gauge data for one GNSS-IR station and day.

Usage
-----
    python water_gauge.py STATION YEAR DOY

Example
-------
    python water_gauge.py islk 2024 200

Inputs
------
STATION is matched case-insensitively against STATIONS below. YEAR is a
four-digit year and DOY is the day of year (1-365 or 1-366). If STATION has no
configured co-located gauge URL, the program prints exactly:

    this station does not have co-loctaed water gauge

and exits without making a network request or creating an output file.

Configured station
------------------
islk
    Overview:
    https://water.usace.army.mil/overview/spk/locations/lake%20isabella

    Authoritative CWMS series:
    Lake Isabella-Pool.Elev.Inst.1Hour.0.Calc-val

    The series is the hourly instantaneous Lake Isabella pool elevation from
    the USACE Sacramento District (office SPK). Its native vertical datum is
    reported by the API (currently NGVD-29). The query window is local midnight
    to the next local midnight in America/Los_Angeles. The API uses inclusive
    endpoints, so returned values are filtered by local calendar date.

Default output
--------------
    intermediate_files/water_gauge/
        {station}_{year}_{DOY}_water_gauge.txt

Each data row contains:
    local_date_time, UTC_date_time, elevation_ft, elevation_m, quality_code

The conversion is:
    elevation_m = elevation_ft * 0.3048

The file header records the overview URL, API endpoint, time-series ID, office,
time zone, vertical datum metadata, query interval, formula, and column names.
Use --output to override the output path and --timeout to change the HTTP
timeout in seconds.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
API_ENDPOINT = "https://cwms-data.usace.army.mil/cwms-data/timeseries"
NO_GAUGE_MESSAGE = "this station does not have co-loctaed water gauge"
FT_TO_M = 0.3048


@dataclass(frozen=True)
class Gauge:
    overview_url: str
    office: str
    timeseries: str
    time_zone: str
    requested_unit: str = "ft"


STATIONS: dict[str, Gauge] = {
    "islk": Gauge(
        overview_url=(
            "https://water.usace.army.mil/overview/spk/locations/"
            "lake%20isabella"
        ),
        office="SPK",
        timeseries="Lake Isabella-Pool.Elev.Inst.1Hour.0.Calc-val",
        time_zone="America/Los_Angeles",
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").strip().splitlines()[0]
    )
    parser.add_argument("station", help="GNSS-IR station code, for example islk")
    parser.add_argument("year", type=int, help="Four-digit year")
    parser.add_argument("doy", type=int, help="Day of year")
    parser.add_argument(
        "--output",
        type=Path,
        help="Override the default intermediate_files/water_gauge output path",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="HTTP timeout in seconds (default: 60)",
    )
    return parser.parse_args()


def date_from_year_doy(year: int, doy: int) -> date:
    if year < 1900 or year > 2200:
        raise ValueError("year must be between 1900 and 2200")
    target = date(year, 1, 1) + timedelta(days=doy - 1)
    if doy < 1 or target.year != year:
        raise ValueError(f"invalid day of year {doy} for {year}")
    return target


def build_request_url(gauge: Gauge, target: date) -> tuple[str, datetime, datetime]:
    local_zone = ZoneInfo(gauge.time_zone)
    begin = datetime.combine(target, datetime.min.time(), tzinfo=local_zone)
    end = begin + timedelta(days=1)
    query = urlencode(
        {
            "name": gauge.timeseries,
            "office": gauge.office,
            "begin": begin.isoformat(),
            "end": end.isoformat(),
            "unit": gauge.requested_unit,
        }
    )
    return f"{API_ENDPOINT}?{query}", begin, end


def download_json(url: str, timeout: float) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "Accept": "application/json;version=2",
            "User-Agent": "gnssir-vmf3-bennett/water_gauge.py",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"USACE API returned HTTP {exc.code}: {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"cannot reach USACE API: {exc.reason}") from exc
    except (TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read USACE API response: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("USACE API response is not a JSON object")
    return payload


def extract_rows(
    payload: dict[str, Any], target: date, time_zone: str
) -> list[tuple[datetime, datetime, float, float, int]]:
    zone = ZoneInfo(time_zone)
    raw_values = payload.get("values")
    if not isinstance(raw_values, list):
        raise RuntimeError("USACE API response does not contain a values array")
    rows: list[tuple[datetime, datetime, float, float, int]] = []
    for index, item in enumerate(raw_values, 1):
        if not isinstance(item, list) or len(item) < 3:
            raise RuntimeError(f"malformed USACE value row {index}")
        if item[1] is None:
            continue
        try:
            epoch_ms = int(item[0])
            elevation_ft = float(item[1])
            quality_code = int(item[2])
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"non-numeric USACE value row {index}") from exc
        if not math.isfinite(elevation_ft):
            continue
        utc_time = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)
        local_time = utc_time.astimezone(zone)
        if local_time.date() == target:
            rows.append(
                (
                    local_time,
                    utc_time,
                    elevation_ft,
                    elevation_ft * FT_TO_M,
                    quality_code,
                )
            )
    rows.sort(key=lambda row: row[0])
    if not rows:
        raise RuntimeError(
            f"USACE returned no pool-elevation data for {target.isoformat()}"
        )
    return rows


def vertical_datum_text(payload: dict[str, Any]) -> tuple[str, str]:
    info = payload.get("vertical-datum-info")
    if not isinstance(info, dict):
        return "unknown", "not supplied"
    native = str(info.get("native-datum", "unknown"))
    offsets = info.get("offsets")
    if not isinstance(offsets, list) or not offsets:
        return native, "not supplied"
    descriptions = []
    for offset in offsets:
        if isinstance(offset, dict):
            descriptions.append(
                f"to {offset.get('to-datum', 'unknown')}: "
                f"{offset.get('value', 'unknown')} {info.get('unit', '')}"
            )
    return native, "; ".join(descriptions) if descriptions else "not supplied"


def write_output(
    path: Path,
    station: str,
    target: date,
    gauge: Gauge,
    request_url: str,
    begin: datetime,
    end: datetime,
    payload: dict[str, Any],
    rows: list[tuple[datetime, datetime, float, float, int]],
) -> None:
    native_datum, datum_offsets = vertical_datum_text(payload)
    api_units = str(payload.get("units", gauge.requested_unit))
    api_zone = str(payload.get("time-zone", gauge.time_zone))
    header = (
        "% Co-located water-gauge data for GNSS-IR comparison\n"
        f"% station: {station}\n"
        f"% target_date: {target.isoformat()}\n"
        f"% overview_url: {gauge.overview_url}\n"
        f"% api_endpoint: {API_ENDPOINT}\n"
        f"% request_url: {request_url}\n"
        f"% office: {gauge.office}\n"
        f"% timeseries: {gauge.timeseries}\n"
        f"% requested_local_interval: {begin.isoformat()} to {end.isoformat()}\n"
        f"% configured_time_zone: {gauge.time_zone}\n"
        f"% api_time_zone: {api_zone}\n"
        f"% api_units: {api_units}\n"
        f"% native_vertical_datum: {native_datum}\n"
        f"% reported_vertical_datum_offsets: {datum_offsets}\n"
        "% elevation_m = elevation_ft * 0.3048\n"
        "% Rows are filtered to target_date in the configured local time zone.\n"
        "% (1)local_date_time (2)UTC_date_time (3)elevation_ft "
        "(4)elevation_m (5)quality_code\n"
    )
    lines = [header]
    for local_time, utc_time, elevation_ft, elevation_m, quality_code in rows:
        lines.append(
            f"{local_time.isoformat()} {utc_time.isoformat()} "
            f"{elevation_ft:.6f} {elevation_m:.6f} {quality_code}\n"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    station = args.station.strip().lower()
    gauge = STATIONS.get(station)
    if gauge is None:
        print(NO_GAUGE_MESSAGE)
        return 0
    try:
        target = date_from_year_doy(args.year, args.doy)
        if args.timeout <= 0:
            raise ValueError("timeout must be positive")
        request_url, begin, end = build_request_url(gauge, target)
        payload = download_json(request_url, args.timeout)
        rows = extract_rows(payload, target, gauge.time_zone)
        output = args.output or (
            HERE
            / "intermediate_files"
            / "water_gauge"
            / f"{station}_{args.year}_{args.doy:03d}_water_gauge.txt"
        )
        write_output(
            output, station, target, gauge, request_url, begin, end, payload, rows
        )
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Station: {station}")
    print(f"Date: {target.isoformat()} (DOY {args.doy:03d})")
    print(f"Records written: {len(rows)}")
    print(f"Output: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
