import { useEffect, useRef, useState, type FormEvent } from 'react'
import { loadData } from './data'
import { createDatasetJob, datasetFilesBase, getActiveDataset, getDatasetJob, INPUT_NAMES, MAX_FILE_BYTES, validateFiles, waitForPoll, type DatasetJob, type InputFiles, type InputName } from './datasets'
import type { GraphData } from './types'

interface Props {
  data: GraphData | null
  onDatasetReady: (data: GraphData | null) => void
  onLegacy: () => Promise<void>
  onInitialCheckDone: () => void
}
type Phase = 'checking' | 'empty' | 'selected' | 'uploading' | 'validating' | 'running' | 'publishing' | 'ready' | 'error'
const phaseLabels: Record<Phase, string> = {
  checking: 'Проверяем доступные данные', empty: 'Данные ещё не загружены', selected: 'Файлы выбраны',
  uploading: 'Передаём файлы', validating: 'Проверяем структуру данных', running: 'Рассчитываем роли и связи',
  publishing: 'Открываем готовый граф', ready: 'Анализ завершён', error: 'Загрузка не завершена',
}
const labels: Record<InputName, string> = { nodes: 'Клиенты', edges: 'Связи', transactions: 'Переводы' }
const downloadFiles = ['nodes_roles.csv', 'clusters.csv', 'top_nodes.csv']
const formatSeconds = (seconds: number) => `${seconds.toFixed(1)} с`

