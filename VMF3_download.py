"""Download the VMF3 data required at GNSS-IR arc epochs.
1. The required 1 degree x 1 degree VMF3 grid files.
2. The matching 1 degree x 1 degree ellipsoidal-orography file.

Input file
----------
The positional ``arc_times`` argument is a text file containing one GNSS-IR
arc per data row. Its first six columns must be:

    year  month  day  hour  minute  second

For example::

    % Remaining columns may contain GNSS-IR and height information.
    2024 01 01 03 54 12.960 10.0000 2.5000 0 0 0 0 754.070200 823.454200 0
    2024 01 01 20 52 58.973  2.5000 10.0000 0 0 0 0 753.735200 823.454200 0

Input-format requirements:

* Date/time fields must form valid calendar epochs. They are treated as UTC
  labels by this downloader; no GPS-to-UTC conversion is performed here.
* In this study, gnssrefl GPS-like times are intentionally used without
  subtracting the 18-second GPS-UTC offset applicable in 2024. This offset is
  accepted as negligible relative to the six-hour VMF3 interval.
* Columns may be separated by whitespace or commas.
* Additional columns after the first six are allowed and ignored.
* Blank lines and lines beginning with ``%`` or ``#`` are ignored.
* Fractional seconds are allowed.
* Every non-comment data row must contain at least six numeric columns.
* The annotated 15-column file created by ``input_table_for_VMF3.py`` is a
  valid input and can be passed directly to this script.

VMF3 epoch selection
--------------------
Operational gridded VMF3 data are provided at 00:00, 06:00, 12:00, and
18:00 UTC. For each arc, the script downloads both grid epochs needed for
linear time interpolation. For example, an arc at 20:52 UTC requires the
18:00 grid from that day and the 00:00 grid from the following day. Day and
year boundaries are handled automatically. If an arc lies exactly on a VMF3
epoch, the current implementation still includes the following six-hour file;
this is harmless but may download one file that MATLAB does not need.

Usage
-----
Always inspect the download plan first::

    python VMF3_download.py intermediate_files/VMF3_input_files/islk_2024_001_VMF3_input.txt --dry-run

Download the files::

    python VMF3_download.py intermediate_files/VMF3_input_files/islk_2024_001_VMF3_input.txt

Download existing files again when replacement is intentional::

    python VMF3_download.py intermediate_files/VMF3_input_files/islk_2024_001_VMF3_input.txt --overwrite

Command-line parameters
-----------------------
``arc_times``
    Optional positional path to the input table described above. If omitted,
    the script reads ``intermediate_files/VMF3_input_files/
    islk_2024_001_VMF3_input.txt`` beside this script.

``--grid-dir PATH``
    Root output directory for VMF3 grids. Files are placed in a year
    subdirectory. The default is
    ``./intermediate_files/VMF3_grid``, relative to the directory containing
    this script.

``--orography-dir PATH``
    Output directory for ``orography_ell_1x1``. The default is
    ``./intermediate_files/orography``, relative to the directory containing
    this script.

``--timeout SECONDS``
    Network timeout for each file. It must be greater than zero. The default
    is 60 seconds.

``--overwrite``
    Download a target again even if a file with the expected name already
    exists. Without this option, existing files are skipped.

``--dry-run``
    Print the required epochs, source URLs, and destination paths without
    creating directories or downloading files.

Output
------
With the default paths, output has the structure::

    intermediate_files/
      VMF3_grid/
        2024/
          VMF3_20240101.H00
          VMF3_20240101.H06
          VMF3_20240101.H12
          VMF3_20240101.H18
          VMF3_20240102.H00
      orography/
        orography_ell_1x1

Grid files are written atomically through a temporary ``.part`` file. A
successful rename occurs only after the response has been copied. Partial
files created by a failed request are removed. Individual failures are
reported, and the run ends with an error if any required file failed.

Important correctness notes and common failure modes
----------------------------------------------------
* If ``arc_times`` is omitted, the default ISLK 2024 DOY 001 input table is
  used. Pass another path explicitly when processing a different station or day.
* Default input and output paths are relative to the directory containing this
  script, so invoking it from another working directory does not change them.
* The downloader performs no time-scale conversion. Normally, input should be
  UTC. For the present 2024 study, the unconverted 18-second GPS-UTC offset is
  an explicit approximation and is considered negligible. This assumption
  must be revisited for other dates or arcs near a six-hour grid boundary.
  Local civil time or an incorrect date must never be used.
* Keep grid resolution consistent. This script downloads 1x1 VMF3 grids and
  ``orography_ell_1x1``; downstream MATLAB must use ``grid_res = 1``. A 5x5
  orography file must not be mixed with 1x1 grids.
* Keep the year subdirectories. ``vmf3_grid.m`` expects paths such as
  ``<grid-dir>/2024/VMF3_20240101.H06``.
* The downloader checks whether a destination exists but does not validate the
  scientific contents, number of rows, or size of an existing file. A corrupt
  or zero-length existing file will be skipped unless ``--overwrite`` is used.
* Network outages, DNS errors, TLS errors, server errors, permissions, disk
  space, and timeouts can prevent a complete download. Do not run MATLAB until
  every required file has been downloaded successfully.
* The script downloads historical/operational ``VMF3_OP`` products. Changing
  the base URL or product family without also checking the downstream model
  assumptions can produce inconsistent results.
* MATLAB paths are configured separately. Set ``fileHr``, ``dir_VMF3_grid``,
  and ``dir_orography`` in ``matlab_corrTrop_GNSSir.m`` to the input table and
  directories actually used here.
* Station latitude and longitude are not used by this downloader; they are
  used later by ``vmf3_grid.m``. Incorrect coordinates, degrees supplied where
  radians are expected, or a negative longitude passed to code expecting
  0-360 degrees can produce scientifically wrong corrections even when all
  downloaded files are correct.

Recommended workflow
--------------------
1. Create the annotated arc table with ``input_table_for_VMF3.py``.
2. Run this script with ``--dry-run`` and inspect every epoch and path.
3. Run this script without ``--dry-run``.
4. Verify that every listed file exists and is non-empty.
5. Configure the MATLAB input/data paths, station coordinates, and
   ``grid_res = 1``.
6. Run ``matlab_corrTrop_GNSSir.m``; it will call ``vmf3_grid.m`` for the
   actual VMF3 interpolation and tropospheric correction.
"""


