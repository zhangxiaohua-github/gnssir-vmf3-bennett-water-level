#!/usr/bin/env python3
"""Convert USACE Isabella Project Datum water levels to NAVD88.

Usage:
    python water_gauge_NAVD88.py STATION YEAR DOY

Example:
    python water_gauge_NAVD88.py islk 2024 200

Default input:
    intermediate_files/water_gauge/{station}_{year}_{DOY}_water_gauge.txt

Default output:
    intermediate_files/water_gauge_NAVD88/
        {station}_{year}_{DOY}_water_gauge_NAVD88.txt

Method:
The Lake Isabella pool series is expressed in Isabella Project Datum (IPD), a
local vertical datum. The USACE Sacramento District datum documentation gives:

    H_NAVD88_ft = H_IPD_ft + 3.76 ft
    H_NAVD88_m = H_IPD_m + 1.146048 m

NAVD88 is an orthometric height, not an ellipsoidal height. Do not compare this output
directly with IGS20 GNSS-IR heights; first convert GNSS-IR to NAVD88.

Output columns:
1 local_date_time; 2 UTC_date_time; 3 elevation_IPD_ft; 4 elevation_IPD_m;
5 elevation_NAVD88_ft; 6 elevation_NAVD88_m; 7 quality_code.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
FT_TO_M = 0.3048
IPD_TO_NAVD88_FT = 3.76
IPD_TO_NAVD88_M = IPD_TO_NAVD88_FT * FT_TO_M
MANUAL_URL = (
    "https://water.usace.army.mil/cda/documents/wc/3128/"
    "Isabella1978WCManual%5BR%5D.pdf"
)


@dataclass(frozen=True)
class GaugeRow:
    local_time: str
    utc_time: str
    elevation_ipd_ft: float
    elevation_ipd_m: float
    quality_code: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").strip().splitlines()[0]
    )
    parser.add_argument("station", help="Station code, for example islk")
    parser.add_argument("year", type=int, help="Four-digit year")
    parser.add_argument("doy", type=int, help="Day of year")
    parser.add_argument("--input", type=Path, help="Override water-gauge input")
    parser.add_argument("--output", type=Path, help="Override NAVD88 output")
    return parser.parse_args()


def date_from_year_doy(year: int, doy: int) -> date:
    if year < 1900 or year > 2200:
        raise ValueError("year must be between 1900 and 2200")
    target = date(year, 1, 1) + timedelta(days=doy - 1)
    if doy < 1 or target.year != year:
        raise ValueError(f"invalid day of year {doy} for {year}")
    return target


def read_gauge_file(path: Path) -> tuple[list[GaugeRow], str]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise RuntimeError(f"cannot read water-gauge input {path}: {exc}") from exc

    datum_prefix = "% native_vertical_datum:"
    declared_datums = [
        line.split(":", 1)[1].strip()
        for line in lines
        if line.lower().startswith(datum_prefix)
    ]
    if len(declared_datums) != 1:
        raise RuntimeError(
            "input must contain exactly one native_vertical_datum declaration"
        )
    declared_datum = declared_datums[0]

    rows: list[GaugeRow] = []
    for line_number, line in enumerate(lines, 1):
        text = line.strip()
        if not text or text.startswith("%"):
            continue
        fields = text.split()
        if len(fields) != 5:
            raise RuntimeError(f"{path}:{line_number}: expected 5 columns")
        try:
            row = GaugeRow(
                fields[0], fields[1], float(fields[2]), float(fields[3]),
                int(fields[4])
            )
        except ValueError as exc:
            raise RuntimeError(
                f"{path}:{line_number}: invalid numeric value"
            ) from exc
        if not all(math.isfinite(v) for v in (
            row.elevation_ipd_ft, row.elevation_ipd_m
        )):
            raise RuntimeError(f"{path}:{line_number}: non-finite elevation")
        if abs(row.elevation_ipd_ft * FT_TO_M - row.elevation_ipd_m) > 5e-7:
            raise RuntimeError(
                f"{path}:{line_number}: ft-to-m columns are inconsistent"
            )
        rows.append(row)
    if not rows:
        raise RuntimeError(f"no water-gauge rows found in {path}")
    return rows, declared_datum


def write_output(
    path: Path,
    input_path: Path,
    station: str,
    target: date,
    input_declared_datum: str,
    rows: list[GaugeRow],
) -> None:
    header = (
        "% USACE Lake Isabella water-gauge elevations converted from IPD to NAVD88\n"
        f"% station: {station}\n"
        f"% target_date: {target.isoformat()}\n"
        f"% input_file: {input_path.resolve()}\n"
        f"% input_API_declared_vertical_datum: {input_declared_datum}\n"
        "% source_vertical_datum: Isabella Project Datum (IPD; local datum)\n"
        "% datum_interpretation: values are treated as IPD according to the manual;\n"
        "%   this overrides conflicting historical API metadata.\n"
        "% target_vertical_datum: NAVD88\n"
        f"% datum_manual: {MANUAL_URL}\n"
        "% manual_conversion: Isabella Project Datum to NAVD88 = +3.76 ft\n"
        f"% applied_IPD_to_NAVD88_correction_ft: +{IPD_TO_NAVD88_FT:.6f}\n"
        f"% applied_IPD_to_NAVD88_correction_m: +{IPD_TO_NAVD88_M:.6f}\n"
        "% Formula: H_NAVD88_ft = H_IPD_ft + 3.76 ft\n"
        "% Formula: H_NAVD88_m = H_IPD_m + 1.146048 m\n"
        "% NAVD88 is orthometric; no IGS20/ellipsoidal conversion is applied.\n"
        "% quality_code is copied unchanged from the USACE input file.\n"
        "% (1)local_date_time (2)UTC_date_time (3)elevation_IPD_ft\n"
        "% (4)elevation_IPD_m (5)elevation_NAVD88_ft\n"
        "% (6)elevation_NAVD88_m (7)quality_code\n"
    )
    output_lines = [header]
    for row in rows:
        navd88_ft = row.elevation_ipd_ft + IPD_TO_NAVD88_FT
        navd88_m = row.elevation_ipd_m + IPD_TO_NAVD88_M
        output_lines.append(
            f"{row.local_time} {row.utc_time} "
            f"{row.elevation_ipd_ft:.6f} {row.elevation_ipd_m:.6f} "
            f"{navd88_ft:.6f} {navd88_m:.6f} {row.quality_code}\n"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(output_lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    station = args.station.strip().lower()
    if station != "islk":
        print(
            f"Error: no local-datum conversion is configured for {station}",
            file=sys.stderr,
        )
        return 1
    try:
        target = date_from_year_doy(args.year, args.doy)
        input_path = args.input or (
            HERE / "intermediate_files" / "water_gauge"
            / f"{station}_{args.year}_{args.doy:03d}_water_gauge.txt"
        )
        output_path = args.output or (
            HERE / "intermediate_files" / "water_gauge_NAVD88"
            / f"{station}_{args.year}_{args.doy:03d}_water_gauge_NAVD88.txt"
        )
        rows, declared_datum = read_gauge_file(input_path)
        write_output(
            output_path, input_path, station, target, declared_datum, rows
        )
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Input: {input_path}")
    print(f"IPD to NAVD88 correction: +{IPD_TO_NAVD88_FT:.6f} ft")
    print(f"IPD to NAVD88 correction: +{IPD_TO_NAVD88_M:.6f} m")
    print(f"Records written: {len(rows)}")
    print(f"Output: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
