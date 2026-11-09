#!/usr/bin/env python3
"""Create the annotated 15-column input table used by VMF3 correction.

The script reads GNSS-IR.py results and the matching station-day APC file from GNSS-IR-geodetic-height.py.

Example:
    python input_table_for_VMF3.py islk 2024 001

Default input:
    ./intermediate_files/gnssir/001_noatm_peng.txt

Default output:
    ./intermediate_files/VMF3_input_files/islk_2024_001_VMF3_input.txt

Time-scale convention
---------------------
The installed gnssrefl version labels its result time as UTC, although its
underlying seconds-of-day are effectively GPS time. This script intentionally
does not apply a GPS-to-UTC leap-second correction. For the 2024 data used in
this study, GPS time is 18 seconds ahead of UTC. That offset is considered
negligible for interpolation between six-hour VMF3 epochs and does not change
the required grid files for the current arcs. This is a documented research
approximation, not a general time conversion. Reassess it for other years or
for arcs close to 00:00, 06:00, 12:00, or 18:00 UTC, because the GPS-UTC offset
varies historically and a boundary-adjacent arc could select different files.
"""

from __future__ import annotations

import argparse
import math
import re
from datetime import datetime, timedelta
from pathlib import Path


HERE = Path(__file__).resolve().parent
MJD_EPOCH = datetime(1858, 11, 17)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("station", help="Station name, for example: islk")
    parser.add_argument("year", type=int, help="Four-digit year")
    parser.add_argument("doy", type=int, help="Day of year (1-366)")
    parser.add_argument(
        "--extension",
        default="noatm",
        help="gnssrefl results extension (default: noatm)",
    )
    parser.add_argument(
        "--results-file",
        type=Path,
        help="Override the Peng-version GNSS-IR results file",
    )
    parser.add_argument(
        "--apc-file",
        type=Path,
        help="Override the station-day APC height file",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output path (default: intermediate_files/VMF3_input_files)",
    )
    return parser.parse_args()


def validate_inputs(args: argparse.Namespace) -> tuple[str, str]:
    """Validate and normalize station, extension, year, and day of year."""
    station = args.station.strip().lower()
    extension = args.extension.strip()
    safe_name = re.compile(r"[A-Za-z0-9_-]+")
    if not safe_name.fullmatch(station):
        raise SystemExit("Station contains unsupported characters.")
    if not safe_name.fullmatch(extension):
        raise SystemExit("Extension contains unsupported characters.")
    if args.year < 1900 or args.year > 2200:
        raise SystemExit("Year must be between 1900 and 2200.")
    start = datetime(args.year, 1, 1)
    day = start + timedelta(days=args.doy - 1)
    if args.doy < 1 or day.year != args.year:
        raise SystemExit(f"Invalid day of year {args.doy} for {args.year}.")
    return station, extension


def read_apc_records(path: Path) -> list[dict[str, str]]:
    """Read one or more key-value APC records from a text file."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise SystemExit(f"Cannot read APC height file {path}: {exc}") from exc

    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line_number, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise SystemExit(f"Malformed APC line {line_number}: {line}")
        key, value = (part.strip() for part in line.split(":", 1))
        if key == "date" and current:
            records.append(current)
            current = {}
        if key in current:
            raise SystemExit(f"Duplicate APC field {key!r} in {path}")
        current[key] = value
    if current:
        records.append(current)
    if not records:
        raise SystemExit(f"No APC records found in {path}")
    return records


def select_apc_record(
    records: list[dict[str, str]], station: str, year: int, doy: int
) -> dict[str, str]:
    """Select exactly one APC record for the requested station and day."""
    target_date = f"{year} {doy:03d}"
    matches = [
        record
        for record in records
        if record.get("date") == target_date
        and record.get("station", "").upper() == station.upper()
    ]
    if len(matches) != 1:
        raise SystemExit(
            f"Expected one APC record for {station.upper()} {target_date}; "
            f"found {len(matches)} in the APC file."
        )
    if "h_APC_m" not in matches[0]:
        raise SystemExit("The matching APC record does not contain h_APC_m.")
    try:
        height = float(matches[0]["h_APC_m"])
    except ValueError as exc:
        raise SystemExit("h_APC_m is not numeric.") from exc
    if not math.isfinite(height):
        raise SystemExit("h_APC_m must be finite.")
    return matches[0]


def read_results(path: Path, year: int, doy: int) -> list[list[float]]:
    """Read and validate accepted arcs from a gnssrefl daily results file."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise SystemExit(f"Cannot read results file {path}: {exc}") from exc

    rows: list[list[float]] = []
    for line_number, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("%") or line.startswith("#"):
            continue
        try:
            fields = [float(value) for value in line.split()]
        except ValueError as exc:
            raise SystemExit(
                f"Non-numeric value in results line {line_number}."
            ) from exc
        if len(fields) < 17:
            raise SystemExit(
                f"Results line {line_number} has {len(fields)} columns; expected 17."
            )
        if not all(math.isfinite(value) for value in fields[:17]):
            raise SystemExit(f"Non-finite value in results line {line_number}.")
        if int(fields[0]) != year or int(fields[1]) != doy:
            raise SystemExit(
                f"Results line {line_number} does not match {year} DOY {doy:03d}."
            )
        rows.append(fields)
    if not rows:
        raise SystemExit(f"No accepted arcs found in {path}")
    return rows


