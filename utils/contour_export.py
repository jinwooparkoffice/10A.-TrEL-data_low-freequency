"""TrEL CSV를 contour plot용 와이드 엑셀로 정리한다.

예시 형식 (시트 TrEL_Consolidated):
    Time (us) | Normalized intensity (rise) | ...
    <빈 칸>   | 측정 분                      | ...
    <빈 칸>   | 상대 휘도 (%)                | ...
    0         | intensity                    | ...
"""
import os
import re

import numpy as np
import pandas as pd
import openpyxl

from utils.local_files import read_text, safe_child, scan_analysis
from utils.master_processor import parse_vil_processed
from utils.trel_common import parse_minutes_from_filename, parse_trel_csv_frame
from utils.vil_processor import parse_duty_from_filename


def _as_percent(values):
    arr = np.asarray(values, dtype=float)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return arr
    if float(np.nanmax(finite)) <= 2.0:
        return arr * 100.0
    return arr


def _load_vil(path):
    """VIL processed CSV/XLSX에서 시간, 상대휘도(%), shift, duty를 읽는다."""
    lower = path.lower()
    shift_min = 0.0
    duty = parse_duty_from_filename(os.path.basename(path)) or 1.0

    if lower.endswith('.xlsx'):
        xl = pd.ExcelFile(path)
        if 'Metadata' in xl.sheet_names:
            meta = pd.read_excel(xl, sheet_name='Metadata')
            if len(meta):
                row = meta.iloc[0]
                if 'time_shift_min' in meta.columns and pd.notna(row.get('time_shift_min')):
                    shift_min = float(row['time_shift_min'])
                elif 'time_shift_s' in meta.columns and pd.notna(row.get('time_shift_s')):
                    shift_min = float(row['time_shift_s']) / 60.0
                if 'duty_fraction' in meta.columns and pd.notna(row.get('duty_fraction')):
                    duty = float(row['duty_fraction']) or duty
        sheet = 'Data' if 'Data' in xl.sheet_names else xl.sheet_names[0]
        df = pd.read_excel(xl, sheet_name=sheet)
        df.columns = df.columns.astype(str).str.strip()
        time_col = next((c for c in df.columns if 'Time (min)' in c), df.columns[0])
        lum_col = next((c for c in df.columns if 'Relative luminance' in c or 'Luminance' in c), None)
        if lum_col is None and len(df.columns) >= 2:
            lum_col = df.columns[1]
        if lum_col is None:
            raise ValueError('VIL 파일에서 상대 휘도 열을 찾지 못했습니다.')
        t_min = pd.to_numeric(df[time_col], errors='coerce').to_numpy(dtype=float)
        lum = pd.to_numeric(df[lum_col], errors='coerce').to_numpy(dtype=float)
    else:
        text = read_text(path)
        for line in text.splitlines():
            if not line.startswith('#'):
                break
            match = re.search(r'time_shift_s:\s*([\d.eE+-]+)', line, re.IGNORECASE)
            if match:
                shift_min = float(match.group(1)) / 60.0
            duty_match = re.search(r'duty_fraction:\s*([\d.eE+-]+)', line, re.IGNORECASE)
            if duty_match:
                duty = float(duty_match.group(1)) or duty
        frame = parse_vil_processed(text)
        if frame.empty:
            raise ValueError('VIL CSV를 읽지 못했습니다.')
        t_min = frame['Time (min)'].to_numpy(dtype=float)
        lum = frame['Relative luminance (a.u.)'].to_numpy(dtype=float)

    mask = np.isfinite(t_min) & np.isfinite(lum)
    t_min = t_min[mask]
    lum = _as_percent(lum[mask])
    order = np.argsort(t_min)
    if len(order) < 2:
        raise ValueError('VIL 데이터가 부족합니다.')
    return {
        'time_min': t_min[order],
        'luminance_pct': lum[order],
        'shift_min': shift_min,
        'duty': duty if duty and duty > 0 else 1.0,
        'name': os.path.basename(path),
    }


def _luminance_at_minute(vil, minute):
    t_vil = (float(minute) - vil['shift_min']) * vil['duty']
    return float(np.interp(t_vil, vil['time_min'], vil['luminance_pct']))


def _select_traces(traces, lum_high, lum_low):
    """측정 시간 순으로, 상한(피크가 더 높으면 피크)부터 하한 직후까지 고른다."""
    ordered = sorted(traces, key=lambda item: (item['minutes'], item['name']))
    luminances = [item['luminance'] for item in ordered if item['luminance'] is not None]
    if not luminances:
        return ordered

    upper = max(float(lum_high), max(luminances))
    chosen = []
    crossed_floor = False
    for item in ordered:
        lum = item['luminance']
        if lum is None or lum > upper + 1e-6:
            continue
        if lum + 1e-9 >= lum_low:
            if crossed_floor:
                continue
            chosen.append(item)
            continue
        if not crossed_floor:
            chosen.append(item)
            crossed_floor = True
    return chosen


