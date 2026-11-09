# Portfolio and resume copy

Use the text below after replacing `<REPOSITORY_URL>` with the published GitHub URL.

## GitHub repository description

End-to-end GPS L1 GNSS-IR workflow comparing VMF3 and Bennett atmospheric corrections against USACE water-gauge elevations in the NAVD88 absolute vertical reference frame.

Suggested repository name: `gnssir-vmf3-bennett-water-level`

Suggested GitHub topics:

`gnss-ir`, `geodesy`, `remote-sensing`, `gnss`, `vmf3`, `water-level`, `navd88`, `matlab`, `python`

## Resume — concise version

- Developed a reproducible Python/MATLAB GNSS-IR pipeline that integrates antenna phase-center correction, VMF3 and Bennett atmospheric treatments, IGS20-to-NAVD88 transformation, and USACE gauge validation.
- Evaluated GPS L1 water-surface estimates at ISLK over 23 days; Bennett reduced RMSE from 0.475 m (VMF3) to 0.227 m and MAE from 0.403 m to 0.173 m for the accepted result sets.
- Built a one-command workflow with explicit data provenance, cached-stage recovery, automated absolute-error statistics, and publication-ready figures.

## Resume — one-line version

Built an end-to-end GNSS-IR water-level validation pipeline comparing VMF3 and Bennett corrections in NAVD88; Bennett achieved 0.227 m RMSE versus 0.475 m for VMF3 in the ISLK case study.

## Website project card

### GNSS-IR Water-Level Validation: VMF3 vs. Bennett

A Python/MATLAB geodetic workflow that converts GPS L1 multipath observations into absolute NAVD88 water-surface elevations. The project combines antenna PCO/APC correction, VMF3 and Bennett atmospheric treatments, NOAA VDatum transformations, and USACE gauge validation. For the ISLK 2024 case study, Bennett produced lower MAE and RMSE than VMF3 for the accepted result sets.

**Technologies:** Python, MATLAB, gnssrefl, GNSS-IR, VMF3, NOAA VDatum, GEOID18, USACE CWMS

**Repository:** `<REPOSITORY_URL>`

## 中文项目简介

构建了一套可复现的 GPS L1 GNSS-IR 水位反演流程，集成天线 PCO/APC 校正、VMF3 与 Bennett 大气折射处理、IGS20 至 NAVD88 高程框架转换，以及 USACE 水尺数据验证。在 ISLK 2024 年 DOY 183–205 案例中，Bennett 结果的 MAE/RMSE 为 0.173/0.227 m，低于 VMF3 的 0.403/0.475 m。项目提供一键式命令行工作流、逐弧绝对误差表和可直接用于论文或网站的结果图。

## Accuracy note for public descriptions

The VMF3 and Bennett branches contain different accepted arc counts (64 and 73). Public text should say “for the accepted result sets” and should not describe the statistics as a strictly paired identical-arc test.
