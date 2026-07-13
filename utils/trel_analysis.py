"""
TrEL 자동 배치 분석
- Rise, Saturation, Decay 파라미터 추출
- 입력: TrEL_processed CSV (Col0: Time(μs), Col1: Shifted Time(μs), Col2: Norm.Luminance, Col3: Current Density)
- Time Definitions:
  - Time (μs): Raw Time. t=0 is Voltage ON (Rise Start).
  - Shifted Time (μs): t=0 is Voltage OFF (Decay Start).
- Analysis:
  - Rise: Uses 'Time (μs)' around t=0.
  - Decay: Uses 'Shifted Time (μs)' for t >= decay_fit_start_us (default 4 us).
"""
import re
import io
from typing import List, Dict, Tuple, Optional
import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from utils.trel_common import parse_minutes_from_filename, parse_trel_csv_frame, detect_trel_format

_trapz = np.trapezoid if hasattr(np, 'trapezoid') else np.trapz

DEFAULT_RISE_ANALYSIS_MODE = 'tangent'
DEFAULT_TANGENT_WINDOW_POINTS = 17
VALID_RISE_ANALYSIS_MODES = {'threshold', 'tangent'}


def normalize_rise_analysis_mode(mode: Optional[str]) -> str:
    """상승부 분석 모드 문자열을 정규화."""
    normalized = (mode or DEFAULT_RISE_ANALYSIS_MODE).strip().lower()
    return normalized if normalized in VALID_RISE_ANALYSIS_MODES else DEFAULT_RISE_ANALYSIS_MODE


def format_rise_analysis_mode(mode: Optional[str]) -> str:
    """엑셀/응답용 표시 문자열."""
    return 'Tangent' if normalize_rise_analysis_mode(mode) == 'tangent' else 'Threshold'


def parse_vil_processed_for_voltage(content: str) -> Tuple[Optional[float], List[Tuple[float, float]]]:
    """VIL_processed CSV 파싱: time_shift_s 및 (Time min, Voltage) 목록."""
    time_shift_s, data = parse_vil_processed_for_voltage_luminance(content)
    return time_shift_s, [(t, v) for t, v, _ in data] if data else []


def parse_vil_processed_for_voltage_luminance(content: str) -> Tuple[Optional[float], List[Tuple[float, float, float]]]:
    """
    VIL_processed CSV 파싱: time_shift_s 및 (Time min, Voltage, Relative luminance) 목록
    컬럼 순서: Time (min), Relative luminance (a.u.), Voltage (V), Current density
    """
    time_shift_s = None
    lines = content.splitlines()
    if lines and lines[0].startswith('#'):
        m = re.search(r'time_shift_s:\s*([\d.eE+-]+)', lines[0])
        if m:
            time_shift_s = float(m.group(1))

    try:
        df = pd.read_csv(io.StringIO(content), comment='#')
        df.columns = df.columns.str.strip()

        time_col = next((c for c in df.columns if 'Time (min)' in c), None)
        volt_col = next((c for c in df.columns if 'Voltage (V)' in c), None)
        lum_col = next((c for c in df.columns if 'Relative luminance' in c or 'Luminance' in c), None)

        if time_col and volt_col and lum_col:
            data = list(zip(df[time_col], df[volt_col], df[lum_col]))
            return time_shift_s, data
        if time_col and volt_col:
            data = [(t, v, float('nan')) for t, v in zip(df[time_col], df[volt_col])]
            return time_shift_s, data

        # Fallback: 0=Time, 1=Luminance, 2=Voltage
        if len(df.columns) >= 3:
            data = list(zip(df.iloc[:, 0], df.iloc[:, 2], df.iloc[:, 1]))
            return time_shift_s, data
        if len(df.columns) >= 2:
            data = [(t, v, float('nan')) for t, v in zip(df.iloc[:, 0], df.iloc[:, 1])]
            return time_shift_s, data

    except Exception:
        pass
    return time_shift_s, []


def wall_clock_to_vil_duty_min(
    time_min_wall: float,
    vil_time_shift_min: float = 0.0,
    duty_fraction: Optional[float] = None,
) -> float:
    """TrEL wall-clock 분 → VIL processed Time(min) (duty 보정 축)."""
    duty = duty_fraction if duty_fraction and duty_fraction > 0 else 1.0
    return (time_min_wall - vil_time_shift_min) * duty


def filter_vil_time_outliers(
    vil_data: List[Tuple[float, ...]],
    max_gap_factor: float = 10.0,
) -> List[Tuple[float, ...]]:
    """VIL 시계열에서 비정상 시간 점프 이후 데이터 제거."""
    if len(vil_data) < 2:
        return vil_data

    sorted_data = sorted(vil_data, key=lambda x: x[0])
    times = np.array([x[0] for x in sorted_data], dtype=float)
    dt = np.diff(times)
    positive_dt = dt[dt > 0]
    median_dt = float(np.median(positive_dt)) if len(positive_dt) else 1.0
    if not np.isfinite(median_dt) or median_dt <= 0:
        median_dt = 1.0
    threshold = median_dt * max_gap_factor

    cut_idx = len(sorted_data)
    for i in range(1, len(times)):
        if times[i] - times[i - 1] > threshold:
            cut_idx = i
            break
    return sorted_data[:cut_idx]


def interpolate_voltage_at_time(t_min: float, vil_data: List) -> Optional[float]:
    """VIL 데이터에서 주어진 t_min에 해당하는 voltage 보간. vil_data는 (t,v) 또는 (t,v,lum) 튜플 리스트."""
    if not vil_data:
        return None
    vil_arr = np.array([(x[0], x[1]) for x in vil_data])
    vil_arr = vil_arr[vil_arr[:, 0].argsort()]
    t_arr, v_arr = vil_arr[:, 0], vil_arr[:, 1]
    if t_min <= t_arr[0]:
        return float(v_arr[0])
    if t_min >= t_arr[-1]:
        return float(v_arr[-1])
    return float(np.interp(t_min, t_arr, v_arr))


