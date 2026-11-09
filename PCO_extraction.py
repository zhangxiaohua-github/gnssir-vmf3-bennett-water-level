"""
For phase correction, we need to extract the PCO from igs20.anx.

-----------------------------
Phase Center Correction - Questions and Answers

Q1. What is phase center correction?
It converts measurements between the antenna reference point (ARP)
and the antenna phase center (APC). APC is always above the ARP.

Q2. What is PCO?
PCO (Phase Center Offset) is the offset from the ARP to the mean APC.
ANTEX gives it as NORTH / EAST / UP in millimeters.

Q3. What is PCV?
PCV (Phase Center Variation) changes with elevation and azimuth.
It is listed under NOAZI or azimuth-dependent rows.

Q4. Where can igs20.atx be downloaded?
IGS: International GNSS Service
Download it from the official IGS directory:
https://files.igs.org/pub/station/general/igs20.atx

Q5. How is PCO obtained from igs20.atx?
1. Find the exact antenna model and radome from the RINEX header.
2. Find the matching TYPE / SERIAL NO entry.
3. Select the signal frequency, e.g., G01 for GPS L1.
4. Read the NORTH / EAST / UP line.
5. Convert millimeters to meters.

Example:
0.18  -0.58  91.15  NORTH / EAST / UP

L1 vertical PCO:
PCO_up = 91.15 / 1000 = 0.09115 m

Q6. Is PCO selected by elevation angle?
No. PCO is fixed for a given antenna and frequency.
PCV, not PCO, is selected using elevation and azimuth.

Q7. How is the ARP-referenced reflector height obtained?
If GNSS-IR reflector height H is measured from the phase center:

H_ARP = H - PCO_up

Example:
H_ARP = H - 0.09115 m

Q8. How is the reflector surface elevation obtained?
If the antenna coordinate is the ARP ellipsoidal height:

h_surface = h_ARP + PCO_up - H

where H is the GNSS-IR phase-center-referenced reflector height. where h_ARP and h_surface are ellipsoidal heights / Geodetic heights.

Q9. How is h_ARP obtained?

h_ARP = h_marker + Delta_H

where Delta_H is the vertical offset from the station marker to
the antenna reference point (ARP); h_marker is the ellipsoidal height of the station marker.

h_marker(t) at every time can be obtained from tenv3 file from the UNR station page, like
https://geodesy.unr.edu/NGLStationPages/stations/SC02.sta
or, consider h_marker is a fixed value, can be also obtained from this website.

Delta_H is a fixed value if no changing antenna, can be obtained from rinex file header.

Q10. Should NOAZI PCV be added directly to H?
No. Standard ANTEX PCV should not be directly added to GNSS-IR
reflector height without a specific GNSS-IR phase model.

Q11. How to get the rinex file?
https://data.earthscope.org/archive/gnss/rinex/obs/


-----------------------------
this coding has two main parts:
B. extract PCO from igs20.anx
C. phase center correction

------------------------------
three very tall stations:ac59, av02, lrsk,
two lake stations:islk, p006
three pending station: tnpp, upvk, kyc1
"""

##############################################
from __future__ import annotations

import argparse
import gzip
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


DEFAULT_STATIONS = ("ac59", "av02", "lrsk", "islk", "p006")
DEFAULT_RINEX_DIRECTORY = Path(__file__).resolve().parent / "input" / "rinex"


@dataclass(frozen=True)
class PCO:
    """Phase center offset in meters, converted from ANTEX millimeters."""

    north_m: float
    east_m: float
    up_m: float


@dataclass(frozen=True)
class AntennaCalibration:
    model: str
    radome: str
    serial: str | None
    valid_from: datetime | None
    valid_until: datetime | None
    pco_by_frequency: dict[str, PCO]


@dataclass(frozen=True)
class RinexHeader:
    source: Path
    station: str | None
    antenna_number: str | None
    antenna_model: str
    radome: str
    delta_h_m: float | None
    delta_e_m: float | None
    delta_n_m: float | None


@dataclass(frozen=True)
class StationPCOResult:
    station: str | None
    antenna_model: str
    radome: str
    frequency: str
    pco: PCO


def normalize_text(value: str | None) -> str:
    return " ".join((value or "").upper().split())


