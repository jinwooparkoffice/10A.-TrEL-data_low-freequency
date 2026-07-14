const yieldToUi = async (counter) => {
  if (counter.count % 200 === 0) {
    await new Promise(resolve => setTimeout(resolve, 0))
  }
}

const SKIP_DIRECTORIES = new Set([
  '.git',
  '.cursor',
  '.github',
  'node_modules',
  'dist',
  '__pycache__',
  '.venv',
  'venv',
])

const shouldSkipDirectory = (name) => name.startsWith('.') || SKIP_DIRECTORIES.has(name)

const isRiseFilename = (name) => /_rise(?:_|\.|$)/i.test(name)
const isDecayFilename = (name) => /_decay(?:_|\.|$)/i.test(name)

export const getPairKey = (filename) => {
  const base = (filename || '').replace(/\.[^.]+$/i, '')
  if (!/_rise(?:_|$)|_decay(?:_|$)/i.test(base)) return null
  const key = base.replace(/_(?:rise|decay)(?=_|$)/gi, '')
  return key || null
}

export const buildOscPairs = (oscFiles) => {
  const riseByKey = new Map()
  const decayByKey = new Map()
  const unpairedOsc = []

  for (const entry of oscFiles) {
    const key = getPairKey(entry.name)
    if (!key) {
      unpairedOsc.push({ ...entry, reason: 'rise/decay 접미사 없음' })
      continue
    }
    if (isRiseFilename(entry.name)) {
      if (riseByKey.has(key)) {
        unpairedOsc.push({ ...entry, reason: `중복 rise 페어: ${key}` })
      } else {
        riseByKey.set(key, entry)
      }
      continue
    }
    if (isDecayFilename(entry.name)) {
      if (decayByKey.has(key)) {
        unpairedOsc.push({ ...entry, reason: `중복 decay 페어: ${key}` })
      } else {
        decayByKey.set(key, entry)
      }
      continue
    }
    unpairedOsc.push({ ...entry, reason: 'rise/decay 접미사 없음' })
  }

  const oscPairs = []
  for (const [key, rise] of riseByKey.entries()) {
    const decay = decayByKey.get(key)
    if (decay) {
      oscPairs.push({ key, rise, decay })
      decayByKey.delete(key)
    } else {
      unpairedOsc.push({ ...rise, reason: `decay 페어 없음: ${key}` })
    }
  }
  for (const [key, decay] of decayByKey.entries()) {
    unpairedOsc.push({ ...decay, reason: `rise 페어 없음: ${key}` })
  }

  oscPairs.sort((a, b) => a.key.localeCompare(b.key))
  return { oscPairs, unpairedOsc }
}

export const collectFiles = async (dir, prefix, filter, counter = { count: 0 }) => {
  const list = []
  for await (const [name, handle] of dir.entries()) {
    counter.count += 1
    await yieldToUi(counter)

    const relPath = prefix ? `${prefix}/${name}` : name
    if (handle.kind === 'directory') {
      if (shouldSkipDirectory(name)) {
        continue
      }
      list.push(...await collectFiles(handle, relPath, filter, counter))
      continue
    }

    if (filter(name)) {
      list.push({ name, relPath, handle })
    }
  }
  return list
}

export const scanBatchFolder = async (dir) => {
  const vilFiles = []
  const oscFiles = []
  const existingTrel = []
  const counter = { count: 0 }

  const walk = async (currentDir, prefix = '') => {
    for await (const [name, handle] of currentDir.entries()) {
      counter.count += 1
      await yieldToUi(counter)

      const relPath = prefix ? `${prefix}/${name}` : name
      if (handle.kind === 'directory') {
        if (shouldSkipDirectory(name)) {
          continue
        }
        await walk(handle, relPath)
        continue
      }

      if (name.startsWith('._') || !name.endsWith('.csv')) {
        continue
      }

      const upperName = name.toUpperCase()
      if (upperName.includes('VIL')) {
        vilFiles.push({ name, relPath, handle })
        continue
      }

      if (name.endsWith('_TrEL.csv')) {
        existingTrel.push({ name, relPath, handle })
      }

      if (name.includes('Hz')) {
        oscFiles.push({ name, relPath, handle })
      }
    }
  }

  await walk(dir)
  const { oscPairs, unpairedOsc } = buildOscPairs(oscFiles)
  return { vilFiles, oscFiles, oscPairs, unpairedOsc, existingTrel }
}

