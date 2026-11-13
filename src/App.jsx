import React, { useState, useEffect, useMemo, useRef } from 'react'
import './App.css'
import AnalysisTab from './AnalysisTab'
import ContourTab from './ContourTab'
import { apiUrl } from './api'

function PreviewChart({ timeNs, ch1, baselineStart, baselineEnd, normStart, normEnd }) {
  const w = 600
  const h = 200
  const pad = { top: 10, right: 10, bottom: 30, left: 50 }
  const plotW = w - pad.left - pad.right
  const plotH = h - pad.top - pad.bottom

  const { path, xScale, yScale, xMin, xMax } = useMemo(() => {
    if (!timeNs?.length || !ch1?.length) return {}
    
    // 유효한 데이터만 필터링 (null, NaN, Infinity 제외)
    const validPoints = []
    for (let i = 0; i < timeNs.length; i++) {
      const t = timeNs[i]
      const v = ch1[i]
      if (t != null && v != null && Number.isFinite(t) && Number.isFinite(v)) {
        validPoints.push({ t, v })
      }
    }
    
    if (validPoints.length === 0) return {}

    const xMin = Math.min(...validPoints.map(p => p.t))
    const xMax = Math.max(...validPoints.map(p => p.t))
    const yMin = Math.min(...validPoints.map(p => p.v))
    const yMax = Math.max(...validPoints.map(p => p.v))
    
    const yRange = yMax - yMin || 1
    const xScale = v => pad.left + ((v - xMin) / (xMax - xMin || 1)) * plotW
    const yScale = v => pad.top + plotH - ((v - yMin) / yRange) * plotH
    
    // 필터링된 포인트로 경로 생성
    const pts = validPoints.map(p => `${xScale(p.t)},${yScale(p.v)}`).join(' L ')
    const path = pts ? `M ${pts}` : ''
    return { path, xScale, yScale, xMin, xMax }
  }, [timeNs, ch1])

  if (!path) return <div style={{ height: h, background: '#f5f5f5', borderRadius: 4 }} />

  const blX1 = xScale ? xScale(Math.max(xMin, baselineStart)) : 0
  const blX2 = xScale ? xScale(Math.min(xMax, baselineEnd)) : 0
  const normX1 = normStart != null && normEnd != null && xScale ? xScale(Math.max(xMin, normStart)) : 0
  const normX2 = normStart != null && normEnd != null && xScale ? xScale(Math.min(xMax, normEnd)) : 0

  return (
    <svg width={w} height={h} style={{ display: 'block', background: '#fff', borderRadius: 4, border: '1px solid #ddd' }}>
      {baselineStart <= baselineEnd && (
        <rect x={blX1} y={pad.top} width={Math.max(0, blX2 - blX1)} height={plotH} fill="rgba(33,150,243,0.15)" stroke="rgba(33,150,243,0.5)" strokeWidth={1} />
      )}
      {normStart != null && normEnd != null && normStart <= normEnd && (
        <rect x={normX1} y={pad.top} width={Math.max(0, normX2 - normX1)} height={plotH} fill="rgba(76,175,80,0.15)" stroke="rgba(76,175,80,0.5)" strokeWidth={1} />
      )}
      <path d={path} fill="none" stroke="#333" strokeWidth={1.5} strokeLinejoin="round" />
      <text x={pad.left} y={h - 8} fontSize={10} fill="#666">Time (ns)</text>
      <text x={w - 120} y={pad.top + 14} fontSize={9} fill="rgba(33,150,243,0.9)">■ Zero 기준</text>
      <text x={w - 120} y={pad.top + 26} fontSize={9} fill="rgba(76,175,80,0.9)">■ Saturation</text>
    </svg>
  )
}

const parseMinutesFromFilename = (filename = '') => {
  const baseName = filename.replace(/\.[^.]+$/, '')
  const patterns = [
    /(?:^|[_\-\s])(?:(\d+)\s*h)?\s*(\d+)\s*min(?=$|[_\-\s])/gi,
    /(?:^|[_\-\s])(?:(\d+)\s*h)?\s*(\d+)\s*m(?=$|[_\-\s])/gi,
    /(?:^|[_\-\s])(\d+)\s*h(?=$|[_\-\s])/gi,
  ]

  for (const pattern of patterns) {
    const matches = [...baseName.matchAll(pattern)]
    if (matches.length === 0) continue

    const match = matches[matches.length - 1]
    if (match.length >= 3) {
      const hours = match[1] ? Number(match[1]) : 0
      const minutes = Number(match[2])
      return hours * 60 + minutes
    }

    if (match.length >= 2) {
      return Number(match[1]) * 60
    }
  }

  return null
}