def split_antenna_type(antenna_type: str, radome: str | None = None) -> tuple[str, str]:
    """Return ANTEX model/radome from a RINEX-style antenna type string."""

    if radome:
        return normalize_text(antenna_type), normalize_text(radome)

    raw = (antenna_type or "").rstrip()
    parts = raw.split()
    if len(parts) >= 2:
        return normalize_text(parts[0]), normalize_text(parts[1])

    model = raw[:16].strip()
    dome = raw[16:20].strip()
    return normalize_text(model), normalize_text(dome or "NONE")


def parse_antex_datetime(line: str) -> datetime | None:
    values = line[:43].split()
    if len(values) < 6:
        return None

    year, month, day, hour, minute = map(int, values[:5])
    second = float(values[5])
    whole_second = int(second)
    microsecond = int((second - whole_second) * 1_000_000)
    return datetime(year, month, day, hour, minute, whole_second, microsecond)


def parse_igs_atx(atx_path: str | Path) -> list[AntennaCalibration]:
    """Parse receiver/satellite ANTEX blocks and keep PCO values by frequency."""

    calibrations: list[AntennaCalibration] = []
    current: dict[str, object] | None = None
    current_frequency: str | None = None

    with Path(atx_path).open("rt", encoding="ascii", errors="replace") as handle:
        for line in handle:
            label = line[60:].strip() if len(line) > 60 else ""

            if label == "START OF ANTENNA":
                current = {
                    "model": "",
                    "radome": "NONE",
                    "serial": None,
                    "valid_from": None,
                    "valid_until": None,
                    "pco_by_frequency": {},
                }
                current_frequency = None
                continue

            if current is None:
                continue

            if label == "TYPE / SERIAL NO":
                model = normalize_text(line[:20])
                radome = normalize_text(line[20:40])
                if not radome:
                    model, radome = split_antenna_type(line[:20])
                serial = line[40:60].strip() or None
                current["model"] = model
                current["radome"] = radome or "NONE"
                current["serial"] = serial
                continue

            if label == "VALID FROM":
                current["valid_from"] = parse_antex_datetime(line)
                continue

            if label == "VALID UNTIL":
                current["valid_until"] = parse_antex_datetime(line)
                continue

            if label == "START OF FREQUENCY":
                current_frequency = normalize_text(line[:10])
                continue

            if label == "NORTH / EAST / UP" and current_frequency:
                north_mm, east_mm, up_mm = map(float, line[:30].split()[:3])
                pco_by_frequency = current["pco_by_frequency"]
                assert isinstance(pco_by_frequency, dict)
                pco_by_frequency[current_frequency] = PCO(
                    north_m=north_mm / 1000.0,
                    east_m=east_mm / 1000.0,
                    up_m=up_mm / 1000.0,
                )
                continue

            if label == "END OF FREQUENCY":
                current_frequency = None
                continue

            if label == "END OF ANTENNA":
                pco_by_frequency = current["pco_by_frequency"]
                if current["model"] and pco_by_frequency:
                    calibrations.append(
                        AntennaCalibration(
                            model=str(current["model"]),
                            radome=str(current["radome"] or "NONE"),
                            serial=current["serial"] if isinstance(current["serial"], str) else None,
                            valid_from=current["valid_from"]
                            if isinstance(current["valid_from"], datetime)
                            else None,
                            valid_until=current["valid_until"]
                            if isinstance(current["valid_until"], datetime)
                            else None,
                            pco_by_frequency=pco_by_frequency,  # type: ignore[arg-type]
                        )
                    )
                current = None
                current_frequency = None

    return calibrations


