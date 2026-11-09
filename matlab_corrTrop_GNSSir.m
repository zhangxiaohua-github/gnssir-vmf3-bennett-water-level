function output_file = matlab_corrTrop_GNSSir(station, year_value, doy)
%MATLAB_CORRTROP_GNSSIR Apply VMF3 corrections to one station-day.
%
% Usage:
%   matlab_corrTrop_GNSSir('islk', 2024, 1)
%
% The function automatically reads the station-day input table, downloaded
% VMF3 files, and station coordinates from the gnssrefl noatm JSON file.
% It writes a station-day tropospheric-correction result in this directory.
%
% =========================================================================
% GNSS-IR VMF3 TROPOSPHERIC CORRECTION
% =========================================================================
%
% ================== Correction Model Description ========================
%
% Q1. What is VMF3, and who developed it?
% A1. VMF3 (Vienna Mapping Functions 3) is a tropospheric mapping-function
%     model developed by researchers at TU Wien (Vienna University of
%     Technology). It provides parameters for estimating atmospheric
%     signal delays at a specified location, time, height, and elevation
%     angle.
%
% Q2. Is VMF3 fixed, or does it change over time?
% A2. The VMF3 grid data change with atmospheric conditions. Grid files
%     are provided at 00:00, 06:00, 12:00, and 18:00 UTC. vmf3_grid.m
%     interpolates between the two surrounding grid epochs to obtain
%     values at the time specified for each GNSS-IR arc.
%
% Q6. How is the tropospheric correction for a satellite arc calculated?
% A6. The tropospheric correction dH is calculated as:
%
%         dH = [A(e1) - A(e0)] / [sin(e1) - sin(e0)].
%
%     Here, A(e) is the tropospheric delay difference between surface and antenna at elevation angle
%     e; e0 and e1 are the elevation angles at the two ends of the arc.
%
% Q5. How are A(e1) and A(e0) calculated?
% A5. At each elevation angle, the script calculates:
%
%         A(e) = L(h_surface,e) - L(h_antenna,e).
%
%     Here, L(h_surface,e) and L(h_antenna,e) are the tropospheric delays
%     evaluated at the surface and antenna heights, respectively.
%
% Q4. What is L(h,e)?
% A4. L(h,e) is the modeled slant tropospheric delay, in meters, at
%     ellipsoidal height h and satellite elevation angle e:
%
%         L(h,e) = ZHD(h)*mfh(h,e) + ZWD(h)*mfw(h,e).
%
%     A single L(h,e) represents the delay at one height and one
%     elevation angle. It is not the delay difference between the
%     antenna and the water surface.
%
% Q3. How are the four parameters above obtained?
% A3. They are obtained from the VMF3 data through vmf3_grid.m:
%
%         ZHD: zenith hydrostatic delay, in meters.
%         ZWD: zenith wet delay, in meters.
%         mfh: hydrostatic mapping factor, dimensionless.
%         mfw: wet mapping factor, dimensionless.
%
%     "Zenith" refers to the vertical direction. The mapping factors
%     convert zenith delays to delays at the specified satellite
%     elevation angle.
%
% Q7. What does the script output?
% A7. It outputs the troposphere-corrected surface ellipsoidal height:
%
%         h_surface_corrected = h_surface_input + dH.
%
%     Input column 13 is interpreted as the uncorrected reflecting-
%     surface ellipsoidal height. Both h_surface_corrected and
%     h_surface_input are ellipsoidal heights.
%
%     The corresponding corrected reflector height is:
%
%         H_corrected = H_LSP - dH.
%
% Q8. When should this script be run?
% A8. Run it after GNSS-IR has estimated H_LSP for each accepted arc.
%     Convert H_LSP to an uncorrected surface ellipsoidal height before
%     preparing the input table HH_forTropCorr1.txt. Column 13 of that
%     table contains the uncorrected surface ellipsoidal height.
%
% Q9. Is HH_forTropCorr1.txt a standard gnssrefl output file?
% A9. No. It is a custom intermediate input table for this script.
%     A standard gnssrefl/gnssir daily results file cannot simply be
%     renamed because its column order and quantities differ. Convert
%     the accepted per-arc gnssir results to the format below.
%
% Q10. What columns must HH_forTropCorr1.txt contain?
% A10. It must be a numeric, whitespace-separated file with one arc per
%      row and at least 15 columns:
%
%        1-6 : year, month, day, hour, minute, second;
%          7 : first endpoint elevation angle e0, in degrees;
%          8 : second endpoint elevation angle e1, in degrees;
%       9-12 : fields copied to the output; confirm their meanings
%              against the upstream workflow;
%         13 : uncorrected surface ellipsoidal height, in meters;
%         14 : antenna ellipsoidal height, in meters;
%         15 : field copied to the output; confirm its meaning
%              against the upstream workflow.
%
%      Columns 13 and 14 must use the same terrestrial reference frame,
%      ellipsoid, and antenna reference-point convention. In particular,
%      account consistently for marker-to-antenna phase-center offsets.
%      The two endpoint elevation angles must be different.
%
% Q11. What time scale should be used?
% A11. VMF3 grid epochs are in UTC. Confirm the time scale of the
%      upstream GNSS-IR results before preparing columns 1-6. If those
%      results are in GPS time, convert them to UTC before selecting or
%      interpolating the VMF3 grid data. This script uses one time for
%      the entire arc: e0 and e1 are evaluated at the same time, rather
%      than at separate endpoint times.
%
% Q12. What external VMF3 files are required?
% A12. With grid_res = 1, vmf3_grid.m requires:
%
%      (a) VMF3 1-degree x 1-degree grid files for the two 6-hour epochs
%          surrounding each arc time, stored as:
%
%          dir_VMF3_grid/YYYY/VMF3_YYYYMMDD.H00
%          dir_VMF3_grid/YYYY/VMF3_YYYYMMDD.H06
%          dir_VMF3_grid/YYYY/VMF3_YYYYMMDD.H12
%          dir_VMF3_grid/YYYY/VMF3_YYYYMMDD.H18
%
%      (b) The auxiliary file:
%
%          dir_orography/orography_ell_1x1
%
%      The two surrounding files may fall on different days or years.
%      VMF3 products: https://vmf.geo.tuwien.ac.at/products.html
%
% Q14. Which settings must be changed for a new station?
% A14. No source-code paths need to be changed. Pass station, year, and DOY
%      to this function. Coordinates are read from the station noatm JSON
%      below REFL_CODE, and data paths are derived from this file location.
%
% output
% 1  时间 YYYYMMDDhhmmss
% 2  起始高度角 e0
% 3  结束高度角 e1
% 4-7  输入文件第 9-12 列
% 8  VMF3 高度校正量 dH，单位 m
% 9  校正后的反射面椭球高，单位 m
% 10 输入文件第 15 列
% 11 VMF3 校正后的反射高度 RH，单位 m
%
% =========================================================================