const parseMinutesFromEntry = (entry) => {
  const candidates = [
    entry.name,
    entry.filename,
    entry.originalFilename,
    entry.relPath,
  ].filter(Boolean)

  for (const candidate of candidates) {
    const minutes = parseMinutesFromFilename(candidate)
    if (Number.isFinite(minutes)) {
      return minutes
    }
  }

  return null
}

const parseMasterPercents = (rawValue) => {
  const values = rawValue
    .split(',')
    .map(value => Number.parseInt(value.trim(), 10))
    .filter(value => Number.isFinite(value))

  return values.length > 0 ? values : [100, 90, 80, 70, 60, 50]
}

const parseDutyFromFilename = (filename = '') => {
  const match = (filename || '').match(/duty\s*(\d+(?:\.\d+)?)\s*%?/i)
  if (!match) return 1
  const val = Number.parseFloat(match[1])
  if (!Number.isFinite(val)) return 1
  return val > 1 ? val / 100 : val
}

const parseVilProcessedCsv = (csvText) => {
  const rows = csvText
    .split(/\r?\n/)
    .filter(line => line.trim() && !line.startsWith('#'))
    .map(line => line.split(',').map(cell => cell.trim()))

  if (rows.length < 2) return []

  const header = rows[0]
  let timeIndex = header.findIndex(cell => cell.includes('Time (min)'))
  let relLumIndex = header.findIndex(cell => cell.includes('Relative luminance'))

  if (timeIndex === -1 || relLumIndex === -1) {
    if (header.length >= 2) {
      timeIndex = 0
      relLumIndex = 1
    } else {
      return []
    }
  }

  return rows
    .slice(1)
    .map(row => ({
      timeMin: Number.parseFloat(row[timeIndex]),
      relLum: Number.parseFloat(row[relLumIndex]),
    }))
    .filter(point => Number.isFinite(point.timeMin) && Number.isFinite(point.relLum))
}

const interpolateTimeAtRatio = (points, targetRatio) => {
  if (points.length < 2) return null

  let peakIndex = 0
  for (let index = 1; index < points.length; index += 1) {
    if (points[index].relLum >= points[peakIndex].relLum) {
      peakIndex = index
    }
  }

  if (targetRatio >= 0.999) {
    return points[peakIndex].timeMin
  }

  for (let index = peakIndex; index < points.length - 1; index += 1) {
    const current = points[index]
    const next = points[index + 1]
    if (current.relLum >= targetRatio && next.relLum <= targetRatio) {
      const delta = next.relLum - current.relLum
      if (Math.abs(delta) < 1e-12) return current.timeMin
      const fraction = (targetRatio - current.relLum) / delta
      return current.timeMin + fraction * (next.timeMin - current.timeMin)
    }
  }

  return null
}

const vilDutyTimeToWallClockMin = (tVilDutyMin, dutyFraction, vilTimeShiftMin = 0) => {
  const duty = dutyFraction > 0 ? dutyFraction : 1
  return (Number(vilTimeShiftMin) || 0) + (tVilDutyMin / duty)
}

const pickEvenlySpacedEntries = (entries, maxCount) => {
  if (entries.length === 0) return []
  if (entries.length <= maxCount) return entries

  const selected = []
  const usedIndexes = new Set()
  if (maxCount <= 1) return [entries[0]]

  const step = (entries.length - 1) / (maxCount - 1)

  for (let index = 0; index < maxCount; index += 1) {
    const pickedIndex = Math.round(index * step)
    if (!usedIndexes.has(pickedIndex)) {
      selected.push(entries[pickedIndex])
      usedIndexes.add(pickedIndex)
    }
  }

  return selected
}