def _resample(time_us, values, grid):
    mask = np.isfinite(time_us) & np.isfinite(values)
    time_us = np.asarray(time_us, dtype=float)[mask]
    values = np.asarray(values, dtype=float)[mask]
    out = np.full(grid.shape, np.nan)
    if time_us.size < 2:
        return out
    order = np.argsort(time_us)
    time_us = time_us[order]
    values = values[order]
    unique_time, unique_index = np.unique(time_us, return_index=True)
    values = values[unique_index]
    inside = (grid >= unique_time[0]) & (grid <= unique_time[-1])
    out[inside] = np.interp(grid[inside], unique_time, values)
    return out


def _write_workbook(path, signal_name, grid, columns):
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet(title='TrEL_Consolidated')
    ws.append(['Time (us)', *([signal_name] * len(columns))])
    ws.append([None, *[item['minutes'] for item in columns]])
    ws.append([None, *[item['luminance'] for item in columns]])
    for row_index, time_us in enumerate(grid):
        ws.append([float(time_us), *[
            None if not np.isfinite(item['values'][row_index]) else float(item['values'][row_index])
            for item in columns
        ]])
    wb.save(path)


def export_contour_workbooks(root, output_dir=None, lum_high=100.0, lum_low=50.0, time_start_us=0.0):
    scanned = scan_analysis(root)
    trel_entries = [
        entry for entry in scanned['analysisFiles']
        if 'VIL' not in entry['name'].upper() and entry['name'].lower().endswith('.csv')
    ]
    if not trel_entries:
        raise ValueError('TrEL CSV가 없습니다. _TrEL.csv가 있는 폴더를 선택해주세요.')

    vil = None
    vil_error = None
    if scanned['analysisVilFiles']:
        vil_entry = sorted(scanned['analysisVilFiles'], key=lambda item: item['relPath'])[0]
        try:
            vil = _load_vil(safe_child(root, vil_entry['relPath']))
        except Exception as exc:
            vil_error = str(exc)

    traces = []
    skipped = []
    for entry in trel_entries:
        minutes = parse_minutes_from_filename(entry['name'])
        if minutes is None:
            skipped.append(entry['name'])
            continue
        frame = parse_trel_csv_frame(read_text(safe_child(root, entry['relPath'])))
        if frame.empty:
            skipped.append(entry['name'])
            continue
        time_us = frame['Time (μs)'].to_numpy(dtype=float)
        keep = time_us >= float(time_start_us)
        if not np.any(keep):
            skipped.append(entry['name'])
            continue
        luminance = _luminance_at_minute(vil, minutes) if vil else None
        trace = {
            'name': entry['name'],
            'relPath': entry['relPath'],
            'minutes': float(minutes),
            'luminance': luminance,
            'time_us': time_us[keep],
        }
        if 'Normalized intensity (rise)' in frame.columns:
            trace['rise'] = frame['Normalized intensity (rise)'].to_numpy(dtype=float)[keep]
            trace['decay'] = frame['Normalized intensity (decay)'].to_numpy(dtype=float)[keep]
        else:
            trace['rise'] = frame['Normalized intensity (a.u.)'].to_numpy(dtype=float)[keep]
            trace['decay'] = None
        traces.append(trace)

    if not traces:
        raise ValueError('측정 시간(분)을 읽을 수 있는 TrEL CSV가 없습니다. 파일명에 1min 같은 시간이 있어야 합니다.')

    selected = _select_traces(traces, float(lum_high), float(lum_low))
    reference = max(selected, key=lambda item: item['time_us'].size)
    grid = reference['time_us']

    rise_columns = []
    decay_columns = []
    for item in selected:
        rise_columns.append({
            'minutes': item['minutes'],
            'luminance': item['luminance'],
            'values': _resample(item['time_us'], item['rise'], grid),
        })
        if item['decay'] is not None:
            decay_columns.append({
                'minutes': item['minutes'],
                'luminance': item['luminance'],
                'values': _resample(item['time_us'], item['decay'], grid),
            })

    destination = output_dir or root
    os.makedirs(destination, exist_ok=True)
    if vil and selected and selected[0]['luminance'] is not None:
        high_label = int(round(float(lum_high)))
        low_label = int(round(float(lum_low)))
        prefix = f'{high_label}-{low_label}'
    else:
        prefix = 'TrEL_contour'

    outputs = []
    rise_name = f'{prefix} rise.xlsx'
    rise_path = os.path.join(destination, rise_name)
    _write_workbook(rise_path, 'Normalized intensity (rise)', grid, rise_columns)
    outputs.append({'kind': 'rise', 'filename': rise_name, 'path': rise_path})

    if decay_columns:
        decay_name = f'{prefix} decay.xlsx'
        decay_path = os.path.join(destination, decay_name)
        _write_workbook(decay_path, 'Normalized intensity (decay)', grid, decay_columns)
        outputs.append({'kind': 'decay', 'filename': decay_name, 'path': decay_path})

    lum_values = [item['luminance'] for item in selected if item['luminance'] is not None]
    return {
        'files': outputs,
        'n_traces': len(selected),
        'n_source': len(traces),
        'skipped': skipped,
        'minutes': [item['minutes'] for item in selected],
        'luminance_min': min(lum_values) if lum_values else None,
        'luminance_max': max(lum_values) if lum_values else None,
        'time_start_us': float(grid[0]) if len(grid) else None,
        'time_end_us': float(grid[-1]) if len(grid) else None,
        'n_times': int(len(grid)),
        'vil_file': vil['name'] if vil else None,
        'vil_error': vil_error,
        'prefix': prefix,
    }
