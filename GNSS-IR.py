#!/usr/bin/env python3
"""Estimate initial reflector heights from saved GNSS-IR arcs.

Run ``gnssir`` with arc saving before using this script, for example::

    gnssir islk 2024 001 -extension noatm -savearcs T -fr 1
    python GNSS-IR.py islk 2024 001

For every arc that passed gnssrefl quality control, this script uses elevation
angle and detrended SNR to compute a conventional Lomb-Scargle periodogram on
``sin(e) / (wavelength / 2)``. The periodogram peak is the initial reflector
height used by the later tropospheric correction.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

import numpy as np

try:
    from gnssrefl import gps as gnssrefl_gps  # type: ignore[import-untyped]
except ImportError as exc:  # pragma: no cover - depends on the user environment
    raise SystemExit(
        "gnssrefl is required. Install it in the active Python environment."
    ) from exc


HERE = Path(__file__).resolve().parent


ARC_PATTERN = re.compile(
    r"^sat(?P<sat>\d+)_L(?P<band>\d+)_(?P<constellation>[GREC])_"
    r"az(?P<azimuth>\d+)\.txt$"
)


@dataclass(frozen=True)
class ArcMetadata:
    """Metadata encoded in a gnssrefl arc filename."""

    satellite: int
    band: int
    constellation: str
    azimuth_deg: int


class ArcResult(TypedDict):
    """Calculated values and identifiers for one accepted GNSS-IR arc."""

    arc_file: str
    satellite: int
    constellation: str
    frequency_code: int
    band: str
    azimuth_deg: int
    mean_utc_hour: float
    rise_set: int
    min_elevation_deg: float
    max_elevation_deg: float
    observations: int
    wavelength_m: float
    initial_rh_m: float
    lsp_peak_amplitude: float


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Estimate initial reflector height from saved GNSS-IR arcs."
    )
    parser.add_argument("station", help="Four-character station name, e.g. islk")
    parser.add_argument("year", type=int, help="Four-digit year")
    parser.add_argument("doy", type=int, help="Day of year (1-366)")
    parser.add_argument(
        "--extension",
        default="noatm",
        help="gnssrefl extension used to create the arcs (default: noatm)",
    )
    parser.add_argument("--min-rh", type=float, help="Minimum RH in metres")
    parser.add_argument("--max-rh", type=float, help="Maximum RH in metres")
    parser.add_argument(
        "--precision",
        type=float,
        help="RH grid spacing in metres (default: desiredP from station JSON)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output text path (default: intermediate_files/gnssir/DOY_extension_peng.txt)",
    )
    return parser.parse_args()


def validate_inputs(args: argparse.Namespace) -> tuple[str, str]:
    """Normalize and validate station, extension, year, and day of year."""
    station = args.station.strip().lower()
    extension = args.extension.strip()
    safe_name = re.compile(r"[A-Za-z0-9_-]+")

    if not safe_name.fullmatch(station):
        raise SystemExit("Station contains unsupported characters.")
    if not safe_name.fullmatch(extension):
        raise SystemExit("Extension contains unsupported characters.")
    if args.year < 1900 or args.year > 2200:
        raise SystemExit("Year must be between 1900 and 2200.")
    if args.doy < 1 or args.doy > 366:
        raise SystemExit("Day of year must be between 1 and 366.")
    if (args.min_rh is None) != (args.max_rh is None):
        raise SystemExit("Use --min-rh and --max-rh together.")

    return station, extension


def load_lsp_settings(
    root: Path,
    station: str,
    extension: str,
    min_rh_override: float | None,
    max_rh_override: float | None,
    precision_override: float | None,
) -> tuple[float, float, float, Path]:
    """Load the LSP search limits from the extension station JSON."""
    config_path = root / "input" / station / extension / f"{station}.json"
    if not config_path.is_file():
        raise SystemExit(f"Station configuration does not exist: {config_path}")

    try:
        with config_path.open(encoding="utf-8") as stream:
            config = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read station configuration: {exc}") from exc

    min_rh = min_rh_override if min_rh_override is not None else config.get("minH")
    max_rh = max_rh_override if max_rh_override is not None else config.get("maxH")
    precision = (
        precision_override
        if precision_override is not None
        else config.get("desiredP", 0.005)
    )

    if min_rh is None or max_rh is None:
        raise SystemExit("The station JSON must define minH and maxH.")

    min_rh = float(min_rh)
    max_rh = float(max_rh)
    precision = float(precision)
    if min_rh < 0 or max_rh <= min_rh:
        raise SystemExit("RH limits must satisfy 0 <= minH < maxH.")
    if precision <= 0:
        raise SystemExit("LSP precision must be positive.")

    return min_rh, max_rh, precision, config_path


def parse_arc_filename(path: Path) -> ArcMetadata:
    """Extract satellite, frequency band, constellation, and azimuth."""
    match = ARC_PATTERN.fullmatch(path.name)
    if match is None:
        raise ValueError(f"Unsupported arc filename: {path.name}")
    return ArcMetadata(
        satellite=int(match.group("sat")),
        band=int(match.group("band")),
        constellation=match.group("constellation"),
        azimuth_deg=int(match.group("azimuth")),
    )


def frequency_code(metadata: ArcMetadata) -> int:
    """Convert an arc filename frequency to a gnssrefl frequency code."""
    band = metadata.band
    constellation = metadata.constellation

    if constellation == "G":
        # gnssrefl represents GPS L2C with frequency code 20.
        return 20 if band == 2 else band
    if constellation == "R":
        return 100 + band
    if constellation == "E":
        return 200 + band
    if constellation == "C":
        return 300 + band
    raise ValueError(f"Unsupported constellation: {constellation}")


def read_arc(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Read elevation, detrended SNR, and seconds-of-day from one arc."""
    try:
        data = np.loadtxt(path, comments="%", ndmin=2)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read arc: {exc}") from exc

    if data.shape[1] < 3:
        raise ValueError("Arc must contain at least three columns.")

    data = data[:, :3]
    finite = np.all(np.isfinite(data), axis=1)
    data = data[finite]
    if len(data) < 3:
        raise ValueError("Arc has fewer than three finite observations.")
    if np.ptp(data[:, 0]) == 0:
        raise ValueError("Arc elevation angles have zero range.")

    return data[:, 0], data[:, 1], data[:, 2]


