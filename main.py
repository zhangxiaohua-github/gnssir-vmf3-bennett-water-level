#!/usr/bin/env python3
"""One-command PCO, VMF3, Bennett, and absolute-height comparison workflow.

Project purpose
---------------
This project estimates inland-water surface height from GPS L1 GNSS-IR and
answers one physical question in an absolute vertical reference frame:
which atmospheric treatment, VMF3 or Bennett, agrees more closely with the
co-located water gauge?

The workflow has three main parts:

1. PCO correction
   Extract the GPS L1 antenna phase-centre offset (PCO), combine it with the
   station marker and ARP heights, and obtain the antenna phase-centre (APC)
   ellipsoidal height used to turn reflector height (RH) into surface height.

2. VMF3 versus Bennett correction
   The VMF3 branch disables gnssrefl refraction, recomputes planar RH from the
   saved arcs, and then applies time-varying VMF3 grids.  The Bennett branch
   uses the standard gnssrefl result with Bennett atmospheric refraction.  Only
   GPS L1 is used; this workflow does not compare frequencies.

3. Comparison in an absolute frame
   VMF3 surfaces are transformed IGS20 -> NAD83(2011) -> NAVD88.  Bennett RH
   uses the same daily APC NAVD88 reference.  USACE gauge elevations are also
   converted to NAVD88.  Per-arc signed error, absolute error, bias, MAE, RMSE,
   and correlation are then written and plotted.

Main data flow
--------------

    local RINEX + igs20.atx
              |
              v
    PCO_extraction.py -----------------> intermediate_files/PCO_APC/PCO.txt
              |
              v
    GNSS-IR-geodetic-height.py --------> daily APC ellipsoidal heights
              |                                      |
              |                    +-----------------+------------------+
              |                    |                                    |
              v                    v                                    v
    noatm station JSON       Bennett gnssrefl                    water_gauge.py
              |                    result                               |
              v                       |                                 v
    gnssir -extension noatm             |                     water_gauge_NAVD88.py
      -savearcs T                       |                                 |
              |                         |                                 |
              v                         |                                 |
    GNSS-IR.py -> input_table_for_VMF3.py                                 |
              |                                                           |
              v                                                           |
    VMF3_download.py -> matlab_corrTrop_GNSSir.m                           |
              |                  |                                        |
              v                  v                                        |
    gnssir_surface_IGS20.py -> gnssir_surface_NAVD88.py                    |
              |                           |                               |
              +---------------------------+-------------------------------+
                                          |
                                          v
                         compare_vmf3_bennett_navd88.py
                                          |
                         +----------------+----------------+
                         v                                 v
             comparison CSV/metrics                final PNG/PDF

Script contracts (inputs -> outputs)
------------------------------------
- PCO_extraction.py:
  local converted RINEX + local igs20.atx -> PCO_APC/PCO.txt.
- GNSS-IR-geodetic-height.py:
  PCO.txt + UNR IGS20 tenv3 + station/day -> daily APC height file.
- make_noatm_jason.py:
  base gnssrefl station JSON -> noatm station JSON (refraction disabled).
- external ``gnssir`` command:
  SNR + station JSON -> accepted daily results and saved L1 arcs.  It is run
  once for noatm/VMF3 and once with the base Bennett configuration.
- GNSS-IR.py:
  noatm saved arcs + noatm daily result -> planar 17-column RH result.
- input_table_for_VMF3.py:
  planar RH + daily APC -> 15-column VMF3 input table.
- VMF3_download.py:
  VMF3 input epochs -> six-hour VMF3 grids + ellipsoidal orography.
- matlab_corrTrop_GNSSir.m (calls vmf3_grid.m):
  VMF3 input + grids + orography + station coordinates -> VMF3-corrected RH
  and IGS20 surface height.
- gnssir_surface_IGS20.py:
  APC + planar RH + VMF3 output -> normalized per-arc IGS20 table.
- gnssir_surface_NAVD88.py:
  IGS20 table + NOAA VDatum -> per-arc NAD83(2011)/NAVD88 table.
- water_gauge.py:
  USACE API + station/day -> raw gauge elevations.
- water_gauge_NAVD88.py:
  raw gauge elevations -> NAVD88 gauge elevations.
- compare_vmf3_bennett_navd88.py:
  VMF3 NAVD88 + Bennett L1 RH + APC NAVD88 + gauge NAVD88 -> per-method CSV,
  absolute-error metrics CSV, and the final comparison PNG/PDF.

Usage
-----
Run the existing ISLK 2024 DOY 183-205 project (cached products are reused):

    python main.py

Run another range:

    python main.py run-all STATION YEAR START_DOY END_DOY

Inspect the workflow without running it:

    python main.py flow

Use ``--force`` to rebuild existing products, ``--dry-run`` to print commands,
and ``--offline`` to forbid any missing network/external product from being
created.  A completely new run needs REFL_CODE, gnssrefl/gnssir, MATLAB,
converted station RINEX, and igs20.atx; pass the last two with --rinex-dir and
--atx when PCO.txt does not already exist.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
INTERMEDIATE = HERE / "intermediate_files"


@dataclass(frozen=True)
class Contract:
    name: str
    inputs: str
    outputs: str


CONTRACTS = (
    Contract("PCO", "converted RINEX + igs20.atx", "PCO_APC/PCO.txt"),
    Contract("APC", "PCO.txt + UNR tenv3", "daily *_APC_geodetic_height.txt"),
    Contract("GNSS-IR noatm", "SNR + noatm JSON", "saved arcs + planar RH"),
    Contract("VMF3", "planar RH + grids + APC", "VMF3-corrected RH/surface"),
    Contract("Bennett", "SNR + Bennett base JSON", "gnssrefl L1 RH"),
    Contract("absolute frames", "IGS20 surfaces + VDatum", "NAVD88 surfaces"),
    Contract("gauge", "USACE elevations", "NAVD88 gauge series"),
    Contract("comparison", "VMF3 + Bennett + gauge NAVD88", "CSV + PNG + PDF"),
)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", nargs="?", choices=("run-all", "flow"), default="run-all")
    p.add_argument("station", nargs="?", default="islk")
    p.add_argument("year", nargs="?", type=int, default=2024)
    p.add_argument("start_doy", nargs="?", type=int, default=183)
    p.add_argument("end_doy", nargs="?", type=int, default=205)
    p.add_argument("--refl-code", type=Path,
        default=Path(os.environ["REFL_CODE"]) if os.environ.get("REFL_CODE") else None,
        help="gnssrefl working root (default: REFL_CODE environment variable)")
    p.add_argument("--atx", type=Path, help="Local igs20.atx; needed only when PCO.txt is absent")
    p.add_argument("--rinex-dir", type=Path, help="Converted RINEX directory used for PCO extraction")
    p.add_argument("--force", action="store_true", help="Rebuild products even when outputs exist")
    p.add_argument("--dry-run", action="store_true", help="Print commands without running them")
    p.add_argument("--offline", action="store_true", help="Use cached products only; fail when one is missing")
    p.add_argument("--timeout", type=float, default=60.0, help="Network timeout in seconds")
    return p


def show_flow() -> None:
    print("Main data flow (inputs -> outputs):")
    for number, item in enumerate(CONTRACTS, 1):
        print(f"  {number}. {item.name}: {item.inputs} -> {item.outputs}")


class Pipeline:
    def __init__(self, args: argparse.Namespace):
        self.a = args; self.station = args.station.lower(); self.env = os.environ.copy()
        if args.refl_code is None:
            raise SystemExit("Set REFL_CODE or pass --refl-code PATH")
        self.refl_code = args.refl_code.expanduser().resolve()
        self.env["REFL_CODE"] = str(self.refl_code)
        self.python = sys.executable
        if not 1 <= args.start_doy <= args.end_doy <= 366:
            raise SystemExit("START_DOY/END_DOY must satisfy 1 <= start <= end <= 366")
        if args.timeout <= 0: raise SystemExit("--timeout must be positive")

    def product_ready(self, path: Path) -> bool:
        if path.is_file():
            return path.stat().st_size > 0
        if path.is_dir():
            return any(path.iterdir())
        return False

    def run(self, label: str, command: list[str], outputs: tuple[Path, ...] = (), always: bool = False) -> None:
        complete = bool(outputs) and all(self.product_ready(path) for path in outputs)
        if complete and not self.a.force and not always:
            print(f"[cached] {label}")
            return
        if self.a.offline and outputs and not complete:
            missing = ", ".join(str(path) for path in outputs if not self.product_ready(path))
            raise SystemExit(f"Offline mode: {label} needs missing product(s): {missing}")
        printable = subprocess.list2cmdline(command)
        print(f"[run] {label}\n      {printable}")
        if self.a.dry_run:
            return
        subprocess.run(command, cwd=HERE, env=self.env, check=True)
        for path in outputs:
            if not self.product_ready(path):
                raise RuntimeError(f"{label} did not create expected output: {path}")

    def script(self, name: str, *arguments: object) -> list[str]:
        return [self.python, str(HERE / name), *(str(value) for value in arguments)]

    def ensure_tools(self) -> None:
        if not self.refl_code.is_dir(): raise SystemExit(f"REFL_CODE not found: {self.refl_code}")
        if not self.a.offline and shutil.which("gnssir") is None: raise SystemExit("gnssir command not found")
        if not self.a.offline and shutil.which("matlab") is None: raise SystemExit("MATLAB command not found")

    def ensure_bennett_config(self) -> None:
        candidates = (self.refl_code/"input"/self.station/f"{self.station}.json",
                      self.refl_code/"input"/f"{self.station}.json")
        path = next((p for p in candidates if p.is_file()), None)
        if path is None: raise SystemExit("Base gnssrefl station JSON required for Bennett was not found")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not data.get("refraction", False) or int(data.get("refr_model", -1)) != 1:
            raise SystemExit(f"Bennett JSON must set refraction=true and refr_model=1: {path}")

    def pco(self) -> None:
        output = INTERMEDIATE/"PCO_APC"/"PCO.txt"
        if output.is_file() and not self.a.force: print("[cached] PCO extraction"); return
        if self.a.offline: raise SystemExit(f"Offline mode: missing {output}")
        if not self.a.atx or not self.a.rinex_dir:
            raise SystemExit("PCO.txt is missing; provide --atx and --rinex-dir")
        cmd = self.script("PCO_extraction.py", "--stage", 2, "--atx", self.a.atx,
            "--rinex-dir", self.a.rinex_dir, "--stations", self.station,
            "--year", self.a.year, "--doy", self.a.start_doy, "--frequency", "G01", "--output", output)
        self.run("PCO extraction", cmd, (output,))

    def apc(self, doy: int) -> Path:
        output = INTERMEDIATE/"PCO_APC"/f"{self.station}_{self.a.year}_{doy:03d}_APC_geodetic_height.txt"
        if output.is_file() and not self.a.force: print(f"[cached] APC DOY {doy:03d}"); return output
        if self.a.offline: raise SystemExit(f"Offline mode: missing {output}")
        self.run(f"APC DOY {doy:03d}", self.script("GNSS-IR-geodetic-height.py", "--date", self.a.year,
            f"{doy:03d}", "--station", self.station, "-fr", 1, "--output", output), (output,))
        return output

    def gnss_branches(self, doy: int) -> None:
        noatm_result = self.refl_code/str(self.a.year)/"results"/self.station/"noatm"/f"{doy:03d}.txt"
        arc_dir = self.refl_code/str(self.a.year)/"arcs"/self.station/"noatm"/f"{doy:03d}"
        bennett = self.refl_code/str(self.a.year)/"results"/self.station/f"{doy:03d}.txt"
        self.run(f"noatm gnssrefl + saved arcs DOY {doy:03d}", ["gnssir", self.station, str(self.a.year),
            str(doy), "-extension", "noatm", "-savearcs", "T", "-fr", "1"],
            (noatm_result, arc_dir))
        if not self.a.dry_run and not arc_dir.is_dir(): raise RuntimeError(f"saved arc directory missing: {arc_dir}")
        self.run(f"Bennett gnssrefl DOY {doy:03d}", ["gnssir", self.station, str(self.a.year), str(doy), "-fr", "1"], (bennett,))

    def day(self, doy: int) -> None:
        stem = f"{self.station}_{self.a.year}_{doy:03d}"
        apc = self.apc(doy); self.gnss_branches(doy)
        planar = INTERMEDIATE/"gnssir"/f"{doy:03d}_noatm_peng.txt"
        self.run(f"planar RH DOY {doy:03d}", self.script("GNSS-IR.py", self.station, self.a.year, doy), (planar,))
        vmf_input = INTERMEDIATE/"VMF3_input_files"/f"{stem}_VMF3_input.txt"
        self.run(f"VMF3 input DOY {doy:03d}", self.script("input_table_for_VMF3.py", self.station,
            self.a.year, doy, "--apc-file", apc), (vmf_input,))
        # VMF3_download checks and reuses each individual grid/orography file.
        if self.a.offline:
            print(f"[cached] VMF3 grids DOY {doy:03d} (offline)")
        else:
            self.run(f"VMF3 grids DOY {doy:03d}", self.script("VMF3_download.py", vmf_input,
                "--timeout", self.a.timeout), always=True)
        vmf_output = INTERMEDIATE/"VMF3_output"/f"{stem}_trop_corrected.txt"
        matlab_expr = f"matlab_corrTrop_GNSSir('{self.station}',{self.a.year},{doy})"
        self.run(f"VMF3 correction DOY {doy:03d}", ["matlab", "-batch", matlab_expr], (vmf_output,))
        igs = INTERMEDIATE/"gnssir_surface_IGS20"/f"{stem}_gnssir_surface_IGS20.txt"
        self.run(f"IGS20 surface DOY {doy:03d}", self.script("gnssir_surface_IGS20.py", self.station,
            self.a.year, doy, "--apc-file", apc), (igs,))
        navd = INTERMEDIATE/"gnssir_surface_NAVD88"/f"{stem}_gnssir_surface_NAVD88.txt"
        self.run(f"NAVD88 surface DOY {doy:03d}", self.script("gnssir_surface_NAVD88.py", self.station,
            self.a.year, doy, "--timeout", self.a.timeout), (navd,))
        gauge = INTERMEDIATE/"water_gauge"/f"{stem}_water_gauge.txt"
        self.run(f"USACE gauge DOY {doy:03d}", self.script("water_gauge.py", self.station,
            self.a.year, doy, "--timeout", self.a.timeout), (gauge,))
        gauge_navd = INTERMEDIATE/"water_gauge_NAVD88"/f"{stem}_water_gauge_NAVD88.txt"
        self.run(f"gauge NAVD88 DOY {doy:03d}", self.script("water_gauge_NAVD88.py", self.station,
            self.a.year, doy), (gauge_navd,))

    def execute(self) -> None:
        self.ensure_tools(); self.ensure_bennett_config()
        noatm = self.refl_code/"input"/self.station/"noatm"/f"{self.station}.json"
        self.run("create noatm configuration", self.script("make_noatm_jason.py", self.station), (noatm,))
        self.pco()
        for doy in range(self.a.start_doy, self.a.end_doy+1):
            print(f"\n=== {self.station.upper()} {self.a.year} DOY {doy:03d} ===")
            self.day(doy)
        self.run("VMF3/Bennett absolute comparison", self.script("compare_vmf3_bennett_navd88.py",
            self.station, self.a.year, self.a.start_doy, self.a.end_doy,
            "--refl-code", self.refl_code, "--data-dir", INTERMEDIATE, "--results-dir", HERE / "results"), always=True)
        print("\nWorkflow complete.")


def main() -> int:
    args = parser().parse_args()
    if args.command == "flow": show_flow(); return 0
    try: Pipeline(args).execute()
    except (OSError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__": raise SystemExit(main())