def find_calibration(
    calibrations: Iterable[AntennaCalibration],
    antenna_model: str,
    radome: str = "NONE",
    frequency: str = "G01",
    epoch: datetime | None = None,
) -> AntennaCalibration:
    """Find the best matching ANTEX calibration block."""

    model = normalize_text(antenna_model)
    dome = normalize_text(radome) or "NONE"
    freq = normalize_text(frequency)

    exact_matches: list[AntennaCalibration] = []
    fallback_matches: list[AntennaCalibration] = []

    for calibration in calibrations:
        if calibration.model != model or freq not in calibration.pco_by_frequency:
            continue
        if epoch is not None:
            if calibration.valid_from and epoch < calibration.valid_from:
                continue
            if calibration.valid_until and epoch > calibration.valid_until:
                continue
        if calibration.radome == dome:
            exact_matches.append(calibration)
        elif calibration.radome == "NONE":
            fallback_matches.append(calibration)

    matches = exact_matches or fallback_matches
    if not matches:
        raise LookupError(
            f"No PCO found for antenna={model} radome={dome} frequency={freq}"
        )

    matches.sort(key=lambda item: item.valid_from or datetime.min, reverse=True)
    return matches[0]


def get_pco_from_atx(
    atx_path: str | Path,
    antenna_model: str,
    radome: str = "NONE",
    frequency: str = "G01",
    epoch: datetime | None = None,
) -> PCO:
    calibrations = parse_igs_atx(atx_path)
    calibration = find_calibration(calibrations, antenna_model, radome, frequency, epoch)
    return calibration.pco_by_frequency[normalize_text(frequency)]


def open_text_maybe_gzip(path: str | Path):
    path = Path(path)
    if path.suffix.lower() == ".gz":
        return gzip.open(path, "rt", encoding="ascii", errors="replace")
    return path.open("rt", encoding="ascii", errors="replace")


def parse_rinex_header(rinex_path: str | Path) -> RinexHeader:
    """Read station, antenna type, radome, and Delta H/E/N from a RINEX header."""

    source = Path(rinex_path)
    station: str | None = None
    antenna_number: str | None = None
    antenna_model = ""
    radome = "NONE"
    delta_h_m: float | None = None
    delta_e_m: float | None = None
    delta_n_m: float | None = None

    with open_text_maybe_gzip(source) as handle:
        for line in handle:
            label = line[60:].strip() if len(line) > 60 else ""

            if label == "MARKER NAME":
                station = line[:60].strip() or None
            elif label == "ANT # / TYPE":
                antenna_number = line[:20].strip() or None
                antenna_model, radome = split_antenna_type(line[20:40])
            elif label == "ANTENNA: DELTA H/E/N":
                values = line[:60].split()
                if len(values) >= 3:
                    delta_h_m, delta_e_m, delta_n_m = map(float, values[:3])
            elif label == "END OF HEADER":
                break

    if not antenna_model:
        raise ValueError(f"Could not find ANT # / TYPE in RINEX header: {source}")

    return RinexHeader(
        source=source,
        station=station,
        antenna_number=antenna_number,
        antenna_model=antenna_model,
        radome=radome,
        delta_h_m=delta_h_m,
        delta_e_m=delta_e_m,
        delta_n_m=delta_n_m,
    )


def read_rinex_header_lines(rinex_path: str | Path) -> list[str]:
    """Return the complete RINEX header, including END OF HEADER."""

    header_lines: list[str] = []
    with open_text_maybe_gzip(rinex_path) as handle:
        for line in handle:
            header_lines.append(line.rstrip("\r\n"))
            if "END OF HEADER" in line[60:]:
                return header_lines

    raise ValueError(f"RINEX header has no END OF HEADER line: {rinex_path}")


def find_station_rinex(
    output_directory: str | Path,
    station: str,
    year: int,
    doy: int,
) -> Path | None:
    """Find one converted RINEX 2 file for a station and date."""

    directory = Path(output_directory)
    if not directory.exists():
        return None
    yy = year % 100
    prefix = f"{station.lower()}{doy:03d}"
    candidates = [
        path
        for path in directory.iterdir()
        if path.is_file()
        and path.name.lower().startswith(prefix)
        and re.search(rf"\.{yy:02d}o(?:\.gz)?$", path.name, re.IGNORECASE)
    ]
    return sorted(candidates)[0] if candidates else None


def find_compressed_rinex(
    raw_directory: str | Path,
    station: str,
    year: int,
    doy: int,
) -> Path | None:
    """Find a manually downloaded Hatanaka/Unix compressed file."""

    directory = Path(raw_directory)
    if not directory.exists():
        return None
    expected_name = f"{station.lower()}{doy:03d}0.{year % 100:02d}d.Z"
    candidate = directory / expected_name
    return candidate if candidate.is_file() else None


