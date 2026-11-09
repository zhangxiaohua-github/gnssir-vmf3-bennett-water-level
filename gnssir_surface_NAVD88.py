#!/usr/bin/env python3
"""Convert GNSS-IR IGS20 ellipsoidal surface heights to NAVD88.

Usage
-----
    python gnssir_surface_NAVD88.py STATION YEAR DOY

Example
-------
    python gnssir_surface_NAVD88.py islk 2024 200

Default input
-------------
    intermediate_files/gnssir_surface_IGS20/
        {station}_{year}_{DOY}_gnssir_surface_IGS20.txt

Default output
--------------
    intermediate_files/gnssir_surface_NAVD88/
        {station}_{year}_{DOY}_gnssir_surface_NAVD88.txt

Method
------
GEOID18 cannot be applied directly to IGS20 ellipsoidal heights. NGS specifies
that GEOID18 is used with NAD83(2011), epoch 2010.0 ellipsoidal coordinates.
This script therefore performs two explicit NOAA VDatum transformations:

1. IGS20 at the observation epoch -> NAD83(2011), epoch 2010.0.
   The API is evaluated with h_IGS20=0 to obtain the local ellipsoid-height
   shift dh. For every IGS20 surface height:

       h_NAD83(2011) = h_IGS20 + dh

2. NAD83(2011) ellipsoidal height -> NAVD88 using GEOID18.
   GEOID18 follows:

       H_NAVD88 = h_NAD83(2011) - N_GEOID18

   The API is evaluated with h_NAD83(2011)=0. Its returned NAVD88 height H0
   equals -N_GEOID18, so:

       H_NAVD88 = h_NAD83(2011) + H0

The station coordinates come from the station JSON. The output keeps the
original 22 IGS20 columns and appends the NAD83(2011) intermediate and NAVD88
surface heights for the no-VMF3 and VMF3 cases.
The header records both complete API requests, epochs, shifts, geoid separation,
and uncertainties.

References
----------
GEOID18:
    https://www.ngs.noaa.gov/GEOID/GEOID18/computation.html
VDatum API:
    https://vdatum.noaa.gov/docs/services.html
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


HERE = Path(__file__).resolve().parent
VDATUM_ENDPOINT = "https://vdatum.noaa.gov/vdatumweb/api/convert"
GEOID18_EPOCH = 2010.0


@dataclass(frozen=True)
class Transformation:
    observation_epoch: float
    nad83_longitude: float
    nad83_latitude: float
    igs20_to_nad83_height_shift_m: float
    geoid18_height_m: float
    navd88_at_zero_nad83_m: float
    step1_uncertainty_m: float
    step2_uncertainty_m: float
    combined_uncertainty_m: float
    step1_url: str
    step2_url: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").strip().splitlines()[0]
    )
    parser.add_argument("station", help="Four-character station code")
    parser.add_argument("year", type=int, help="Four-digit year")
    parser.add_argument("doy", type=int, help="Day of year")
    parser.add_argument("--input", type=Path, help="Override IGS20 input file")
    parser.add_argument("--output", type=Path, help="Override NAVD88 output file")
    parser.add_argument(
        "--station-json", type=Path, help="Override station JSON"
    )
    parser.add_argument(
        "--extension", default="noatm", help="Station JSON extension"
    )
    parser.add_argument(
        "--timeout", type=float, default=60.0, help="API timeout in seconds"
    )
    return parser.parse_args()


def validate_date(year: int, doy: int) -> date:
    if year < 1900 or year > 2200:
        raise ValueError("year must be between 1900 and 2200")
    try:
        value = date.fromordinal(date(year, 1, 1).toordinal() + doy - 1)
    except ValueError as exc:
        raise ValueError(f"invalid day of year {doy}") from exc
    if doy < 1 or value.year != year:
        raise ValueError(f"invalid day of year {doy} for {year}")
    return value


def decimal_year(value: datetime) -> float:
    start = datetime(value.year, 1, 1)
    end = datetime(value.year + 1, 1, 1)
    return value.year + (value - start).total_seconds() / (
        end - start
    ).total_seconds()


def api_request(parameters: dict[str, str], timeout: float) -> tuple[dict[str, Any], str]:
    url = f"{VDATUM_ENDPOINT}?{urlencode(parameters)}"
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "gnssir-vmf3-bennett/gnssir_surface_NAVD88.py",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except HTTPError as exc:
        raise RuntimeError(
            f"NOAA VDatum returned HTTP {exc.code}: {exc.reason}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"cannot reach NOAA VDatum: {exc.reason}") from exc
    except (TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read NOAA VDatum response: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("NOAA VDatum response is not a JSON object")
    if "errorCode" in payload:
        raise RuntimeError(
            f"NOAA VDatum error {payload.get('errorCode')}: "
            f"{payload.get('message', 'unknown error')}"
        )
    return payload, url


def numeric(payload: dict[str, Any], key: str) -> float:
    try:
        value = float(payload[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"VDatum response lacks numeric {key}") from exc
    if not math.isfinite(value):
        raise RuntimeError(f"VDatum response has non-finite {key}")
    return value


def build_transformation(
    latitude: float,
    longitude: float,
    observation_epoch: float,
    timeout: float,
) -> Transformation:
    common = {
        "region": "contiguous",
        "s_coor": "geo",
        "s_h_unit": "deg",
        "s_v_unit": "m",
        "s_v_elevation": "height",
        "s_v_geoid": "geoid18",
        "t_coor": "geo",
        "t_h_unit": "deg",
        "t_v_unit": "m",
        "t_v_elevation": "height",
        "t_v_geoid": "geoid18",
    }
    step1_parameters = {
        **common,
        "s_x": f"{longitude:.12f}",
        "s_y": f"{latitude:.12f}",
        "s_z": "0",
        "s_h_frame": "IGS20",
        "s_v_frame": "IGS20",
        "t_h_frame": "NAD83_2011",
        "t_v_frame": "NAD83_2011",
        "epoch_in": f"{observation_epoch:.9f}",
        "epoch_out": f"{GEOID18_EPOCH:.1f}",
    }
    step1, step1_url = api_request(step1_parameters, timeout)
    nad83_lon = numeric(step1, "t_x")
    nad83_lat = numeric(step1, "t_y")
    height_shift = numeric(step1, "t_z")
    uncertainty1 = numeric(step1, "uncertainty")

    step2_parameters = {
        **common,
        "s_x": f"{nad83_lon:.12f}",
        "s_y": f"{nad83_lat:.12f}",
        "s_z": "0",
        "s_h_frame": "NAD83_2011",
        "s_v_frame": "NAD83_2011",
        "t_h_frame": "NAD83_2011",
        "t_v_frame": "NAVD88",
        "epoch_in": f"{GEOID18_EPOCH:.1f}",
        "epoch_out": f"{GEOID18_EPOCH:.1f}",
    }
    step2, step2_url = api_request(step2_parameters, timeout)
    navd88_at_zero = numeric(step2, "t_z")
    uncertainty2 = numeric(step2, "uncertainty")
    return Transformation(
        observation_epoch=observation_epoch,
        nad83_longitude=nad83_lon,
        nad83_latitude=nad83_lat,
        igs20_to_nad83_height_shift_m=height_shift,
        geoid18_height_m=-navd88_at_zero,
        navd88_at_zero_nad83_m=navd88_at_zero,
        step1_uncertainty_m=uncertainty1,
        step2_uncertainty_m=uncertainty2,
        combined_uncertainty_m=math.hypot(uncertainty1, uncertainty2),
        step1_url=step1_url,
        step2_url=step2_url,
    )


def read_input(path: Path) -> tuple[list[str], list[list[str]], datetime]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise RuntimeError(f"cannot read IGS20 input {path}: {exc}") from exc
    header = [line for line in lines if line.startswith("%")]
    rows: list[list[str]] = []
    times: list[datetime] = []
    for line_number, line in enumerate(lines, 1):
        text = line.strip()
        if not text or text.startswith("%"):
            continue
        fields = text.split()
        if len(fields) != 17:
            raise RuntimeError(f"{path}:{line_number}: expected 22 columns")
        try:
            times.append(datetime.fromisoformat(fields[0]))
            for index in (14, 16):
                value = float(fields[index])
                if not math.isfinite(value):
                    raise ValueError("non-finite surface height")
        except ValueError as exc:
            raise RuntimeError(
                f"{path}:{line_number}: invalid IGS20 row"
            ) from exc
        rows.append(fields)
    if not rows:
        raise RuntimeError(f"no IGS20 surface rows found in {path}")
    mean_seconds = sum(t.timestamp() for t in times) / len(times)
    mean_time = datetime.fromtimestamp(mean_seconds)
    return header, rows, mean_time


def load_coordinates(path: Path) -> tuple[float, float]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        latitude = float(payload["lat"])
        longitude = float(payload["lon"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"cannot read station coordinates from {path}: {exc}") from exc
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise RuntimeError("station coordinates are outside valid bounds")
    return latitude, longitude


def write_output(
    path: Path,
    input_path: Path,
    station_json: Path,
    station: str,
    target: date,
    latitude: float,
    longitude: float,
    transform: Transformation,
    rows: list[list[str]],
) -> None:
    header = f"""% GNSS-IR surface heights converted from IGS20 to NAVD88
