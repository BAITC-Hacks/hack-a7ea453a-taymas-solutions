/** Small RFC4180-compatible parser so the local UI has no runtime API dependency. */
export function parseCsv(text: string): Record<string, string>[] {
  const rows: string[][] = []
  let row: string[] = []
  let cell = ''
  let quoted = false
  const input = text.replace(/^\uFEFF/, '')
  for (let i = 0; i < input.length; i += 1) {
    const char = input[i]
    if (quoted) {
      if (char === '"' && input[i + 1] === '"') { cell += '"'; i += 1 }
      else if (char === '"') quoted = false
      else cell += char
    } else if (char === '"' && cell.length === 0) quoted = true
    else if (char === ',') { row.push(cell); cell = '' }
    else if (char === '\n') { row.push(cell); rows.push(row); row = []; cell = '' }
    else if (char !== '\r') cell += char
  }
  if (cell.length > 0 || row.length > 0) { row.push(cell); rows.push(row) }
  if (rows.length === 0) return []
  const headers = rows[0].map((header) => header.trim())
  return rows.slice(1).filter((values) => values.some((v) => v !== '')).map((values) =>
    Object.fromEntries(headers.map((header, index) => [header, values[index] ?? ''])),
  )
}

export function numberValue(value: string | undefined, fallback = 0): number {
  const result = Number(value)
  return Number.isFinite(result) ? result : fallback
}

export function booleanValue(value: string | undefined): boolean {
  return ['true', '1', 'yes', 'да'].includes((value ?? '').trim().toLowerCase())
}