def convert_compressed_rinex(
    compressed_file: str | Path,
    output_directory: str | Path,
) -> Path:
    """Convert a local *.YYd.Z file and retain only the final *.YYo output."""

    compressed_file = Path(compressed_file)
    seven_zip = shutil.which("7z")
    crx2rnx = shutil.which("crx2rnx")
    if seven_zip is None:
        raise RuntimeError("7z was not found; the .Z file cannot be extracted")
    if crx2rnx is None:
        raise RuntimeError("crx2rnx was not found; Hatanaka conversion cannot run")

    output_directory = Path(output_directory).resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    final_name = compressed_file.with_suffix("").name[:-1] + "o"
    final_path = output_directory / final_name
    crx_file = output_directory / compressed_file.with_suffix("").name

    # Always remove the intermediate *.YYd file, including after a failed conversion.
    try:
        print(f"[Step 1/3: Extract] {compressed_file.name} -> {crx_file.name}", flush=True)
        unzip_result = subprocess.run(
            [seven_zip, "e", str(compressed_file), f"-o{output_directory}", "-y"],
            check=False,
        )
        if unzip_result.returncode != 0 or not crx_file.exists():
            raise RuntimeError(
                f".Z extraction failed (7z exit code: {unzip_result.returncode})"
            )

        print(f"[Step 2/3: Hatanaka conversion] {crx_file.name} -> {final_name}", flush=True)
        convert_result = subprocess.run(
            [crx2rnx, str(crx_file)], cwd=output_directory, check=False
        )
        if convert_result.returncode != 0 or not final_path.exists():
            raise RuntimeError(
                f"Hatanaka conversion failed (crx2rnx exit code: {convert_result.returncode})"
            )
    finally:
        crx_file.unlink(missing_ok=True)

    print(f"[Conversion complete] Final RINEX: {final_path}", flush=True)
    return final_path


def run_stage_1(
    stations: Iterable[str],
    year: int,
    doy: int,
    raw_directory: str | Path,
    output_directory: str | Path,
) -> list[RinexHeader]:
    """Convert local RINEX files, print headers, and summarize antenna data."""

    headers: list[RinexHeader] = []
    failures: list[tuple[str, str]] = []

    for station in stations:
        station = station.lower()
        print(f"\n{'=' * 72}\nStation {station.upper()}: locate/convert local RINEX\n{'=' * 72}")
        try:
            rinex_file = find_station_rinex(output_directory, station, year, doy)
            if rinex_file is not None:
                print(f"[Existing final file] {rinex_file}")
            else:
                compressed = find_compressed_rinex(
                    raw_directory, station, year, doy
                )
                if compressed is None:
                    raise FileNotFoundError(
                        f"Place {station}{doy:03d}0.{year % 100:02d}d.Z "
                        f"in {Path(raw_directory)} before running this stage"
                    )
                print(f"[Source file found] {compressed}")
                rinex_file = convert_compressed_rinex(compressed, output_directory)
            print(f"\n--- {rinex_file.name} header start ---")
            print(f"[Step 3/3: Read header] {rinex_file}")
            for line in read_rinex_header_lines(rinex_file):
                print(line)
            print(f"--- {rinex_file.name} header end ---")
            headers.append(parse_rinex_header(rinex_file))
        except (OSError, RuntimeError, ValueError) as error:
            failures.append((station, str(error)))
            print(f"[Failed] {error}")

    print("\nStage 1 summary")
    print("station,antenna_model,radome,Delta_H_m,rinex_file")
    for header in headers:
        print(
            f"{(header.station or '').lower()},"
            f"{header.antenna_model},{header.radome},"
            f"{'' if header.delta_h_m is None else f'{header.delta_h_m:.4f}'},"
            f"{header.source}"
        )
    for station, error in failures:
        print(f"{station},ERROR,,,\"{error}\"")

    return headers


def build_station_result(
    rinex_path: str | Path,
    atx_path: str | Path,
    frequency: str = "G01",
    epoch: datetime | None = None,
) -> StationPCOResult:
    header = parse_rinex_header(rinex_path)
    pco = get_pco_from_atx(
        atx_path,
        header.antenna_model,
        header.radome,
        frequency=frequency,
        epoch=epoch,
    )
    return StationPCOResult(
        station=header.station,
        antenna_model=header.antenna_model,
        radome=header.radome,
        frequency=normalize_text(frequency),
        pco=pco,
    )


