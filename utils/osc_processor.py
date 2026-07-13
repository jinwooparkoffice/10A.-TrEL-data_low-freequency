"""
TrEL (Transient Electroluminescence) 오실로스코프 데이터 처리

Low-frequency 모드 (rise/decay 페어):
- rise CSV: 전압 ON·상승 구간
- decay CSV: 포화·감쇠 구간 (동일 time grid, 동일 degradation 시각)
- 출력 _TrEL.csv (4컬럼):
  Time (us), Normalized intensity (rise), Normalized intensity (decay), Current density (mA/cm2)
- 정규화: decay CH1 기준 sat/zero 구간 (미리보기에서 선택)

Legacy 모드 (단일 파일):
- Time (μs), Shifted Time (μs), Norm. Luminance, Current Density, Corrected Current Density
"""
import re
import io
import csv
from typing import Optional, Tuple, List, Dict
import pandas as pd
import numpy as np

# 실험 상수
from utils.circuit_config import calculate_r_total, resolve_device_area_mm2


def get_pair_key(filename: str) -> Optional[str]:
    """rise/decay 파일명에서 페어 키 추출. 해당 없으면 None."""
    base = re.sub(r'\.csv$', '', filename, flags=re.IGNORECASE)
    if not re.search(r'_(?:rise|decay)(?:_|$)', base, re.IGNORECASE):
        return None
    key = re.sub(r'_(?:rise|decay)(?=_|$)', '', base, flags=re.IGNORECASE)
    return key or None


def is_rise_filename(filename: str) -> bool:
    return bool(re.search(r'_rise(?:_|\.|$)', filename, re.IGNORECASE))


def is_decay_filename(filename: str) -> bool:
    return bool(re.search(r'_decay(?:_|\.|$)', filename, re.IGNORECASE))


