"""
TrEL 마스터 파일 생성 (VIL 기반)
1. VIL luminance smoothing + 재정규화 (max=100%)
2. 목표 current 95% 이후 데이터만 사용
3. 목표 luminance % 시점 보간 → time_shift 적용 → 가장 가까운 TrEL 선택
4. 동적 파일명: TrEL_Master (0, 40, 50, 60, 70 min).xlsx
"""
import io
import re
from typing import List, Dict, Tuple, Optional
import numpy as np
import pandas as pd
import openpyxl

from utils.trel_common import parse_minutes_from_filename, parse_trel_csv_frame, detect_trel_format
from utils.circuit_config import calculate_r_total, resolve_device_area_mm2
from utils.vil_processor import parse_duty_from_filename


def parse_vil_processed_full(content: str) -> pd.DataFrame:
    """VIL processed CSV 파싱 (Time, Luminance, Current density)."""
    try:
        df = pd.read_csv(io.StringIO(content), comment='#')
        df.columns = df.columns.str.strip()

        time_col = next((c for c in df.columns if 'Time (min)' in c), None)
        rel_lum_col = next((c for c in df.columns if 'Relative luminance' in c), None)
        curr_col = next((c for c in df.columns if 'Current density' in c), None)

        if time_col and rel_lum_col and curr_col:
            res = df[[time_col, rel_lum_col, curr_col]].copy()
            res.columns = ['Time (min)', 'Relative luminance (a.u.)', 'Current density (mA/cm2)']
            return res.dropna()

        if len(df.columns) >= 4:
            res = df.iloc[:, [0, 1, 3]].copy()
            res.columns = ['Time (min)', 'Relative luminance (a.u.)', 'Current density (mA/cm2)']
            return res.dropna()

        if len(df.columns) >= 2:
            res = df.iloc[:, [0, 1]].copy()
            res.columns = ['Time (min)', 'Relative luminance (a.u.)']
            res['Current density (mA/cm2)'] = np.nan
            return res.dropna(subset=['Time (min)', 'Relative luminance (a.u.)'])
    except Exception:
        pass
    return pd.DataFrame()


def _smooth_moving_average(values: np.ndarray, window_size: int) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if len(arr) < 3:
        return arr
    window_size = int(max(3, min(window_size, len(arr))))
    if window_size % 2 == 0:
        window_size += 1
    kernel = np.ones(window_size, dtype=float) / window_size
    return np.convolve(arr, kernel, mode='same')


