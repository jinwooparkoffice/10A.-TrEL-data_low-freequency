"""
TrEL (Transient Electroluminescence) VIL 데이터 처리
- 목표 전류로부터 5% 이내 데이터만 유지
- 영점 조정: 처음 5% 이내 들어오는 시점을 t=0으로
- Relative luminance: 최대값을 1로 정규화 (0~1 범위)
- Low-side Sensing 보정: V_device = V_applied - (I × R_total)
- 출력: Time(min), Voltage(V), Current density(mA/cm²), Relative luminance(a.u.)
"""
import re
import io
from typing import Optional, Tuple, Dict
import pandas as pd
import numpy as np
from utils.circuit_config import calculate_r_total, calculate_device_voltage, resolve_device_area_mm2
TOLERANCE = 0.05  # 5% 허용 오차


def parse_target_current_from_filename(filename: str) -> Optional[float]:
    """
    파일명에서 목표 전류(µA) 추출
    형식: YYMMDD_CC_7000uA_1000Hz_duty25%_VIL_1552.csv
    """
    # 7000uA, 7000µA, 7000uA 등 패턴
    match = re.search(r'(\d+(?:\.\d+)?)\s*(?:uA|µA|microA)', filename, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None


def parse_duty_from_filename(filename: str) -> Optional[float]:
    """
    파일명에서 Duty(%) 추출
    형식: YYMMDD_CC_7000uA_1000Hz_duty25%_VIL_1552.csv
    """
    duty_match = re.search(r'duty\s*(\d+(?:\.\d+)?)\s*%?', filename, re.IGNORECASE)
    if duty_match:
        val = float(duty_match.group(1))
        # duty25 -> 0.25, duty0.5 -> 0.005? 보통 파일명엔 % 단위 사용 (25 = 25%)
        duty_fraction = val / 100.0 if val > 1.0 else val
        return duty_fraction
    return None


def process_vil_data(
    csv_content: str,
    target_current_ua: float,
    filename: str = "",
    r_shunt: Optional[float] = None,
    r_osc: Optional[float] = None,
    device_area_mm2: Optional[float] = None,
) -> Tuple[str, float, Dict]:
    """
    VIL CSV 데이터 처리 (Pandas Optimized)
    
    Returns:
        (processed_csv_string, time_shift_seconds, metadata)
    """
    try:
        # Pandas로 CSV 읽기 (속도 최적화)
        # 헤더가 첫 줄에 있다고 가정
        df = pd.read_csv(io.StringIO(csv_content))
    except Exception:
        raise ValueError("CSV 파일 형식이 올바르지 않습니다.")

    if len(df) < 1:
        raise ValueError("CSV 파일에 데이터가 없습니다.")

    # 컬럼 인덱스로 접근 (0=t, 1=V, 2=I, 3=L)
    # 컬럼 이름이 무엇이든 순서대로 가져옴
    if len(df.columns) < 4:
         raise ValueError("CSV 파일의 컬럼 수가 부족합니다. (최소 4개 필요)")

    # 필요한 4개 컬럼만 숫자로 강제 변환하고 유효한 행만 사용
    numeric_df = df.iloc[:, :4].apply(pd.to_numeric, errors='coerce')
    numeric_df = numeric_df.dropna(subset=numeric_df.columns[:4])
    if len(numeric_df) == 0:
        raise ValueError("유효한 숫자 데이터가 없습니다.")

    # 데이터를 numpy array로 변환하여 처리 (컬럼명 무관하게 인덱스로 접근)
    t = numeric_df.iloc[:, 0].values
    v_applied = numeric_df.iloc[:, 1].values  # 인가 전압 (V)
    i_ua = numeric_df.iloc[:, 2].values  # 전류 (µA)
    l = numeric_df.iloc[:, 3].values

    # 비정상 시간 점프(측정기 오류 등) 이후 데이터 제거
    if len(t) >= 2:
        dt = np.diff(t)
        positive_dt = dt[dt > 0]
        median_dt = float(np.median(positive_dt)) if len(positive_dt) else 1.0
        if not np.isfinite(median_dt) or median_dt <= 0:
            median_dt = 1.0
        gap_threshold = median_dt * 10.0
        cut_idx = len(t)
        for i in range(1, len(t)):
            if t[i] - t[i - 1] > gap_threshold:
                cut_idx = i
                break
        t = t[:cut_idx]
        v_applied = v_applied[:cut_idx]
        i_ua = i_ua[:cut_idx]
        l = l[:cut_idx]
    
    # Low-side Sensing 보정: R_total 계산
    r_total = calculate_r_total(r_shunt, r_osc)
    
    # 유효 인가 전압 계산: V_device = V_applied - (I × R_total)
    # I는 µA 단위이므로 A로 변환 필요
    v_device = calculate_device_voltage(v_applied, i_ua, r_total)

    # 5% 허용 범위
    # low = target_current_ua * (1 - TOLERANCE)
    # high = target_current_ua * (1 + TOLERANCE)
    
    # 마스크 생성 (목표 전류 범위 내)
    # mask = (i_ua >= low) & (i_ua <= high)

    # VIL 데이터는 보통 전류가 점진적으로 증가하다가 목표 전류에 도달하면 일정하게 유지되거나
    # 혹은 전체 스윕 데이터일 수 있음.
    # 여기서는 목표 전류에 도달한 시점부터 끝까지를 유효 구간으로 보는 것이 더 적절할 수 있음.
    # 또는 단순히 목표 전류 근처에 도달한 시점(t0)을 찾고, 그 이후 모든 데이터를 사용하는 방식.
    
    # VIL 데이터는 보통 전류가 점진적으로 증가하다가 목표 전류에 도달하면 일정하게 유지되거나
    # 혹은 전체 스윕 데이터일 수 있음.
    # 여기서는 목표 전류에 도달한 시점부터 끝까지를 유효 구간으로 보는 것이 더 적절할 수 있음.
    # 또는 단순히 목표 전류 근처에 도달한 시점(t0)을 찾고, 그 이후 모든 데이터를 사용하는 방식.
    
    # 1. 목표 전류의 95% 이상에 도달하는 첫 시점 찾기
    threshold = target_current_ua * 0.95
    reached_indices = np.where(i_ua >= threshold)[0]
    
    if len(reached_indices) == 0:
         # 목표 전류에 도달하지 못한 경우, 최대 전류 지점이라도 사용 (경고 필요할 수 있음)
         # 하지만 여기서는 에러 처리
         raise ValueError(f"목표 전류 {target_current_ua} µA (95% 이상)에 도달한 데이터가 없습니다. (Max: {np.max(i_ua):.2f} µA)")
         
    first_idx = reached_indices[0]
    t0 = t[first_idx]
    time_shift = float(t0)
    
    # 2. t0 이후의 모든 데이터를 유효 데이터로 사용
    # 단, 전류가 급격히 떨어지는 구간(실험 종료 등)이 있다면 제외해야 함.
    # 일단은 끝까지 사용하되, 너무 낮은 값(예: 10% 미만)으로 떨어지면 자르는 로직 추가 가능.
    
    # 종료 지점 찾기: 전류가 다시 50% 이하로 떨어지는 지점
    end_threshold = target_current_ua * 0.5
    end_indices = np.where(i_ua[first_idx:] < end_threshold)[0]
    
    if len(end_indices) > 0:
        last_idx = first_idx + end_indices[0]
    else:
        last_idx = len(t)
        
    t_filtered = t[first_idx:last_idx]
    v_device_filtered = v_device[first_idx:last_idx]  # 보정된 전압 사용
    i_filtered = i_ua[first_idx:last_idx]
    l_filtered = l[first_idx:last_idx]

    # 시간 시프트 (0초부터 시작)
    t_new = t_filtered - t0

    if len(t_new) == 0:
        raise ValueError("필터링 후 유효한 데이터가 없습니다.")

    # Current density: I(µA) -> mA/cm²
    area_mm2 = resolve_device_area_mm2(device_area_mm2)
    area_cm2 = area_mm2 * 1e-2
    j_ma_cm2 = (i_filtered / 1000.0) / area_cm2

    # Relative luminance: 최대값을 1로 정규화
    l_max = np.nanmax(l_filtered)
    if not np.isfinite(l_max) or l_max == 0:
        l_max = 1e-10
    
    rel_lum = l_filtered / l_max

    # Duty를 고려한 시간 계산
    duty_fraction = parse_duty_from_filename(filename)
    if duty_fraction is None:
        duty_fraction = 1.0  # duty 정보가 없으면 1.0 (변경 없음)
    
    # 실제 경과 시간에 duty를 곱하여 duty 고려 시간 계산
    time_min_duty_adjusted = (t_new / 60.0) * duty_fraction

    # 결과 DataFrame 생성 (보정된 전압 사용)
    # 컬럼 순서: 1열 Time, 2열 Luminance, 3열 Voltage, 4열 Current density
    res_df = pd.DataFrame({
        'Time (min)': time_min_duty_adjusted,
        'Relative luminance (a.u.)': rel_lum,
        'Voltage (V)': v_device_filtered,  # Low-side Sensing 보정된 전압
        'Current density (mA/cm2)': j_ma_cm2,
    })

    # 출력 CSV 생성
    output = io.StringIO()
    # 메타데이터 주석 쓰기
    output.write(f'# time_shift_s: {time_shift:.6f}\n')
    # DataFrame 쓰기 (인덱스 제외)
    res_df.to_csv(output, index=False, float_format='%.6f')

    # 출력 파일명 설정
    base = filename.replace('.csv', '') if filename else 'output'
    output_filename = f"{base}_processed.xlsx"

    metadata = {
        'target_current_ua': target_current_ua,
        'time_shift_s': time_shift,
        'time_shift_min': time_shift / 60.0,
        'duty_fraction': duty_fraction,
        'original_points': len(df),
        'filtered_points': len(res_df),
        'filename': filename,
        'output_filename': output_filename,
        'r_total_ohm': r_total,
        'r_shunt_ohm': r_shunt,
        'r_osc_ohm': r_osc,
        'device_area_mm2': area_mm2,
        'area_cm2': area_cm2,
    }

    return output.getvalue(), time_shift, metadata