% VMF3 products can be found at https://vmf.geo.tuwien.ac.at/products.html
validateattributes(station, {'char', 'string'}, {'scalartext'}, mfilename, 'station');
validateattributes(year_value, {'numeric'}, ...
    {'scalar', 'integer', '>=', 1900}, mfilename, 'year_value');
validateattributes(doy, {'numeric'}, ...
    {'scalar', 'integer', '>=', 1, '<=', 366}, mfilename, 'doy');

station = lower(char(station));
date_value = datetime(year_value, 1, 1) + days(doy - 1);
if year(date_value) ~= year_value
    error('DOY %d is invalid for year %d.', doy, year_value);
end

script_dir = fileparts(mfilename('fullpath'));
doy_text = sprintf('%03d', doy);
file_prefix = sprintf('%s_%04d_%s', station, year_value, doy_text);
fileHr = fullfile(script_dir, 'intermediate_files', ...
    'VMF3_input_files', [file_prefix '_VMF3_input.txt']);
output_dir = fullfile(script_dir, 'intermediate_files', 'VMF3_output');
output_file = fullfile(output_dir, [file_prefix '_trop_corrected.txt']);
dir_VMF3_grid = fullfile(script_dir, 'intermediate_files', 'VMF3_grid');
dir_orography = fullfile(script_dir, 'intermediate_files', 'orography');
orography_file = fullfile(dir_orography, 'orography_ell_1x1');
if ~isfolder(output_dir)
    mkdir(output_dir);
end

assert_file_exists(fileHr, 'VMF3 input table');
assert_folder_exists(dir_VMF3_grid, 'VMF3 grid directory');
assert_file_exists(orography_file, 'VMF3 orography file');
[lat_deg, lon_deg, station_json] = read_station_coordinates(station);

addpath(script_dir);
lat_rad = deg2rad(lat_deg);
lon_rad = deg2rad(lon_deg);
grid_res = 1;
VMF3_grid_file = [];

Hr = load(fileHr);
[nobs, ncol] = size(Hr);
if nobs == 0 || ncol < 15
    error('Input table must contain at least one row and 15 numeric columns: %s', fileHr);
end

row_dates = datetime(Hr(:,1), Hr(:,2), Hr(:,3), Hr(:,4), Hr(:,5), Hr(:,6));
row_doy = day(row_dates, 'dayofyear');
if any(year(row_dates) ~= year_value | row_doy ~= doy)
    error('Input table contains epochs outside requested year %d and DOY %03d.', ...
        year_value, doy);
end
if any(abs(sind(Hr(:,8)) - sind(Hr(:,7))) < eps)
    error('Every arc must have two different endpoint elevation angles.');
end

fprintf('Station: %s (latitude %.9f deg, longitude %.9f deg)\n', ...
    upper(station), lat_deg, lon_deg);
fprintf('Station metadata: %s\n', station_json);
fprintf('Input table: %s\n', fileHr);
fprintf('VMF3 grid directory: %s\n', dir_VMF3_grid);
fprintf('Orography directory: %s\n', dir_orography);
fprintf('Output file: %s\n', output_file);