def estimate_initial_rh(
    elevation_deg: np.ndarray,
    detrended_snr: np.ndarray,
    wavelength_factor: float,
    min_rh: float,
    max_rh: float,
    precision: float,
) -> tuple[float, float]:
    """Return the RH and amplitude at the conventional LSP peak."""
    rh, amplitude, *_ = gnssrefl_gps.strip_compute(
        elevation_deg,
        detrended_snr,
        wavelength_factor,
        max_rh,
        precision,
        0,
        min_rh,
    )
    if not np.isfinite(rh) or rh <= 0:
        raise ValueError("LSP did not return a valid reflector height.")
    return float(rh), float(amplitude)


def process_arcs(
    arc_dir: Path,
    min_rh: float,
    max_rh: float,
    precision: float,
) -> tuple[list[ArcResult], list[str]]:
    """Compute an initial RH for each accepted text arc in a directory."""
    arc_paths = sorted(arc_dir.glob("sat*.txt"))
    if not arc_paths:
        raise SystemExit(
            f"No accepted arc files found in {arc_dir}. Re-run gnssir with "
            "-savearcs T and verify that arcs pass quality control."
        )

    rows: list[ArcResult] = []
    failures: list[str] = []
    for path in arc_paths:
        try:
            metadata = parse_arc_filename(path)
            freq = frequency_code(metadata)
            wavelength_factor = float(
                gnssrefl_gps.arc_scaleF(freq, metadata.satellite)
            )
            if wavelength_factor <= 0:
                raise ValueError(f"No wavelength is available for frequency {freq}.")

            elevation, detrended_snr, seconds = read_arc(path)
            rh, amplitude = estimate_initial_rh(
                elevation,
                detrended_snr,
                wavelength_factor,
                min_rh,
                max_rh,
                precision,
            )
            rows.append(
                {
                    "arc_file": path.name,
                    "satellite": metadata.satellite,
                    "constellation": metadata.constellation,
                    "frequency_code": freq,
                    "band": f"L{metadata.band}",
                    "azimuth_deg": metadata.azimuth_deg,
                    "mean_utc_hour": float(np.mean(seconds) / 3600.0),
                    "rise_set": 1 if elevation[-1] > elevation[0] else -1,
                    "min_elevation_deg": float(np.min(elevation)),
                    "max_elevation_deg": float(np.max(elevation)),
                    "observations": len(elevation),
                    "wavelength_m": 2.0 * wavelength_factor,
                    "initial_rh_m": rh,
                    "lsp_peak_amplitude": amplitude,
                }
            )
        except ValueError as exc:
            failures.append(f"{path.name}: {exc}")

    return rows, failures


