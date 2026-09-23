import { useCallback, useEffect, useMemo, useState } from 'react'
import { DataLoadError, loadData } from './data'
import { buildGraphView, filterNodes, findNodeByGid } from './filters'
import type { FilterState, GraphData } from './types'
import { Header } from './components/Header'
import { FilterPanel } from './components/FilterPanel'
import { GraphPanel } from './components/GraphPanel'
import { EmptyInspector, NodeInspector } from './components/NodeInspector'
import { PriorityTable } from './components/PriorityTable'
import { CopilotSlot } from './components/CopilotSlot'
import { ErrorState, LoadingState } from './components/States'
import { Icon } from './components/Icon'
import CopilotPanel from './copilot/CopilotPanel'
import type { CopilotAnswer } from './copilot/api'
import { answerHighlights, includeEvidence } from './copilot/graph'

const initialFilters: FilterState = {
  search: '',
  role: 'all',
  cluster: 'all',
  depth: 'all',
  seed: 'all',
  topOnly: false,
  limit: 90,
}

export default function App() {
  const [data, setData] = useState<GraphData | null>(null)
  const [error, setError] = useState<string>()
  const [loading, setLoading] = useState(true)
  const [filters, setFilters] = useState(initialFilters)
  const [selectedId, setSelectedId] = useState<string>()
  const [hoveredId, setHoveredId] = useState<string>()
  const [neighborsOnly, setNeighborsOnly] = useState(true)
  const [neighborhoodId, setNeighborhoodId] = useState<string>()
  const [inspectorTab, setInspectorTab] = useState<'profile' | 'copilot'>('copilot')
  const [copilotAnswer, setCopilotAnswer] = useState<CopilotAnswer | null>(null)
  const [focused, setFocused] = useState<{ gid: string; sequence: number }>()
  const navigateFromCopilot = useCallback((gid: string) => {
    setSelectedId(gid)
    setFocused(previous => ({ gid, sequence: (previous?.sequence ?? 0) + 1 }))
    setInspectorTab('profile')
  }, [])
  const receiveAnswer = useCallback((answer: CopilotAnswer | null) => {
    setCopilotAnswer(answer)
    setFocused(undefined)
  }, [])

  const fetchData = useCallback(async (source = '/out') => {
    setLoading(true)
    setError(undefined)
    setFilters(initialFilters)
    setNeighborsOnly(true)
    setCopilotAnswer(null)
    setFocused(undefined)
    setHoveredId(undefined)
    try {
      const loaded = await loadData(source)
      setData(loaded)
      setSelectedId(loaded.topNodes[0]?.gid)
      setNeighborhoodId(loaded.topNodes[0]?.gid)
    } catch (caught) {
      setData(null)
      setError(caught instanceof DataLoadError ? caught.message : 'Не удалось загрузить выгрузки')
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => {
    void fetchData()
  }, [fetchData])

  const topIds = useMemo(() => new Set(data?.topNodes.map((n) => n.gid) ?? []), [data])
  const filteredNodes = useMemo(
    () => (data ? filterNodes(data.nodes, filters, topIds) : []),
    [data, filters, topIds],
  )
  const graphFocusId = neighborsOnly ? neighborhoodId : undefined
  const baseGraph = useMemo(
    () =>
      data
        ? buildGraphView(data.nodes, data.edges, filters, topIds, graphFocusId, neighborsOnly)
        : { nodes: [], edges: [], total: 0, focused: false },
    [data, filters, topIds, graphFocusId, neighborsOnly],
  )
  const highlights = useMemo(() => answerHighlights(copilotAnswer), [copilotAnswer])
  const graph = useMemo(() => data
    ? { ...baseGraph, ...includeEvidence(baseGraph, data, highlights.gids, focused?.gid) }
    : { ...baseGraph, extraCount: 0 }, [baseGraph, data, highlights, focused?.gid])
  const byId = useMemo(() => new Map(data?.nodes.map((n) => [n.gid, n]) ?? []), [data])
  const selected = selectedId ? byId.get(selectedId) : undefined
  const hovered = hoveredId ? byId.get(hoveredId) : undefined
  const selectOnGraph = useCallback((gid: string) => {
    setSelectedId(gid)
    setInspectorTab('profile')
  }, [])

  const setFilter = <K extends keyof FilterState>(key: K, value: FilterState[K]) => {
    setFilters((current) => ({
      ...current,
      [key]: value,
      search: key === 'limit' ? current.search : '',
    }))
    setNeighborsOnly(false)
    setHoveredId(undefined)
    setFocused(undefined)
    if (key !== 'limit') setSelectedId(undefined)
  }
  const updateSearch = (value: string) => {
    setFilters((current) => ({ ...current, search: value }))
    setNeighborsOnly(false)
    setHoveredId(undefined)
    setFocused(undefined)
    setSelectedId(data ? findNodeByGid(data.nodes, value)?.gid : undefined)
    setInspectorTab('profile')
  }
  const reset = () => {
    setFilters(initialFilters)
    setSelectedId(undefined)
    setHoveredId(undefined)
    setNeighborsOnly(false)
    setFocused(undefined)
  }
  const focusNode = useCallback((gid: string) => {
    setFilters((current) => ({ ...initialFilters, limit: current.limit }))
    setSelectedId(gid)
    setNeighborhoodId(gid)
    setFocused(undefined)
    setNeighborsOnly(true)
    setHoveredId(undefined)
    setInspectorTab('profile')
    document.getElementById('network')?.scrollIntoView({
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches
        ? 'instant'
        : 'smooth',
      block: 'start',
    })
  }, [])
  const start = () => {
    if (data?.topNodes[0]) focusNode(data.topNodes[0].gid)
  }
  const openCopilot = () => {
    setInspectorTab('copilot')
    document.getElementById('inspector')?.scrollIntoView({ block: 'nearest' })
  }

  return (
    <div className="app-shell">
      <nav className="nav-rail" aria-label="Навигация по рабочему пространству">
        <a className="rail-logo" href="#network" title="Taymas">
          T<span>↗</span>
        </a>
        <a className="rail-link active" href="#network" aria-label="Карта связей">
          <Icon name="network" size={21} />
        </a>
        <a className="rail-link" href="#priorities" aria-label="Очередь на проверку">
          <Icon name="list" size={21} />
        </a>
        <button
          className={`rail-link ${inspectorTab === 'copilot' ? 'active' : ''}`}
          aria-label="Открыть AI Copilot"
          onClick={openCopilot}
        >
          <Icon name="spark" size={21} />
        </button>
        <span className="rail-spacer" />
        <a className="rail-link" href="#methodology" aria-label="О данных и ограничениях">
          <Icon name="info" size={20} />
        </a>
        <span className="rail-caption">
          HACKALEM
          <br />
          2026
        </span>
      </nav>
      <div className="app-body">
        <Header data={data} onStart={start} onCopilot={openCopilot} />
        <main>
          {loading && <LoadingState />}
          {!loading && error && (
            <ErrorState
              error={error}
              onRetry={() => void fetchData()}
              onDemo={() => void fetchData('/fixtures')}
            />
          )}
          {!loading && data && (
            <>
              <div className="workspace-heading" id="network">
                <div className="workspace-label">
                  <span className="section-index">01 /</span>
                  <h2>Исследование сети</h2>
                </div>
                <span className="local-note">
                  <Icon name="shield" size={13} />
                  Граф по локальной выгрузке
                </span>
              </div>
              <section className="workspace">
                <FilterPanel
                  filters={filters}
                  clusters={data.clusters}
                  nodes={data.nodes}
                  filteredCount={filteredNodes.length}
                  onChange={setFilter}
                  onReset={reset}
                />
                <GraphPanel
                  graph={graph}
                  selected={selected}
                  hovered={hovered}
                  search={filters.search}
                  neighborsOnly={neighborsOnly}
                  focus={focused}
                  highlights={highlights}
                  hasAnswer={Boolean(copilotAnswer)}
                  onClearEvidence={() => { setCopilotAnswer(null); setFocused(undefined) }}
                  onCopilot={openCopilot}
                  onSearch={updateSearch}
                  onSelect={selectOnGraph}
                  onHover={setHoveredId}
                  onNeighbors={() => {
                    setNeighborsOnly(!(neighborsOnly || graph.focused))
                    setNeighborhoodId(selectedId)
                    setFilters((current) => ({ ...initialFilters, limit: current.limit }))
                  }}
                  onReset={reset}
                />
                <aside className="inspector panel" id="inspector">
                  <div className="inspector-tabs" role="tablist" aria-label="Панель исследования">
                    <button
                      id="profile-tab" role="tab" aria-controls="profile-panel"
                      aria-selected={inspectorTab === 'profile'}
                      onClick={() => setInspectorTab('profile')}
                    >
                      <Icon name="list" size={15} />
                      Обзор клиента
                    </button>
                    <button
                      id="copilot-tab" role="tab" aria-controls="copilot-panel"
                      aria-selected={inspectorTab === 'copilot'}
                      onClick={() => setInspectorTab('copilot')}
                    >
                      <Icon name="spark" size={15} />
                      AI Copilot
                      <span className="copilot-tab-badge">{copilotAnswer ? '1' : '✦'}</span>
                    </button>
                  </div>
                  <div
                    className="inspector-scroll"
                    id="profile-panel" role="tabpanel" aria-labelledby="profile-tab"
                    hidden={inspectorTab !== 'profile'}
                    key={selectedId ?? 'empty'}
                  >
                    {selected ? (
                      <NodeInspector
                        node={selected}
                        edges={data.edges}
                        rank={data.topNodes.find((n) => n.gid === selected.gid)?.rank}
                        onSelect={focusNode}
                        onClose={() => setSelectedId(undefined)}
                      />
                    ) : (
                      <EmptyInspector search={filters.search} onStart={start} />
                    )}
                  </div>
                  <div className="inspector-scroll" id="copilot-panel" role="tabpanel" aria-labelledby="copilot-tab" hidden={inspectorTab !== 'copilot'}>
                    <CopilotSlot><CopilotPanel key={data.source} data={data} selectedId={selectedId} onNavigate={navigateFromCopilot} onAnswer={receiveAnswer} /></CopilotSlot>
                  </div>
                </aside>
              </section>
              <PriorityTable rows={data.topNodes} selectedId={selectedId} onSelect={focusNode} />
              <footer className="page-footer" id="methodology">
                <div className="footer-brand">
                  TAYMAS <span>FINANCIAL INTELLIGENCE</span>
                </div>
                <details>
                  <summary>
                    О данных и ограничениях <Icon name="info" size={13} />
                  </summary>
                  <p>
                    Граф направленный: стрелка указывает получателя. Цвет обозначает роль или
                    кластер, размер — приоритет проверки. Обход ограничен четырьмя шагами от seed: у
                    4-го колена исходящие не наблюдаются, вход seed может быть неполным. Все выводы
                    — гипотезы для аналитика. Источник: {data.source}.
                  </p>
                </details>
                <span>HackAlem · 2026</span>
              </footer>
            </>
          )}
        </main>
      </div>
    </div>
  )
}
