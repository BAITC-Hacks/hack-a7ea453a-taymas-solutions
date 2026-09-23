import { useEffect, useMemo, useState } from 'react'
import type { GraphData } from '../types'
import { ROLE_LABELS } from '../presentation'
import ResilienceGraph, { type ViewEdge, type ViewNode } from './ResilienceGraph'
import {
  HEADLINE, METRIC_LABELS, RECOMPUTE, STRATEGY_LABELS, checkConsistency, conclusion, formatMetric, fragmentMismatch,
  fragments, loadResilience, type Deterministic, type LoadResult, type ResiliencePayload,
} from './resilience'
import './resilience.css'

export const DISPLAY_LIMIT = 150
const EXTRA = ['largest_weak_size', 'weak_components', 'new_isolates'] as const

interface Props { data: GraphData; onSelect: (gid: string) => void }

function Unavailable({ title, reason }: { title: string; reason: string }) {
  return <div className="resilience-empty" role="status">
    <p><strong>{title}</strong> ({reason}). Основной экран и обязательные выгрузки от этого расчёта не зависят.</p>
    <p>Чтобы посчитать устойчивость (около минуты на полном графе): <code>{RECOMPUTE}</code></p>
  </div>
}

export default function ResiliencePanel({ data, onSelect }: Props) {
  const [load, setLoad] = useState<LoadResult | null>(null)
  const [n, setN] = useState<number>()
  const [strategy, setStrategy] = useState<Deterministic>('priority')

  useEffect(() => {
    let alive = true
    setLoad(null)
    void loadResilience(data.source).then(result => { if (alive) setLoad(result) })
    return () => { alive = false }
  }, [data.source])

  const payload = load?.status === 'ok' ? load.payload : undefined
  const issues = useMemo(() => (payload ? checkConsistency(payload, data) : []), [payload, data])
  const steps = payload?.parameters.steps ?? [0]
  const positive = steps.filter(s => s > 0)
  const current = n !== undefined && steps.includes(n) ? n : steps.includes(20) ? 20 : positive.at(-1) ?? 0

  return <section className="resilience-panel panel" id="resilience" aria-labelledby="resilience-heading">
    <div className="resilience-heading">
      <div>
        <p className="eyebrow">БОНУС ТЗ · ИЗЪЯТИЕ TOP-N</p>
        <h2 id="resilience-heading">Устойчивость сети</h2>
      </div>
      <p>Что станет со структурой наблюдаемого графа, если убрать приоритетные узлы, — на фоне контрольных сценариев</p>
    </div>
    {!load && <p className="resilience-empty" role="status">Загрузка предвычисленного расчёта…</p>}
    {load?.status === 'missing' && <Unavailable title="Расчёт устойчивости не найден" reason={load.reason} />}
    {load?.status === 'invalid' && <Unavailable title="Файл расчёта не прочитан" reason={load.reason} />}
    {payload && issues.length > 0 && <div className="resilience-empty resilience-stale" role="alert">
      <p><strong>Расчёт относится к другой версии данных</strong> — числа не показаны, чтобы не выдать их за текущие.</p>
      <ul>{issues.map(issue => <li key={issue}>{issue}</li>)}</ul>
      <p>Пересчитайте после пайплайна: <code>{RECOMPUTE}</code></p>
    </div>}
    {payload && !issues.length &&
      <Experiment payload={payload} data={data} n={current} steps={steps} strategy={strategy}
        onN={setN} onStrategy={setStrategy} onSelect={onSelect} />}
  </section>
}