def prepare_vil_for_master(
    vil_processed_csv: str,
    target_current_ua: Optional[float] = None,
    device_area_mm2: Optional[float] = None,
    r_shunt: Optional[float] = None,
    r_osc: Optional[float] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    마스터 생성용 VIL 전처리:
    1) 목표 전류 95% 이상 구간만 유효
    2) luminance moving average smoothing
    3) 최고 intensity를 1.0(100%)으로 재정규화
    """
    vil_df = parse_vil_processed_full(vil_processed_csv)
    if len(vil_df) < 5:
        raise ValueError("VIL 데이터가 부족합니다. (최소 5개 포인트 필요)")

    t_min = vil_df['Time (min)'].values.astype(float)
    rel_lum = vil_df['Relative luminance (a.u.)'].values.astype(float)
    j_arr = vil_df['Current density (mA/cm2)'].values.astype(float) if 'Current density (mA/cm2)' in vil_df.columns else np.full_like(t_min, np.nan)

    if target_current_ua is not None and target_current_ua > 0:
        area_mm2 = resolve_device_area_mm2(device_area_mm2)
        area_cm2 = area_mm2 * 1e-2
        j_target = (target_current_ua / 1000.0) / area_cm2
        threshold = j_target * 0.95
        valid_mask = np.isfinite(j_arr) & (j_arr >= threshold)
        if np.any(valid_mask):
            first_idx = int(np.argmax(valid_mask))
            t_min = t_min[first_idx:]
            rel_lum = rel_lum[first_idx:]
            j_arr = j_arr[first_idx:]

    if len(t_min) < 5:
        raise ValueError("95% 목표 전류 이후 유효 VIL 데이터가 부족합니다.")

    window = min(11, max(3, (len(rel_lum) // 50) * 2 + 1))
    rel_lum_smooth = _smooth_moving_average(rel_lum, window)
    l_max = float(np.nanmax(rel_lum_smooth))
    if not np.isfinite(l_max) or l_max <= 0:
        l_max = 1e-10
    rel_lum_norm = rel_lum_smooth / l_max

    return t_min, rel_lum_norm


def build_master_filename(selected_minutes: List[float]) -> str:
    """선택된 TrEL 분 목록으로 동적 마스터 파일명 생성."""
    if not selected_minutes:
        return 'TrEL_Master.xlsx'
    labels = ', '.join(str(int(round(m))) for m in sorted(set(selected_minutes)))
    return f'TrEL_Master ({labels} min).xlsx'

def parse_vil_processed(content: str) -> pd.DataFrame:
    """
    VIL 처리된 CSV 파싱 (Pandas Optimized)
    Returns: DataFrame with columns ['Time (min)', 'Relative luminance (a.u.)']
    """
    try:
        # 주석 행 무시하고 읽기
        df = pd.read_csv(io.StringIO(content), comment='#')
        # 컬럼명 공백 제거
        df.columns = df.columns.str.strip()
        
        # 필요한 컬럼만 선택
        time_col = next((c for c in df.columns if 'Time (min)' in c), None)
        rel_lum_col = next((c for c in df.columns if 'Relative luminance' in c), None)
        
        if time_col and rel_lum_col:
            res = df[[time_col, rel_lum_col]].copy()
            res.columns = ['Time (min)', 'Relative luminance (a.u.)'] # Normalize names
            return res.dropna()
            
        # 컬럼명이 다를 경우 인덱스로 접근 (Fallback: 0=Time, 1=Relative luminance)
        if len(df.columns) >= 2:
            res = df.iloc[:, [0, 1]].copy()
            res.columns = ['Time (min)', 'Relative luminance (a.u.)']
            return res.dropna()
            
    except Exception:
        pass
    return pd.DataFrame()


def interpolate_time_at_ratio(t_min: np.ndarray, rel_lum: np.ndarray, target_ratio: float) -> Optional[float]:
    """
    정규화된 VIL luminance에서 목표 비율에 해당하는 시각(분) 보간.
    - 100%: 피크(최대 luminance) 시각
    - 100% 미만: 피크 이후 열화(하강) 구간에서만 하향 교차 탐색
    """
    if len(t_min) < 2 or len(rel_lum) < 2:
        return None

    peak_idx = int(np.argmax(rel_lum))

    if target_ratio >= 0.999:
        return float(t_min[peak_idx])

    for i in range(peak_idx, len(t_min) - 1):
        a, b = rel_lum[i], rel_lum[i + 1]
        ta, tb = t_min[i], t_min[i + 1]
        if a >= target_ratio >= b:
            if abs(b - a) < 1e-12:
                return float(ta)
            frac = (target_ratio - a) / (b - a)
            return float(ta + frac * (tb - ta))
    return None


def vil_duty_time_to_wall_clock_min(
    t_vil_duty_min: float,
    duty_fraction: Optional[float],
    vil_time_shift_min: float = 0.0,
) -> float:
    """
    VIL processed Time(min) (duty 보정) → 실험 시작 기준 wall-clock 분.
    processed Time = (경과 wall-clock 초 / 60) × duty 이므로 역변환: t_wall = shift + t_vil / duty
    """
    duty = duty_fraction if duty_fraction and duty_fraction > 0 else 1.0
    return vil_time_shift_min + (t_vil_duty_min / duty)


def parse_minutes_display(filename: str) -> Optional[str]:
    """표시용: 1min, 1h 2min 등"""
    total_minutes = parse_minutes_from_filename(filename)
    if total_minutes is None:
        return None

    whole_minutes = int(round(total_minutes))
    hours = whole_minutes // 60
    minutes = whole_minutes % 60
    return f"{hours}h {minutes}min" if hours > 0 else f"{minutes}min"


def find_closest_file(files_with_minutes: List[Tuple[str, float]], target_min: float) -> Optional[Tuple[str, float]]:
    """target_min에 가장 가까운 (filename, minutes) 반환"""
    if not files_with_minutes:
        return None
    return min(files_with_minutes, key=lambda x: abs(x[1] - target_min))


def process_master(
    vil_processed_csv: str,
    vil_time_shift_min: float,
    trel_files_data: List[Tuple[str, str]],
    percent_list: Optional[List[int]] = None,
    target_current_ua: Optional[float] = None,
    device_area_mm2: Optional[float] = None,
    r_shunt: Optional[float] = None,
    r_osc: Optional[float] = None,
    vil_filename: str = '',
) -> Tuple[bytes, List[Dict], Dict]:
    """
    VIL 기반 마스터 XLSX 생성 (Pandas & Openpyxl Optimized)
    """
    decay_thresholds = [p / 100.0 for p in (percent_list or [100, 75, 50, 25])]
    pct_labels = [f"{int(p * 100)}%" for p in decay_thresholds]

    # 1. VIL 전처리 (95% J 필터, smoothing, 재정규화)
    t_min, rel_lum = prepare_vil_for_master(
        vil_processed_csv,
        target_current_ua=target_current_ua,
        device_area_mm2=device_area_mm2,
        r_shunt=r_shunt,
        r_osc=r_osc,
    )

    duty_fraction = parse_duty_from_filename(vil_filename) if vil_filename else None
    if duty_fraction is None:
        duty_fraction = 1.0

    times_vil_duty = {}
    for ratio in decay_thresholds:
        times_vil_duty[ratio] = interpolate_time_at_ratio(t_min, rel_lum, ratio)

    # 2. VIL duty 시간 → wall-clock 분 (TrEL 파일명 분과 동일 축)
    target_times = {}
    for ratio in decay_thresholds:
        t_vil = times_vil_duty.get(ratio)
        if t_vil is not None:
            target_times[ratio] = vil_duty_time_to_wall_clock_min(
                t_vil, duty_fraction, vil_time_shift_min
            )
        else:
            target_times[ratio] = None

    # 3. TrEL 파일 목록에서 분 추출
    files_with_minutes = []
    for filename, _ in trel_files_data:
        mins = parse_minutes_from_filename(filename)
        print(f"[process_master] parsed_minutes: {filename} -> {mins}", flush=True)
        if mins is not None:
            files_with_minutes.append((filename, mins))

    if not files_with_minutes:
        raise ValueError("TrEL 파일명에서 측정 시간(분)을 추출할 수 없습니다. (예: 1min, 57min)")

    # 4. 각 목표 시간에 가장 가까운 파일 선택
    selected = {}
    for ratio in decay_thresholds:
        t_target = target_times.get(ratio)
        print(f"[process_master] target ratio={ratio:.4f}, shifted_target_min={t_target}", flush=True)
        if t_target is None:
            continue
        closest = find_closest_file(files_with_minutes, t_target)
        if closest:
            print(
                f"[process_master] selected ratio={ratio:.4f}: filename={closest[0]}, minutes={closest[1]}",
                flush=True,
            )
            selected[ratio] = closest  # (filename, minutes)

    # 5. 선택된 파일 데이터 로드 및 병합 준비
    trel_by_name = {f: c for f, c in trel_files_data}
    
    # 6. 마스터 XLSX: Write-Only Mode (메모리 최적화)
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet(title="TrEL_Master")
    
    col_headers_legacy = [
        'Time (μs)',
        'Shifted Time (μs)',
        'Normalized intensity (a.u.)',
        'Current density (mA cm⁻²)',
        'Corrected current density (mA cm⁻²)',
    ]
    col_headers_lowfreq = [
        'Time (μs)',
        'Normalized intensity (rise)',
        'Normalized intensity (decay)',
        'Current density (mA cm⁻²)',
    ]
    
    # 포맷 감지: 선택된 파일 중 첫 유효 TrEL로 결정
    master_format = 'legacy'
    for ratio in decay_thresholds:
        if ratio in selected:
            filename, _ = selected[ratio]
            content = trel_by_name.get(filename)
            if content and detect_trel_format(content) == 'lowfreq':
                master_format = 'lowfreq'
                break

    col_headers = col_headers_lowfreq if master_format == 'lowfreq' else col_headers_legacy

    # 1행: 헤더
    row1 = []
    for _ in pct_labels:
        row1.extend(col_headers)
    ws.append(row1)
    
    # 2행: 빈 행
    ws.append([])
    
    # 3행: 퍼센트 라벨
    row3 = []
    for pct in pct_labels:
        row3.extend([pct] * len(col_headers))
    ws.append(row3)
    
    # 데이터 준비
    data_frames = []
    files_used = []
    
    max_len = 0
    
    for ratio in decay_thresholds:
        if ratio in selected:
            filename, mins = selected[ratio]
            content = trel_by_name.get(filename)
            df = parse_trel_csv_frame(content) if content else pd.DataFrame()
        else:
            filename = None
            df = pd.DataFrame()
            
        if not df.empty:
            if master_format == 'lowfreq':
                base_cols = [
                    'Time (μs)',
                    'Normalized intensity (rise)',
                    'Normalized intensity (decay)',
                    'Current density (mA cm⁻²)',
                ]
                df = df[base_cols].copy()
            else:
                base_cols = ['Time (μs)', 'Shifted Time (μs)', 'Normalized intensity (a.u.)']
                curr_col = 'Current density (mA cm⁻²)'
                corr_col = 'Corrected current density (mA cm⁻²)'
                if corr_col in df.columns:
                    df = df[base_cols + [curr_col, corr_col]].copy()
                else:
                    df = df[base_cols + [curr_col]].copy()
                    df[corr_col] = np.nan
            max_len = max(max_len, len(df))
            data_frames.append(df)
            if filename:
                files_used.append({
                    'filename': filename,
                    'minutes': parse_minutes_display(filename) or f"{mins}min",
                    'percent': pct_labels[decay_thresholds.index(ratio)],
                })
        else:
            data_frames.append(pd.DataFrame(columns=col_headers))
            
    # 4행부터: 데이터 쓰기
    # 모든 DataFrame을 하나의 DataFrame으로 가로 병합 (concat)
    # 길이가 다르면 NaN으로 채워짐 -> 빈 문자열로 변환 필요
    
    if data_frames:
        # 각 DataFrame의 인덱스를 리셋하여 병합 시 정렬 문제 방지
        dfs_reset = [df.reset_index(drop=True) for df in data_frames]
        # 가로 병합
        master_df = pd.concat(dfs_reset, axis=1)
        # NaN을 빈 문자열로 변환
        master_df = master_df.fillna('')
        
        # 행 단위로 append (openpyxl write-only는 iterable을 받음)
        # DataFrame.values는 numpy array -> tolist()로 변환
        for row in master_df.values.tolist():
            ws.append(row)
            
    metadata = {
        'files_used': files_used,
        'target_times_min': target_times,
        'vil_crossing_times_duty_min': {str(k): v for k, v in times_vil_duty.items()},
        'duty_fraction': duty_fraction,
        'selected_minutes': sorted({int(round(mins)) for _, mins in selected.values()}),
        'output_filename': build_master_filename([mins for _, mins in selected.values()]),
        'vil_smoothing_applied': True,
    }
    summary = [{'file': f['filename'], 'success': True, 'minutes': f['minutes'], 'percent': f['percent']} for f in files_used]
    
    output = io.BytesIO()
    wb.save(output)
    return output.getvalue(), summary, metadata