def write_pco_report(
    results: Iterable[StationPCOResult], output_path: str | Path
) -> Path:
    """Write station antenna metadata and PCO values to a text report."""

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "station\tantenna_type\tradome\tfrequency\t"
        "PCO_NORTH_mm\tPCO_EAST_mm\tPCO_UP_mm\t"
        "PCO_NORTH_m\tPCO_EAST_m\tPCO_UP_m"
    ]
    for result in results:
        pco = result.pco
        lines.append(
            f"{result.station or ''}\t{result.antenna_model}\t{result.radome}\t"
            f"{result.frequency}\t{pco.north_m * 1000:.2f}\t"
            f"{pco.east_m * 1000:.2f}\t{pco.up_m * 1000:.2f}\t"
            f"{pco.north_m:.5f}\t{pco.east_m:.5f}\t{pco.up_m:.5f}"
        )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert local RINEX files and extract antenna PCO from igs20.atx."
    )
    parser.add_argument(
        "--stage",
        type=int,
        choices=(1, 2),
        default=1,
        help="1=convert/read local RINEX; 2=extract PCO_UP from stage 1 files.",
    )
    parser.add_argument(
        "--atx",
        default=Path(__file__).with_name("igs20.atx"),
        type=Path,
        help="Path to the local igs20.atx file. This program never downloads it.",
    )
    parser.add_argument("--frequency", default="G01", help="ANTEX frequency, e.g. G01.")
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=Path(__file__).parent,
        help="Directory containing manually downloaded *.YYd.Z files.",
    )
    parser.add_argument(
        "--rinex-dir",
        type=Path,
        default=DEFAULT_RINEX_DIRECTORY,
        help=r"Directory for final readable RINEX files.",
    )
    parser.add_argument(
        "--stations",
        nargs="+",
        default=DEFAULT_STATIONS,
        help="Station codes to process.",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=2024,
        help="RINEX data year (default: 2024).",
    )
    parser.add_argument(
        "--doy",
        type=int,
        default=274,
        help="Day of year (default: 274, 2024-09-30).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "intermediate_files" / "PCO_APC" / "PCO.txt",
        help="PCO report path (default: intermediate_files/PCO_APC/PCO.txt).",
    )
    return parser.parse_args()


def main() -> None:
    # Keep output consistent on Windows terminals using legacy code pages.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True, write_through=True)
        sys.stderr.reconfigure(encoding="utf-8", line_buffering=True, write_through=True)

    args = parse_args()
    if not 1 <= args.doy <= 366:
        raise ValueError("--doy must be between 1 and 366")
    if args.stage == 1:
        run_stage_1(
            stations=args.stations,
            year=args.year,
            doy=args.doy,
            raw_directory=args.raw_dir,
            output_directory=args.rinex_dir,
        )
        return

    # Stage 2 reads local files only; it never downloads RINEX or ANTEX data.
    if not args.atx.exists():
        raise FileNotFoundError(f"Local ANTEX file not found: {args.atx}")

    rinex_files = [
        path
        for station in args.stations
        if (path := find_station_rinex(args.rinex_dir, station, args.year, args.doy))
        is not None
    ]
    if not rinex_files:
        raise FileNotFoundError("No converted RINEX files found; run --stage 1 first")

    results = [
        build_station_result(rinex_file, args.atx, args.frequency)
        for rinex_file in rinex_files
    ]
    print("Stage 2 summary: GPS L1 (G01) PCO_UP from local igs20.atx")
    print("station,antenna_model,radome,frequency,PCO_UP_mm,PCO_UP_m")
    for result in results:
        print(
            f"{result.station or ''},{result.antenna_model},{result.radome},"
            f"{result.frequency},{result.pco.up_m * 1000:.2f},{result.pco.up_m:.5f}"
        )

    report_path = write_pco_report(results, args.output)
    print(f"PCO report written to: {report_path}")


if __name__ == "__main__":
    main()