export default function DatasetUpload({ data, onDatasetReady, onLegacy, onInitialCheckDone }: Props) {
  const [files, setFiles] = useState<InputFiles>({})
  const [phase, setPhase] = useState<Phase>('checking')
  const [error, setError] = useState('')
  const [available, setAvailable] = useState<boolean | null>(null)
  const [connectAttempt, setConnectAttempt] = useState(0)
  const [expanded, setExpanded] = useState(true)
  const [uploadSeconds, setUploadSeconds] = useState<number | null>(null)
  const [elapsed, setElapsed] = useState<number | null>(null)
  const [retryJob, setRetryJob] = useState<string | null>(null)
  const controller = useRef<AbortController | null>(null)
  const locked = useRef(false)
  const busy = ['checking', 'uploading', 'validating', 'running', 'publishing'].includes(phase)

  async function publish(id: string, signal: AbortSignal) {
    setPhase('publishing')
    const next = await loadData(datasetFilesBase(id))
    if (signal.aborted) return
    onDatasetReady({ ...next, datasetId: id })
    setPhase('ready'); setExpanded(false); setRetryJob(null)
  }
  async function followJob(first: DatasetJob, signal: AbortSignal) {
    let job = first
    setRetryJob(job.job_id)
    while (!signal.aborted) {
      setPhase(job.state); setElapsed(job.elapsed_seconds)
      if (job.state === 'error') { setRetryJob(null); throw new Error(job.error || 'Не удалось рассчитать граф. Проверьте входные файлы.') }
      if (job.state === 'ready') { await publish(job.dataset_id!, signal); return }
      await waitForPoll(signal)
      job = await getDatasetJob(job.job_id, signal)
    }
  }
  useEffect(() => {
    const current = new AbortController()
    controller.current = current; locked.current = true
    setAvailable(null); setPhase('checking'); setError(''); setElapsed(null); setUploadSeconds(null)
    void (async () => {
      let active
      try { active = await getActiveDataset(current.signal) }
      catch {
        if (current.signal.aborted) return
        setAvailable(false); setPhase('empty')
        if (connectAttempt === 0) await onLegacy()
        return
      }
      if (current.signal.aborted) return
      setAvailable(true); setRetryJob(null)
      try {
        if (active.dataset_id) await publish(active.dataset_id, current.signal)
        else { onDatasetReady(null); setPhase('empty') }
        if (active.job && (active.job.state === 'running' || active.job.state === 'validating' || !active.dataset_id)) {
          setExpanded(true); await followJob(active.job, current.signal)
        }
      } catch (caught) {
        if (!current.signal.aborted) { setError(caught instanceof Error ? caught.message : 'Не удалось открыть данные.'); setPhase('error'); setExpanded(true) }
      }
    })().finally(() => { if (!current.signal.aborted) { locked.current = false; onInitialCheckDone() } })
    return () => { current.abort(); controller.current?.abort() }
    // This effect owns initialization only. Callback props are stable in App.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onDatasetReady, onLegacy, onInitialCheckDone, connectAttempt])

  function selectFile(name: InputName, file: File | undefined) {
    if (locked.current) return
    setError('')
    if (file && file.name !== `${name}.parquet`) {
      const duplicate = Object.values(files).some(value => value?.name === file.name)
      setFiles(current => ({ ...current, [name]: undefined }))
      setError(duplicate ? `${file.name} уже выбран. В этом поле нужен ${name}.parquet.` : `В поле «${labels[name]}» нужен файл ${name}.parquet.`)
      return
    }
    if (file && (file.size > MAX_FILE_BYTES || file.size === 0)) { setFiles(current => ({ ...current, [name]: undefined })); setError(file.size === 0 ? `${file.name}: файл пустой.` : `${file.name}: размер превышает 10 МиБ.`); return }
    setFiles(current => ({ ...current, [name]: file })); setPhase('selected'); setUploadSeconds(null); setElapsed(null)
  }
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (locked.current) return
    const problem = validateFiles(files)
    if (problem) { setError(problem); return }
    locked.current = true; controller.current?.abort()
    const current = new AbortController(); controller.current = current
    setError(''); setPhase('uploading'); setUploadSeconds(null); setElapsed(null); setRetryJob(null)
    const started = performance.now()
    try {
      const job = await createDatasetJob(files, current.signal)
      if (current.signal.aborted) return
      setUploadSeconds((performance.now() - started) / 1000)
      await followJob(job, current.signal)
    } catch (caught) {
      if (!current.signal.aborted) { setError(caught instanceof Error ? caught.message : 'Нет соединения с сервисом загрузки.'); setPhase('error') }
    } finally { if (!current.signal.aborted) locked.current = false }
  }
  async function retry() {
    if (!retryJob || locked.current) return
    locked.current = true; controller.current?.abort()
    const current = new AbortController(); controller.current = current
    setError(''); setPhase('validating')
    try { await followJob(await getDatasetJob(retryJob, current.signal), current.signal) }
    catch (caught) { if (!current.signal.aborted) { setError(caught instanceof Error ? caught.message : 'Статус пока недоступен.'); setPhase('error') } }
    finally { if (!current.signal.aborted) locked.current = false }
  }

  return <section className={`dataset-panel panel ${data ? 'has-dataset' : ''}`} aria-label="Загрузка данных" aria-busy={busy}>
    <div className="dataset-heading"><div><p className="eyebrow">ИСХОДНЫЕ ДАННЫЕ</p><h2>{data ? 'Набор данных для анализа' : 'Начните с банковской выгрузки'}</h2></div>
      {data && <button className="secondary-button" type="button" onClick={() => setExpanded(value => !value)} aria-expanded={expanded}>{expanded ? 'Свернуть загрузку' : 'Загрузить другой набор'}</button>}
    </div>
    {expanded && <>
      <p className="dataset-intro">Выберите три файла Parquet. Приложение построит граф, определит роли и подготовит очередь проверки.</p>
      {available === false && <div className="dataset-offline" role="status"><p>Сервис загрузки недоступен. Можно просматривать готовую выгрузку; для загрузки файлов запустите приложение с API по инструкции в README.</p><button className="secondary-button" type="button" onClick={() => setConnectAttempt(attempt => attempt + 1)}>Повторить подключение</button></div>}
      <form onSubmit={event => void submit(event)} aria-busy={busy}>
        <div className="dataset-files">{INPUT_NAMES.map(name => <div className="dataset-file" key={name}>
          <label htmlFor={`upload-${name}`}><strong>{labels[name]}</strong><span>{name}.parquet</span></label>
          <input id={`upload-${name}`} type="file" accept=".parquet" aria-label={`${name}.parquet`} disabled={busy || available === false} onChange={event => { selectFile(name, event.target.files?.[0]); event.target.value = '' }} />
          <small>{files[name] ? `${files[name]!.name} · ${(files[name]!.size / 1024).toFixed(1)} КиБ` : 'Файл не выбран'}</small>
        </div>)}</div>
        <div className="dataset-actions"><p>До 10 МиБ на файл · данные обрабатываются локально</p><button type="submit" className="dataset-build" disabled={busy || available === false || Boolean(retryJob)}>{busy ? phaseLabels[phase] : 'Построить граф'} <span aria-hidden="true">→</span></button></div>
      </form>
    </>}
    <div className="dataset-status" role="status" aria-live="polite"><span className={busy ? 'spinner' : 'dataset-status-dot'} /><span>{phaseLabels[phase]}{uploadSeconds !== null && ` · передача ${formatSeconds(uploadSeconds)}`}{elapsed !== null && ` · расчёт ${formatSeconds(elapsed)}`}</span></div>
    {error && <div className="dataset-error" role="alert"><p>{error}</p>{data && <small>Предыдущий граф остаётся доступным.</small>}{retryJob && <><button type="button" className="secondary-button" onClick={() => void retry()} disabled={busy}>Повторить проверку статуса</button><button type="button" className="secondary-button" onClick={() => setConnectAttempt(attempt => attempt + 1)} disabled={busy}>Восстановить подключение</button></>}</div>}
    {data && <nav className="dataset-downloads" aria-label="Скачать результаты"><span>Скачать CSV</span>{downloadFiles.map(file => <a key={file} href={`${data.source}/${file}`} download={file}>{file} <span aria-hidden="true">↓</span></a>)}</nav>}
  </section>
}