from __future__ import annotations

import argparse
import math
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BASE_URL = "https://vmf.geo.tuwien.ac.at/trop_products/GRID/1x1/VMF3/VMF3_OP"
OROGRAPHY_URL = (
    "https://vmf.geo.tuwien.ac.at/station_coord_files/orography_ell_1x1"
)
HERE = Path(__file__).resolve().parent
DEFAULT_DOWNLOAD_ROOT = HERE / "intermediate_files"
DEFAULT_GRID_DIR = DEFAULT_DOWNLOAD_ROOT / "VMF3_grid"
DEFAULT_OROGRAPHY_DIR = DEFAULT_DOWNLOAD_ROOT / "orography"
DEFAULT_ARC_TIMES = (
    DEFAULT_DOWNLOAD_ROOT / "VMF3_input_files" / "islk_2024_001_VMF3_input.txt"
)
OROGRAPHY_NAME = "orography_ell_1x1"
GRID_INTERVAL_HOURS = 6

# -----------------------------------------------------------------------------
# Arc-time parameters injected directly into this script for testing.
# Each row is: (year, month, day, hour, minute, second), in UTC.
# Replace these rows with real arc epochs when they become available.
# -----------------------------------------------------------------------------
ARC_TIME_PARAMETERS = [
    (2024, 9, 30, 1, 15, 0.0),
    (2024, 9, 30, 7, 40, 30.0),
    (2024, 9, 30, 13, 20, 0.0),
    (2024, 9, 30, 19, 5, 15.0),
]


def injected_arc_epochs() -> list[datetime]:
    """Convert the built-in arc-time parameters to UTC datetimes."""

    epochs: list[datetime] = []
    for index, values in enumerate(ARC_TIME_PARAMETERS, start=1):
        if len(values) != 6:
            raise ValueError(f"ARC_TIME_PARAMETERS row {index} must contain six values")
        line = " ".join(str(value) for value in values)
        epoch = parse_arc_epoch(line, index)
        if epoch is not None:
            epochs.append(epoch)
    if not epochs:
        raise ValueError("ARC_TIME_PARAMETERS is empty")
    return epochs


def parse_arc_epoch(line: str, line_number: int) -> datetime | None:
    """Parse and label the first six columns as UTC without scale conversion."""

    stripped = line.strip()
    if not stripped or stripped.startswith(("#", "%")):
        return None

    columns = re.split(r"[\s,]+", stripped)
    if len(columns) < 6:
        raise ValueError(
            f"Line {line_number}: expected at least six date/time columns"
        )

    try:
        year, month, day, hour, minute = (int(float(value)) for value in columns[:5])
        second_value = float(columns[5])
        second = math.floor(second_value)
        microsecond = round((second_value - second) * 1_000_000)
        if microsecond == 1_000_000:
            second += 1
            microsecond = 0
        return datetime(
            year, month, day, hour, minute, second, microsecond, tzinfo=timezone.utc
        )
    except (ValueError, OverflowError) as error:
        raise ValueError(f"Line {line_number}: invalid UTC epoch") from error