def _extract_osc_channels(df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """DataFrame에서 time_ms, CH1, CH2 배열 추출."""
    cols = df.columns
    time_col = next((c for c in cols if 'time' in c), None)
    ch1_col = next((c for c in cols if 'ch1' in c), None)
    ch2_col = next((c for c in cols if 'ch2' in c), None)

    if not time_col:
        raise ValueError("Time 컬럼 없음")
    if not ch1_col:
        raise ValueError("CH1(Light) 컬럼 없음")
    if not ch2_col:
        raise ValueError("CH2(Current) 컬럼 없음")

    df.dropna(subset=[time_col, ch1_col, ch2_col], inplace=True)
    if len(df) == 0:
        raise ValueError("필수 데이터(Time, CH1, CH2)가 모두 유효한 행이 없습니다.")

    return df[time_col].values, df[ch1_col].values, df[ch2_col].values


def process_lowfreq_pair(
    rise_csv_content: str,
    decay_csv_content: str,
    zero_start_ns: float,
    zero_end_ns: float,
    sat_start_ns: float,
    sat_end_ns: float,
    filename_base: str = "",
    r_shunt: Optional[float] = None,
    r_osc: Optional[float] = None,
    device_area_mm2: Optional[float] = None,
) -> Tuple[str, Dict]:
    """
    Low-frequency rise/decay 페어 → 4컬럼 _TrEL.csv 생성.
    정규화 기준은 decay 파일 CH1(반전)의 sat/zero 구간 평균.
    """
    r_total = calculate_r_total(r_shunt, r_osc)
    area_mm2 = resolve_device_area_mm2(device_area_mm2)
    area_cm2 = area_mm2 * 1e-2

    rise_df = load_osc_csv(rise_csv_content)
    decay_df = load_osc_csv(decay_csv_content)

    t_rise_ms, ch1_rise, ch2_rise = _extract_osc_channels(rise_df)
    t_decay_ms, ch1_decay, _ = _extract_osc_channels(decay_df)

    if len(t_rise_ms) != len(t_decay_ms):
        raise ValueError(
            f"rise/decay 행 수 불일치: rise={len(t_rise_ms)}, decay={len(t_decay_ms)}"
        )
    if not np.allclose(t_rise_ms, t_decay_ms, rtol=0, atol=1e-9):
        raise ValueError("rise/decay time_ms 축이 일치하지 않습니다.")

    t_ns = t_decay_ms * 1e6
    l_decay_inv = -ch1_decay
    l_rise_inv = -ch1_rise

    mask_zero = (t_ns >= zero_start_ns) & (t_ns <= zero_end_ns)
    mask_sat = (t_ns >= sat_start_ns) & (t_ns <= sat_end_ns)

    if not np.any(mask_zero):
        raise ValueError("Zero 기준 구간에 데이터가 없습니다.")
    if not np.any(mask_sat):
        raise ValueError("Saturation 기준 구간에 데이터가 없습니다.")

    zero_val = float(np.mean(l_decay_inv[mask_zero]))
    sat_val = float(np.mean(l_decay_inv[mask_sat]))
    denom = sat_val - zero_val
    if abs(denom) < 1e-12:
        raise ValueError("Saturation과 Zero 기준값이 동일합니다. 구간을 다시 선택해주세요.")

    rise_norm = (l_rise_inv - zero_val) / denom
    decay_norm = (l_decay_inv - zero_val) / denom

    offset_ch2 = float(np.mean(ch2_rise[mask_zero])) if np.any(mask_zero) else 0.0
    j_raw = ((ch2_rise - offset_ch2) / r_total) / area_cm2 * 1000.0

    time_us = t_rise_ms * 1000.0
    out_df = pd.DataFrame({
        'Time (us)': time_us,
        'Normalized intensity (rise)': rise_norm,
        'Normalized intensity (decay)': decay_norm,
        'Current density (mA/cm2)': j_raw,
    })

    output = io.StringIO()
    output.write(
        'Time (us),Normalized intensity (rise),Normalized intensity (decay),Current density (mA/cm2)\n\n\n'
    )
    out_df.to_csv(output, index=False, header=False, float_format='%.6f')

    base = filename_base.replace('.csv', '') if filename_base else 'output'
    output_filename = f"{base}_TrEL.csv"

    metadata = {
        'filename': filename_base,
        'output_filename': output_filename,
        'original_points': len(t_rise_ms),
        'zero_start_ns': zero_start_ns,
        'zero_end_ns': zero_end_ns,
        'sat_start_ns': sat_start_ns,
        'sat_end_ns': sat_end_ns,
        'sat_value': sat_val,
        'zero_value': zero_val,
        'r_total_ohm': r_total,
        'r_shunt_ohm': r_shunt,
        'r_osc_ohm': r_osc,
        'device_area_mm2': area_mm2,
        'area_cm2': area_cm2,
        'format': 'lowfreq',
    }

    return output.getvalue(), metadata


def parse_frequency_duty(filename: str) -> Tuple[Optional[float], Optional[float]]:
    """
    파일명에서 Frequency(Hz), Duty(%) 추출
    형식: 260223_CC_7000uA_1000Hz_duty25%_1h0min.csv
    """
    freq_match = re.search(r'(\d+(?:\.\d+)?)\s*Hz', filename, re.IGNORECASE)
    duty_match = re.search(r'duty\s*(\d+(?:\.\d+)?)\s*%?', filename, re.IGNORECASE)
    
    freq = float(freq_match.group(1)) if freq_match else None
    
    duty_pct = None
    if duty_match:
        val = float(duty_match.group(1))
        # duty25 -> 0.25, duty0.5 -> 0.005? 보통 파일명엔 % 단위 사용 (25 = 25%)
        duty_pct = val / 100.0 if val > 1.0 else val
        
    return freq, duty_pct


def load_osc_csv(content: str) -> pd.DataFrame:
    """
    오실로스코프 CSV를 Pandas DataFrame으로 로드하고 컬럼명을 표준화합니다.
    """
    try:
        # 1차 시도: 일반적인 CSV 읽기
        df = pd.read_csv(io.StringIO(content))
        
        # 컬럼명 소문자 변환 및 공백 제거
        df.columns = df.columns.str.lower().str.strip()
        
        # 'time' 컬럼이 없으면 헤더가 다른 줄에 있을 수 있음
        if not any('time' in col for col in df.columns):
            # 2차 시도: 헤더를 찾아서 다시 읽기 (최대 10줄 검색)
            lines = content.split('\n')
            header_row = -1
            for i, line in enumerate(lines[:10]):
                if 'time' in line.lower():
                    header_row = i
                    break
            
            if header_row != -1:
                df = pd.read_csv(io.StringIO(content), header=header_row)
                df.columns = df.columns.str.lower().str.strip()
            else:
                 raise ValueError("Time 컬럼을 찾을 수 없습니다.")

        # 단위 행(Unit Row) 제거: 데이터가 숫자가 아닌 경우 NaN 처리 후 드랍
        # 첫 번째 행의 첫 번째 컬럼이 숫자로 변환 불가능하면 단위 행으로 간주
        if len(df) > 0:
            first_val = df.iloc[0, 0]
            try:
                float(str(first_val))
            except ValueError:
                 df = df.iloc[1:].reset_index(drop=True)
             
        # 모든 데이터를 숫자로 변환 (오류 발생 시 NaN)
        df = df.apply(pd.to_numeric, errors='coerce')
        # Inf, -Inf를 NaN으로 변환
        df.replace([np.inf, -np.inf], np.nan, inplace=True)
        # 여기서 전체 dropna를 하면 안됨 (불필요한 컬럼이 비어있을 수 있음)
        # df = df.dropna(how='any') 
        
        return df

    except Exception as e:
        raise ValueError(f"CSV 파싱 오류: {str(e)}")


def process_osc_data(
    csv_content: str,
    baseline_start_ns: float,
    baseline_end_ns: float,
    frequency_hz: float,
    duty_fraction: float,
    filename: str = "",
    norm_start_ns: Optional[float] = None,
    norm_end_ns: Optional[float] = None,
    r_shunt: Optional[float] = None,
    r_osc: Optional[float] = None,
    device_area_mm2: Optional[float] = None,
) -> Tuple[str, Dict]:
    """
    오실로스코프 TrEL 데이터 처리 (Pandas Optimized)
    """
    # R_total, 소자 면적 (mm² → cm²)
    r_total = calculate_r_total(r_shunt, r_osc)
    area_mm2 = resolve_device_area_mm2(device_area_mm2)
    area_cm2 = area_mm2 * 1e-2
    
    df = load_osc_csv(csv_content)

    # 필수 컬럼 찾기 (유연한 매칭)
    cols = df.columns
    time_col = next((c for c in cols if 'time' in c), None)
    ch1_col = next((c for c in cols if 'ch1' in c), None) # PMT (Light)
    ch2_col = next((c for c in cols if 'ch2' in c), None) # Current (Voltage across R)
    # CH4 (Trigger) Optional
    
    if not time_col: raise ValueError("Time 컬럼 없음")
    if not ch1_col: raise ValueError("CH1(Light) 컬럼 없음")
    if not ch2_col: raise ValueError("CH2(Current) 컬럼 없음")

    # 필수 컬럼에 NaN이 있는 행 제거 (ch3, ch4가 비어있어도 ch1, ch2, time이 있으면 유지)
    df.dropna(subset=[time_col, ch1_col, ch2_col], inplace=True)

    if len(df) == 0:
        raise ValueError("필수 데이터(Time, CH1, CH2)가 모두 유효한 행이 없습니다.")

    # 벡터 연산 준비
    t_ms = df[time_col].values
    ch1 = df[ch1_col].values
    ch2 = df[ch2_col].values
    
    # ms -> ns 변환
    t_ns = t_ms * 1e6

    # 1. CH1 (Light): Baseline Correction & Inversion & Normalization
    # Invert signal (PMT negative signal)
    l_raw_inverted = -ch1
    
    # Baseline mask
    mask_base = (t_ns >= baseline_start_ns) & (t_ns <= baseline_end_ns)
    
    # Calculate baseline offset
    if np.any(mask_base):
        offset_l = np.mean(l_raw_inverted[mask_base])
    else:
        offset_l = 0.0
        
    l_corrected = l_raw_inverted - offset_l

    # Normalization
    norm_factor = 1.0
    if norm_start_ns is not None and norm_end_ns is not None:
        mask_norm = (t_ns >= norm_start_ns) & (t_ns <= norm_end_ns)
        if np.any(mask_norm):
            norm_factor = np.mean(l_corrected[mask_norm])
        else:
            norm_factor = np.max(l_corrected) if len(l_corrected) > 0 else 1e-10
    else:
        norm_factor = np.max(l_corrected) if len(l_corrected) > 0 else 1e-10
        
    if norm_factor == 0: norm_factor = 1e-10
    l_norm = l_corrected / norm_factor

    # 2. CH2 (Current): V -> I -> J
    # V_ch2 -> I_led = V_ch2 / R_total
    # J = I_led / Area
    offset_ch2 = 0.0
    if np.any(mask_base):
        offset_ch2 = np.mean(ch2[mask_base])
        
    j_raw = ((ch2 - offset_ch2) / r_total) / area_cm2 * 1000.0

    # 3. Time Shift Calculation
    # Trigger (t=0) is Voltage ON.
    # We want Shifted Time t=0 to be Voltage OFF (Decay Start).
    shift_offset_us = 0.0
    if frequency_hz and frequency_hz > 0:
        period_us = (1.0 / frequency_hz) * 1e6
        t_on_us = period_us * duty_fraction
        shift_offset_us = t_on_us
    else:
        # Fallback: Max luminance index
        imax = np.argmax(l_norm)
        shift_offset_us = t_ms[imax] * 1000.0

    # 4. Symmetric capacitance cancellation
    # I_corrected(t_on + Δt) = I_measured(t_on + Δt) + I_measured(t_off + Δt)
    # Raw time axis uses t=0 at voltage ON, so pair current at t with current at t + t_on.
    time_us = t_ms * 1000.0
    shifted_us = time_us - shift_offset_us
    paired_times_us = time_us + shift_offset_us
    j_off_interp = np.interp(paired_times_us, time_us, j_raw, left=np.nan, right=np.nan)
    j_corrected = np.where(np.isfinite(j_off_interp), j_raw + j_off_interp, np.nan)

    # 결과 DataFrame 생성

    # ASCII-safe 컬럼명 사용 (CSV 인코딩 시 μ, ⁻² 등 유니코드 깨짐 방지)
    out_df = pd.DataFrame({
        'Time (us)': time_us,
        'Shifted Time (us)': shifted_us,
        'Normalized intensity (a.u.)': l_norm,
        'Current density (mA/cm2)': j_raw,
        'Corrected current density (mA/cm2)': j_corrected,
    })
    
    # 출력 CSV 생성 (헤더는 ASCII-safe로 저장)
    output = io.StringIO()
    # 빈 줄 2개 추가 (기존 포맷 유지)
    output.write(
        'Time (us),Shifted Time (us),Normalized intensity (a.u.),Current density (mA/cm2),Corrected current density (mA/cm2)\n\n\n'
    )
    # DataFrame 데이터만 쓰기 (헤더 제외)
    out_df.to_csv(output, index=False, header=False, float_format='%.6f')

    base = filename.replace('.csv', '') if filename else 'output'
    output_filename = f"{base}_TrEL.csv"

    metadata = {
        'filename': filename,
        'output_filename': output_filename,
        'original_points': len(df),
        'baseline_start_ns': baseline_start_ns,
        'baseline_end_ns': baseline_end_ns,
        'norm_start_ns': norm_start_ns,
        'norm_end_ns': norm_end_ns,
        'frequency_hz': frequency_hz,
        'duty_fraction': duty_fraction,
        'shift_offset_us': shift_offset_us,
        'r_total_ohm': r_total,
        'r_shunt_ohm': r_shunt,
        'r_osc_ohm': r_osc,
        'device_area_mm2': area_mm2,
        'area_cm2': area_cm2,
    }

    return output.getvalue(), metadata


def _parse_osc_csv_legacy(content: str) -> List[Tuple[float, float, float, float]]:
    """
    오실로스코프 CSV 파싱 (Legacy Pure Python Version)
    - 미리보기용으로 사용
    - Returns: list of (time_ms, CH1, CH2, CH4) tuples
    """
    lines = content.splitlines()
    if len(lines) < 2:
        return [] # Return empty instead of raising

    # 1. Header Search
    header_row = -1
    headers = []
    
    for i, line in enumerate(lines[:20]): # Check first 20 lines
        line_clean = line.lower().replace('\ufeff', '').strip()
        if 'time' in line_clean and ',' in line_clean:
             header_row = i
             headers = [h.strip() for h in line_clean.split(',')]
             break
    
    if header_row == -1:
         return [] # Time column not found

    try:
        time_idx = next(i for i, h in enumerate(headers) if 'time' in h)
        # Relaxed matching for CH1/CH2 (e.g. "Channel 1", "CH1", "Volt")
        ch1_idx = next((i for i, h in enumerate(headers) if 'ch1' in h or 'channel 1' in h), -1)
        ch2_idx = next((i for i, h in enumerate(headers) if 'ch2' in h or 'channel 2' in h), -1)
        ch4_idx = next((i for i, h in enumerate(headers) if 'ch4' in h or 'channel 4' in h), -1)

        if ch1_idx == -1 or ch2_idx == -1:
             return [] # Missing essential columns

    except StopIteration:
        return []

    # 2. Data Parsing
    data = []
    start_idx = header_row + 1
    
    # Check for unit row
    if len(lines) > start_idx:
        first_line = lines[start_idx]
        first_val = first_line.split(',')[time_idx].strip()
        # If not a number, skip unit row
        if not re.match(r'^-?\d+(\.\d+)?([eE][+-]?\d+)?$', first_val):
            start_idx += 1

    for line in lines[start_idx:]:
        if not line.strip(): continue
        parts = line.split(',')
        if len(parts) <= max(time_idx, ch1_idx, ch2_idx): continue
        
        try:
            t = float(parts[time_idx])
            c1 = float(parts[ch1_idx])
            c2 = float(parts[ch2_idx])
            c4 = float(parts[ch4_idx]) if ch4_idx != -1 and len(parts) > ch4_idx and parts[ch4_idx].strip() else 0.0
            data.append((t, c1, c2, c4))
        except ValueError:
            continue
            
    return data


def get_lowfreq_preview_data(csv_content: str, max_points: int = 2000) -> Dict:
    """Low-frequency decay 파일 미리보기 (CH1 반전)."""
    return get_preview_data(csv_content, max_points=max_points)


def get_preview_data(csv_content: str, max_points: int = 2000) -> Dict:
    """
    미리보기용 데이터 추출 (CH1 Inverted, CH2 Raw) - Legacy Pure Python Version
    """
    try:
        data = _parse_osc_csv_legacy(csv_content)
        if not data:
            return {'error': 'CSV 파싱 실패 또는 데이터 없음'}

        # Unpack data
        t_ms_list = [row[0] for row in data]
        ch1_list = [-row[1] for row in data] # Invert CH1
        ch2_list = [row[2] for row in data]
        
        # Downsample
        n = len(t_ms_list)
        step = max(1, n // max_points)
        
        t_ns_preview = [t * 1e6 for t in t_ms_list[::step]]
        ch1_preview = ch1_list[::step]
        ch2_preview = ch2_list[::step]
        
        return {
            'time_ns': t_ns_preview,
            'ch1': ch1_preview,
            'ch2': ch2_preview,
            'n_points': n
        }

    except Exception as e:
         return {'error': str(e)}