def read_daily_results(path: Path) -> list[list[float]]:
    """Read accepted 17-column gnssrefl results as output-row templates."""
    if not path.is_file():
        raise SystemExit(f"Daily gnssrefl results do not exist: {path}")
    templates: list[list[float]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip() or line.lstrip().startswith("%"):
                    continue
                fields = line.split()
                if len(fields) != 17:
                    raise SystemExit(
                        f"Daily results line {line_number} has {len(fields)} "
                        "columns; expected 17."
                    )
                values = [float(field) for field in fields]
                if not np.all(np.isfinite(values)):
                    raise SystemExit(
                        f"Daily results line {line_number} contains a non-finite value."
                    )
                templates.append(values)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Cannot read daily gnssrefl results: {exc}") from exc
    if not templates:
        raise SystemExit(f"No result rows found in {path}")
    return templates


def match_template(row: ArcResult, templates: list[list[float]]) -> list[float]:
    """Match a saved arc to its original accepted gnssrefl result row."""
    candidates = [
        values for values in templates
        if int(values[3]) == int(row["satellite"])
        and int(values[10]) == int(row["frequency_code"])
        and int(values[11]) == int(row["rise_set"])
    ]
    if not candidates:
        raise ValueError(
            f"no matching 17-column result for {row['arc_file']} "
            "(satellite/frequency/rise-set)"
        )
    mean_hour = float(row["mean_utc_hour"])
    return min(candidates, key=lambda values: abs(values[4] - mean_hour)).copy()


def write_results(
    output_path: Path, station: str, rows: list[ArcResult],
    templates: list[list[float]],
) -> None:
    """Write Peng-version results in gnssrefl 17-column text format."""
    if not rows:
        raise SystemExit("No arc produced a valid initial RH.")
    output_rows: list[list[float]] = []
    for row in rows:
        values = match_template(row, templates)
        values[2] = float(row["initial_rh_m"])
        values[4] = float(row["mean_utc_hour"])
        values[6] = float(row["lsp_peak_amplitude"])
        values[7] = float(row["min_elevation_deg"])
        values[8] = float(row["max_elevation_deg"])
        values[9] = float(row["observations"])
        output_rows.append(values)
    output_rows.sort(key=lambda values: values[4])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as stream:
        stream.write(f"% station {station} Peng's version\n")
        stream.write("% Initial RH recomputed from saved accepted GNSS-IR arcs\n")
        stream.write("%(1)  (2)   (3) (4)  (5)     (6)   (7)    (8)    (9)   (10)  (11) (12) (13)    (14)     (15)    (16) (17)\n")
        stream.write("% year, doy, RH, sat,UTCtime, Azim, Amp,  eminO, emaxO,NumbOf,freq,rise,EdotF, PkNoise,  DelT,    MJD, refr\n")
        stream.write("%            m        hrs    deg   v/v    deg    deg  values        set  hrs            min           model\n%\n")
        for values in output_rows:
            stream.write(
                f"{int(values[0]):4d} {int(values[1]):3d} {values[2]:6.3f} "
                f"{int(values[3]):3d} {values[4]:6.3f} {values[5]:6.2f} "
                f"{values[6]:6.2f} {values[7]:6.2f} {values[8]:6.2f} "
                f"{int(values[9]):4d} {int(values[10]):4d} {int(values[11]):3d} "
                f"{values[12]:8.5f} {values[13]:6.2f} {values[14]:7.2f} "
                f"{values[15]:12.6f} {int(values[16]):2d}\n"
            )


def main() -> None:
    """Run the complete saved-arc LSP workflow."""
    args = parse_args()
    station, extension = validate_inputs(args)

    refl_code = os.environ.get("REFL_CODE")
    if not refl_code:
        raise SystemExit("The REFL_CODE environment variable is not set.")
    root = Path(refl_code).expanduser()
    doy = f"{args.doy:03d}"

    min_rh, max_rh, precision, config_path = load_lsp_settings(
        root,
        station,
        extension,
        args.min_rh,
        args.max_rh,
        args.precision,
    )
    arc_dir = root / str(args.year) / "arcs" / station / extension / doy
    daily_results_path = (
        root / str(args.year) / "results" / station / extension / f"{doy}.txt"
    )
    output_path = args.output or (
        HERE / "intermediate_files" / "gnssir" / f"{doy}_{extension}_peng.txt"
    )

    print(f"Station configuration: {config_path}")
    print(f"Accepted arc directory: {arc_dir}")
    print(f"Daily results template: {daily_results_path}")
    print(
        f"LSP search: {min_rh:.3f}-{max_rh:.3f} m, "
        f"grid spacing {precision:g} m"
    )

    rows, failures = process_arcs(arc_dir, min_rh, max_rh, precision)
    templates = read_daily_results(daily_results_path)
    try:
        write_results(output_path, station, rows, templates)
    except ValueError as exc:
        raise SystemExit(f"Cannot write Peng-version results: {exc}") from exc

    for failure in failures:
        print(f"Warning: {failure}")
    print(f"Initial RH estimates written: {len(rows)}")
    print(f"Failed arcs: {len(failures)}")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    main()
