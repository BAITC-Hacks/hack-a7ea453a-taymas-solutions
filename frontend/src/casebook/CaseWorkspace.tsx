import { useLayoutEffect, useRef, useState } from 'react'
import type { GraphData } from '../types'
import type { Claim } from '../copilot/api'
import { Icon } from '../components/Icon'
import { MAX_CASE_BYTES, MAX_NOTE_LENGTH, parseCase, serializeCase, snapshotStatus, STATUS_LABELS, SCOPE,
  type CaseFile, type CaseItem, type DatasetVersion, type SnapshotStatus } from './model'
import { downloadFile, FACT_LABELS, factValue, formatDate, renderReport } from './report'
import type { CaseController } from './useCaseFile'
import './casebook.css'

export default function CaseWorkspace({ controller: c, data, onNavigate, onExplore }: {
  controller: CaseController; data: GraphData | null; onNavigate: (gid: string) => void; onExplore: () => void
}) {
  const input = useRef<HTMLInputElement>(null)
  const titleInput = useRef<HTMLTextAreaElement>(null)
  const importSequence = useRef(0)
  const [pending, setPending] = useState<CaseFile | null>(null)
  const [error, setError] = useState('')
  const [resetting, setResetting] = useState(false)
  const { file, version } = c
  useLayoutEffect(() => {
    const field = titleInput.current
    if (!field) return
    const resize = () => { field.style.height = 'auto'; field.style.height = `${Math.max(46, field.scrollHeight)}px` }
    resize()
    let width = field.clientWidth
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => {
      if (field.clientWidth !== width) { width = field.clientWidth; resize() }
    })
    observer?.observe(field)
    return () => observer?.disconnect()
  }, [file.title])
  const statuses = file.items.map(item => snapshotStatus(item, data, version))
  const outdated = statuses.filter(s => s !== 'current').length
  const gids = new Set(file.items.flatMap(i => i.gids))
  const facts = file.items.reduce((sum, i) => sum + i.claims.length, 0)

  async function importFile(file?: File) {
    if (!file) return
    const sequence = ++importSequence.current
    setError(''); setPending(null)
    try {
      if (file.size > MAX_CASE_BYTES) throw new Error('Файл дела больше 4 МБ.')
      const parsed = parseCase(await file.text())
      if (sequence === importSequence.current) setPending(parsed)
    } catch (error) { if (sequence === importSequence.current) setError(error instanceof Error ? error.message : 'Не удалось прочитать файл.') }
  }
  function download(kind: 'json' | 'html') {
    setError('')
    try {
      downloadFile(`taymas-case-${file.id.replace(/[^a-zA-Z0-9-]/g, '').slice(0, 40)}.${kind}`,
        kind === 'json' ? serializeCase(file) : renderReport(file, data, version),
        kind === 'json' ? 'application/json;charset=utf-8' : 'text/html;charset=utf-8')
    } catch (error) { setError(error instanceof Error ? error.message : 'Не удалось подготовить экспорт.') }
  }

  return <section className="case-workspace" aria-label="Дело расследования">
    <header className="case-heading">
      <div><p className="eyebrow">INVESTIGATION FILE <span>/</span> ЛОКАЛЬНОЕ ДЕЛО</p>
        <label className="case-title-label" htmlFor="case-title">Название дела</label>
        <textarea ref={titleInput} className="case-title" id="case-title" rows={1} value={file.title} maxLength={200} placeholder="Название дела" onChange={e => c.update({ title: e.target.value })} />
        <p className="case-description">Соберите основания. Сохраните контекст. Передайте проверяемую историю.</p>
      </div>
      <div className="case-local-state"><Icon name="shield" size={17} /><span>{c.storageError ? 'Изменения в этой вкладке' : 'Сохраняется в этом браузере'}<small>{formatDate(file.updated_at)}</small></span></div>
    </header>

    <div className="case-toolbar">
      <div><button className="primary-button" onClick={() => download('json')}><Icon name="download" size={16} />Скачать JSON</button>
        <button className="secondary-button" onClick={() => download('html')}><Icon name="print" size={16} />Печатная справка</button>
        <button className="secondary-button" onClick={() => input.current?.click()}><Icon name="upload" size={16} />Импорт дела</button>
        <input ref={input} className="case-file-input" type="file" accept=".json,application/json" aria-label="JSON дела" onChange={e => { void importFile(e.target.files?.[0]); e.target.value = '' }} />
      </div>
      <button className="text-button" onClick={() => setResetting(true)}>Новое дело <Icon name="plus" size={14} /></button>
    </div>
    <p className="case-export-hint">JSON — для восстановления. HTML-справка — для чтения и сохранения в PDF через печать браузера.</p>

    {c.storageError && <p className="case-alert" role="alert">{c.storageError}</p>}
    {c.fingerprintError && <p className="case-alert" role="alert">{c.fingerprintError}</p>}
    {error && <p className="case-alert" role="alert">{error} Текущее дело не изменено.</p>}
    {pending && <section className="case-import-preview" aria-label="Проверка импорта">
      <Icon name="folder" size={28} /><div><p className="eyebrow">ФАЙЛ ПРОВЕРЕН · СХЕМА 1</p><h3>{pending.title || 'Без названия'}</h3>
        <p>{pending.items.length} материалов. {pending.items.filter(i => snapshotStatus(i, data, version) !== 'current').length} требуют сверки или относятся к другой выгрузке.</p>
        <p>Импорт заменит открытое дело. При необходимости сначала скачайте его JSON.</p></div>
      <div className="case-confirm-actions"><button className="primary-button" onClick={() => { if (c.replace(pending)) setPending(null) }}>Заменить дело</button><button className="text-button" onClick={() => setPending(null)}>Отмена</button></div>
    </section>}
    {resetting && <section className="case-import-preview" aria-label="Новое дело">
      <div><h3>Начать новое дело?</h3><p>Сохраните JSON текущего дела, если хотите вернуться к нему позже.</p></div>
      <div className="case-confirm-actions"><button className="primary-button" onClick={() => { if (c.reset()) { setResetting(false); setPending(null) } }}>Создать пустое дело</button><button className="text-button" onClick={() => setResetting(false)}>Отмена</button></div>
    </section>}

    <div className="case-metrics" aria-label="Состав дела">
      <div><span>Материалов</span><strong>{String(file.items.length).padStart(2, '0')}</strong></div>
      <div><span>Узлов в материалах</span><strong>{String(gids.size).padStart(2, '0')}</strong></div>
      <div><span>Фактов с источниками</span><strong>{String(facts).padStart(2, '0')}</strong></div>
      <div className={outdated ? 'case-metric-warning' : ''}><span>{outdated ? 'Требуют сверки' : 'Версия данных'}</span><strong>{outdated ? String(outdated).padStart(2, '0') : file.items.length ? <Icon name="check" size={27} /> : '—'}</strong><small>{outdated ? 'Снимки сохранены без изменений' : file.items.length ? 'Проверяем при каждом открытии' : 'Пока нет сохранённых снимков'}</small></div>
    </div>
    {outdated > 0 && <div className="case-version-warning" role="note"><Icon name="info" size={20} /><p>В деле есть материалы, которые не подтверждены текущей выгрузкой. Их исходные значения и источники сохранены; они не считаются текущими фактами.</p></div>}

    <div className="case-columns">
      <div className="case-materials">
        <div className="case-section-heading"><div><span className="section-index">01 /</span><h2>Материалы дела</h2></div><button className="text-button" onClick={onExplore}>Продолжить исследование <Icon name="arrow" size={14} /></button></div>
        {!file.items.length ? <div className="case-empty panel"><div className="case-folder-orbit"><Icon name="folder" size={40} /></div><p className="eyebrow">ОТ НАБЛЮДЕНИЯ К ОБОСНОВАНИЮ</p><h3>У расследования появилась память</h3><p>Добавьте клиента из карточки или сохраните ответ Copilot.<br />Факты, источники и границы данных останутся рядом.</p><button className="primary-button" onClick={onExplore}>Выбрать первый материал <Icon name="arrow" size={15} /></button></div>
          : file.items.map((item, index) => <Material key={item.id} item={item} index={index} status={statuses[index]} version={version} onRemove={() => c.remove(item.id)} onNavigate={onNavigate} />)}
      </div>
      <aside className="case-notebook" aria-label="Контекст аналитика">
        <div className="case-section-heading"><div><span className="section-index">02 /</span><h2>Контекст аналитика</h2></div></div>
        <section className="case-editor case-hypothesis"><p className="eyebrow">ГИПОТЕЗА</p><label htmlFor="case-hypothesis">Что вы проверяете?</label><p>Рабочая версия, которую ещё предстоит подтвердить или опровергнуть.</p><textarea id="case-hypothesis" rows={5} maxLength={MAX_NOTE_LENGTH} value={file.hypothesis} onChange={e => c.update({ hypothesis: e.target.value })} placeholder="Сформулируйте предположение и условия его проверки…" /></section>
        <section className="case-editor case-notes"><p className="eyebrow">ЗАМЕТКИ</p><label htmlFor="case-notes">Ваши наблюдения и вопросы</label><p>Личный комментарий. Не становится подтверждённым фактом.</p><textarea id="case-notes" rows={8} maxLength={MAX_NOTE_LENGTH} value={file.notes} onChange={e => c.update({ notes: e.target.value })} placeholder="Что важно не потерять? Какие вопросы остались?" /><small>{file.notes.length.toLocaleString('ru-RU')} / 20 000</small></section>
        <div className="case-scope"><Icon name="shield" size={19} /><div><strong>Границы вывода</strong><p>{SCOPE}</p></div></div>
      </aside>
    </div>
    <footer className="case-footer"><span>TAYMAS <b>/</b> CASE FILE</span><span className="mono">{file.id}</span><span>Локально · без аккаунта и облака</span></footer>
  </section>
}

