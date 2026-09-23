export const INPUT_NAMES = ['nodes', 'edges', 'transactions'] as const
export type InputName = typeof INPUT_NAMES[number]
export type InputFiles = Partial<Record<InputName, File>>
export const MAX_FILE_BYTES = 10 * 1024 * 1024
export interface DatasetJob {
  job_id: string
  state: 'validating' | 'running' | 'ready' | 'error'
  dataset_id: string | null
  error: string | null
  elapsed_seconds: number
}
export interface ActiveDataset { dataset_id: string | null; files_base: string | null; job: DatasetJob | null }

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Некорректный ответ сервера загрузки.')
  return value as Record<string, unknown>
}
function identifier(value: unknown): value is string {
  return typeof value === 'string' && /^[a-zA-Z0-9_-]{1,100}$/.test(value)
}
function parseJob(value: unknown): DatasetJob {
  const job = object(value)
  if (!identifier(job.job_id) || !['validating', 'running', 'ready', 'error'].includes(String(job.state)) ||
    !(job.dataset_id === null || identifier(job.dataset_id)) || !(job.error === null || typeof job.error === 'string') ||
    typeof job.elapsed_seconds !== 'number' || !Number.isFinite(job.elapsed_seconds) || job.elapsed_seconds < 0 ||
    (job.state === 'ready' && !job.dataset_id)) throw new Error('Некорректный статус анализа.')
  return job as unknown as DatasetJob
}
async function json(url: string, init: RequestInit = {}): Promise<unknown> {
  const response = await fetch(url, { ...init, cache: 'no-store' })
  let value: unknown
  try { value = await response.json() }
  catch { throw new Error('Сервис загрузки недоступен. Проверьте подключение и повторите попытку.') }
  if (!response.ok) {
    const message = object(value).error
    throw new Error(typeof message === 'string' ? message : `Сервис загрузки вернул ошибку ${response.status}.`)
  }
  return value
}
export function datasetFilesBase(id: string): string { return `/api/datasets/${encodeURIComponent(id)}/files` }
export async function getActiveDataset(signal: AbortSignal): Promise<ActiveDataset> {
  const raw = object(await json('/api/datasets/active', { signal: AbortSignal.any([signal, AbortSignal.timeout(5000)]) }))
  if (!(raw.dataset_id === null || identifier(raw.dataset_id)) ||
    (raw.dataset_id !== null && raw.files_base !== datasetFilesBase(raw.dataset_id))) throw new Error('Некорректная версия набора данных.')
  return { dataset_id: raw.dataset_id, files_base: raw.dataset_id ? datasetFilesBase(raw.dataset_id) : null,
    job: raw.job == null ? null : parseJob(raw.job) }
}
export function validateFiles(files: InputFiles): string | null {
  const missing = INPUT_NAMES.filter(name => !files[name])
  if (missing.length) return `Добавьте файлы: ${missing.map(name => `${name}.parquet`).join(', ')}.`
  const names = INPUT_NAMES.map(name => files[name]!.name)
  if (new Set(names).size !== names.length) return 'Файлы повторяются. Нужны три разных файла: nodes.parquet, edges.parquet и transactions.parquet.'
  for (const name of INPUT_NAMES) {
    const file = files[name]!
    if (file.name !== `${name}.parquet`) return `В поле ${name} нужен файл ${name}.parquet.`
    if (file.size === 0) return `${file.name}: файл пустой.`
    if (file.size > MAX_FILE_BYTES) return `${file.name}: размер превышает 10 МиБ.`
  }
  return null
}
export async function createDatasetJob(files: InputFiles, signal: AbortSignal): Promise<DatasetJob> {
  const problem = validateFiles(files)
  if (problem) throw new Error(problem)
  const form = new FormData()
  INPUT_NAMES.forEach(name => form.append(name, files[name]!, `${name}.parquet`))
  return parseJob(await json('/api/datasets/jobs', { method: 'POST', body: form, signal: AbortSignal.any([signal, AbortSignal.timeout(120_000)]) }))
}
export async function getDatasetJob(id: string, signal: AbortSignal): Promise<DatasetJob> {
  return parseJob(await json(`/api/datasets/jobs/${encodeURIComponent(id)}`, { signal: AbortSignal.any([signal, AbortSignal.timeout(10_000)]) }))
}
export function waitForPoll(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(signal.reason); return }
    const stop = () => { clearTimeout(timer); reject(signal.reason) }
    const timer = setTimeout(() => { signal.removeEventListener('abort', stop); resolve() }, 700)
    signal.addEventListener('abort', stop, { once: true })
  })
}