def result_frequency_to_antex(frequency: int) -> str:
    """Map the GPS frequency codes used in results to ANTEX codes."""
    mapping = {1: "G01", 2: "G02", 20: "G02", 5: "G05"}
    if frequency not in mapping:
        raise SystemExit(
            f"Unsupported result frequency {frequency}; cannot verify APC frequency."
        )
    return mapping[frequency]


def datetime_from_mjd(mjd: float, year: int, doy: int) -> datetime:
    """Convert MJD to calendar fields without applying GPS-to-UTC correction."""
    value = MJD_EPOCH + timedelta(days=mjd)
    expected = datetime(year, 1, 1) + timedelta(days=doy - 1)
    if value.date() != expected.date():
        raise SystemExit(
            f"MJD {mjd:.6f} resolves to {value.date()}, not {expected.date()}."
        )
    return value


def build_rows(
    results: list[list[float]],
    apc_record: dict[str, str],
    year: int,
    doy: int,
) -> list[tuple[float | int, ...]]:
    """Convert each results arc to the required 15-column VMF3 row."""
    h_apc = float(apc_record["h_APC_m"])
    apc_frequency = apc_record.get("frequency", "").upper()
    output_rows: list[tuple[float | int, ...]] = []

    for index, result in enumerate(results, 1):
        rh = result[2]
        min_elevation = result[7]
        max_elevation = result[8]
        frequency = int(result[10])
        rise_set = int(result[11])
        mjd = result[15]

        if rise_set not in {-1, 1}:
            raise SystemExit(f"Arc {index} has invalid rise/set flag {rise_set}.")
        if min_elevation == max_elevation:
            raise SystemExit(f"Arc {index} has identical endpoint elevations.")
        if rise_set == 1:
            e0, e1 = min_elevation, max_elevation
        else:
            e0, e1 = max_elevation, min_elevation
        expected_frequency = result_frequency_to_antex(frequency)
        if apc_frequency and apc_frequency != expected_frequency:
            raise SystemExit(
                f"Arc {index} uses frequency {expected_frequency}, but the APC "
                f"record uses {apc_frequency}."
            )

        arc_time = datetime_from_mjd(mjd, year, doy)
        second = arc_time.second + arc_time.microsecond / 1_000_000
        surface_height = h_apc - rh
        output_rows.append(
            (
                arc_time.year,
                arc_time.month,
                arc_time.day,
                arc_time.hour,
                arc_time.minute,
                second,
                e0,
                e1,
                0,
                0,
                0,
                0,
                surface_height,
                h_apc,
                0,
            )
        )
    return output_rows


def write_table(
    path: Path,
    rows: list[tuple[float | int, ...]],
    results_path: Path,
    apc_path: Path,
) -> None:
    """Write an annotated, whitespace-separated 15-column numeric table."""
    header = (
        "% VMF3 input table generated from accepted GNSS-IR arcs\n"
        f"% GNSS-IR results source: {results_path}\n"
        f"% APC height source: {apc_path}\n"
        "% Time scale: gnssrefl GPS-like time used without GPS-to-UTC correction\n"
        "% For this 2024 study, the accepted GPS-UTC offset is 18 seconds\n"
        "% Columns 1-6: year, month, day, hour, minute, second\n"
        "% Column 7: first endpoint elevation angle e0 (degrees)\n"
        "% Column 8: second endpoint elevation angle e1 (degrees)\n"
        "% Columns 9-12: reserved; set to 0\n"
        "% Column 13: uncorrected surface ellipsoidal height (m) = h_APC_m - RH\n"
        "% Column 14: antenna ellipsoidal height h_APC_m (m)\n"
        "% Column 15: reserved; set to 0\n"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as stream:
            stream.write(header)
            for row in rows:
                stream.write(
                    f"{row[0]:4d} {row[1]:02d} {row[2]:02d} "
                    f"{row[3]:02d} {row[4]:02d} {row[5]:06.3f} "
                    f"{row[6]:.4f} {row[7]:.4f} "
                    f"0 0 0 0 {row[12]:.6f} {row[13]:.6f} 0\n"
                )
    except OSError as exc:
        raise SystemExit(f"Cannot write output file {path}: {exc}") from exc


def main() -> None:
    """Create the VMF3 input table for one station and day."""
    args = parse_args()
    station, extension = validate_inputs(args)

    doy_text = f"{args.doy:03d}"
    if args.results_file is not None:
        results_path = args.results_file.expanduser()
    else:
        results_path = (
            HERE
            / "intermediate_files"
            / "gnssir"
            / f"{doy_text}_{extension}_peng.txt"
        )
    apc_path = (args.apc_file.expanduser() if args.apc_file else
        HERE / "intermediate_files" / "PCO_APC" /
        f"{station}_{args.year}_{doy_text}_APC_geodetic_height.txt")
    output_path = args.output or (
        HERE / "intermediate_files" / "VMF3_input_files"
        / f"{station}_{args.year}_{doy_text}_VMF3_input.txt"
    )

    records = read_apc_records(apc_path)
    apc_record = select_apc_record(records, station, args.year, args.doy)
    results = read_results(results_path, args.year, args.doy)
    rows = build_rows(results, apc_record, args.year, args.doy)
    write_table(output_path, rows, results_path, apc_path)

    print(f"GNSS-IR results: {results_path}")
    print(f"APC height: {float(apc_record['h_APC_m']):.6f} m")
    print(f"Arcs written: {len(rows)}")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    main()