const selectMasterSourceFiles = (vilCsv, vilTimeShiftMin, percentText, trelEntries, vilFilename = '') => {
  const targetCount = Math.max(1, parseMasterPercents(percentText).length)
  const vilPoints = parseVilProcessedCsv(vilCsv)
  const fallbackEntries = pickEvenlySpacedEntries(trelEntries, Math.min(targetCount, trelEntries.length))

  if (vilPoints.length < 2) return fallbackEntries

  const fileEntries = trelEntries
    .map(entry => ({ ...entry, minutes: parseMinutesFromEntry(entry) }))
    .filter(entry => Number.isFinite(entry.minutes))

  if (fileEntries.length === 0) return fallbackEntries

  const dutyFraction = parseDutyFromFilename(vilFilename)
  const selected = []
  const selectedKeys = new Set()

  for (const percent of parseMasterPercents(percentText)) {
    const vilCrossing = interpolateTimeAtRatio(vilPoints, percent / 100)
    if (!Number.isFinite(vilCrossing)) continue

    const shiftedTarget = vilDutyTimeToWallClockMin(vilCrossing, dutyFraction, vilTimeShiftMin)
    const closest = fileEntries.reduce((best, current) => {
      if (!best) return current
      return Math.abs(current.minutes - shiftedTarget) < Math.abs(best.minutes - shiftedTarget) ? current : best
    }, null)

    if (!closest) continue

    const key = closest.relPath || closest.name
    if (!selectedKeys.has(key)) {
      selected.push(closest)
      selectedKeys.add(key)
    }
  }

  if (selected.length > 0) return selected

  const sortedEntries = [...fileEntries].sort((a, b) => a.minutes - b.minutes)
  return pickEvenlySpacedEntries(sortedEntries, Math.min(targetCount, sortedEntries.length))
}

