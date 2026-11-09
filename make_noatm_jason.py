"""Create a GNSS-IR configuration that disables Bennett refraction.

GNSS-IR normally applies the Bennett atmospheric-refraction correction. This
project applies VMF3 separately, so it needs a configuration with the built-in
correction disabled. The original station configuration is left unchanged;
instead, this script creates the extension configuration expected by gnssrefl:

    $REFL_CODE/input/<station>/noatm/<station>.json

Usage:
    python make_noatm_jason.py <station>

Example:
    python make_noatm_jason.py islk
"""

import argparse
import json
import os
import re
from pathlib import Path


def parse_args() -> argparse.Namespace:
    """Parse the station name supplied on the command line."""
    parser = argparse.ArgumentParser(
        description=(
            "Create a noatm GNSS-IR configuration with atmospheric "
            "refraction disabled."
        )
    )
    parser.add_argument("station", help="Station name, for example: islk")
    return parser.parse_args()


def station_paths(root: Path, station: str) -> list[Path]:
    """Return supported source locations in search-priority order."""
    return [
        root / "input" / station / f"{station}.json",
        root / "input" / f"{station}.json",
        root / f"{station}.json",
    ]


def main() -> None:
    args = parse_args()

    # gnssrefl station names are conventionally lowercase.
    station = args.station.strip().lower()

    # Reject path separators and other unsafe characters in user input.
    if not re.fullmatch(r"[a-z0-9_-]+", station):
        raise SystemExit(
            "The station name may contain only letters, numbers, underscores, "
            "and hyphens."
        )

    # REFL_CODE is the root directory used by gnssrefl.
    refl_code = os.environ.get("REFL_CODE")
    if not refl_code:
        raise SystemExit("The REFL_CODE environment variable is not set.")

    root = Path(refl_code).expanduser()
    candidates = station_paths(root, station)

    # With "-extension noatm", gnssrefl reads this configuration path.
    dst = root / "input" / station / "noatm" / f"{station}.json"

    # Preserve an existing extension configuration without modifying it.
    if dst.is_file():
        print(f"Configuration already exists; skipping: {dst}")
        return

    # Use the first existing source configuration from the supported layouts.
    src = next((path for path in candidates if path.is_file()), None)
    if src is None:
        searched = "\n".join(f"  - {path}" for path in candidates)
        raise SystemExit(
            f"Could not find the source JSON configuration for station "
            f"{station}. Checked:\n{searched}"
        )

    # Copy all original settings before changing only the refraction options.
    with src.open(encoding="utf-8") as f:
        config = json.load(f)

    config["refraction"] = False
    config["refr_model"] = 0

    # Create the extension directory. Exclusive mode ("x") also prevents a
    # concurrent process from overwriting a file created after the first check.
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        with dst.open("x", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
            f.write("\n")
    except FileExistsError:
        print(f"Configuration already exists; skipping: {dst}")
        return

    print(f"Source configuration: {src}")
    print(f"Created configuration: {dst}")
    print("refraction = False")
    print("refr_model = 0")


if __name__ == "__main__":
    main()
