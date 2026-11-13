import React, { useState } from 'react'
import { apiUrl } from './api'

const readJson = async (res) => {
  const data = await res.json().catch(() => null)
  if (!data) {
    throw new Error(`서버 응답을 읽을 수 없습니다. (HTTP ${res.status}) 백엔드를 다시 실행해주세요.`)
  }
  return data
}

export default function ContourTab({ backendStatus }) {
  const [root, setRoot] = useState('')
  const [trelCount, setTrelCount] = useState(0)
  const [vilCount, setVilCount] = useState(0)
  const [ready, setReady] = useState(false)
  const [selecting, setSelecting] = useState(false)
  const [scanning, setScanning] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [lumHigh, setLumHigh] = useState(100)
  const [lumLow, setLumLow] = useState(50)
  const [timeStartUs, setTimeStartUs] = useState(0)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)

  const handleFolderSelect = async () => {
    if (selecting || scanning) return
    setError(null)
    setResult(null)
    setSelecting(true)
    try {
      const pickRes = await fetch(apiUrl('/api/pick-folder'), { method: 'POST' })
      const pick = await readJson(pickRes)
      if (!pick.success) throw new Error(pick.error || '폴더 선택에 실패했습니다.')
      if (pick.cancelled || !pick.path) return

      setSelecting(false)
      setScanning(true)
      const scanRes = await fetch(apiUrl('/api/scan-analysis'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: pick.path }),
      })
      const scanned = await readJson(scanRes)
      if (!scanned.success) throw new Error(scanned.error || '폴더 스캔에 실패했습니다.')
      const files = scanned.analysisFiles || []
      if (files.length === 0) {
        setReady(false)
        setRoot('')
        throw new Error('TrEL CSV가 없습니다. _TrEL.csv가 들어 있는 폴더를 선택해주세요.')
      }
      setRoot(pick.path)
      setTrelCount(files.length)
      setVilCount((scanned.analysisVilFiles || []).length)
      setReady(true)
    } catch (err) {
      setError(err.message === 'Failed to fetch' ? '백엔드 연결 실패' : err.message)
      setReady(false)
    } finally {
      setSelecting(false)
      setScanning(false)
    }
  }

  const handleExport = async () => {
    if (!root || exporting) return
    setError(null)
    setResult(null)
    setExporting(true)
    try {
      const res = await fetch(apiUrl('/api/contour-export'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: root,
          lum_high: lumHigh,
          lum_low: lumLow,
          time_start_us: timeStartUs,
        }),
      })
      const data = await readJson(res)
      if (!res.ok || !data.success) throw new Error(data.error || '정리에 실패했습니다.')
      setResult(data)
    } catch (err) {
      setError(err.message === 'Failed to fetch' ? '백엔드 연결 실패' : err.message)
    } finally {
      setExporting(false)
    }
  }

  return (
    <div style={{ marginTop: '20px' }}>
      <h2>Contour 데이터 정리</h2>
      <p style={{ color: '#666', marginBottom: '20px' }}>
        _TrEL.csv를 시간 축에 맞춰 붙여, contour plot에 바로 넣을 수 있는 엑셀을 만듭니다.
        rise와 decay는 각각 <strong>100-50 rise.xlsx</strong>, <strong>100-50 decay.xlsx</strong>처럼 저장됩니다.
        두 번째 행은 측정 분, 세 번째 행은 VIL에서 읽은 상대 휘도(%)입니다.
      </p>

      {backendStatus?.status !== 'ok' && (
        <p style={{ color: '#d32f2f', marginBottom: '16px' }}>백엔드 연결 필요. pnpm dev:all로 실행해주세요.</p>
      )}

      <button
        className="simulate-button"
        onClick={handleFolderSelect}
        disabled={selecting || scanning || exporting || backendStatus?.status !== 'ok'}
      >
        {selecting ? '탐색기에서 폴더를 선택하세요...' : scanning ? '폴더 스캔 중...' : '폴더 선택'}
      </button>

      {ready && (
        <div style={{ marginTop: '20px', padding: '16px', background: '#fafafa', borderRadius: '6px', border: '1px solid #e0e0e0' }}>
          <p style={{ marginBottom: '12px', wordBreak: 'break-all' }}>
            <strong>{root}</strong>
            <span style={{ color: '#666' }}> · TrEL {trelCount}개 · VIL {vilCount}개</span>
          </p>
          {vilCount === 0 && (
            <p style={{ color: '#e65100', marginBottom: '12px' }}>
              VIL_processed 파일이 없으면 휘도 행은 비우고, 측정 시간이 있는 TrEL을 모두 넣습니다.
            </p>
          )}

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '20px', marginBottom: '16px' }}>
            <div>
              <label style={{ display: 'block', fontSize: '0.9em', marginBottom: '4px' }}>휘도 상한 (%)</label>
              <input type="number" value={lumHigh} onChange={e => setLumHigh(Number(e.target.value))} style={{ width: '90px', padding: '6px 8px' }} />
            </div>
            <div>
              <label style={{ display: 'block', fontSize: '0.9em', marginBottom: '4px' }}>휘도 하한 (%)</label>
              <input type="number" value={lumLow} onChange={e => setLumLow(Number(e.target.value))} style={{ width: '90px', padding: '6px 8px' }} />
            </div>
            <div>
              <label style={{ display: 'block', fontSize: '0.9em', marginBottom: '4px' }}>시간 시작 (µs)</label>
              <input type="number" value={timeStartUs} onChange={e => setTimeStartUs(Number(e.target.value))} style={{ width: '100px', padding: '6px 8px' }} />
            </div>
          </div>

          <button className="simulate-button" onClick={handleExport} disabled={exporting || backendStatus?.status !== 'ok'}>
            {exporting ? '정리 중...' : 'Contour 엑셀 만들기'}
          </button>

          {result && (
            <div style={{ marginTop: '16px', color: '#2e7d32' }}>
              <p style={{ fontWeight: 600, marginBottom: '8px' }}>
                {result.n_traces}개 곡선을 정리했습니다.
                {result.luminance_max != null && (
                  <span> 휘도 {result.luminance_max.toFixed(1)}% → {result.luminance_min.toFixed(1)}%</span>
                )}
                {result.time_end_us != null && (
                  <span> · {result.time_start_us}–{Number(result.time_end_us).toFixed(3)} µs ({result.n_times} pts)</span>
                )}
              </p>
              {result.files.map(file => (
                <p key={file.filename} style={{ margin: '4px 0', wordBreak: 'break-all' }}>✓ {file.path}</p>
              ))}
              {result.vil_file && <p style={{ color: '#666', marginTop: '8px' }}>VIL 참조: {result.vil_file}</p>}
              {result.vil_error && <p style={{ color: '#e65100' }}>VIL을 읽지 못했습니다: {result.vil_error}</p>}
              {result.skipped?.length > 0 && (
                <p style={{ color: '#666' }}>시간 정보를 읽지 못해 제외한 파일 {result.skipped.length}개</p>
              )}
            </div>
          )}
        </div>
      )}

      {error && <div className="error-message" style={{ marginTop: '16px' }}>{error}</div>}
    </div>
  )
}
