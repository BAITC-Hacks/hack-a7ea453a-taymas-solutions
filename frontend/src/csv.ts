/** RFC4180 parser with physical line numbers for actionable validation errors. */
export interface CsvTable { headers: string[]; rows: Record<string, string>[]; lines: number[] }
export class CsvParseError extends Error {
  constructor(message: string, public line: number, public field?: string) { super(message); this.name = 'CsvParseError' }
}

export function parseCsvTable(text: string): CsvTable {
  const input = text.replace(/^\uFEFF/, '')
  const records: { values: string[]; line: number }[] = []
  let values: string[] = [], cell = '', quoted = false, closedQuote = false, line = 1, rowLine = 1, started = false
  const finishCell = () => { values.push(cell); cell = ''; closedQuote = false }
  const finishRow = () => {
    if (started || cell || values.length) { finishCell(); records.push({ values, line: rowLine }) }
    values = []; cell = ''; started = false; closedQuote = false
  }
  for (let i = 0; i < input.length; i += 1) {
    const char = input[i]
    if (quoted) {
      if (char === '"' && input[i + 1] === '"') { cell += '"'; i += 1 }
      else if (char === '"') { quoted = false; closedQuote = true }
      else { cell += char; if (char === '\n') line += 1 }
      continue
    }
    if (char === ',') { finishCell(); started = true }
    else if (char === '\n' || char === '\r') {
      if (char === '\r' && input[i + 1] === '\n') i += 1
      finishRow(); line += 1; rowLine = line
    } else if (closedQuote) throw new CsvParseError('лишний символ после закрывающей кавычки', line)
    else if (char === '"') {
      if (cell) throw new CsvParseError('кавычка внутри неэкранированного поля', line)
      quoted = true; started = true
    } else { cell += char; started = true }
  }
  if (quoted) throw new CsvParseError('не закрыта кавычка', rowLine)
  finishRow()
  if (!records.length) return { headers: [], rows: [], lines: [] }
  const headers = records[0].values.map(header => header.trim())
  const seen = new Set<string>()
  for (const header of headers) {
    if (!header) throw new CsvParseError('пустое имя колонки', records[0].line)
    if (seen.has(header)) throw new CsvParseError('колонка повторяется', records[0].line, header)
    seen.add(header)
  }
  const rows = records.slice(1).map(record => {
    if (record.values.length !== headers.length) throw new CsvParseError(`ожидалось ${headers.length} полей, получено ${record.values.length}`, record.line)
    return Object.fromEntries(headers.map((header, index) => [header, record.values[index]]))
  })
  return { headers, rows, lines: records.slice(1).map(record => record.line) }
}

export function parseCsv(text: string): Record<string, string>[] { return parseCsvTable(text).rows }

export function numberValue(value: string | undefined): number {
  const input = value?.trim() ?? ''
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(input)) throw new Error('ожидается непустое число')
  const number = Number(input)
  if (!Number.isFinite(number)) throw new Error('число должно быть конечным')
  return number
}

export function booleanValue(value: string | undefined): boolean {
  const input = value?.trim().toLowerCase()
  if (['true', '1', 'yes', 'да'].includes(input ?? '')) return true
  if (['false', '0', 'no', 'нет'].includes(input ?? '')) return false
  throw new Error('ожидается true/false или 1/0')
}