function Experiment({ payload, data, n, steps, strategy, onN, onStrategy, onSelect }: {
  payload: ResiliencePayload; data: GraphData; n: number; steps: number[]; strategy: Deterministic
  onN: (n: number) => void; onStrategy: (s: Deterministic) => void; onSelect: (gid: string) => void
}) {
  const key = String(n)
  const nodeIds = useMemo(() => data.nodes.map(node => node.gid), [data])
  const byId = useMemo(() => new Map(data.nodes.map(node => [node.gid, node])), [data])
  const originalIsolates = useMemo(() => fragments(nodeIds, data.edges, new Set()).isolates, [nodeIds, data.edges])
  const removedList = useMemo(() => payload.removals[strategy].slice(0, n), [payload, strategy, n])

  const { frag, ms } = useMemo(() => {
    const t0 = performance.now()
    const result = fragments(nodeIds, data.edges, new Set(removedList))
    return { frag: result, ms: performance.now() - t0 }
  }, [nodeIds, data.edges, removedList])
  const mismatch = fragmentMismatch(frag, payload.deterministic[strategy][key], originalIsolates)

  // Показ: удалённые (или при N=0 — будущие первые кандидаты) и их соседи. Метрики от лимита не зависят.
  const view = useMemo(() => {
    const previewK = n > 0 ? n : steps.find(s => s > 0) ?? 0
    const core = payload.removals[strategy].slice(0, previewK)
    const removed = new Set(removedList)
    const shown = new Set(core)
    const neighbors = new Set<string>()
    for (const e of data.edges) {
      if (shown.has(e.src)) neighbors.add(e.dst)
      if (shown.has(e.dst)) neighbors.add(e.src)
    }
    // В лимит сначала попадают соседи, отколовшиеся от основной сети (сам эффект удаления),
    // затем — часть основной сети. Порядок детерминирован: фрагмент, затем gid.
    const rank = (gid: string) => frag.componentOf.get(gid) ?? Number.MAX_SAFE_INTEGER
    const ordered = [...neighbors].filter(gid => !shown.has(gid))
      .sort((a, b) => Number(rank(a) === 0) - Number(rank(b) === 0) || rank(a) - rank(b) || a.localeCompare(b))
    for (const gid of ordered) { if (shown.size >= DISPLAY_LIMIT) break; shown.add(gid) }
    const nodes: ViewNode[] = [...shown].map(gid => ({
      gid, removed: removed.has(gid), core: core.includes(gid),
      fragment: removed.has(gid) ? null : frag.componentOf.get(gid) ?? null,
      label: `…${gid.slice(-6)}`,
    }))
    const edges: ViewEdge[] = data.edges.filter(e => shown.has(e.src) && shown.has(e.dst))
      .map(e => ({ src: e.src, dst: e.dst, cut: removed.has(e.src) || removed.has(e.dst) }))
    return { nodes, edges, neighborhood: core.length + neighbors.size }
  }, [payload, strategy, n, steps, data.edges, removedList, frag])

  const det = (s: Deterministic, metric: string) => formatMetric(metric, payload.deterministic[s][key]?.[metric] ?? null)
  const control = (s: 'random' | 'matched_random', metric: string) => {
    const stat = payload.controls[s][key]?.[metric]
    if (!stat) return 'не определено'
    return `${formatMetric(metric, stat.mean)} [${formatMetric(metric, stat.q05)}–${formatMetric(metric, stat.q95)}]`
  }
  const runs = payload.parameters.random_runs
  const graph = payload.sources.graph

  return <div className="resilience-body">
    <div className="resilience-controls">
      <div role="radiogroup" aria-label="Сколько узлов изъять" className="resilience-steps">
        <span>Изъять top-N</span>
        {steps.map(s => <button key={s} role="radio" aria-checked={s === n} className={s === n ? 'active' : ''}
          onClick={() => onN(s)}>{s}</button>)}
      </div>
      <div role="radiogroup" aria-label="Чей порядок показать на графе" className="resilience-steps">
        <span>На графе</span>
        {(['priority', 'degree'] as const).map(s => <button key={s} role="radio" aria-checked={s === strategy}
          className={s === strategy ? 'active' : ''} onClick={() => onStrategy(s)}>{STRATEGY_LABELS[s]}</button>)}
      </div>
    </div>

    <div className="resilience-table" role="table" aria-label={`Метрики при изъятии ${n} узлов`}>
      <div className="resilience-row head" role="row">
        <span role="columnheader">Метрика, N = {n}</span>
        <span role="columnheader">Исходно</span>
        <span role="columnheader">{STRATEGY_LABELS.priority}</span>
        <span role="columnheader">{STRATEGY_LABELS.degree}</span>
        <span role="columnheader">{STRATEGY_LABELS.random}, ср. [5–95%]</span>
        <span role="columnheader">{STRATEGY_LABELS.matched_random}, ср. [5–95%]</span>
      </div>
      {[...HEADLINE, ...EXTRA].map(metric => <div className="resilience-row" role="row" key={metric} data-metric={metric}>
        <span role="rowheader">{METRIC_LABELS[metric]}</span>
        <span role="cell">{formatMetric(metric, payload.baseline[metric] ?? null)}</span>
        <span role="cell" className="strong" data-testid={`priority-${metric}`}>{det('priority', metric)}</span>
        <span role="cell">{det('degree', metric)}</span>
        <span role="cell">{control('random', metric)}</span>
        <span role="cell">{control('matched_random', metric)}</span>
      </div>)}
    </div>
    <p className="resilience-note">Контроли: {runs} прогонов каждого; «тот же состав» — случайные узлы с тем же числом seed и
      тем же распределением по коленам, что у top-N по приоритету. [5–95%] — разброс сценариев, не доверительный интервал.</p>

    <div className="resilience-conclusion" aria-live="polite">
      <h3>Осторожный вывод</h3>
      {conclusion(payload, n).map(line => <p key={line}>{line}</p>)}
      <ul className="resilience-caveats">{payload.caveats.map(c => <li key={c}>{c}</li>)}</ul>
    </div>

    <div className="resilience-visual">
      <div className="resilience-graph">
        <p className="resilience-note" data-testid="display-limit">
          Показано {view.nodes.length} из {view.neighborhood} узлов окрестности (удалённые и их соседи, лимит {DISPLAY_LIMIT}).
          Метрики и фрагменты — по всему графу: {graph.n_nodes} узлов, {graph.n_edges} рёбер.
        </p>
        <ResilienceGraph nodes={view.nodes} edges={view.edges} onSelect={onSelect} />
        <p className="resilience-legend"><span className="removed">◆ удалён (центр)</span>
          <span className="cut">- - оборванная связь</span><span className="main">● крупнейший фрагмент (внешнее кольцо)</span>
          <span>другие цвета — отколовшиеся фрагменты, серый — мелкие фрагменты и изоляты</span></p>
        <p className={`resilience-note ${mismatch.length ? 'warn' : ''}`} data-testid="fragment-check">
          {mismatch.length
            ? `Фрагменты на графе расходятся с файлом: ${mismatch.join('; ')}`
            : `Фрагменты сверены с файлом: ${frag.components} компонент, крупнейшая — ${frag.largest} узлов, ` +
              `пересчёт для отображения ${ms.toFixed(0)} мс.`}
        </p>
      </div>
      <div className="resilience-removed">
        <h3>{n === 0 ? 'Ничего не удалено' : `Удалены: ${STRATEGY_LABELS[strategy].toLowerCase()}`}</h3>
        <ol>{removedList.map((gid, i) => {
          const node = byId.get(gid)
          return <li key={gid}>
            <span className="rank">{i + 1}</span>
            <button type="button" onClick={() => onSelect(gid)} title="Показать на основном графе">{gid}</button>
            <span>{node ? ROLE_LABELS[node.role] ?? node.role : '—'}</span>
            <span className="score">{node ? node.priority_score.toFixed(3) : ''}</span>
          </li>
        })}</ol>
      </div>
    </div>

    <details className="resilience-meta">
      <summary>Параметры и воспроизводимость</summary>
      <ul>
        <li>N: {steps.join(', ')}; прогонов каждого контроля: {runs}; seed: {payload.parameters.seed}</li>
        <li>Порядок: {payload.parameters.ranking}; контроль по составу: {payload.parameters.matched_on.join(', ')};
          пересчёт порядка после удаления: {payload.parameters.adaptive_ranking ? 'да' : 'нет'}</li>
        <li>NetworkX {payload.parameters.networkx_version}; рассчитано {payload.generated_at}</li>
        <li>Расчёт эксперимента {payload.timing_sec.experiment.toFixed(1)} с — один раз заранее; смена N читает готовые числа</li>
        {Object.entries(payload.sources).filter(([, v]) => 'sha256' in v).map(([file, v]) =>
          <li key={file}><code>{file}</code> sha256 {String((v as { sha256: string }).sha256).slice(0, 16)}…</li>)}
      </ul>
    </details>
  </div>
}