function App() {
  const [logoError, setLogoError] = useState(false)
  const [backendStatus, setBackendStatus] = useState(null)
  const [processing, setProcessing] = useState(false)
  const [selectingFolder, setSelectingFolder] = useState(false)
  const [scanningFolder, setScanningFolder] = useState(false)
  const [error, setError] = useState(null)
  const [results, setResults] = useState({ vil: [], osc: [], master: false, masterMetadata: null })
  const [folderReady, setFolderReady] = useState(false)
  const [folderData, setFolderData] = useState(null) // { dirHandle, vilFiles, oscPairs, unpairedOsc, existingTrel, previewData }
  const [baselineStart, setBaselineStart] = useState(100000)
  const [baselineEnd, setBaselineEnd] = useState(250000)
  const [normStart, setNormStart] = useState('-250000')
  const [normEnd, setNormEnd] = useState('-50000')
  const [masterPercents, setMasterPercents] = useState('100, 90, 80, 70, 60, 50')
  const [trelConfigOpen, setTrelConfigOpen] = useState(false)
  const [rShunt, setRShunt] = useState(100.0)  // 직렬 센서 저항 (Ω)
  const [rOsc, setROsc] = useState(50.0)       // 오실로스코프 내부 저항 (Ω)
  const [deviceAreaMm2, setDeviceAreaMm2] = useState(4.3)  // 소자 유효 면적 (mm²)
  const [activeTab, setActiveTab] = useState('batch')  // 'batch' | 'analysis' | 'contour'
  const batchAbortRef = useRef(null)
  
  // R_total 자동 계산
  const rTotal = (rShunt * rOsc) / (rShunt + rOsc)

  const getReadableError = (err) => {
    if (!err) return '알 수 없는 오류'
    if (err.name === 'AbortError') return '요청이 중단되었습니다.'
    if (err.message === 'Failed to fetch') {
      return '요청 중 연결이 끊겼습니다. 백엔드는 실행 중일 수 있으니 터미널 로그(포트/예외)를 확인해주세요.'
    }
    return err.message || String(err)
  }

  useEffect(() => {
    fetch(apiUrl('/api/health'))
      .then(res => res.json())
      .then(data => setBackendStatus(data))
      .catch(() => setBackendStatus({ status: 'error' }))
  }, [])

  const readJson = async (res) => {
    const data = await res.json().catch(() => null)
    if (!data) {
      throw new Error(`서버 응답을 읽을 수 없습니다. (HTTP ${res.status}) 백엔드를 다시 실행해주세요.`)
    }
    return data
  }

  const handleFolderSelect = async () => {
    if (selectingFolder || scanningFolder) return

    setError(null)
    setSelectingFolder(true)
    let rootPath = ''
    try {
      const pickRes = await fetch(apiUrl('/api/pick-folder'), { method: 'POST' })
      const pick = await readJson(pickRes)
      if (!pick.success) throw new Error(pick.error || '폴더 선택에 실패했습니다.')
      if (pick.cancelled || !pick.path) return

      rootPath = pick.path
      setSelectingFolder(false)
      setScanningFolder(true)
      setResults({ vil: [], osc: [], master: false, masterMetadata: null })

      const scanRes = await fetch(apiUrl('/api/scan-batch'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: rootPath }),
      })
      const scanned = await readJson(scanRes)
      if (!scanned.success) throw new Error(scanned.error || '폴더 스캔에 실패했습니다.')

      const vilFiles = scanned.vilFiles || []
      const oscPairs = scanned.oscPairs || []
      const unpairedOsc = scanned.unpairedOsc || []
      const existingTrel = scanned.existingTrel || []
      if (vilFiles.length === 0 && oscPairs.length === 0 && existingTrel.length === 0) {
        const unpairedMsg = unpairedOsc.length > 0
          ? ` 페어링 실패 파일 ${unpairedOsc.length}개: ${unpairedOsc.map(f => f.name).join(', ')}`
          : ''
        setError(`처리할 CSV 파일이 없습니다. (VIL, rise/decay 페어, 또는 _TrEL.csv)${unpairedMsg}`)
        setFolderReady(false)
        setFolderData(null)
        return
      }

      setFolderData({ rootPath, vilFiles, oscPairs, unpairedOsc, existingTrel, previewData: null })
      setFolderReady(true)
      if (oscPairs.length > 0) setTrelConfigOpen(true)
      setScanningFolder(false)
      await new Promise(resolve => setTimeout(resolve, 0))

      if (oscPairs[0]?.decay?.relPath) {
        const previewRes = await fetch(apiUrl('/api/preview-osc-path'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: rootPath, relPath: oscPairs[0].decay.relPath }),
        })
        const preview = await readJson(previewRes)
        if (preview.success) {
          setFolderData(prev => prev && prev.rootPath === rootPath ? { ...prev, previewData: preview } : prev)
        }
      }
    } catch (err) {
      setError(getReadableError(err))
      if (!rootPath) setFolderReady(false)
    } finally {
      setSelectingFolder(false)
      setScanningFolder(false)
    }
  }

  const handleProcess = async () => {
    if (!folderData?.rootPath) return
    setError(null)
    setProcessing(true)
    const controller = new AbortController()
    batchAbortRef.current = controller
    const signal = controller.signal
    try {
      const res = await fetch(apiUrl('/api/process-batch'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: folderData.rootPath,
          baseline_start_ns: baselineStart,
          baseline_end_ns: baselineEnd,
          norm_start_ns: normStart.trim(),
          norm_end_ns: normEnd.trim(),
          r_shunt: rShunt,
          r_osc: rOsc,
          device_area_mm2: deviceAreaMm2,
          master_percents: masterPercents.replace(/\s/g, ''),
        }),
        signal,
      })
      const data = await readJson(res)
      if (data.vil || data.osc || data.master) {
        setResults({
          vil: data.vil || [],
          osc: data.osc || [],
          master: !!data.master,
          masterMetadata: data.masterMetadata || null,
        })
      }
      if (!res.ok || !data.success) throw new Error(data.error || '처리 실패')
    } catch (err) {
      if (err.name === 'AbortError') {
        setError('사용자가 중단했습니다.')
        return
      }
      setError(getReadableError(err))
    } finally {
      setProcessing(false)
      batchAbortRef.current = null
    }
  }


  return (
    <div className="app">
      <div className="container">
        <div className="title-section">
          <div className="title-content">
            <h1>TrEL Signal Processing & Analysis Automator</h1>
            <p className="subtitle">Batch Processing & Data Export</p>
          </div>
          <img
            src="/PNEL_logo.png"
            alt="PNEL Logo"
            className="title-logo"
            onError={() => setLogoError(true)}
            style={{ display: logoError ? 'none' : 'block' }}
          />
        </div>

        <div className="tabs" style={{ display: 'flex', gap: '4px', marginTop: '24px', borderBottom: '1px solid #ddd' }}>
          <button
            type="button"
            onClick={() => setActiveTab('batch')}
            style={{
              padding: '10px 20px',
              border: 'none',
              background: activeTab === 'batch' ? '#fff' : 'transparent',
              borderBottom: activeTab === 'batch' ? '2px solid #333' : '2px solid transparent',
              cursor: 'pointer',
              fontSize: '0.95em',
              fontWeight: activeTab === 'batch' ? 600 : 400,
              color: activeTab === 'batch' ? '#333' : '#666',
            }}
          >
            배치 처리
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('analysis')}
            style={{
              padding: '10px 20px',
              border: 'none',
              background: activeTab === 'analysis' ? '#fff' : 'transparent',
              borderBottom: activeTab === 'analysis' ? '2px solid #333' : '2px solid transparent',
              cursor: 'pointer',
              fontSize: '0.95em',
              fontWeight: activeTab === 'analysis' ? 600 : 400,
              color: activeTab === 'analysis' ? '#333' : '#666',
            }}
          >
            TrEL 배치 분석
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('contour')}
            style={{
              padding: '10px 20px',
              border: 'none',
              background: activeTab === 'contour' ? '#fff' : 'transparent',
              borderBottom: activeTab === 'contour' ? '2px solid #333' : '2px solid transparent',
              cursor: 'pointer',
              fontSize: '0.95em',
              fontWeight: activeTab === 'contour' ? 600 : 400,
              color: activeTab === 'contour' ? '#333' : '#666',
            }}
          >
            Contour 정리
          </button>
        </div>

        {activeTab === 'batch' && (
        <div style={{ marginTop: '30px' }}>
          <h2>전체 처리</h2>
          <p style={{ color: '#666', marginBottom: '20px' }}>
            폴더를 선택하면 <strong>VIL 처리</strong> → <strong>rise/decay 페어 TrEL 처리</strong> → <strong>마스터 파일 생성</strong>이 순서대로 자동 실행됩니다.
          </p>

          {backendStatus?.status !== 'ok' && (
            <p style={{ color: '#d32f2f', marginBottom: '16px' }}>
              백엔드 연결 필요. pnpm dev:all로 실행해주세요.
            </p>
          )}

          <button
            className="simulate-button"
            onClick={handleFolderSelect}
            disabled={processing || selectingFolder || scanningFolder || backendStatus?.status !== 'ok'}
          >
            {selectingFolder ? '탐색기에서 폴더를 선택하세요...' : scanningFolder ? '폴더 스캔 중...' : '폴더 선택'}
          </button>
          {selectingFolder && (
            <p style={{ marginTop: '12px', color: '#1565c0' }}>
              폴더 선택 창이 다른 창 뒤에 있을 수 있습니다. 폴더를 고른 뒤 선택 버튼을 누르면 이 화면에 결과가 표시됩니다.
            </p>
          )}

          {folderReady && folderData && (
            <div style={{ marginTop: '20px', padding: '16px', background: '#fafafa', borderRadius: '6px', border: '1px solid #e0e0e0' }}>
              <h3 style={{ marginBottom: '12px', fontSize: '1.1em' }}>Low-frequency TrEL 처리 설정</h3>
              <p style={{ marginBottom: '12px', wordBreak: 'break-all' }}>
                <strong>{folderData.rootPath}</strong>
                <span style={{ color: '#666' }}>
                  {' '}· VIL {folderData.vilFiles?.length || 0}개 · 기존 TrEL {folderData.existingTrel?.length || 0}개
                </span>
              </p>
              {folderData.oscPairs?.length > 0 && (
                <p style={{ fontSize: '0.85em', color: '#666', marginBottom: '12px' }}>
                  rise/decay 페어 {folderData.oscPairs.length}개 감지
                  {folderData.unpairedOsc?.length > 0 && (
                    <span style={{ color: '#d32f2f' }}> (페어링 실패 {folderData.unpairedOsc.length}개)</span>
                  )}
                </p>
              )}
              
              {/* 회로 설정 (Low-side Sensing) */}
              <div style={{ marginBottom: '16px', padding: '12px', background: '#fff', borderRadius: '4px', border: '1px solid #ddd' }}>
                <h4 style={{ marginBottom: '8px', fontSize: '0.95em', fontWeight: 600 }}>회로 파라미터 (Low-side Sensing)</h4>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '16px', alignItems: 'flex-end' }}>
                  <div>
                    <label style={{ display: 'block', fontSize: '0.85em', marginBottom: '4px' }}>R_shunt (직렬 센서 저항, Ω)</label>
                    <input
                      type="number"
                      value={rShunt}
                      onChange={e => setRShunt(Number(e.target.value))}
                      step={0.1}
                      min={0.1}
                      style={{ width: '120px', padding: '6px 8px' }}
                    />
                  </div>
                  <div>
                    <label style={{ display: 'block', fontSize: '0.85em', marginBottom: '4px' }}>R_osc (오실로스코프 내부 저항, Ω)</label>
                    <input
                      type="number"
                      value={rOsc}
                      onChange={e => setROsc(Number(e.target.value))}
                      step={0.1}
                      min={0.1}
                      style={{ width: '120px', padding: '6px 8px' }}
                    />
                  </div>
                  <div>
                    <label style={{ display: 'block', fontSize: '0.85em', marginBottom: '4px', color: '#1565c0', fontWeight: 600 }}>R_total (합성 저항, Ω)</label>
                    <div style={{ width: '120px', padding: '6px 8px', background: '#e3f2fd', borderRadius: '4px', fontSize: '0.9em', fontWeight: 600 }}>
                      {rTotal.toFixed(3)}
                    </div>
                  </div>
                  <div>
                    <label style={{ display: 'block', fontSize: '0.85em', marginBottom: '4px' }}>소자 넓이 (mm²)</label>
                    <input
                      type="number"
                      value={deviceAreaMm2}
                      onChange={e => setDeviceAreaMm2(Number(e.target.value))}
                      step={0.01}
                      min={0.01}
                      style={{ width: '120px', padding: '6px 8px' }}
                    />
                  </div>
                </div>
                <div style={{ marginTop: '8px', fontSize: '0.8em', color: '#666' }}>
                  계산식: R_total = (R_shunt × R_osc) / (R_shunt + R_osc). 전류 밀도 J = I / 면적(cm²)에 사용됩니다.
                </div>
              </div>
              
              {folderData.vilFiles?.length > 0 && (
                <div style={{ marginBottom: '16px' }}>
                  <label style={{ display: 'block', fontSize: '0.9em', marginBottom: '4px' }}>마스터 파일 퍼센트 (%)</label>
                  <input
                    type="text"
                    value={masterPercents}
                    onChange={e => setMasterPercents(e.target.value)}
                    placeholder="100, 90, 80, 70, 60, 50"
                    style={{ width: '200px', padding: '6px 8px' }}
                  />
                  <span style={{ fontSize: '0.8em', color: '#666', marginLeft: '8px' }}>쉼표로 구분 (예: 100, 90, 80, 70, 60, 50)</span>
                </div>
              )}
              <button
                type="button"
                onClick={() => setTrelConfigOpen(!trelConfigOpen)}
                style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.95em', color: '#333', marginBottom: '12px' }}
              >
                {trelConfigOpen ? '▼' : '▶'} Saturation / Zero 기준 구간 설정
              </button>
              {trelConfigOpen && (
                <div style={{ marginTop: '8px' }}>
                  {folderData.previewData && (
                    <div style={{ marginBottom: '16px' }}>
                      <div style={{ fontSize: '0.85em', color: '#666', marginBottom: '8px' }}>
                        미리보기: {folderData.previewData.filename} (decay CH1)
                      </div>
                      <PreviewChart
                        timeNs={folderData.previewData.time_ns}
                        ch1={folderData.previewData.ch1}
                        baselineStart={baselineStart}
                        baselineEnd={baselineEnd}
                        normStart={normStart ? parseFloat(normStart) : null}
                        normEnd={normEnd ? parseFloat(normEnd) : null}
                      />
                    </div>
                  )}
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '20px', marginBottom: '16px' }}>
                    <div>
                      <label style={{ display: 'block', fontSize: '0.9em', marginBottom: '4px' }}>Zero 기준 구간 (decay 후, ns)</label>
                      <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                        <input
                          type="number"
                          value={baselineStart}
                          onChange={e => setBaselineStart(Number(e.target.value))}
                          style={{ width: '90px', padding: '6px 8px' }}
                        />
                        <span>~</span>
                        <input
                          type="number"
                          value={baselineEnd}
                          onChange={e => setBaselineEnd(Number(e.target.value))}
                          style={{ width: '90px', padding: '6px 8px' }}
                        />
                      </div>
                      <span style={{ fontSize: '0.8em', color: '#666' }}>decay 완료 후 0 기준값</span>
                    </div>
                    <div>
                      <label style={{ display: 'block', fontSize: '0.9em', marginBottom: '4px' }}>Saturation 기준 구간 (decay 포화, ns)</label>
                      <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                        <input
                          type="number"
                          placeholder="-250000"
                          value={normStart}
                          onChange={e => setNormStart(e.target.value)}
                          style={{ width: '90px', padding: '6px 8px' }}
                        />
                        <span>~</span>
                        <input
                          type="number"
                          placeholder="-50000"
                          value={normEnd}
                          onChange={e => setNormEnd(e.target.value)}
                          style={{ width: '90px', padding: '6px 8px' }}
                        />
                      </div>
                      <span style={{ fontSize: '0.8em', color: '#666' }}>해당 구간 평균을 1.0으로 정규화</span>
                    </div>
                  </div>
                </div>
              )}
              <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
                <button
                  className="simulate-button"
                  onClick={handleProcess}
                  disabled={processing || backendStatus?.status !== 'ok'}
                >
                  {processing ? '처리 중...' : '처리 시작'}
                </button>
                {processing && (
                  <button
                    type="button"
                    onClick={() => batchAbortRef.current?.abort()}
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
            </div>
          )}

          {error && (
            <div className="error-message" style={{ marginTop: '16px' }}>{error}</div>
          )}

          {(results.vil?.length > 0 || results.osc?.length > 0 || results.master) && (
            <div className="results-section" style={{ marginTop: '24px' }}>
              <h3>처리 결과</h3>
              <p style={{ color: '#666', marginBottom: '16px', fontSize: '0.95em' }}>
                {results.vil?.length > 0 && <>✓ VIL {results.vil.filter(r => r.success).length}개 저장</>}
                {results.osc?.length > 0 && <span style={{ marginLeft: '8px' }}>✓ TrEL {results.osc.filter(r => r.success).length}개 → TrEL_processed</span>}
                {results.master && <span style={{ marginLeft: '8px' }}>✓ TrEL_processed/{results.masterMetadata?.output_filename || 'TrEL_Master.xlsx'}</span>}
              </p>
              {results.master && results.masterMetadata?.files_used?.length > 0 && (
                <div style={{ marginBottom: '12px', padding: '12px', background: '#e3f2fd', borderRadius: '6px' }}>
                  <strong>마스터 파일에 사용된 TrEL 데이터 (VIL 보간 기반 선택):</strong>
                  <ul style={{ margin: '8px 0 0 0', paddingLeft: '20px' }}>
                    {results.masterMetadata.files_used.map((f, i) => (
                      <li key={i}>
                        <span style={{ fontWeight: 600, color: '#1565c0' }}>{f.percent}</span>
                        {' → '}
                        {f.filename}
                        {f.minutes != null && <span style={{ color: '#666', marginLeft: '4px' }}>({f.minutes})</span>}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {results.vil?.length > 0 && (
                <div style={{ marginBottom: '12px' }}>
                  <strong>VIL:</strong>
                  {results.vil.map((r, i) => (
                    <div key={i} style={{ padding: '8px', background: r.success ? '#e8f5e9' : '#ffebee', borderRadius: '4px', marginTop: '4px' }}>
                      {r.relPath || r.filename} {r.success ? `(shift: ${r.time_shift_s != null ? r.time_shift_s.toFixed(2) : '?'}s, ${r.filtered_points} pts)` : `- ${r.error}`}
                    </div>
                  ))}
                </div>
              )}
              {results.osc?.length > 0 && (
                <div style={{ marginBottom: '12px' }}>
                  <strong>TrEL (rise/decay 페어):</strong>
                  {results.osc.map((r, i) => (
                    <div key={i} style={{ padding: '8px', background: r.success ? '#e8f5e9' : '#ffebee', borderRadius: '4px', marginTop: '4px' }}>
                      {r.relPath || r.filename}
                      {r.rise_filename && r.decay_filename && (
                        <span style={{ color: '#666', fontSize: '0.85em' }}> ({r.rise_filename} + {r.decay_filename})</span>
                      )}
                      {r.success ? ` (${r.original_points} pts)` : ` - ${r.error}`}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
        )}

        {activeTab === 'analysis' && <AnalysisTab backendStatus={backendStatus} />}
        {activeTab === 'contour' && <ContourTab backendStatus={backendStatus} />}
      </div>
    </div>
  )
}

export default App