def interpolate_luminance_at_time(t_min: float, vil_data: List) -> Optional[float]:
    """VIL 데이터에서 주어진 t_min에 해당하는 relative luminance 보간. vil_data는 (t,v,lum) 튜플 리스트."""
    if not vil_data or len(vil_data[0]) < 3:
        return None
    vil_arr = np.array([(x[0], x[2]) for x in vil_data])
    vil_arr = vil_arr[vil_arr[:, 0].argsort()]
    t_arr, lum_arr = vil_arr[:, 0], vil_arr[:, 1]
    if not np.all(np.isfinite(lum_arr)):
        return None
    if t_min <= t_arr[0]:
        return float(lum_arr[0])
    if t_min >= t_arr[-1]:
        return float(lum_arr[-1])
    return float(np.interp(t_min, t_arr, lum_arr))


def parse_trel_csv(content: str) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    TrEL CSV 파싱 (legacy 5-tuple 호환)

    Legacy:
    Col0: Time (μs), Col1: Shifted Time (μs), Col2: Norm. Intensity,
    Col3: Current Density, Col4: Corrected Current Density

    Low-freq:
    Col0: Time (μs), Col1: Rise intensity, Col2: Decay intensity, Col3: Current Density
    (Col1을 el_signal, Col0을 time_shifted 대용으로 반환 — analyze_single_file에서 분기)
    """
    parsed = parse_trel_csv_extended(content)
    if not parsed:
        return np.array([]), np.array([]), np.array([]), np.array([]), np.array([])

    return (
        parsed['time_raw'],
        parsed['time_shifted'],
        parsed['el_signal'],
        parsed['current_density'],
        parsed['corrected_current_density'],
    )


def parse_trel_csv_extended(content: str) -> Optional[Dict]:
    """TrEL CSV 확장 파싱. lowfreq/legacy 포맷 자동 감지."""
    df = parse_trel_csv_frame(content)
    if df.empty:
        return None

    fmt = df.attrs.get('format') or detect_trel_format(content)
    time_raw = df.iloc[:, 0].to_numpy()

    if fmt == 'lowfreq':
        rise_signal = df['Normalized intensity (rise)'].to_numpy()
        decay_signal = df['Normalized intensity (decay)'].to_numpy()
        current_density = df['Current density (mA cm⁻²)'].fillna(0.0).to_numpy()
        return {
            'format': 'lowfreq',
            'time_raw': time_raw,
            'time_shifted': time_raw,
            'rise_signal': rise_signal,
            'decay_signal': decay_signal,
            'el_signal': rise_signal,
            'current_density': current_density,
            'corrected_current_density': np.full_like(time_raw, np.nan, dtype=float),
        }

    time_shifted = df.iloc[:, 1].to_numpy()
    el_signal = df.iloc[:, 2].to_numpy()
    current_density = df.iloc[:, 3].fillna(0.0).to_numpy()
    corrected_current_density = df.iloc[:, 4].to_numpy()
    return {
        'format': 'legacy',
        'time_raw': time_raw,
        'time_shifted': time_shifted,
        'rise_signal': el_signal,
        'decay_signal': el_signal,
        'el_signal': el_signal,
        'current_density': current_density,
        'corrected_current_density': corrected_current_density,
    }


def _rise_region_mask(time_raw: np.ndarray, time_shifted: np.ndarray, lowfreq: bool) -> np.ndarray:
    """Rise 분석 대상 구간 마스크."""
    if lowfreq:
        return time_raw > 0
    return time_shifted <= 0


def extract_normalized_intensity(content: str) -> List[float]:
    """Normalized intensity 열만 추출 (lowfreq: rise, legacy: col2)"""
    try:
        parsed = parse_trel_csv_extended(content)
        if parsed:
            return parsed['rise_signal'].tolist()

        lines = content.splitlines()
        start_idx = 3
        for i, line in enumerate(lines[:20]):
            if re.match(r'^\s*-?\d', line):
                start_idx = i
                break

        df = pd.read_csv(io.StringIO(content), skiprows=start_idx, header=None, usecols=[1])
        if not df.empty:
            return df.iloc[:, 0].tolist()
    except Exception:
        pass
    return []


def analyze_rise_threshold(
    time_raw: np.ndarray,
    time_shifted: np.ndarray,
    el_signal: np.ndarray,
    low_pct: float,
    high_pct: float,
    lowfreq: bool = False,
) -> Dict:
    """
    Rise 파라미터 분석 (t_delay, t_rise, t_saturation)
    - 분석 대상: time_shifted <= 0 구간 (Pulse ON 전체)
    - 알고리즘: Center-Out Search (t_50 기준 양방향 탐색)
      1. t_50 (50%) 지점 찾기 (가장 가파르고 안정적인 구간)
      2. t_delay: t_50에서 왼쪽(과거)으로 역추적하여 Low% 찾기
      3. t_saturation: t_50에서 오른쪽(미래)으로 탐색하여 High% 찾기
    """
    low_val = low_pct / 100.0
    high_val = high_pct / 100.0
    mid_val = 0.5  # 50% 기준점

    # 1. Rise 구간 필터링
    mask = _rise_region_mask(time_raw, time_shifted, lowfreq)
    
    if not np.any(mask):
        return {'t_delay': None, 't_rise': None, 't_saturation': None, 'error': 'Rise 구간 없음'}

    t_r = time_raw[mask]
    y_r = el_signal[mask]

    if len(t_r) < 5:
        return {'t_delay': None, 't_rise': None, 't_saturation': None, 'error': '데이터 부족'}

    # 2. t_50 (50%) 찾기 - Forward Search
    # User Request: t_50은 t > 0 구간에서 먼저 찾기 (Trigger 이후의 Rise만 유효하다고 가정)
    
    # t > 0 인 인덱스 찾기
    start_idx = 0
    for i, t_val in enumerate(t_r):
        if t_val > 0:
            start_idx = i
            break
            
    idx_50 = -1
    
    # 1차 시도: t > 0 구간에서 탐색
    for i in range(start_idx, len(t_r) - 1):
        if (y_r[i] <= mid_val <= y_r[i+1]) or (y_r[i] >= mid_val >= y_r[i+1]):
            idx_50 = i
            break
            
    # 2차 시도: 만약 못 찾았다면 전체 구간에서 탐색 (Fallback)
    # (예: Pre-trigger 구간에 Rise가 있는 경우 등)
    if idx_50 == -1 and start_idx > 0:
        for i in range(len(t_r) - 1):
             if (y_r[i] <= mid_val <= y_r[i+1]) or (y_r[i] >= mid_val >= y_r[i+1]):
                idx_50 = i
                break
            
    if idx_50 == -1:
        return {'t_delay': None, 't_rise': None, 't_saturation': None, 'error': '50% 도달 실패'}

    # 3. 양방향 탐색 (Center-Out)
    
    def interpolate_crossing(t_arr, y_arr, target, direction='forward'):
        """
        주어진 방향으로 target을 교차하는 지점을 찾아 선형 보간.
        direction='forward': 인덱스 증가 방향 (t_50 -> t_sat)
        direction='backward': 인덱스 감소 방향 (t_50 -> t_delay)
        """
        if direction == 'forward':
            # t_50부터 끝까지 검색
            for i in range(len(t_arr) - 1):
                val_a, val_b = y_arr[i], y_arr[i+1]
                if (val_a <= target <= val_b) or (val_a >= target >= val_b):
                    if abs(val_b - val_a) < 1e-12: return t_arr[i+1]
                    frac = (target - val_a) / (val_b - val_a)
                    return t_arr[i] + frac * (t_arr[i+1] - t_arr[i])
        else: # backward
            # t_50부터 시작점(0)까지 역순 검색
            for i in range(len(t_arr) - 2, -1, -1):
                val_a, val_b = y_arr[i], y_arr[i+1]
                if (val_a <= target <= val_b) or (val_a >= target >= val_b):
                    if abs(val_b - val_a) < 1e-12: return t_arr[i+1]
                    frac = (target - val_a) / (val_b - val_a)
                    return t_arr[i] + frac * (t_arr[i+1] - t_arr[i])
        return None

    try:
        # t_delay: t_50 이전 데이터(idx_50 포함)에서 Backward Search
        t_sub_del = t_r[:idx_50+2] # +2 to include idx_50 and idx_50+1 for interpolation context
        y_sub_del = y_r[:idx_50+2]
        t_del = interpolate_crossing(t_sub_del, y_sub_del, low_val, direction='backward')
        
        # t_saturation: t_50 이후 데이터(idx_50 포함)에서 Forward Search
        t_sub_sat = t_r[idx_50:]
        y_sub_sat = y_r[idx_50:]
        t_sat = interpolate_crossing(t_sub_sat, y_sub_sat, high_val, direction='forward')

        if t_del is None or t_sat is None:
            return {'t_delay': None, 't_rise': None, 't_saturation': None, 'error': '임계값 도달 실패'}

        if t_sat < t_del:
             t_del, t_sat = t_sat, t_del
             
        t_rise = t_sat - t_del
        return {'t_delay': t_del, 't_rise': t_rise, 't_saturation': t_sat}

    except Exception:
        return {'t_delay': None, 't_rise': None, 't_saturation': None, 'error': '분석 중 오류'}


def _smooth_moving_average(values: np.ndarray, window_size: int) -> np.ndarray:
    """접선 탐색용 이동 평균. 노이즈에 민감한 최대 기울기 선택을 완화한다."""
    arr = np.asarray(values, dtype=float)
    if len(arr) < 3:
        return arr

    window_size = int(max(3, min(window_size, len(arr))))
    if window_size % 2 == 0:
        window_size += 1

    kernel = np.ones(window_size, dtype=float) / window_size
    return np.convolve(arr, kernel, mode='same')


def analyze_rise_tangent(
    time_raw: np.ndarray,
    time_shifted: np.ndarray,
    el_signal: np.ndarray,
    window_points: int = DEFAULT_TANGENT_WINDOW_POINTS,
    lowfreq: bool = False,
) -> Dict:
    """
    Single Tangent Method
    - raw 데이터를 가볍게 평활화한 뒤 sliding window 선형 회귀로 상승 접선을 찾음
    - 노이즈 spike만 최대 기울기로 선택되지 않도록 상승 폭이 큰 구간을 우선함
    - t_delay = -b/m, t_saturation = (1-b)/m, t_rise = t_saturation - t_delay
    """
    mask = _rise_region_mask(time_raw, time_shifted, lowfreq)
    if not np.any(mask):
        return {'t_delay': None, 't_rise': None, 't_saturation': None, 'error': 'Rise 구간 없음'}

    t_r = np.asarray(time_raw[mask], dtype=float)
    y_r = np.asarray(el_signal[mask], dtype=float)

    finite_mask = np.isfinite(t_r) & np.isfinite(y_r)
    t_r = t_r[finite_mask]
    y_r = y_r[finite_mask]

    if len(t_r) < 5:
        return {'t_delay': None, 't_rise': None, 't_saturation': None, 'error': '데이터 부족'}

    sort_idx = np.argsort(t_r)
    t_r = t_r[sort_idx]
    y_r = y_r[sort_idx]

    window_points = int(max(3, min(window_points, len(t_r))))
    if window_points < 3:
        return {'t_delay': None, 't_rise': None, 't_saturation': None, 'error': '윈도우 데이터 부족'}

    smooth_window = int(max(window_points, min(15, len(t_r) // 20 * 2 + 1)))
    y_smooth = _smooth_moving_average(y_r, smooth_window)

    preferred_candidates = []
    fallback_candidates = []

    for start in range(0, len(t_r) - window_points + 1):
        end = start + window_points
        win_t = t_r[start:end]
        win_y = y_smooth[start:end]

        if np.ptp(win_t) <= 1e-12:
            continue
        if win_y[-1] <= win_y[0] + 0.01:
            continue

        try:
            slope, intercept = np.polyfit(win_t, win_y, 1)
        except Exception:
            continue

        if not np.isfinite(slope) or not np.isfinite(intercept) or slope <= 0:
            continue

        rise_span = float(np.ptp(win_y))
        score = float(slope * rise_span)
        candidate = {
            'score': score,
            'slope': float(slope),
            'intercept': float(intercept),
            'window_start': float(win_t[0]),
            'window_end': float(win_t[-1]),
        }

        if np.any(win_t >= 0):
            preferred_candidates.append(candidate)
        else:
            fallback_candidates.append(candidate)

    candidates = preferred_candidates or fallback_candidates
    if not candidates:
        return {'t_delay': None, 't_rise': None, 't_saturation': None, 'error': '유효한 접선 구간 없음'}

    best = max(candidates, key=lambda item: item['score'])
    slope = best['slope']
    intercept = best['intercept']

    if abs(slope) < 1e-12:
        return {'t_delay': None, 't_rise': None, 't_saturation': None, 'error': '기울기 계산 실패'}

    t_delay = -intercept / slope
    t_saturation = (1.0 - intercept) / slope

    if not np.isfinite(t_delay) or not np.isfinite(t_saturation):
        return {'t_delay': None, 't_rise': None, 't_saturation': None, 'error': '접선 교차 계산 실패'}

    if t_saturation < t_delay:
        t_delay, t_saturation = t_saturation, t_delay

    return {
        't_delay': float(t_delay),
        't_saturation': float(t_saturation),
        't_rise': float(t_saturation - t_delay),
        'tangent_slope': slope,
        'tangent_intercept': intercept,
        'tangent_window_start': best['window_start'],
        'tangent_window_end': best['window_end'],
        'tangent_window_points': window_points,
    }


def analyze_rise(
    time_raw: np.ndarray,
    time_shifted: np.ndarray,
    el_signal: np.ndarray,
    low_pct: float,
    high_pct: float,
    rise_mode: str = DEFAULT_RISE_ANALYSIS_MODE,
    tangent_window_points: int = DEFAULT_TANGENT_WINDOW_POINTS,
    lowfreq: bool = False,
) -> Dict:
    """상승부 분석 모드별 디스패처."""
    normalized_mode = normalize_rise_analysis_mode(rise_mode)
    if normalized_mode == 'tangent':
        result = analyze_rise_tangent(
            time_raw,
            time_shifted,
            el_signal,
            window_points=tangent_window_points,
            lowfreq=lowfreq,
        )
    else:
        result = analyze_rise_threshold(
            time_raw, time_shifted, el_signal, low_pct, high_pct, lowfreq=lowfreq
        )

    result['rise_mode'] = format_rise_analysis_mode(normalized_mode)
    return result


def find_subsample_peak(t_pts: np.ndarray, y_pts: np.ndarray) -> float:
    """3점 포물선 보간으로 소수점 단위 피크 시점 탐색. Jitter 완화."""
    idx = int(np.argmax(np.abs(y_pts)))
    if 0 < idx < len(t_pts) - 1:
        x = np.asarray(t_pts[idx - 1 : idx + 2], dtype=float)
        y = np.abs(np.asarray(y_pts[idx - 1 : idx + 2], dtype=float))
        if np.any(~np.isfinite(y)) or np.any(y <= 0):
            return float(t_pts[idx])
        try:
            a, b, c = np.polyfit(x, y, 2)
            if a >= 0:
                return float(t_pts[idx])
            t_true = -b / (2.0 * a)
            if t_pts[idx - 1] <= t_true <= t_pts[idx + 1]:
                return float(t_true)
        except Exception:
            pass
    return float(t_pts[idx])


def multi_exponential_shifted(x, *args):
    """y = y0 + Σ A_i * exp(-x/tau_i), where x = t - fit_start (x>=0)"""
    n = (len(args) - 1) // 2
    y0 = args[-1]
    res = y0
    for i in range(n):
        res += args[2 * i] * np.exp(-x / args[2 * i + 1])
    return res


def multi_exponential_shifted_t0(x, t0, *args):
    """y = y0 + Σ A_i * exp(-(x - t0)/tau_i). t0로 x축 정렬 보정 (peak와 decay 시작점 불일치 대응)"""
    n = (len(args) - 1) // 2
    y0 = args[-1]
    x_eff = np.asarray(x, dtype=float) - t0
    res = np.full_like(x_eff, y0, dtype=float)
    for i in range(n):
        res += args[2 * i] * np.exp(-x_eff / args[2 * i + 1])
    return res


def _sort_decay_params(params: np.ndarray) -> np.ndarray:
    """tau 오름차순으로 decay 파라미터 재정렬."""
    n_exps = (len(params) - 1) // 2
    y0 = params[-1]
    pairs = []
    for i in range(n_exps):
        pairs.append((params[2 * i], params[2 * i + 1]))
    pairs.sort(key=lambda item: item[1])

    sorted_params = []
    for amplitude, tau_value in pairs:
        sorted_params.extend([amplitude, tau_value])
    sorted_params.append(y0)
    return np.array(sorted_params, dtype=float)


def _compute_r_squared(y_data: np.ndarray, y_pred: np.ndarray) -> float:
    """R² (coefficient of determination) 계산. 1에 가까울수록 피팅 양호."""
    y_d = np.asarray(y_data, dtype=float)
    y_p = np.asarray(y_pred, dtype=float)
    valid = np.isfinite(y_d) & np.isfinite(y_p)
    if np.sum(valid) < 2:
        return float('nan')
    y_d = y_d[valid]
    y_p = y_p[valid]
    ss_res = np.sum((y_d - y_p) ** 2)
    ss_tot = np.sum((y_d - np.mean(y_d)) ** 2)
    if ss_tot < 1e-20:
        return float('nan')
    return float(1.0 - ss_res / ss_tot)


def _calculate_tau_avg(params: np.ndarray) -> float:
    """가중 평균 tau 계산."""
    amplitudes = params[0:-1:2]
    taus = params[1:-1:2]
    numerator = np.sum(amplitudes * (taus ** 2))
    denominator = np.sum(amplitudes * taus)
    return float(numerator / (denominator + 1e-20))


def _sort_decay_params_with_t0(params: np.ndarray) -> np.ndarray:
    """t0 포함 파라미터: [t0, A1, tau1, A2, tau2, ..., y0]를 tau 오름차순으로 재정렬."""
    t0 = params[0]
    y0 = params[-1]
    n_exps = (len(params) - 2) // 2
    pairs = [(params[1 + 2 * i], params[2 + 2 * i]) for i in range(n_exps)]
    pairs.sort(key=lambda item: item[1])
    out = [t0]
    for a, tau in pairs:
        out.extend([a, tau])
    out.append(y0)
    return np.array(out, dtype=float)


def _calculate_tau_avg_spike(params: np.ndarray) -> float:
    """t0 포함 spike 파라미터의 tau 가중평균. params = [t0, A1, tau1, A2, tau2, ..., y0]"""
    amplitudes = params[1:-1:2]
    taus = params[2:-1:2]
    numerator = np.sum(amplitudes * (taus ** 2))
    denominator = np.sum(amplitudes * taus)
    return float(numerator / (denominator + 1e-20))


def fit_decay(
    time_shifted: np.ndarray,
    el_signal: np.ndarray,
    n_params: int = 2,
    decay_fit_start_us: float = 0.0,
    decay_fit_end_us: Optional[float] = None,
    initial_params: Optional[List[float]] = None,
) -> Tuple[Optional[np.ndarray], Optional[float], Optional[np.ndarray], Optional[float]]:
    """
    Decay Fitting
    - 대상: decay_fit_start_us <= time <= decay_fit_end_us (end 미지정 시 start 이후 전체)
    - x = time - decay_fit_start_us 로 원점을 이동한 뒤
      y = y0 + Σ A_i * exp(-x/tau_i) 를 직접 피팅
    """
    fit_start = max(float(decay_fit_start_us), 0.0)
    mask = time_shifted >= fit_start
    if decay_fit_end_us is not None and np.isfinite(decay_fit_end_us):
        mask = mask & (time_shifted <= float(decay_fit_end_us))
    t_fit = time_shifted[mask]
    y_fit = el_signal[mask]
    x_fit = t_fit - fit_start
    
    if len(t_fit) < n_params * 2 + 2:
        return None, None, None, None

    # Initial Guesses
    max_y = np.max(y_fit) if len(y_fit) > 0 else 1.0
    min_y = np.min(y_fit) if len(y_fit) > 0 else 0.0
    tail_count = max(5, min(len(y_fit), int(np.ceil(len(y_fit) * 0.1))))
    y0_guess = float(np.mean(y_fit[-tail_count:])) if tail_count > 0 else min_y
    y0_guess = max(y0_guess, 0.0)
    
    p0_default = []
    for i in range(n_params):
        p0_default.extend([max(max_y - y0_guess, 1e-10) / n_params, 1.0 * (i + 1)]) # A_i, tau_i
    # Tail average gives a more stable background offset guess than raw min(y).
    p0_default.append(y0_guess) # y0

    # Bounds: log-domain fitting을 위해 전체 모델이 양수가 되도록 y0 >= 0으로 제한
    lower = [0.0, 1e-10] * n_params + [0.0]
    upper = [np.inf, np.inf] * n_params + [np.inf]

    p0 = p0_default
    if initial_params is not None and len(initial_params) == len(p0_default):
        try:
            p0_arr = np.array(initial_params, dtype=float)
            if np.all(np.isfinite(p0_arr)):
                # Respect bounds for stable warm-start.
                for i in range(n_params):
                    amp_idx = 2 * i
                    tau_idx = 2 * i + 1
                    p0_arr[amp_idx] = max(p0_arr[amp_idx], 0.0)
                    p0_arr[tau_idx] = max(p0_arr[tau_idx], 1e-10)
                p0_arr[-1] = max(p0_arr[-1], 0.0)
                p0 = p0_arr.tolist()
        except Exception:
            p0 = p0_default

    # Weighted Linear Fitting: sigma = max(y_data, floor)
    # 값이 작을수록(테일일수록) sigma 작음 → 가중치 높음 (로그 피팅과 유사한 효과)
    # 노이즈 층 방지를 위해 최소값 제한
    y_floor = max(float(np.min(y_fit[y_fit > 0])) if np.any(y_fit > 0) else 1e-10, 0.01)
    sigma = np.maximum(y_fit, y_floor)

    def residuals_weighted(params):
        y_model = multi_exponential_shifted(x_fit, *params)
        return (y_model - y_fit) / sigma

    try:
        result = least_squares(
            residuals_weighted,
            x0=np.array(p0, dtype=float),
            bounds=(np.array(lower, dtype=float), np.array(upper, dtype=float)),
            max_nfev=10000,
        )
        if not result.success:
            raise RuntimeError(result.message)

        popt = _sort_decay_params(result.x)
        tau_avg = _calculate_tau_avg(popt)
        y_pred = multi_exponential_shifted(x_fit, *popt)
        r2 = _compute_r_squared(y_fit, y_pred)
        return popt, tau_avg, y_pred, r2
    except Exception:
        # Warm-start failed: retry once with generic initialization.
        if p0 is not p0_default:
            try:
                result = least_squares(
                    residuals_weighted,
                    x0=np.array(p0_default, dtype=float),
                    bounds=(np.array(lower, dtype=float), np.array(upper, dtype=float)),
                    max_nfev=10000,
                )
                if not result.success:
                    raise RuntimeError(result.message)

                popt = _sort_decay_params(result.x)
                tau_avg = _calculate_tau_avg(popt)
                y_pred = multi_exponential_shifted(x_fit, *popt)
                r2 = _compute_r_squared(y_fit, y_pred)
                return popt, tau_avg, y_pred, r2
            except Exception:
                return None, None, None, None
        return None, None, None, None


def _negative_spike_decay_time_1e(t: np.ndarray, y: np.ndarray) -> Optional[float]:
    """
    abs(current) peak에서 1/e로 감쇠하는 데 걸리는 시간을 interpolation으로 구함.
    반환: decay time (μs), peak 시점부터 1/e 도달 시점까지의 시간.
    """
    if len(t) < 3 or len(y) < 3:
        return None
    y = np.asarray(y, dtype=float)
    idx_peak = int(np.argmax(y))
    y_peak = float(y[idx_peak])
    if y_peak < 1e-20:
        return None
    y_target = y_peak / np.e
    t_peak = float(t[idx_peak])
    for i in range(idx_peak + 1, len(y)):
        if y[i] <= y_target:
            if y[i - 1] > y_target:
                t_lo, t_hi = t[i - 1], t[i]
                y_lo, y_hi = y[i - 1], y[i]
                if y_lo - y_hi < 1e-20:
                    t_1e = t_hi
                else:
                    frac = (y_lo - y_target) / (y_lo - y_hi)
                    t_1e = t_lo + frac * (t_hi - t_lo)
                return float(t_1e - t_peak)
            break
    return None


def analyze_negative_spike(
    time_shifted: np.ndarray,
    current_density: np.ndarray,
) -> Dict:
    """
    Negative spike 단순 분석 (피팅 없음)
    - integral: ∫|J|dt (capacitance 계산용)
    - decay_time_us: peak에서 1/e로 감쇠까지 걸리는 시간 (interpolation)
    """
    mask = time_shifted >= 0
    if not np.any(mask):
        return {'error': 'Negative spike 구간 없음'}

    t_spike = np.asarray(time_shifted[mask], dtype=float)
    j_spike_raw = np.asarray(current_density[mask], dtype=float)
    finite_mask = np.isfinite(t_spike) & np.isfinite(j_spike_raw)
    t_spike = t_spike[finite_mask]
    j_spike_raw = j_spike_raw[finite_mask]

    if len(t_spike) < 3:
        return {'error': 'Negative spike 데이터 부족'}

    spike_signal = np.abs(j_spike_raw)
    if not np.any(spike_signal > 0):
        return {'error': 'Negative spike 신호 없음'}

    integral = float(_trapz(spike_signal, t_spike))
    decay_time = _negative_spike_decay_time_1e(t_spike, spike_signal)
    idx_peak = int(np.argmax(spike_signal))
    peak_time_us = float(t_spike[idx_peak])

    return {
        't_spike': t_spike,
        'spike_signal': spike_signal,
        'integral': integral,
        'decay_time_us': float(decay_time) if decay_time is not None else None,
        'peak_time_us': peak_time_us,
    }


def parse_after_duty_from_filename(filename: str) -> Optional[str]:
    """파일명에서 duty 뒤에 오는 부분 그대로 추출 (예: duty25%_1h0min -> 25%_1h0min)"""
    m = re.search(r'duty\s*(.+)', filename, re.IGNORECASE)
    if not m:
        return None
    s = m.group(1).strip()
    if s.endswith('.csv'):
        s = s[:-4]
    if s.endswith('_TrEL'):
        s = s[:-5]
    return s if s else None


def parse_duty_from_filename(filename: str) -> Optional[float]:
    """
    파일명에서 Duty(%) 추출
    형식: 260223_CC_7000uA_1000Hz_duty25%_1h0min.csv
    """
    duty_match = re.search(r'duty\s*(\d+(?:\.\d+)?)\s*%?', filename, re.IGNORECASE)
    if duty_match:
        val = float(duty_match.group(1))
        # duty25 -> 0.25, duty0.5 -> 0.005? 보통 파일명엔 % 단위 사용 (25 = 25%)
        duty_fraction = val / 100.0 if val > 1.0 else val
        return duty_fraction
    return None


def analyze_single_file(
    content: str,
    filename: str,
    low_pct: float,
    high_pct: float,
    n_decay: int,
    rise_mode: str = DEFAULT_RISE_ANALYSIS_MODE,
    vil_time_voltage: Optional[List[Tuple[float, float]]] = None,
    vil_time_shift_min: float = 0.0,
    vil_duty_fraction: Optional[float] = None,
    decay_fit_start_us: float = 0.0,
    decay_fit_end_us: Optional[float] = None,
    decay_initial_params: Optional[List[float]] = None,
    tangent_window_points: int = DEFAULT_TANGENT_WINDOW_POINTS,
) -> Dict:
    """
    단일 파일 분석 메인 함수
    """
    parsed = parse_trel_csv_extended(content)
    if not parsed or len(parsed['time_raw']) < 10:
        return {'filename': filename, 'error': '데이터 부족', 'time_min': parse_minutes_from_filename(filename)}

    lowfreq = parsed['format'] == 'lowfreq'
    time_raw = parsed['time_raw']
    time_shifted = parsed['time_shifted']
    rise_signal = parsed['rise_signal']
    decay_signal = parsed['decay_signal']
    current_density = parsed['current_density']
    corrected_current_density = parsed['corrected_current_density']

    normalized_rise_mode = normalize_rise_analysis_mode(rise_mode)
    rise_res = analyze_rise(
        time_raw,
        time_shifted,
        rise_signal,
        low_pct,
        high_pct,
        rise_mode=normalized_rise_mode,
        tangent_window_points=tangent_window_points,
        lowfreq=lowfreq,
    )

    decay_time = time_raw if lowfreq else time_shifted
    decay_signal_fit = decay_signal if lowfreq else parsed['el_signal']
    popt, tau_avg, _, decay_r2 = fit_decay(
        decay_time,
        decay_signal_fit,
        n_decay,
        decay_fit_start_us,
        decay_fit_end_us=decay_fit_end_us,
        initial_params=decay_initial_params,
    )
    spike_time = time_raw if lowfreq else time_shifted
    spike_res = analyze_negative_spike(spike_time, current_density)

    # Result Assembly
    time_min_raw = parse_minutes_from_filename(filename)
    after_duty = parse_after_duty_from_filename(filename)
    
    # 파일명의 실측 degradation 시각(wall-clock) 사용 — duty 보정은 VIL Time(min)에만 적용
    time_min = time_min_raw

    result = {
        'filename': filename,
        'time_min': time_min,
        'after_duty': after_duty,
        'rise_mode': rise_res.get('rise_mode', format_rise_analysis_mode(normalized_rise_mode)),
        'rise_slope': rise_res.get('tangent_slope'),
        't_delay': rise_res.get('t_delay'),
        't_rise': rise_res.get('t_rise'),
        't_saturation': rise_res.get('t_saturation'),
        'rise_error': rise_res.get('error')
    }
    
    if vil_time_voltage and time_min is not None:
        vil_lookup_min = wall_clock_to_vil_duty_min(
            time_min, vil_time_shift_min, vil_duty_fraction
        )
        v = interpolate_voltage_at_time(vil_lookup_min, vil_time_voltage)
        if v is not None:
            result['voltage'] = v
        lum = interpolate_luminance_at_time(vil_lookup_min, vil_time_voltage)
        if lum is not None:
            result['relative_luminance'] = lum
            
    if np.any(np.isfinite(corrected_current_density)):
        result['has_corrected_current_density'] = True
        
    if popt is not None:
        # popt format: [A1, tau1, A2, tau2, ..., y0]
        n_exps = (len(popt) - 1) // 2
        amplitudes = np.array([popt[2 * i] for i in range(n_exps)], dtype=float)
        amp_sum = float(np.sum(amplitudes))
        for i in range(n_exps):
            result[f'tau_{i + 1}'] = popt[2 * i + 1]
            if amp_sum > 0:
                result[f'f_{i + 1}'] = float(amplitudes[i] / amp_sum)
            else:
                result[f'f_{i + 1}'] = None
        result['tau_avg'] = tau_avg
        result['popt'] = popt.tolist()
        result['decay_r2'] = float(decay_r2) if decay_r2 is not None and np.isfinite(decay_r2) else None

    if not spike_res.get('error'):
        result['spike_integral'] = spike_res.get('integral')
        result['spike_decay_time_us'] = spike_res.get('decay_time_us')
        result['spike_peak_time_us'] = spike_res.get('peak_time_us')
    else:
        result['spike_error'] = spike_res.get('error')

    return result


def get_preview_data(
    content: str,
    low_pct: float,
    high_pct: float,
    n_decay: int,
    rise_mode: str = DEFAULT_RISE_ANALYSIS_MODE,
    decay_fit_start_us: float = 0.0,
    decay_fit_end_us: Optional[float] = None,
    decay_initial_params: Optional[List[float]] = None,
    tangent_window_points: int = DEFAULT_TANGENT_WINDOW_POINTS,
) -> Dict:
    """미리보기용 데이터 생성 (피팅 없이 신호 + integral + decay_time만)"""
    parsed = parse_trel_csv_extended(content)
    if not parsed or len(parsed['time_raw']) < 10:
        return {'error': '데이터 부족'}

    lowfreq = parsed['format'] == 'lowfreq'
    time_raw = parsed['time_raw']
    time_shifted = parsed['time_shifted']
    rise_signal = parsed['rise_signal']
    decay_signal = parsed['decay_signal']
    current_density = parsed['current_density']

    normalized_rise_mode = normalize_rise_analysis_mode(rise_mode)
    rise_res = analyze_rise(
        time_raw,
        time_shifted,
        rise_signal,
        low_pct,
        high_pct,
        rise_mode=normalized_rise_mode,
        tangent_window_points=tangent_window_points,
        lowfreq=lowfreq,
    )
    fit_start = max(float(decay_fit_start_us), 0.0)
    default_preview_max = 250.0 if lowfreq else 50.0
    fit_end = float(decay_fit_end_us) if decay_fit_end_us is not None and np.isfinite(decay_fit_end_us) else default_preview_max
    decay_time = time_raw if lowfreq else time_shifted
    decay_signal_fit = decay_signal if lowfreq else parsed['el_signal']
    popt, tau_avg, y_fit, _ = fit_decay(
        decay_time,
        decay_signal_fit,
        n_decay,
        fit_start,
        decay_fit_end_us=fit_end,
        initial_params=decay_initial_params,
    )
    # Preview Data Construction
    # 1. Rise Preview
    # Threshold: trigger 이후 0~100us 중심 미리보기
    # Tangent: t_delay/t_saturation 주변으로 확대해 접선과 신호를 함께 표시
    if normalized_rise_mode == 'tangent':
        t_delay = rise_res.get('t_delay')
        t_saturation = rise_res.get('t_saturation')
        if t_delay is not None and t_saturation is not None:
            span = abs(float(t_saturation) - float(t_delay))
            margin = max(5.0, span * 0.35)
            preview_min = min(float(t_delay), float(t_saturation)) - margin
            preview_max = max(float(t_delay), float(t_saturation)) + margin
        else:
            preview_min, preview_max = 0.0, 100.0
        mask_rise = (time_raw >= preview_min) & (time_raw <= preview_max)
    else:
        preview_min, preview_max = 0.0, 100.0
        mask_rise = (time_raw >= preview_min) & (time_raw <= preview_max)
    t_rise_preview = time_raw[mask_rise]
    y_rise_preview = rise_signal[mask_rise]
    
    # Downsample
    if len(t_rise_preview) > 1500:
        step = max(1, (len(t_rise_preview) + 1499) // 1500)
        t_rise_preview = t_rise_preview[::step]
        y_rise_preview = y_rise_preview[::step]

    # Convert to list and replace NaN/Inf with None
    def safe_list(arr):
        return [float(x) if np.isfinite(x) else None for x in arr]

    rise_data = {
        'analysis_mode': rise_res.get('rise_mode', format_rise_analysis_mode(normalized_rise_mode)),
        'axis_mode': 'linear' if normalized_rise_mode == 'tangent' else 'log',
        'preview_x_min': float(preview_min),
        'preview_x_max': float(preview_max),
        'time_raw': safe_list(t_rise_preview),
        'el_signal_rise': safe_list(y_rise_preview),
        't_delay': float(rise_res.get('t_delay')) if rise_res.get('t_delay') is not None else None,
        't_saturation': float(rise_res.get('t_saturation')) if rise_res.get('t_saturation') is not None else None,
        't_rise': float(rise_res.get('t_rise')) if rise_res.get('t_rise') is not None else None,
        'tangent_slope': float(rise_res.get('tangent_slope')) if rise_res.get('tangent_slope') is not None else None,
        'tangent_intercept': float(rise_res.get('tangent_intercept')) if rise_res.get('tangent_intercept') is not None else None,
        'tangent_window_start': float(rise_res.get('tangent_window_start')) if rise_res.get('tangent_window_start') is not None else None,
        'tangent_window_end': float(rise_res.get('tangent_window_end')) if rise_res.get('tangent_window_end') is not None else None,
        'tangent_window_points': int(rise_res.get('tangent_window_points')) if rise_res.get('tangent_window_points') is not None else None,
        'rise_error': rise_res.get('error'),
    }

    # 2. Decay Preview
    decay_preview_max = max(fit_end, fit_start + 1.0)
    mask_decay = (decay_time >= fit_start) & (decay_time <= decay_preview_max)
    t_decay_preview = decay_time[mask_decay]
    y_decay_preview = decay_signal_fit[mask_decay]
    
    # Avoid log(0) or negative (just in case)
    y_decay_log = []
    if len(y_decay_preview) > 0:
         # Use np.maximum to avoid log(<=0)
         y_decay_log = np.log10(np.maximum(y_decay_preview, 1e-10))
    
    # Downsample
    if len(t_decay_preview) > 1500:
        step = max(1, (len(t_decay_preview) + 1499) // 1500)
        t_decay_preview = t_decay_preview[::step]
        if len(y_decay_log) > 0:
            y_decay_log = y_decay_log[::step]
        
    decay_data = {
        'time_decay': safe_list(t_decay_preview),
        'el_signal_decay_log': safe_list(y_decay_log),
        'format': parsed['format'],
        'decay_fit_start_us': float(fit_start),
        'decay_fit_end_us': float(fit_end),
    }

    if popt is not None and y_fit is not None:
        fit_mask = (decay_time >= fit_start) & (decay_time <= decay_preview_max)
        t_fit_preview = decay_time[fit_mask]
        x_fit_preview = t_fit_preview - fit_start
        y_fit_preview = multi_exponential_shifted(x_fit_preview, *popt)
        y_fit_log = np.log10(np.maximum(y_fit_preview, 1e-10))
        
        # Downsample fit as well
        if len(t_fit_preview) > 1500:
            step = max(1, (len(t_fit_preview) + 1499) // 1500)
            t_fit_preview = t_fit_preview[::step]
            y_fit_log = y_fit_log[::step]
            
        decay_data['t_decay_fit'] = safe_list(t_fit_preview)
        decay_data['y_fit_log'] = safe_list(y_fit_log)

    out = {
        'format': parsed['format'],
        'rise': rise_data,
        'decay': decay_data,
        'tau_avg': float(tau_avg) if tau_avg is not None else None,
        'tau_list': [float(popt[2 * i + 1]) for i in range((len(popt) - 1) // 2)] if popt is not None else [],
    }
    if popt is not None:
        out['decay_popt'] = [float(x) for x in popt]
    return out