% station: {station}
% target_date: {target.isoformat()}
% IGS20_input: {input_path.resolve()}
% station_json: {station_json.resolve()}
% source_IGS20_latitude_deg: {latitude:.12f}
% source_IGS20_longitude_deg: {longitude:.12f}
% observation_epoch_decimal_year: {transform.observation_epoch:.9f}
% GEOID18_required_frame: NAD83(2011), epoch 2010.0
% step1: IGS20 observation epoch -> NAD83(2011) epoch 2010.0
% step1_request_url: {transform.step1_url}
% NAD83_2011_latitude_deg: {transform.nad83_latitude:.12f}
% NAD83_2011_longitude_deg: {transform.nad83_longitude:.12f}
% IGS20_to_NAD83_2011_height_shift_m: {transform.igs20_to_nad83_height_shift_m:+.6f}
% step1_uncertainty_m: +/-{transform.step1_uncertainty_m:.6f}
% step2: NAD83(2011) ellipsoidal -> NAVD88 using GEOID18
% step2_request_url: {transform.step2_url}
% GEOID18_geoid_height_N_m: {transform.geoid18_height_m:+.6f}
% NAVD88_height_at_h_NAD83_zero_m: {transform.navd88_at_zero_nad83_m:+.6f}
% step2_uncertainty_m: +/-{transform.step2_uncertainty_m:.6f}
% combined_model_uncertainty_RSS_m: +/-{transform.combined_uncertainty_m:.6f}
% Formula: h_NAD83_2011 = h_IGS20 + IGS20_to_NAD83_2011_height_shift
% Formula: H_NAVD88 = h_NAD83_2011 - N_GEOID18
% Equivalent: H_NAVD88 = h_NAD83_2011 + NAVD88_height_at_h_NAD83_zero
% Original columns (1)-(17) are copied unchanged from the IGS20 input.
% (18)h_surface_no_VMF3_NAD83_2011_m
% (19)h_surface_VMF3_NAD83_2011_m
% (20)H_surface_no_VMF3_NAVD88_m
% (21)H_surface_VMF3_NAVD88_m
"""
    output_lines = [header]
    for fields in rows:
        igs20 = [float(fields[index]) for index in (14, 16)]
        nad83 = [
            value + transform.igs20_to_nad83_height_shift_m
            for value in igs20
        ]
        navd88 = [
            value + transform.navd88_at_zero_nad83_m for value in nad83
        ]
        output_lines.append(
            " ".join(fields)
            + " "
            + " ".join(f"{value:.6f}" for value in (*nad83, *navd88))
            + "\n"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(output_lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    station = args.station.strip().lower()
    try:
        target = validate_date(args.year, args.doy)
        if args.timeout <= 0:
            raise ValueError("timeout must be positive")
        stem = f"{station}_{args.year}_{args.doy:03d}"
        input_path = args.input or (
            HERE / "intermediate_files" / "gnssir_surface_IGS20"
            / f"{stem}_gnssir_surface_IGS20.txt"
        )
        output_path = args.output or (
            HERE / "intermediate_files" / "gnssir_surface_NAVD88"
            / f"{stem}_gnssir_surface_NAVD88.txt"
        )
        refl_code = os.environ.get("REFL_CODE")
        if not refl_code and args.station_json is None:
            raise RuntimeError("set REFL_CODE or pass --station-json")
        reflection_root = Path(refl_code or ".")
        station_json = args.station_json or (
            reflection_root / "input" / station / args.extension
            / f"{station}.json"
        )
        _, rows, mean_time = read_input(input_path)
        latitude, longitude = load_coordinates(station_json)
        transform = build_transformation(
            latitude, longitude, decimal_year(mean_time), args.timeout
        )
        write_output(
            output_path,
            input_path,
            station_json,
            station,
            target,
            latitude,
            longitude,
            transform,
            rows,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Input: {input_path}")
    print(
        "IGS20 -> NAD83(2011) height shift: "
        f"{transform.igs20_to_nad83_height_shift_m:+.6f} m"
    )
    print(f"GEOID18 N: {transform.geoid18_height_m:+.6f} m")
    print(f"Rows written: {len(rows)}")
    print(f"Output: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