def load_arc_epochs(path: str | Path) -> list[datetime]:
    """Load arc epochs that are treated as UTC labels without conversion."""

    path = Path(path)
    epochs: list[datetime] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            epoch = parse_arc_epoch(line, line_number)
            if epoch is not None:
                epochs.append(epoch)

    if not epochs:
        raise ValueError(f"No arc epochs found in {path}")
    return epochs


def surrounding_vmf3_epochs(arc_epochs: list[datetime]) -> list[datetime]:
    """Return unique six-hour VMF3 epochs bracketing all arc epochs."""

    required: set[datetime] = set()
    for epoch in arc_epochs:
        before = epoch.replace(
            hour=(epoch.hour // GRID_INTERVAL_HOURS) * GRID_INTERVAL_HOURS,
            minute=0,
            second=0,
            microsecond=0,
        )
        required.add(before)
        required.add(before + timedelta(hours=GRID_INTERVAL_HOURS))
    return sorted(required)


def grid_name(epoch: datetime) -> str:
    return epoch.strftime("VMF3_%Y%m%d.H%H")


def download_file(
    url: str,
    destination: str | Path,
    *,
    overwrite: bool = False,
    timeout: float = 60.0,
) -> bool:
    """Download one file atomically; return False when it already exists."""

    destination = Path(destination)
    if destination.is_file() and not overwrite:
        print(f"[Existing] {destination}")
        return False

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    request = Request(url, headers={"User-Agent": "VMF3-download/1.0"})
    print(f"[Downloading] {url}")

    try:
        with urlopen(request, timeout=timeout) as response, partial.open("wb") as output:
            shutil.copyfileobj(response, output)
        os.replace(partial, destination)
    except (HTTPError, URLError, TimeoutError, OSError):
        partial.unlink(missing_ok=True)
        raise

    print(f"[Saved] {destination}")
    return True


def download_required_files(
    epochs: list[datetime],
    grid_directory: str | Path,
    orography_directory: str | Path,
    *,
    overwrite: bool = False,
    timeout: float = 60.0,
    dry_run: bool = False,
) -> None:
    """Download the orography file and all required yearly VMF3 grids."""

    grid_directory = Path(grid_directory)
    orography_file = Path(orography_directory) / OROGRAPHY_NAME

    targets = [(OROGRAPHY_URL, orography_file)]
    for epoch in epochs:
        name = grid_name(epoch)
        year = str(epoch.year)
        targets.append((f"{BASE_URL}/{year}/{name}", grid_directory / year / name))

    if dry_run:
        for url, destination in targets:
            print(f"[Dry run] {url} -> {destination}")
        return

    failures: list[str] = []
    for url, destination in targets:
        try:
            download_file(url, destination, overwrite=overwrite, timeout=timeout)
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            failures.append(f"{url}: {error}")
            print(f"[Failed] {url}: {error}", file=sys.stderr)

    if failures:
        raise RuntimeError(f"Failed to download {len(failures)} file(s)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download six-hour VMF3 grids surrounding GNSS-IR arc epochs."
    )
    parser.add_argument(
        "arc_times",
        nargs="?",
        type=Path,
        default=DEFAULT_ARC_TIMES,
        help=(
            "Text file whose first six columns are UTC year month day hour minute "
            "second (default: intermediate_files/VMF3_input_files/"
            "islk_2024_001_VMF3_input.txt)."
        ),
    )
    parser.add_argument(
        "--grid-dir",
        type=Path,
        default=DEFAULT_GRID_DIR,
        help="VMF3 grid output directory (default: ./intermediate_files/VMF3_grid).",
    )
    parser.add_argument(
        "--orography-dir",
        type=Path,
        default=DEFAULT_OROGRAPHY_DIR,
        help="Orography output directory (default: ./intermediate_files/orography).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Per-file network timeout in seconds (default: 60).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Download files again even when they already exist.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print required epochs, URLs, and paths without downloading.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.timeout <= 0:
        raise ValueError("--timeout must be greater than zero")

    arc_epochs = load_arc_epochs(args.arc_times)
    print(f"Using arc epochs from: {args.arc_times}")
    vmf3_epochs = surrounding_vmf3_epochs(arc_epochs)
    print(
        f"Loaded {len(arc_epochs)} arc epoch(s); "
        f"required {len(vmf3_epochs)} unique VMF3 epoch(s)."
    )
    for epoch in vmf3_epochs:
        print(f"  {epoch.strftime('%Y-%m-%d %H:%M:%S UTC')}  {grid_name(epoch)}")

    download_required_files(
        vmf3_epochs,
        args.grid_dir,
        args.orography_dir,
        overwrite=args.overwrite,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
