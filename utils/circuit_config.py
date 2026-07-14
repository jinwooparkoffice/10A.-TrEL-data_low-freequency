"""
회로 설정 모듈 (Low-side Sensing)
- 기본 전압 보정 상수: R_total = 33.333 Ω
- 기본 회로 파라미터: R_shunt = 100Ω, R_osc = 50Ω
- 사용자 지정 저항값이 주어지면 R_total = (R_shunt × R_osc) / (R_shunt + R_osc)
"""
import math
from typing import Optional


# 기본값
DEFAULT_R_SHUNT = 100.0  # Ω
DEFAULT_DEVICE_AREA_MM2 = 4.3  # 소자 유효 면적 (mm²)
DEFAULT_R_OSC = 50.0     # Ω
DEFAULT_R_TOTAL = 33.333  # Ω


def calculate_r_total(r_shunt: Optional[float] = None, r_osc: Optional[float] = None) -> float:
    """
    합성 센서 저항 계산
    R_total = (R_shunt × R_osc) / (R_shunt + R_osc)
    
    Args:
        r_shunt: 직렬 센서 저항 (Ω), None이면 기본값 사용
        r_osc: 오실로스코프 내부 저항 (Ω), None이면 기본값 사용
    
    Returns:
        합성 센서 저항 (Ω)
    """
    if r_shunt is None and r_osc is None:
        return DEFAULT_R_TOTAL

    if r_shunt is None:
        r_shunt = DEFAULT_R_SHUNT
    if r_osc is None:
        r_osc = DEFAULT_R_OSC
    
    if r_shunt <= 0 or r_osc <= 0:
        raise ValueError("R_shunt와 R_osc는 양수여야 합니다.")
    
    return (r_shunt * r_osc) / (r_shunt + r_osc)


def resolve_device_area_mm2(value: Optional[float]) -> float:
    """API/폼에서 받은 소자 넓이(mm²). 무효하면 DEFAULT_DEVICE_AREA_MM2."""
    if value is None:
        return DEFAULT_DEVICE_AREA_MM2
    try:
        v = float(value)
    except (TypeError, ValueError):
        return DEFAULT_DEVICE_AREA_MM2
    if not math.isfinite(v) or v <= 0:
        return DEFAULT_DEVICE_AREA_MM2
    return v


def calculate_device_voltage(
    v_applied,
    current_ua,
    r_total: Optional[float] = None
):
    """
    Low-side Sensing 방식에 따른 유효 인가 전압 계산
    V_device = V_applied - V_measured_at_scope
    V_measured_at_scope = I_device × R_total
    
    Args:
        v_applied: 인가 전압 (V) - 스칼라 또는 numpy array
        current_ua: 측정된 전류 (µA) - 스칼라 또는 numpy array
        r_total: 합성 센서 저항 (Ω), None이면 기본값으로 계산
    
    Returns:
        유효 인가 전압 (V) - 입력과 동일한 형태 (스칼라 또는 numpy array)
    """
    import numpy as np
    
    if r_total is None:
        r_total = calculate_r_total()
    
    # numpy array로 변환 (스칼라도 처리 가능)
    v_applied_arr = np.asarray(v_applied)
    current_ua_arr = np.asarray(current_ua)
    
    # µA -> A 변환
    current_a = current_ua_arr / 1e6
    
    # V_measured_at_scope = I × R_total
    v_measured_at_scope = current_a * r_total
    
    # V_device = V_applied - V_measured_at_scope
    v_device = v_applied_arr - v_measured_at_scope
    
    # 입력이 스칼라였으면 스칼라로 반환, 배열이었으면 배열로 반환
    if np.isscalar(v_applied) and np.isscalar(current_ua):
        return float(v_device)
    return v_device


def calculate_device_voltage_from_target_current(
    v_applied: float,
    target_current_ua: float,
    r_total: Optional[float] = None
) -> float:
    """
    파일명의 목표 전류로부터 유효 인가 전압 역산
    V_device = V_applied - (I_target × R_total)
    
    Args:
        v_applied: 인가 전압 (V)
        target_current_ua: 목표 전류 (µA)
        r_total: 합성 센서 저항 (Ω), None이면 기본값으로 계산
    
    Returns:
        유효 인가 전압 (V)
    """
    if r_total is None:
        r_total = calculate_r_total()
    
    # µA -> A 변환
    target_current_a = target_current_ua / 1e6
    
    # V_device = V_applied - (I_target × R_total)
    v_device = v_applied - (target_current_a * r_total)
    
    return v_device