function Material({ item, index, status, version, onRemove, onNavigate }: {
  item: CaseItem; index: number; status: SnapshotStatus; version: DatasetVersion | null
  onRemove: () => void; onNavigate: (gid: string) => void
}) {
  return <article className="case-material panel" aria-label={`Материал ${index + 1}: ${item.title}`}>
    <header><span className="case-material-symbol"><Icon name={item.kind === 'node' ? 'nodes' : 'spark'} size={20} /></span><div><p className="eyebrow">{String(index + 1).padStart(2, '0')} / {item.kind === 'node' ? 'УЗЕЛ ГРАФА' : 'ОТВЕТ COPILOT'} <span>· {formatDate(item.created_at)}</span></p><h3>{item.title}</h3></div><button className="icon-button" onClick={onRemove} aria-label={`Удалить материал ${index + 1}`}><Icon name="trash" size={16} /></button></header>
    <div className={`case-snapshot-status ${status}`}><Icon name={status === 'current' ? 'check' : 'info'} size={14} />{STATUS_LABELS[status]}</div>
    <div className="case-gids">{item.gids.map(gid => <button key={gid} className="case-gid" onClick={() => onNavigate(gid)} disabled={status !== 'current'} title={status === 'current' ? 'Открыть в текущем графе' : 'GID относится к сохранённому снимку'}>{gid}{status === 'current' && <Icon name="arrow" size={12} />}</button>)}</div>
    <details className="case-facts" open><summary>{status === 'current' ? 'Факты и источники' : 'Сохранённые значения и источники'}<span>{item.claims.length}</span></summary>
      <Facts claims={item.claims.slice(0, 6)} />
      {item.claims.length > 6 && <details className="case-extra-facts"><summary>Открыть остальные факты · {item.claims.length - 6}</summary><Facts claims={item.claims.slice(6)} /></details>}
    </details>
    <details className="case-source"><summary>Версия данных <span className="mono">{item.dataset.id.slice(7, 19)}…</span></summary><p className="mono">{item.dataset.id}</p><p>{item.dataset.node_count} узлов · {item.dataset.edge_count} связей</p>{version && status !== 'current' && <p>Текущая версия: <span className="mono">{version.id}</span></p>}</details>
    <section className="case-material-hypothesis"><p className="eyebrow">ГИПОТЕЗА / ОБЪЯСНЕНИЕ</p><p className="case-preserve-text">{item.hypothesis}</p><small>Свободный текст; сверка фактов не подтверждает смысл гипотезы.</small></section>
    <details className="case-limitations"><summary><Icon name="info" size={14} />Ограничения материала <span>{item.warnings.length + 1}</span></summary><p>{SCOPE}</p>{item.warnings.map((warning, i) => <p key={i}>{warning.gid && <span className="mono">{warning.gid} · </span>}{warning.message}</p>)}</details>
    {item.next_steps.length > 0 && <section className="case-next-steps"><p className="eyebrow">СЛЕДУЮЩИЕ ШАГИ</p><ol>{item.next_steps.map((step, i) => <li key={i}>{step}</li>)}</ol></section>}
  </article>
}

function Facts({ claims }: { claims: Claim[] }) {
  return <div className="case-fact-list">{claims.map((claim, i) => <div className="case-fact" key={i}>
    <div><span>{FACT_LABELS[claim.field] ?? claim.field}</span><strong>{factValue(claim.value, claim.field)}</strong></div>
    <small><span className="mono">{claim.kind === 'node' ? claim.gid : `${claim.src} → ${claim.dst}`}</span><span>{claim.source.file} · {claim.source.column}</span></small>
  </div>)}</div>
}
