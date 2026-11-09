#!/usr/bin/env python3
"""Build per-arc IGS20 surface heights from APC, GNSS-IR, and VMF3 results."""
from __future__ import annotations
import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent

@dataclass(frozen=True)
class Apc:
    station: str; year: int; doy: int
    h_marker: float; marker_to_arp: float; h_arp: float
    pco_up: float; h_apc: float
    antenna: str; radome: str; tenv3: str; pco_source: str

@dataclass(frozen=True)
class Gnssir:
    time: datetime; rh: float; sat: int; freq: int; utc: float; azimuth: float


def data_lines(path: Path):
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            text = line.strip()
            if text and not text.startswith(("%", "#")):
                yield number, text


def compact_time(stamp: str) -> datetime:
    if len(stamp) != 14 or not stamp.isdigit():
        raise ValueError(f"Invalid timestamp: {stamp}")
    base = datetime.strptime(stamp[:12], "%Y%m%d%H%M")
    second = int(stamp[12:])
    if second > 60:
        raise ValueError(f"Invalid timestamp: {stamp}")
    return base + timedelta(seconds=second)


def read_apc(path: Path, station: str, year: int, doy: int) -> Apc:
    values = {}
    for _, line in data_lines(path):
        if ":" in line:
            key, value = line.split(":", 1); values[key.strip()] = value.strip()
    record_year, record_doy = map(int, values["date"].split())
    result = Apc(values["station"], record_year, record_doy,
        float(values["h_marker_m"]), float(values["marker_to_ARP_up_m"]),
        float(values["h_ARP_m"]), float(values["PCO_UP_m"]), float(values["h_APC_m"]),
        values.get("antenna_type", "unknown"), values.get("radome", "unknown"),
        values.get("tenv3_source", "unknown"), values.get("pco_source", "unknown"))
    if (result.station.upper(), result.year, result.doy) != (station.upper(), year, doy):
        raise ValueError("The APC record does not match station/year/doy")
    if abs(result.h_arp-result.h_marker-result.marker_to_arp) > 5e-5:
        raise ValueError("h_ARP identity failed")
    if abs(result.h_apc-result.h_arp-result.pco_up) > 5e-5:
        raise ValueError("h_APC identity failed")
    return result


def read_gnssir(path: Path) -> list[Gnssir]:
    rows = []
    for number, line in data_lines(path):
        f = line.split()
        if len(f) < 17: raise ValueError(f"{path}:{number}: expected 17 columns")
        year, doy, utc = int(f[0]), int(f[1]), float(f[4])
        rows.append(Gnssir(datetime(year,1,1)+timedelta(days=doy-1,hours=utc),
                           float(f[2]), int(f[3]), int(f[10]), utc, float(f[5])))
    return rows


def read_vmf3(path: Path):
    rows = []
    for number, line in data_lines(path):
        f = line.split()
        if len(f) < 11: raise ValueError(f"{path}:{number}: expected 11 columns")
        rows.append((compact_time(f[0]), float(f[7]), float(f[8]), float(f[10])))
    return rows


def match(rows: list[Gnssir], when: datetime) -> Gnssir:
    row = min(rows, key=lambda x: abs((x.time-when).total_seconds()))
    if abs((row.time-when).total_seconds()) > 120:
        raise ValueError(f"No GNSS-IR row within 120 seconds of {when}")
    return row


def header(apc_file: Path, gnss_file: Path, vmf_file: Path, apc: Apc) -> str:
    return f"""% Per-arc IGS20 ellipsoidal surface heights (planar and VMF3)
% APC geometry: {apc_file.resolve()}
% initial planar GNSS-IR RH: {gnss_file.resolve()}
% VMF3 result: {vmf_file.resolve()}
% marker coordinate: {apc.tenv3}
% antenna PCO: {apc.pco_source}
% h_ARP=h_marker+d_marker_ARP; h_APC=h_ARP+PCO_UP
% h_surface_no_VMF3=h_APC-RH_initial
% VMF3_surface_bias=dH; VMF3_RH_bias=-dH
% RH_VMF3=RH_initial-dH; h_surface_VMF3=h_APC-RH_VMF3
% (1)date_time (2)arc_file (3)satellite (4)frequency (5)UTC_hour (6)azimuth_deg
% (7)PCO_UP_m (8)h_marker_m (9)d_marker_ARP_m (10)h_ARP_m (11)h_APC_m
% (12)RH_initial_m (13)VMF3_surface_bias_m_signed (14)VMF3_RH_bias_m_signed
% (15)h_surface_no_VMF3_m (16)RH_VMF3_m (17)h_surface_VMF3_m
"""


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__); p.add_argument("station")
    p.add_argument("year", type=int); p.add_argument("doy", type=int)
    p.add_argument("--apc-file", type=Path); p.add_argument("--gnssir-file", type=Path)
    p.add_argument("--vmf3-file", type=Path); p.add_argument("--output", type=Path)
    a = p.parse_args(); stem = f"{a.station.lower()}_{a.year}_{a.doy:03d}"
    apc_file = a.apc_file or HERE/"intermediate_files/PCO_APC"/f"{stem}_APC_geodetic_height.txt"
    gnss_file = a.gnssir_file or HERE/"intermediate_files/gnssir"/f"{a.doy:03d}_noatm_peng.txt"
    vmf_file = a.vmf3_file or HERE/"intermediate_files/VMF3_output"/f"{stem}_trop_corrected.txt"
    output = a.output or HERE/"intermediate_files/gnssir_surface_IGS20"/f"{stem}_gnssir_surface_IGS20.txt"
    apc = read_apc(apc_file, a.station, a.year, a.doy); gnss = read_gnssir(gnss_file)
    vmf_rows = read_vmf3(vmf_file); lines = [header(apc_file, gnss_file, vmf_file, apc)]
    for when, d_h, surface_vmf3, rh_vmf3 in vmf_rows:
        initial = match(gnss, when); surface_no = apc.h_apc-initial.rh
        if abs(initial.rh-d_h-rh_vmf3)>5e-4 or abs(surface_no+d_h-surface_vmf3)>5e-4:
            raise ValueError(f"{when}: VMF3 consistency check failed")
        arc = f"sat{initial.sat:03d}_L{initial.freq}_G_az{round(initial.azimuth):03d}.txt"
        lines.append(f"{when:%Y-%m-%dT%H:%M:%S} {arc} {initial.sat:3d} {initial.freq:3d} "
          f"{initial.utc:9.5f} {initial.azimuth:9.3f} {apc.pco_up:11.6f} {apc.h_marker:12.6f} "
          f"{apc.marker_to_arp:+12.6f} {apc.h_arp:12.6f} {apc.h_apc:12.6f} {initial.rh:12.6f} "
          f"{d_h:+12.6f} {-d_h:+12.6f} {surface_no:12.6f} {rh_vmf3:12.6f} {surface_vmf3:12.6f}\n")
    output.parent.mkdir(parents=True, exist_ok=True); output.write_text("".join(lines), encoding="utf-8")
    print(f"Wrote {len(vmf_rows)} arcs to {output}")

if __name__ == "__main__": main()
