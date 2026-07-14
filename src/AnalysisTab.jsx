import React, { useState, useEffect, useMemo, useRef } from 'react'
import { apiUrl } from './api'
import { scanAnalysisFolder, indexClosestTo10Min, parseDutyFromFilename, readVilTimeShiftMin } from './fileUtils'

function RisePreviewChart({
  timeRaw,
  elSignal,
  tDelay,
  tSaturation,
  tRise,
  analysisMode,
  axisMode,
  previewXMin,
  previewXMax,
  tangentSlope,
  tangentIntercept,
}) {
  const w = 650
  const h = 260
  const pad = { top: 20, right: 20, bottom: 40, left: 55 }
  const plotW = w - pad.left - pad.right
  const plotH = h - pad.top - pad.bottom

  const isTangentMode = analysisMode === 'Tangent'

  const { pathOrig, pathTangent, xScale, yScale, xTicks, xMin, xMax } = useMemo(() => {
    try {
      if (!timeRaw?.length || !elSignal?.length) return {}

      const rawPoints = timeRaw
        .map((t, i) => ({ t, y: elSignal[i] }))
        .filter(({ t, y }) => Number.isFinite(t) && Number.isFinite(y))
        .sort((a, b) => a.t - b.t)

      const filtered = axisMode === 'linear'
        ? rawPoints
        : rawPoints.filter(({ t }) => t > 0)

      if (filtered.length === 0) return {}

      const tVals = filtered.map(({ t }) => t)
      const xMinVal = axisMode === 'linear'
        ? (Number.isFinite(previewXMin) ? previewXMin : Math.min(...tVals))
        : Math.min(...tVals)
      const xMaxVal = axisMode === 'linear'
        ? (Number.isFinite(previewXMax) ? previewXMax : Math.max(...tVals))
        : Math.max(...tVals)
      const yVals = filtered.map(({ y }) => y)
      const validY = yVals.filter(y => Number.isFinite(y))
      const yMin = validY.length ? Math.min(...validY) : 0
      const yMax = validY.length ? Math.max(...validY) : 1
      const yRange = yMax - yMin || 1

      let xScale
      let xTicks = []
      if (axisMode === 'linear') {
        const xRange = xMaxVal - xMinVal || 1
        xScale = v => {
          if (v == null || !Number.isFinite(v)) return pad.left
          return pad.left + ((v - xMinVal) / xRange) * plotW
        }

        const tickCount = 5
        for (let i = 0; i <= tickCount; i += 1) {
          xTicks.push(xMinVal + ((xMaxVal - xMinVal) * i) / tickCount)
        }
      } else {
        const logMin = Math.log10(Math.max(xMinVal, 0.1))
        const logMax = Math.log10(Math.max(xMaxVal, 10))
        if (!Number.isFinite(logMin) || !Number.isFinite(logMax)) return {}

        const logRange = logMax - logMin || 1
        xScale = v => {
          if (v == null || !Number.isFinite(v) || v <= 0) return pad.left
          const logV = Math.log10(v)
          if (!Number.isFinite(logV)) return pad.left
          if (logV < logMin) return pad.left
          if (logV > logMax) return pad.left + plotW
          return pad.left + ((logV - logMin) / logRange) * plotW
        }

        let p = Math.floor(logMin)
        let safety = 0
        const endP = Math.ceil(logMax)
        while (p <= endP && safety < 100) {
          const val = Math.pow(10, p)
          if (val >= Math.pow(10, logMin) && val <= Math.pow(10, logMax)) {
            xTicks.push(val)
          }
          p += 1
          safety += 1
        }
      }

      const yScale = v => {
        if (v == null || !Number.isFinite(v)) return pad.top + plotH
        return pad.top + plotH - ((v - yMin) / yRange) * plotH
      }

      const ptsOrig = filtered
        .map(({ t, y }) => {
          const x = xScale(t)
          const yCoord = yScale(y)
          if (!Number.isFinite(x) || !Number.isFinite(yCoord)) return null
          return `${x},${yCoord}`
        })
        .filter(pt => pt !== null)
        .join(' L ')

      const pathOrig = ptsOrig ? `M ${ptsOrig}` : ''

      let pathTangent = ''
      if (isTangentMode && Number.isFinite(tangentSlope) && Number.isFinite(tangentIntercept)) {
        const tangentTimes = []
        const tangentStart = Number.isFinite(tDelay) && Number.isFinite(tSaturation)
          ? Math.min(tDelay, tSaturation)
          : xMinVal
        const tangentEnd = Number.isFinite(tDelay) && Number.isFinite(tSaturation)
          ? Math.max(tDelay, tSaturation)
          : xMaxVal
        const sampleCount = 120
        for (let i = 0; i < sampleCount; i += 1) {
          tangentTimes.push(tangentStart + ((tangentEnd - tangentStart) * i) / (sampleCount - 1))
        }

        const tangentPts = tangentTimes
          .map(t => {
            const y = tangentSlope * t + tangentIntercept
            const x = xScale(t)
            const yCoord = yScale(y)
            if (!Number.isFinite(x) || !Number.isFinite(yCoord)) return null
            return `${x},${yCoord}`
          })
          .filter(Boolean)
          .join(' L ')

        pathTangent = tangentPts ? `M ${tangentPts}` : ''
      }

      return { pathOrig, pathTangent, xScale, yScale, xTicks, xMin: xMinVal, xMax: xMaxVal }
    } catch (e) {
      console.error('Rise Chart Error:', e)
      return {}
    }
  }, [timeRaw, elSignal, axisMode, isTangentMode, previewXMin, previewXMax, tangentSlope, tangentIntercept, tDelay, tSaturation])

  if (!pathOrig) return (
    <div style={{ height: h, background: '#f5f5f5', borderRadius: 6, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#888' }}>
      데이터 없음
    </div>
  )

  const showDelayLine = Number.isFinite(tDelay) && xScale && (axisMode === 'linear' || tDelay > 0)
  const showSaturationLine = Number.isFinite(tSaturation) && xScale && (axisMode === 'linear' || tSaturation > 0)
  const showSaturationPoint = Number.isFinite(tSaturation) && yScale && (axisMode === 'linear' || tSaturation > 0)
  const riseLabel = Number.isFinite(tRise) ? `t_rise = ${tRise.toFixed(3)} μs` : 't_rise'

  return (
    <svg width={w} height={h} style={{ display: 'block', background: '#fff', borderRadius: 6, border: '1px solid #ddd' }}>
      <path d={pathOrig} fill="none" stroke="#2196f3" strokeWidth={1.5} strokeLinejoin="round" opacity={0.9} />
      {pathTangent && (
        <path d={pathTangent} fill="none" stroke="#9c27b0" strokeWidth={1.8} strokeDasharray="4,2" strokeLinejoin="round" opacity={0.95} />
      )}
      {showDelayLine && (
        <line x1={xScale(tDelay)} y1={pad.top} x2={xScale(tDelay)} y2={pad.top + plotH} stroke="#4caf50" strokeWidth={1} strokeDasharray="2,2" />
      )}
      {showSaturationLine && (
        <line x1={xScale(tSaturation)} y1={pad.top} x2={xScale(tSaturation)} y2={pad.top + plotH} stroke="#ff9800" strokeWidth={1} strokeDasharray="2,2" />
      )}
      {showSaturationPoint && (
        <circle cx={xScale(tSaturation)} cy={yScale(1)} r={4} fill="#ff9800" stroke="#fff" strokeWidth={1} />
      )}
      {xTicks?.map((v, i) => (
        <g key={`tick-${i}-${v}`}>
          <line x1={xScale(v)} y1={pad.top + plotH} x2={xScale(v)} y2={pad.top + plotH + 4} stroke="#333" strokeWidth={1} />
          <text x={xScale(v)} y={h - 8} fontSize={9} fill="#333" textAnchor="middle">
            {axisMode === 'linear' ? v.toFixed(2) : (v >= 1 ? v : v.toExponential(0))}
          </text>
        </g>
      ))}
      <text x={pad.left} y={h - 10} fontSize={11} fill="#666">
        {axisMode === 'linear' ? `Time (μs), Linear (${xMin.toFixed(2)}~${xMax.toFixed(2)})` : 'Time (μs), Log (0.1~100μs)'}
      </text>
      {isTangentMode && <text x={w - 140} y={pad.top + 14} fontSize={10} fill="#9c27b0">-- tangent fit</text>}
      <text x={w - 140} y={pad.top + 28} fontSize={10} fill="#4caf50">| t_delay</text>
      <text x={w - 140} y={pad.top + 42} fontSize={10} fill="#ff9800">| t_saturation</text>
      {isTangentMode && <text x={w - 170} y={pad.top + 56} fontSize={10} fill="#555">{riseLabel}</text>}
    </svg>
  )
}

function LogFitPreviewChart({ timeData, signalLog, timeFit, fitLog, xLabel, fitStartUs, fitEndUs }) {
  const w = 650
  const h = 260
  const pad = { top: 20, right: 20, bottom: 40, left: 55 }
  const plotW = w - pad.left - pad.right
  const plotH = h - pad.top - pad.bottom

  const { pathOrig, pathFit, xScale, yScale, xMin, xMax } = useMemo(() => {
    try {
      if (!timeData?.length || !signalLog?.length) return {}
      
      // 유효한 데이터만 필터링 (null, NaN, Infinity 제외)
      // 원본 데이터
      const validPoints = []
      for (let i = 0; i < timeData.length; i++) {
        const t = timeData[i]
        const v = signalLog[i]
        if (t != null && v != null && Number.isFinite(t) && Number.isFinite(v)) {
          validPoints.push({ t, v })
        }
      }
      
      if (validPoints.length === 0) return {}

      const validFit = []
      if (timeFit?.length && fitLog?.length) {
        for (let i = 0; i < timeFit.length; i++) {
          const t = timeFit[i]
          const v = fitLog[i]
          if (t != null && v != null && Number.isFinite(t) && Number.isFinite(v)) {
            validFit.push({ t, v })
          }
        }
      }

      const allPoints = validFit.length > 0 ? [...validPoints, ...validFit] : validPoints
      const xMin = Math.min(...allPoints.map(p => p.t))
      const xMax = Math.max(...allPoints.map(p => p.t))
      const yMin = Math.min(...allPoints.map(p => p.v))
      const yMax = Math.max(...allPoints.map(p => p.v))

      const yRange = yMax - yMin || 1
      const xScale = v => {
        if (v == null || !Number.isFinite(v)) return pad.left
        return pad.left + ((v - xMin) / (xMax - xMin || 1)) * plotW
      }
      const yScale = v => {
        if (v == null || !Number.isFinite(v)) return pad.top + plotH
        return pad.top + plotH - ((v - yMin) / yRange) * plotH
      }
      
      const ptsOrig = validPoints
        .map(p => {
          const x = xScale(p.t)
          const y = yScale(p.v)
          if (!Number.isFinite(x) || !Number.isFinite(y)) return null
          return `${x},${y}`
        })
        .filter(pt => pt !== null)
        .join(' L ')
        
      const pathOrig = ptsOrig ? `M ${ptsOrig}` : ''
      
      let pathFit = ''
      if (validFit.length > 0) {
        const ptsFit = validFit
          .map(p => {
            const x = xScale(p.t)
            const y = yScale(p.v)
            if (!Number.isFinite(x) || !Number.isFinite(y)) return null
            return `${x},${y}`
          })
          .filter(pt => pt !== null)
          .join(' L ')
        pathFit = ptsFit ? `M ${ptsFit}` : ''
      }
      return { pathOrig, pathFit, xScale, yScale, xMin, xMax }
    } catch (e) {
      console.error('Log Fit Chart Error:', e)
      return {}
    }
  }, [timeData, signalLog, timeFit, fitLog])

  if (!pathOrig) return (
    <div style={{ height: h, background: '#f5f5f5', borderRadius: 6, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#888' }}>
      데이터 없음
    </div>
  )

  const fitX1 = xScale && Number.isFinite(fitStartUs) ? xScale(Math.max(xMin, fitStartUs)) : 0
  const fitX2 = xScale && Number.isFinite(fitEndUs) ? xScale(Math.min(xMax, fitEndUs)) : 0

  return (
    <svg width={w} height={h} style={{ display: 'block', background: '#fff', borderRadius: 6, border: '1px solid #ddd' }}>
      {Number.isFinite(fitStartUs) && Number.isFinite(fitEndUs) && fitStartUs <= fitEndUs && (
        <rect
          x={fitX1}
          y={pad.top}
          width={Math.max(0, fitX2 - fitX1)}
          height={plotH}
          fill="rgba(76,175,80,0.12)"
          stroke="rgba(76,175,80,0.5)"
          strokeWidth={1}
        />
      )}
      <path d={pathOrig} fill="none" stroke="#2196f3" strokeWidth={1.5} strokeLinejoin="round" opacity={0.9} />
      {pathFit && <path d={pathFit} fill="none" stroke="#e91e63" strokeWidth={1.5} strokeDasharray="4,2" strokeLinejoin="round" />}
      <text x={pad.left} y={h - 10} fontSize={11} fill="#666">{xLabel}</text>
      <text x={pad.left} y={pad.top - 8} fontSize={10} fill="#666">Y: Log scale</text>
      <text x={w - 100} y={pad.top + 14} fontSize={10} fill="#2196f3">— 원본</text>
      <text x={w - 100} y={pad.top + 28} fontSize={10} fill="#e91e63">— 피팅</text>
      {Number.isFinite(fitStartUs) && Number.isFinite(fitEndUs) && (
        <text x={w - 140} y={pad.top + 42} fontSize={10} fill="rgba(76,175,80,0.9)">■ 피팅 구간</text>
      )}
    </svg>
  )
}

export default function AnalysisTab({ backendStatus }) {
  const [analysisFolderReady, setAnalysisFolderReady] = useState(false)
  const [analysisFiles, setAnalysisFiles] = useState([])
  const [analysisVilFiles, setAnalysisVilFiles] = useState([])
  const [analysisDirHandle, setAnalysisDirHandle] = useState(null)
  const [riseMode, setRiseMode] = useState('tangent')
  const [lowPct, setLowPct] = useState(0.1)
  const [highPct, setHighPct] = useState(99)
  const [nDecay, setNDecay] = useState(2)
  const [decayFitStartUs, setDecayFitStartUs] = useState(0)
  const [decayFitEndUs, setDecayFitEndUs] = useState(40)
  const [tangentWindowPoints, setTangentWindowPoints] = useState(17)
  const [decayInitialParamsInput, setDecayInitialParamsInput] = useState('')
  const [previewSubTab, setPreviewSubTab] = useState('rise')  // 'rise' | 'decay'
  const [analysisPreview, setAnalysisPreview] = useState(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [analysisError, setAnalysisError] = useState(null)
  const [analysisProcessing, setAnalysisProcessing] = useState(false)
  const [analysisSelectingFolder, setAnalysisSelectingFolder] = useState(false)
  const [analysisScanningFolder, setAnalysisScanningFolder] = useState(false)
  const [analysisDone, setAnalysisDone] = useState(false)
  const [analysisProgress, setAnalysisProgress] = useState({ current: 0, total: 0, filename: '', stage: '' })
  const analysisAbortRef = useRef(null)

  const parseInitialParams = (str) => {
    if (!str || typeof str !== 'string') return null
    const s = str.trim()
    if (!s) return null
    try {
      const arr = JSON.parse(s)
      if (!Array.isArray(arr) || arr.length < 3) return null  // n=1: [A1,tau1,y0]
      const parsed = arr.map(x => (typeof x === 'number' && Number.isFinite(x)) ? x : parseFloat(x))
      return parsed.every(x => Number.isFinite(x)) ? parsed : null
    } catch {
      return null
    }
  }

  const sanitizeTangentWindowPoints = (value) => {
    const parsed = Number.parseInt(String(value), 10)
    if (!Number.isFinite(parsed)) return 17
    return Math.max(3, parsed)
  }

  const getDownloadFilename = (response, fallback) => {
    const disposition = response.headers.get('content-disposition') || ''
    const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i)
    if (utf8Match?.[1]) return decodeURIComponent(utf8Match[1])
    const plainMatch = disposition.match(/filename="?([^"]+)"?/i)
    if (plainMatch?.[1]) return plainMatch[1]
    return fallback
  }

  const loadAnalysisPreview = async (fileHandle, opts = {}) => {
    const { decayParams } = opts
    const windowPoints = sanitizeTangentWindowPoints(tangentWindowPoints)
    const fd = new FormData()
    fd.append('file', await fileHandle.getFile())
    fd.append('rise_mode', riseMode)
    fd.append('low_pct', lowPct)
    fd.append('high_pct', highPct)
    fd.append('n_decay', nDecay)
    fd.append('decay_fit_start_us', decayFitStartUs)
    fd.append('decay_fit_end_us', decayFitEndUs)
    fd.append('tangent_window_points', windowPoints)
    if (decayParams?.length) fd.append('decay_initial_params', JSON.stringify(decayParams))

    setPreviewLoading(true)
    try {
      const res = await fetch(apiUrl('/api/trel-analysis-preview'), { method: 'POST', body: fd })
      let data = null
      try {
        data = await res.json()
      } catch {
        throw new Error(`미리보기 응답을 읽을 수 없습니다. (HTTP ${res.status})`)
      }

      if (!res.ok || !data.success) {
        throw new Error(data?.error || `미리보기 로드 실패 (HTTP ${res.status})`)
      }

      setAnalysisPreview(data)
      setAnalysisError(null)
    } finally {
      setPreviewLoading(false)
    }
  }

  const ensureReadWritePermission = async (dirHandle) => {
    const options = { mode: 'readwrite' }
    if ((await dirHandle.queryPermission(options)) === 'granted') {
      return true
    }
    return (await dirHandle.requestPermission(options)) === 'granted'
  }

  const handleAnalysisFolderSelect = async () => {
    if (analysisSelectingFolder) return
    if (!('showDirectoryPicker' in window)) {
      setAnalysisError('이 브라우저는 폴더 선택을 지원하지 않습니다.')
      return
    }

    let dirHandle
    try {
      setAnalysisSelectingFolder(true)
      dirHandle = await window.showDirectoryPicker({ id: 'trel-analysis-folder' })
    } catch (err) {
      setAnalysisSelectingFolder(false)
      if (err.name === 'AbortError') return
      setAnalysisPreview(null)
      setAnalysisError(err.message === 'Failed to fetch' ? '백엔드 연결 실패' : err.message)
      setAnalysisFolderReady(false)
      return
    }

    setAnalysisError(null)
    setAnalysisPreview(null)
    setAnalysisDone(false)
    try {
      setAnalysisSelectingFolder(false)
      setAnalysisScanningFolder(true)

      const scanned = await scanAnalysisFolder(dirHandle)
      const files = scanned.analysisFiles
      const vilFiles = scanned.analysisVilFiles
      if (files.length === 0) {
        setAnalysisError('TrEL 형식 CSV 파일이 없습니다. (_TrEL.csv 또는 TrEL_processed 내 파일)')
        setAnalysisFolderReady(false)
        return
      }
      setAnalysisFiles(files)
      setAnalysisVilFiles(vilFiles)
      setAnalysisDirHandle(dirHandle)
      setAnalysisFolderReady(true)
    } catch (err) {
      setAnalysisPreview(null)
      setAnalysisError(err.message === 'Failed to fetch' ? '백엔드 연결 실패' : err.message)
      setAnalysisFolderReady(false)
    } finally {
      setAnalysisSelectingFolder(false)
      setAnalysisScanningFolder(false)
    }
  }

  const refreshPreview = (opts = {}) => {
    if (!analysisFiles?.length || previewLoading) return
    const idx = indexClosestTo10Min(analysisFiles)
    const decayParams = opts.decayParams ?? parseInitialParams(decayInitialParamsInput)
    loadAnalysisPreview(analysisFiles[idx].handle, { decayParams }).catch(err => {
      setAnalysisError(err.message === 'Failed to fetch' ? '백엔드 연결 실패' : err.message)
    })
  }

  useEffect(() => {
    if (analysisFolderReady && analysisFiles.length > 0) {
      refreshPreview()
    }
  }, [riseMode, lowPct, highPct, nDecay, decayFitStartUs, decayFitEndUs, analysisFolderReady, analysisFiles])

  useEffect(() => {
    if (!analysisProcessing) return
    const fetchProgress = async () => {
      try {
        const res = await fetch(apiUrl('/api/trel-analysis-progress'))
        const data = await res.json()
        setAnalysisProgress(data)
      } catch {
        // ignore
      }
    }
    fetchProgress()
    const iv = setInterval(fetchProgress, 5000)
    return () => clearInterval(iv)
  }, [analysisProcessing])

  const handleAnalysisBatch = async () => {
    if (!analysisDirHandle || analysisFiles.length === 0) return
    setAnalysisError(null)
    setAnalysisProcessing(true)
    setAnalysisDone(false)
    setAnalysisProgress({ current: 0, total: 0, filename: '', stage: '' })
    const controller = new AbortController()
    analysisAbortRef.current = controller
    try {
      const hasWritePermission = await ensureReadWritePermission(analysisDirHandle)
      if (!hasWritePermission) {
        throw new Error('폴더 쓰기 권한이 필요합니다. 권한 요청을 허용한 뒤 다시 시도해주세요.')
      }

      const fd = new FormData()
      for (const { handle } of analysisFiles) {
        fd.append('files', await handle.getFile())
      }
      for (const { handle } of analysisVilFiles) {
        fd.append('vil_files', await handle.getFile())
      }
      if (analysisVilFiles.length > 0) {
        const vilRef = analysisVilFiles[0]
        const dutyFraction = parseDutyFromFilename(vilRef.name)
        if (Number.isFinite(dutyFraction)) {
          fd.append('vil_duty_fraction', String(dutyFraction))
        }
        const shiftMin = await readVilTimeShiftMin(vilRef.handle)
        if (Number.isFinite(shiftMin)) {
          fd.append('vil_time_shift_min', String(shiftMin))
        }
      }
      fd.append('rise_mode', riseMode)
      fd.append('low_pct', lowPct)
      fd.append('high_pct', highPct)
      fd.append('n_decay', nDecay)
      fd.append('decay_fit_start_us', decayFitStartUs)
    fd.append('decay_fit_end_us', decayFitEndUs)
      fd.append('tangent_window_points', sanitizeTangentWindowPoints(tangentWindowPoints))
      if (analysisPreview?.decay_popt?.length) {
        fd.append('decay_initial_params', JSON.stringify(analysisPreview.decay_popt))
      }
      const res = await fetch(apiUrl('/api/trel-analysis-batch'), { method: 'POST', body: fd, signal: controller.signal })
      const ct = res.headers.get('content-type') || ''
      if (ct.includes('json')) {
        const data = await res.json()
        throw new Error(data.error || '분석 실패')
      }
      const blob = await res.blob()
      const outputFilename = getDownloadFilename(res, 'TrEL_Analysis.xlsx')
      const fh = await analysisDirHandle.getFileHandle(outputFilename, { create: true })
      const w = await fh.createWritable()
      await w.write(blob)
      await w.close()
      setAnalysisDone(true)
    } catch (err) {
      if (err.name === 'AbortError') {
        setAnalysisError('사용자가 중단했습니다.')
        return
      }
      setAnalysisError(err.message === 'Failed to fetch' ? '백엔드 연결 실패' : err.message)
    } finally {
      setAnalysisProcessing(false)
      analysisAbortRef.current = null
    }
  }

  return (
    <div style={{ marginTop: '20px' }}>
      <h2>TrEL 자동 배치 분석</h2>
      <p style={{ color: '#666', marginBottom: '20px' }}>
        TrEL_processed 폴더 내 CSV에서 Rise, Saturation, Decay 파라미터를 추출하여 Excel로 저장합니다.
      </p>

      {backendStatus?.status !== 'ok' && (
        <p style={{ color: '#d32f2f', marginBottom: '16px' }}>백엔드 연결 필요. pnpm dev:all로 실행해주세요.</p>
      )}

      <button
        className="simulate-button"
        onClick={handleAnalysisFolderSelect}
        disabled={analysisSelectingFolder || analysisScanningFolder || backendStatus?.status !== 'ok'}
      >
        {analysisSelectingFolder ? '폴더 선택 창 여는 중...' : analysisScanningFolder ? '폴더 스캔 중...' : '폴더 선택 (TrEL_processed)'}
      </button>

      {analysisFolderReady && analysisFiles.length > 0 && (
        <div style={{ marginTop: '20px', padding: '16px', background: '#fafafa', borderRadius: '6px', border: '1px solid #e0e0e0' }}>
          <p style={{ marginBottom: '16px', color: '#666' }}>
            총 {analysisFiles.length}개 TrEL CSV 파일
            {analysisVilFiles.length > 0 && <span style={{ marginLeft: '8px', color: '#1565c0' }}>· VIL_processed {analysisVilFiles.length}개 (Voltage 참조)</span>}
          </p>

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '24px', marginBottom: '20px' }}>
            <div>
              <label style={{ display: 'block', fontSize: '0.9em', marginBottom: '4px' }}>상승부 분석 모드</label>
              <select value={riseMode} onChange={e => setRiseMode(e.target.value)} style={{ padding: '6px 8px', minWidth: '120px' }}>
                <option value="threshold">Threshold</option>
                <option value="tangent">Tangent</option>
              </select>
            </div>
            <div>
              <label style={{ display: 'block', fontSize: '0.9em', marginBottom: '4px' }}>Low% (절대값, 예: 1 → 0.01)</label>
              <input
                type="number"
                value={lowPct}
                onChange={e => setLowPct(Number(e.target.value))}
                step={0.5}
                min={0}
                max={100}
                disabled={riseMode === 'tangent'}
                style={{ width: '80px', padding: '6px 8px' }}
              />
            </div>
            <div>
              <label style={{ display: 'block', fontSize: '0.9em', marginBottom: '4px' }}>High% (절대값, 예: 90 → 0.9)</label>
              <input
                type="number"
                value={highPct}
                onChange={e => setHighPct(Number(e.target.value))}
                step={0.5}
                min={0}
                max={100}
                disabled={riseMode === 'tangent'}
                style={{ width: '80px', padding: '6px 8px' }}
              />
            </div>
            <div>
              <label style={{ display: 'block', fontSize: '0.9em', marginBottom: '4px' }}>Decay 지수 개수 (n)</label>
              <select value={nDecay} onChange={e => setNDecay(Number(e.target.value))} style={{ padding: '6px 8px' }}>
                {[1, 2, 3].map(n => <option key={n} value={n}>{n}</option>)}
              </select>
            </div>
            <div>
              <label style={{ display: 'block', fontSize: '0.9em', marginBottom: '4px' }}>Decay 피팅 시작 (μs)</label>
              <input
                type="number"
                value={decayFitStartUs}
                onChange={e => setDecayFitStartUs(Number(e.target.value))}
                step={0.1}
                min={0}
                style={{ width: '90px', padding: '6px 8px' }}
              />
            </div>
            <div>
              <label style={{ display: 'block', fontSize: '0.9em', marginBottom: '4px' }}>Decay 피팅 종료 (μs)</label>
              <input
                type="number"
                value={decayFitEndUs}
                onChange={e => setDecayFitEndUs(Number(e.target.value))}
                step={0.1}
                min={0}
                style={{ width: '90px', padding: '6px 8px' }}
              />
              <span style={{ fontSize: '0.8em', color: '#666', marginLeft: '4px' }}>기본값 40</span>
            </div>
            {riseMode === 'tangent' && (
              <div>
                <label style={{ display: 'block', fontSize: '0.9em', marginBottom: '4px' }}>접선 윈도우 포인트 수</label>
                <input
                  type="number"
                  value={tangentWindowPoints}
                  onChange={e => {
                    const next = Number(e.target.value)
                    setTangentWindowPoints(Number.isFinite(next) ? next : 17)
                  }}
                  onBlur={() => setTangentWindowPoints(sanitizeTangentWindowPoints(tangentWindowPoints))}
                  step={2}
                  min={3}
                  style={{ width: '90px', padding: '6px 8px' }}
                />
                <span style={{ fontSize: '0.8em', color: '#666', marginLeft: '4px' }}>기본값 17 · 새로고침 필요</span>
              </div>
            )}
            <div>
              <button
                type="button"
                onClick={() => refreshPreview()}
                disabled={previewLoading}
                style={{ padding: '6px 12px', marginTop: '22px' }}
              >
                {previewLoading ? '미리보기 로딩 중...' : '미리보기 새로고침'}
              </button>
            </div>
          </div>

          <details open style={{ marginTop: '12px', marginBottom: '12px' }}>
            <summary style={{ cursor: 'pointer', fontSize: '0.9em', color: '#555', fontWeight: 500 }}>피팅 초기값 (피팅이 잘 안될 때 직접 입력)</summary>
            <div style={{ marginTop: '12px', display: 'flex', flexDirection: 'column', gap: '12px', maxWidth: '500px' }}>
              {analysisPreview?.decay_popt && (
                <button
                  type="button"
                  onClick={() => setDecayInitialParamsInput(JSON.stringify(analysisPreview.decay_popt))}
                  style={{ alignSelf: 'flex-start', padding: '6px 12px', fontSize: '0.85em' }}
                >
                  현재 미리보기 결과로 채우기
                </button>
              )}
              <div>
                <label style={{ display: 'block', fontSize: '0.85em', marginBottom: '4px' }}>Decay 초기값 JSON (예: [1, 2, 0.5, 4, 0.01])</label>
                <input
                  type="text"
                  value={decayInitialParamsInput}
                  onChange={e => setDecayInitialParamsInput(e.target.value)}
                  placeholder="비우면 자동 추정"
                  style={{ width: '100%', padding: '6px 8px', fontFamily: 'monospace', fontSize: '0.9em' }}
                />
              </div>
              <p style={{ fontSize: '0.8em', color: '#666' }}>
                Decay n=1: [A₁, τ₁, y0], n=2: [A₁, τ₁, A₂, τ₂, y0] 형식. 입력 후 미리보기 새로고침을 누르세요.
              </p>
            </div>
          </details>
          {riseMode === 'tangent' && (
            <p style={{ marginTop: '-8px', marginBottom: '16px', fontSize: '0.85em', color: '#666' }}>
              Tangent 모드는 노이즈를 줄인 뒤 설정한 포인트 수의 sliding window 선형 회귀로 상승 폭이 큰 접선을 찾습니다. 홀수 값을 권장합니다. 윈도우 포인트를 바꾼 뒤에는 미리보기 새로고침을 눌러주세요. Low%/High% 값은 이 모드에서 사용되지 않습니다.
            </p>
          )}

          {analysisPreview && (
            <div style={{ marginBottom: '20px', opacity: previewLoading ? 0.55 : 1 }}>
              <h4 style={{ marginBottom: '8px' }}>
                미리보기: {analysisPreview.filename}
                {analysisPreview.rise?.analysis_mode && (
                  <span style={{ marginLeft: '8px', fontSize: '0.9em', color: '#1565c0', fontWeight: 500 }}>
                    ({analysisPreview.rise.analysis_mode} Mode)
                  </span>
                )}
              </h4>
              {riseMode === 'tangent' && analysisPreview.rise?.tangent_window_points != null && (
                <p style={{ marginTop: '-4px', marginBottom: '8px', fontSize: '0.85em', color: '#666' }}>
                  접선 윈도우: {analysisPreview.rise.tangent_window_points} points
                </p>
              )}
              {analysisPreview.rise?.rise_error && (
                <p style={{ marginTop: '-4px', marginBottom: '8px', fontSize: '0.85em', color: '#c62828' }}>
                  Rise 분석 경고: {analysisPreview.rise.rise_error}
                </p>
              )}
              <div style={{ display: 'flex', gap: '4px', marginBottom: '12px', borderBottom: '1px solid #ddd' }}>
                <button
                  type="button"
                  onClick={() => setPreviewSubTab('rise')}
                  style={{
                    padding: '8px 16px',
                    border: 'none',
                    background: previewSubTab === 'rise' ? '#e3f2fd' : 'transparent',
                    borderBottom: previewSubTab === 'rise' ? '2px solid #2196f3' : '2px solid transparent',
                    cursor: 'pointer',
                    fontSize: '0.9em',
                  }}
                >
                  Rise
                </button>
                <button
                  type="button"
                  onClick={() => setPreviewSubTab('decay')}
                  style={{
                    padding: '8px 16px',
                    border: 'none',
                    background: previewSubTab === 'decay' ? '#e3f2fd' : 'transparent',
                    borderBottom: previewSubTab === 'decay' ? '2px solid #2196f3' : '2px solid transparent',
                    cursor: 'pointer',
                    fontSize: '0.9em',
                  }}
                >
                  Decay (Log)
                </button>
              </div>
              {previewSubTab === 'rise' && analysisPreview.rise && (
                <RisePreviewChart
                  timeRaw={analysisPreview.rise.time_raw}
                  elSignal={analysisPreview.rise.el_signal_rise}
                  analysisMode={analysisPreview.rise.analysis_mode}
                  axisMode={analysisPreview.rise.axis_mode}
                  tDelay={analysisPreview.rise.t_delay}
                  tRise={analysisPreview.rise.t_rise}
                  tSaturation={analysisPreview.rise.t_saturation}
                  previewXMin={analysisPreview.rise.preview_x_min}
                  previewXMax={analysisPreview.rise.preview_x_max}
                  tangentSlope={analysisPreview.rise.tangent_slope}
                  tangentIntercept={analysisPreview.rise.tangent_intercept}
                />
              )}
              {previewSubTab === 'decay' && analysisPreview.decay && (
                <LogFitPreviewChart
                  timeData={analysisPreview.decay.time_decay}
                  signalLog={analysisPreview.decay.el_signal_decay_log}
                  timeFit={analysisPreview.decay.t_decay_fit}
                  fitLog={analysisPreview.decay.y_fit_log}
                  xLabel={analysisPreview.format === 'lowfreq' ? 'Time (μs)' : 'Time_Decay (μs)'}
                  fitStartUs={analysisPreview.decay?.decay_fit_start_us}
                  fitEndUs={analysisPreview.decay?.decay_fit_end_us}
                />
              )}
              {analysisPreview.tau_avg != null && (
                <p style={{ marginTop: '8px', fontSize: '0.9em', color: '#666' }}>
                  τ_avg = {Number.isFinite(analysisPreview.tau_avg) ? analysisPreview.tau_avg.toFixed(4) : '?'} μs
                  {analysisPreview.tau_list?.length > 0 && ` (${analysisPreview.tau_list.map((t, i) => `τ${i + 1}=${Number.isFinite(t) ? t.toFixed(4) : '?'}`).join(', ')})`}
                </p>
              )}
            </div>
          )}

          <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
            <button
              className="simulate-button"
              onClick={handleAnalysisBatch}
              disabled={analysisProcessing || backendStatus?.status !== 'ok'}
            >
              {analysisProcessing ? '분석 중...' : '배치 분석 실행'}
            </button>
            {analysisProcessing && (
              <button
                type="button"
                onClick={() => analysisAbortRef.current?.abort()}
                style={{
                  padding: '10px 20px',
                  background: '#d32f2f',
                  color: '#fff',
                  border: 'none',
                  borderRadius: '6px',
                  cursor: 'pointer',
                  fontWeight: 600,
                }}
              >
                중단
              </button>
            )}
          </div>

          {analysisProcessing && (
            <p style={{ marginTop: '12px', fontSize: '0.9em', color: '#555' }}>
              {analysisProgress?.total > 0
                ? `${analysisProgress.current}/${analysisProgress.total}${analysisProgress.stage ? ` (${analysisProgress.stage})` : ''}${analysisProgress.filename ? ` · ${analysisProgress.filename}` : ''}`
                : '분석 준비 중...'}
            </p>
          )}

          {analysisDone && <p style={{ marginTop: '12px', color: '#2e7d32', fontWeight: 600 }}>✓ TrEL_Analysis.xlsx 저장 완료</p>}
        </div>
      )}

      {analysisError && <div className="error-message" style={{ marginTop: '16px' }}>{analysisError}</div>}
    </div>
  )
}
