import { useCallback, useEffect, useMemo, useState } from 'react'
import GraphCanvas from './GraphCanvas'
import { DataLoadError, loadData } from './data'
import { capGraph, filterNodes, findNodeByGid, isBoundaryNode, nodeNeighbors, summarizeNodeFlows } from './filters'
import { ROLE_COLORS, ROLES, type FilterState, type GraphData, type NodeRecord } from './types'

const initialFilters: FilterState = { search: '', role: 'all', cluster: 'all', depth: 'all', seed: 'all', topOnly: false, limit: 160 }
const fmt = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 })
const money = (value: number) => value >= 1_000_000 ? `${(value / 1_000_000).toFixed(1)} млн` : `${fmt.format(value / 1000)} тыс.`

function Stat({ label, value, accent = '' }: { label: string, value: string | number, accent?: string }) { return <div className="stat"><span>{label}</span><strong className={accent}>{value}</strong></div> }

export default function App() {
  const [data, setData] = useState<GraphData | null>(null)
  const [error, setError] = useState<string>()
  const [loading, setLoading] = useState(true)
  const [filters, setFilters] = useState(initialFilters)
  const [selectedId, setSelectedId] = useState<string>()
  const [hoveredId, setHoveredId] = useState<string>()

  const fetchData = useCallback(async (source = '/out') => {
    setLoading(true); setError(undefined)
    try { setData(await loadData(source)) }
    catch (caught) { setData(null); setError(caught instanceof DataLoadError ? caught.message : 'Не удалось загрузить выгрузки') }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { void fetchData() }, [fetchData])

  const topIds = useMemo(() => new Set(data?.topNodes.map((node) => node.gid) ?? []), [data])
  const filteredNodes = useMemo(() => data ? filterNodes(data.nodes, filters, topIds) : [], [data, filters, topIds])
  const graph = useMemo(() => data ? capGraph(filteredNodes, data.edges, filters.limit) : { nodes: [], edges: [] }, [data, filteredNodes, filters.limit])
  const byId = useMemo(() => new Map(data?.nodes.map((node) => [node.gid, node]) ?? []), [data])
  const selected = selectedId ? byId.get(selectedId) : undefined
  const selectedNeighbors = selected && data ? nodeNeighbors(selected.gid, data.edges) : { incoming: [], outgoing: [] }
  const selectedFlowSummary = selected && data ? summarizeNodeFlows(selected.gid, data.edges) : undefined
  const hovered = hoveredId ? byId.get(hoveredId) : undefined
  const searchQuery = filters.search.trim()
  const searchHasNoMatches = Boolean(searchQuery && filteredNodes.length === 0 && !selected)

  const setFilter = <K extends keyof FilterState>(key: K, value: FilterState[K]) => setFilters((current) => ({ ...current, [key]: value }))
  const updateSearch = (value: string) => {
    setFilter('search', value)
    const match = data ? findNodeByGid(data.nodes, value) : undefined
    setSelectedId(match?.gid)
  }
  const clearFilters = () => { setFilters(initialFilters); setSelectedId(undefined) }
  const stats = data ? { nodes: data.nodes.length, edges: data.edges.length, clusters: data.clusters.length, seeds: data.nodes.filter((node) => node.is_seed).length } : { nodes: 0, edges: 0, clusters: 0, seeds: 0 }

  return <div className="app-shell">
    <header className="topbar">
      <div className="brand"><div className="brand-mark">↗</div><div><p className="eyebrow">AML INVESTIGATION DESK · PAN-38</p><h1>Money Graph</h1></div></div>
      <div className="header-status"><span className={`status-dot ${data ? 'live' : ''}`} /> {data ? 'локальная выгрузка' : 'ожидание данных'} <span className="source-path">{data?.source ?? '/out'}</span></div>
    </header>
    <main className="content">
      {loading && <div className="state-card"><div className="spinner" /><h2>Загружаем граф</h2><p>Читаем четыре CSV из локальной папки out/</p></div>}
      {!loading && error && <div className="state-card error-state"><div className="state-icon">!</div><h2>{error.includes('отсутствуют обязательные колонки') ? 'Ошибка схемы выгрузки' : error.includes('пустой файл') ? 'Пустая выгрузка' : 'Выгрузки не найдены'}</h2><p>{error}. Запустите Python-пайплайн, затем обновите страницу.</p><code>python -m money_graph --data data --out out</code><button className="secondary-button" onClick={() => void fetchData('/fixtures')}>Открыть демо-набор</button></div>}
      {!loading && data && <>
        <section className="stat-row"><Stat label="УЗЛОВ В СЕТИ" value={fmt.format(stats.nodes)} accent="mint" /><Stat label="СВЯЗЕЙ" value={fmt.format(stats.edges)} /><Stat label="КЛАСТЕРОВ" value={fmt.format(stats.clusters)} /><Stat label="SEED-КЛИЕНТОВ" value={fmt.format(stats.seeds)} accent="gold" /><div className="stat-note"><span className="pulse" /> {graph.nodes.length} на экране из {filteredNodes.length} после фильтров</div></section>
        <section className="workspace">
          <aside className="sidebar panel">
            <div className="panel-heading"><div><p className="eyebrow">СЕГМЕНТ</p><h2>Фильтры</h2></div><button className="reset-button" onClick={clearFilters}>Сбросить</button></div>
            <label className="field-label" htmlFor="search">Поиск по GID</label><div className="search-wrap"><span>⌕</span><input id="search" value={filters.search} onChange={(event) => updateSearch(event.target.value)} placeholder="например, 100000..." /></div>
            <label className="field-label" htmlFor="role">Роль</label><select id="role" value={filters.role} onChange={(event) => setFilter('role', event.target.value)}><option value="all">Все роли</option>{ROLES.map((role) => <option key={role} value={role}>{role}</option>)}</select>
            <label className="field-label" htmlFor="cluster">Кластер</label><select id="cluster" value={filters.cluster} onChange={(event) => setFilter('cluster', event.target.value)}><option value="all">Все кластеры</option>{data.clusters.map((cluster) => <option key={cluster.cluster_id} value={cluster.cluster_id}>Кластер {cluster.cluster_id} · {cluster.n_nodes} узлов</option>)}</select>
            <label className="field-label" htmlFor="depth">Глубина обхода</label><select id="depth" value={filters.depth} onChange={(event) => setFilter('depth', event.target.value)}><option value="all">Все уровни</option>{[0, 1, 2, 3, 4].map((depth) => <option key={depth} value={depth}>Колено {depth}</option>)}</select>
            <label className="field-label" htmlFor="seed">Статус клиента</label><select id="seed" value={filters.seed} onChange={(event) => setFilter('seed', event.target.value as FilterState['seed'])}><option value="all">Все клиенты</option><option value="seed">Только seed</option><option value="non-seed">Без seed</option></select>
            <div className="toggle-row"><div><strong>Верхние приоритеты</strong><small>Только top_nodes.csv</small></div><button className={`toggle ${filters.topOnly ? 'on' : ''}`} aria-label="Включить верхние приоритеты" aria-pressed={filters.topOnly} onClick={() => setFilter('topOnly', !filters.topOnly)}><span /></button></div>
            <label className="field-label" htmlFor="limit">Лимит узлов на графе <b>{filters.limit}</b></label><input className="range" id="limit" type="range" min="20" max="300" step="10" value={filters.limit} onChange={(event) => setFilter('limit', Number(event.target.value))} />
            <div className="legend"><p className="field-label">Роли</p>{ROLES.map((role) => <div className="legend-item" key={role}><i style={{ background: ROLE_COLORS[role] }} /> <span>{role}</span></div>)}<div className="legend-item"><i className="seed-ring" /> <span>seed-клиент</span></div></div>
          </aside>
          <section className="graph-panel panel"><div className="graph-toolbar"><div><p className="eyebrow">ТРАНЗАКЦИОННАЯ СЕТЬ</p><h2>Направление потоков</h2></div><div className="graph-hint"><span className="arrow-key">→</span> стрелка показывает получателя</div></div><div className="graph-wrap"><GraphCanvas nodes={graph.nodes} edges={graph.edges} selectedId={selectedId} onSelect={setSelectedId} onHover={setHoveredId} />{hovered && <div className="hover-card"><strong>{hovered.gid}</strong><span>{hovered.role} · priority {hovered.priority_score.toFixed(2)}</span></div>}{!graph.nodes.length && <div className="empty-graph">{searchHasNoMatches ? `GID «${searchQuery}» не найден в текущей выгрузке` : 'По текущим фильтрам узлы не найдены'}</div>}</div><div className="graph-footer"><span>Кликните узел для подробностей</span><span>Колесо мыши — масштаб · drag — перемещение</span></div></section>
          <aside className="inspector panel">{selected ? <NodeInspector node={selected} incoming={selectedNeighbors.incoming} outgoing={selectedNeighbors.outgoing} flowSummary={selectedFlowSummary!} onSelectNeighbor={setSelectedId} onClose={() => setSelectedId(undefined)} /> : <div className="inspector-empty"><div className="crosshair">⊹</div><h2>{searchHasNoMatches ? 'GID не найден' : 'Выберите узел'}</h2><p>{searchHasNoMatches ? `В выгрузке нет узла с GID «${searchQuery}». Проверьте значение или сбросьте поиск.` : 'Нажмите на точку графа, чтобы увидеть роль, evidence и денежные потоки.'}</p><div className="tip"><span>TIP</span> Используйте поиск по GID, если нужен конкретный клиент.</div></div>}</aside>
        </section>
        <section className="priority-panel panel"><div className="panel-heading"><div><p className="eyebrow">ПЕРВЫЕ СИГНАЛЫ</p><h2>Верхние приоритеты</h2></div><span className="muted">{data.topNodes.length} узлов из top_nodes.csv</span></div><div className="priority-table"><div className="table-head"><span>RANK</span><span>GID</span><span>ROLE</span><span>SCORE</span><span>ПОЧЕМУ</span></div>{data.topNodes.slice(0, 8).map((item) => <button className="table-row" key={item.gid} onClick={() => setSelectedId(item.gid)}><span className="rank">{String(item.rank).padStart(2, '0')}</span><span className="gid">{item.gid}</span><span className="role-pill" style={{ color: ROLE_COLORS[item.role] }}>{item.role}</span><span className="score">{item.priority_score.toFixed(3)}</span><span className="why">{item.priority_why || item.why}</span></button>)}</div></section>
      </>}
    </main>
  </div>
}

function NodeInspector({ node, incoming, outgoing, flowSummary, onSelectNeighbor, onClose }: { node: NodeRecord, incoming: { src: string, sum_kzt: number, n_tx: number }[], outgoing: { dst: string, sum_kzt: number, n_tx: number }[], flowSummary: ReturnType<typeof summarizeNodeFlows>, onSelectNeighbor: (gid: string) => void, onClose: () => void }) {
  return <div className="node-inspector" role="region" aria-label={`Карточка узла ${node.gid}`}><div className="inspector-header"><div><p className="eyebrow">NODE PROFILE</p><h2>{node.gid}</h2></div><button className="close-button" onClick={onClose} aria-label="Закрыть карточку">×</button></div><div className="role-banner" style={{ borderColor: ROLE_COLORS[node.role] ?? ROLE_COLORS.peripheral }}><span className="role-dot" style={{ background: ROLE_COLORS[node.role] }} /><strong>{node.role}</strong><span>{node.is_seed ? 'seed' : `depth ${node.depth}`}</span></div><div className="score-grid"><div><small>ROLE SCORE</small><strong>{node.role_score.toFixed(2)}</strong></div><div><small>PRIORITY</small><strong className="gold-text">{node.priority_score.toFixed(3)}</strong></div><div><small>CLUSTER</small><strong>{node.cluster_id}</strong></div><div><small>DEPTH</small><strong>{node.depth}</strong></div><div><small>SEED</small><strong>{node.is_seed ? 'да' : 'нет'}</strong></div></div><div className="evidence"><p className="eyebrow">ОБОСНОВАНИЕ</p><p>{node.evidence}</p>{isBoundaryNode(node) && <div className="boundary-warning">⚠ 4-е колено: исходящие переводы не наблюдаются после границы обхода. Это граница выборки, а не доказательство конечного получателя.</div>}</div><div className="why-block"><p className="eyebrow">ПОЧЕМУ В ПРИОРИТЕТЕ</p><p>{node.priority_why}</p></div><div className="flow-summary" aria-label="Агрегаты денежных потоков"><div><small>ПОЛУЧЕНО</small><strong>{money(flowSummary.incoming.sum_kzt)} KZT</strong><span>{fmt.format(flowSummary.incoming.n_tx)} tx · {flowSummary.incoming.counterpart_count} отправителей</span></div><div><small>ОТПРАВЛЕНО</small><strong>{money(flowSummary.outgoing.sum_kzt)} KZT</strong><span>{fmt.format(flowSummary.outgoing.n_tx)} tx · {flowSummary.outgoing.counterpart_count} получателей</span></div></div><details className="accessible-summary"><summary>Текстовая сводка для клавиатуры</summary><p>{node.gid}: роль {node.role}, приоритет {node.priority_score.toFixed(3)}. Получено {flowSummary.incoming.sum_kzt} KZT за {flowSummary.incoming.n_tx} транзакций; отправлено {flowSummary.outgoing.sum_kzt} KZT за {flowSummary.outgoing.n_tx} транзакций.</p></details><FlowList title="ПОЛУЧАЕТ ОТ" rows={incoming.map((edge) => ({ gid: edge.src, sum: edge.sum_kzt, n: edge.n_tx }))} empty="Входящие связи не найдены" onSelect={onSelectNeighbor} /><FlowList title="ОТПРАВЛЯЕТ" rows={outgoing.map((edge) => ({ gid: edge.dst, sum: edge.sum_kzt, n: edge.n_tx }))} empty="Исходящие связи не найдены" onSelect={onSelectNeighbor} /></div>
}

function FlowList({ title, rows, empty, onSelect }: { title: string, rows: { gid: string, sum: number, n: number }[], empty: string, onSelect: (gid: string) => void }) { const shown = rows.slice(0, 5); return <div className="flow-list"><p className="eyebrow">{title} · {rows.length ? `${shown.length} из ${rows.length}` : '0'}</p>{shown.length ? shown.map((row) => <button className="flow-row" key={row.gid} onClick={() => onSelect(row.gid)} aria-label={`Открыть узел ${row.gid}`}><span className="flow-gid">{row.gid}</span><span>{money(row.sum)} KZT <small>· {row.n} tx</small></span></button>) : <p className="muted small">{empty}</p>}</div> }