/** 파일명에서 Duty fraction 추출 (duty50% → 0.5) */
export const parseDutyFromFilename = (filename = '') => {
  const match = (filename || '').match(/duty\s*(\d+(?:\.\d+)?)\s*%?/i)
  if (!match) return null
  const val = Number.parseFloat(match[1])
  if (!Number.isFinite(val)) return null
  return val > 1 ? val / 100 : val
}

/** VIL processed CSV 첫 줄 주석에서 time_shift_s 추출 */
export const parseVilTimeShiftMinFromText = (text = '') => {
  const firstLine = (text || '').split(/\r?\n/).find(line => line.startsWith('#'))
  if (!firstLine) return null
  const match = firstLine.match(/time_shift_s:\s*([\d.eE+-]+)/i)
  if (!match) return null
  const seconds = Number.parseFloat(match[1])
  return Number.isFinite(seconds) ? seconds / 60 : null
}

/** VIL processed 파일 handle에서 time_shift_min 추출 (CSV 주석 또는 null) */
export const readVilTimeShiftMin = async (handle) => {
  if (!handle) return null
  const file = await handle.getFile()
  const name = file.name.toLowerCase()
  if (!name.endsWith('.csv')) return null
  const text = await file.text()
  return parseVilTimeShiftMinFromText(text)
}

/** 파일명에서 측정 시간(분) 추출. duty25%_1h0min, 10min 등 형식 지원 */
export const parseMinutesFromFilename = (filename) => {
  const base = (filename || '').replace(/\.[^.]+$/, '')
  const pats = [
    /(?:^|[_\-\s])(?:(\d+)\s*h)?\s*(\d+)\s*min(?:$|[_\-\s])/gi,
    /(?:^|[_\-\s])(?:(\d+)\s*h)?\s*(\d+)\s*m(?:$|[_\-\s])/gi,
    /(?:^|[_\-\s])(\d+)\s*h(?:$|[_\-\s])/gi,
  ]
  for (const re of pats) {
    const m = [...base.matchAll(re)]
    if (m.length) {
      const g = m[m.length - 1]
      if (g[2] != null) {
        const h = g[1] ? parseInt(g[1], 10) : 0
        return h * 60 + parseInt(g[2], 10)
      }
      if (g[1]) return parseInt(g[1], 10) * 60
    }
  }
  return null
}

/** 10분에 가장 가까운 파일 인덱스. 없으면 0 */
export const indexClosestTo10Min = (files) => {
  if (!files?.length) return 0
  let best = 0
  let bestDiff = Math.abs((parseMinutesFromFilename(files[0].name) ?? 10) - 10)
  for (let i = 1; i < files.length; i++) {
    const tm = parseMinutesFromFilename(files[i].name) ?? 10
    const d = Math.abs(tm - 10)
    if (d < bestDiff) {
      bestDiff = d
      best = i
    }
  }
  return best
}

export const scanAnalysisFolder = async (dir) => {
  const analysisFiles = []
  const analysisVilFiles = []
  const counter = { count: 0 }

  const walk = async (currentDir, prefix = '') => {
    for await (const [name, handle] of currentDir.entries()) {
      counter.count += 1
      await yieldToUi(counter)

      const relPath = prefix ? `${prefix}/${name}` : name
      if (handle.kind === 'directory') {
        if (shouldSkipDirectory(name)) {
          continue
        }
        await walk(handle, relPath)
        continue
      }

      if (name.startsWith('._')) {
        continue
      }

      const upperName = name.toUpperCase()
      const isCsv = name.endsWith('.csv')
      const isXlsx = name.endsWith('.xlsx')

      if (isCsv && (name.includes('_TrEL') || name.includes('TrEL'))) {
        analysisFiles.push({ name, relPath, handle })
        continue
      }

      if ((isCsv || isXlsx) && upperName.includes('VIL') && name.includes('_processed')) {
        analysisVilFiles.push({ name, relPath, handle })
      }
    }
  }

  await walk(dir)
  return { analysisFiles, analysisVilFiles }
}

export const dedupFilesByPath = (list) => {
  const seen = new Set()
  return list.filter(({ relPath }) => {
    if (seen.has(relPath)) {
      return false
    }
    seen.add(relPath)
    return true
  })
}