[fid, message] = fopen(output_file, 'wt');
if fid == -1
    error('Cannot open output file %s: %s', output_file, message);
end
file_guard = onCleanup(@() fclose(fid));

for ih = 1:nobs
    mjd = mjuliandate([Hr(ih,1), Hr(ih,2), Hr(ih,3), ...
        Hr(ih,4), Hr(ih,5), Hr(ih,6)]);
    gps_h = Hr(ih,14);
    ir_h = Hr(ih,13);
    ir_zd_rad0 = deg2rad(90.0 - Hr(ih,7));
    ir_zd_rad1 = deg2rad(90.0 - Hr(ih,8));

    [mfh_g0, mfw_g0, zhd_m_g0, zwd_m_g0, VMF3_grid_file] = ...
        vmf3_grid(dir_VMF3_grid, dir_orography, VMF3_grid_file, ...
        mjd, lat_rad, lon_rad, gps_h, ir_zd_rad0, grid_res);
    [mfh_ir0, mfw_ir0, zhd_m_ir0, zwd_m_ir0, VMF3_grid_file] = ...
        vmf3_grid(dir_VMF3_grid, dir_orography, VMF3_grid_file, ...
        mjd, lat_rad, lon_rad, ir_h, ir_zd_rad0, grid_res);
    dL_g0 = zhd_m_g0*mfh_g0 + zwd_m_g0*mfw_g0;
    dL_ir0 = zhd_m_ir0*mfh_ir0 + zwd_m_ir0*mfw_ir0;
    delta_dL0 = 2.0*(dL_ir0 - dL_g0);

    [mfh_g1, mfw_g1, zhd_m_g1, zwd_m_g1, VMF3_grid_file] = ...
        vmf3_grid(dir_VMF3_grid, dir_orography, VMF3_grid_file, ...
        mjd, lat_rad, lon_rad, gps_h, ir_zd_rad1, grid_res);
    [mfh_ir1, mfw_ir1, zhd_m_ir1, zwd_m_ir1, VMF3_grid_file] = ...
        vmf3_grid(dir_VMF3_grid, dir_orography, VMF3_grid_file, ...
        mjd, lat_rad, lon_rad, ir_h, ir_zd_rad1, grid_res);
    dL_g1 = zhd_m_g1*mfh_g1 + zwd_m_g1*mfw_g1;
    dL_ir1 = zhd_m_ir1*mfh_ir1 + zwd_m_ir1*mfw_ir1;
    delta_dL1 = 2.0*(dL_ir1 - dL_g1);

    dH = 0.5*(delta_dL1 - delta_dL0) / ...
        (sind(Hr(ih,8)) - sind(Hr(ih,7)));
    surface_height_vmf3 = Hr(ih,13) + dH;
    rh_vmf3 = Hr(ih,14) - surface_height_vmf3;

    ti_str = int64(10000000000*Hr(ih,1) + 100000000*Hr(ih,2) + ...
        1000000*Hr(ih,3) + 10000*Hr(ih,4) + 100*Hr(ih,5) + Hr(ih,6));
    fprintf(fid, ...
        '%14d %5.2f %5.2f %5.2f %6.2f %6.2f %6.2f %7.4f %10.4f %6.2f %10.4f\n', ...
        ti_str, Hr(ih,7), Hr(ih,8), Hr(ih,9), Hr(ih,10), Hr(ih,11), ...
        Hr(ih,12), dH, surface_height_vmf3, Hr(ih,15), rh_vmf3);
end

clear file_guard;
fprintf('Completed VMF3 correction for %d arc(s).\n', nobs);
end


function [lat_deg, lon_deg, station_json] = read_station_coordinates(station)
reflection_root = getenv('REFL_CODE');
if isempty(reflection_root)
    error(['REFL_CODE is not set. It must point to the gnssrefl reflection_data ' ...
        'directory so station coordinates can be read.']);
end

station_json = fullfile(reflection_root, 'input', station, 'noatm', ...
    [station '.json']);
assert_file_exists(station_json, 'gnssrefl station JSON');
metadata = jsondecode(fileread(station_json));
if ~isfield(metadata, 'lat') || ~isfield(metadata, 'lon')
    error('Station JSON must contain numeric lat and lon fields: %s', station_json);
end
lat_deg = metadata.lat;
lon_deg = metadata.lon;
validateattributes(lat_deg, {'numeric'}, ...
    {'scalar', 'finite', '>=', -90, '<=', 90});
validateattributes(lon_deg, {'numeric'}, ...
    {'scalar', 'finite', '>=', -180, '<=', 360});
end


function assert_file_exists(path_value, description)
if ~isfile(path_value)
    error('%s does not exist: %s', description, path_value);
end
end


function assert_folder_exists(path_value, description)
if ~isfolder(path_value)
    error('%s does not exist: %s', description, path_value);
end
end
