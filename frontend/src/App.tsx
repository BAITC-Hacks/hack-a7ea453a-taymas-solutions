import { useCallback, useMemo, useState } from 'react'
import DatasetUpload from './DatasetUpload'
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
import ResiliencePanel from './resilience/ResiliencePanel'
import CaseWorkspace from './casebook/CaseWorkspace'
import { useCaseFile } from './casebook/useCaseFile'

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
  const casebook = useCaseFile(data)
  const [workspaceView, setWorkspaceView] = useState<'network' | 'case'>('network')
  const [error, setError] = useState<string>()
  const [loading, setLoading] = useState(true)
  const [filters, setFilters] = useState(initialFilters)
  const [selectedId, setSelectedId] = useState<string>()
  const [hoveredId, setHoveredId] = useState<string>()
  const [neighborsOnly, setNeighborsOnly] = useState(true)
  const [neighborhoodId, setNeighborhoodId] = useState<string>()
  const [focusMode, setFocusMode] = useState(false)
  const [inspectorTab, setInspectorTab] = useState<'profile' | 'copilot'>('copilot')
  const [copilotAnswer, setCopilotAnswer] = useState<CopilotAnswer | null>(null)
  const [focused, setFocused] = useState<{ gid: string; sequence: number }>()
  const receiveAnswer = useCallback((answer: CopilotAnswer | null) => {
    setCopilotAnswer(answer)
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
  const receiveDataset = useCallback((next: GraphData | null) => {
    setData(next); setLoading(false); setError(undefined); setFilters(initialFilters)
    setSelectedId(undefined); setHoveredId(undefined); setCopilotAnswer(null); setFocused(undefined)
    setNeighborhoodId(undefined); setNeighborsOnly(true); setFocusMode(false); setInspectorTab('copilot')
    setWorkspaceView('network')
  }, [])
  const loadLegacy = useCallback(() => fetchData(), [fetchData])
  const finishInitialCheck = useCallback(() => setLoading(false), [])

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
        : { nodes: [], edges: [], total: 0, focused: false, outsideFilterCount: 0, outsideLimitCount: 0 },
    [data, filters, topIds, graphFocusId, neighborsOnly],
  )
  const highlights = useMemo(() => answerHighlights(copilotAnswer), [copilotAnswer])
  const graph = useMemo(() => {
    if (!data) return { ...baseGraph, extraCount: 0 }
    const enriched = includeEvidence(baseGraph, data, highlights.gids)
    // Highlighting already-visible evidence must not reset the layout or camera.
    return enriched.extraCount ? { ...baseGraph, ...enriched } : { ...baseGraph, extraCount: 0 }
  }, [baseGraph, data, highlights])
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
  const reset = () => {
    setFilters(initialFilters)
    setSelectedId(undefined)
    setHoveredId(undefined)
    setNeighborsOnly(false)
    setFocused(undefined)
  }
  const navigateNode = useCallback((gid: string, search = '') => {
    if (!data || !findNodeByGid(data.nodes, gid)) return
    setWorkspaceView('network')
    setFilters((current) => ({ ...current, search }))
    setSelectedId(gid)
    setNeighborhoodId(gid)
    setFocused(previous => ({ gid, sequence: (previous?.sequence ?? 0) + 1 }))
    setNeighborsOnly(true)
    setHoveredId(undefined)
    setInspectorTab('profile')
    document.getElementById('network')?.scrollIntoView({
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches
        ? 'instant'
        : 'smooth',
      block: 'start',
    })
  }, [data])
  const focusNode = useCallback((gid: string) => navigateNode(gid), [navigateNode])
  const clearContext = () => {
    setFilters((current) => ({ ...current, search: '' }))
    setSelectedId(undefined)
    setNeighborhoodId(undefined)
    setNeighborsOnly(false)
    setHoveredId(undefined)
    setFocused(undefined)
  }
  const updateSearch = (value: string) => {
    const exact = data ? findNodeByGid(data.nodes, value) : undefined
    if (exact) navigateNode(exact.gid, value)
    else {
      clearContext()
      setFilters((current) => ({ ...current, search: value }))
      setInspectorTab('profile')
    }
  }
  const start = () => {
    if (data?.topNodes[0]) focusNode(data.topNodes[0].gid)
  }
  const openCopilot = () => {
    setWorkspaceView('network')
    setInspectorTab('copilot')
    document.getElementById('inspector')?.scrollIntoView({ block: 'nearest' })
  }
  const openCase = () => { setWorkspaceView('case'); setFocusMode(false); window.scrollTo({ top: 0, behavior: 'instant' }) }

  return (
    <div className={`app-shell ${focusMode ? 'focus-mode' : ''} ${workspaceView === 'case' ? 'case-mode' : ''}`}>
      <nav className="nav-rail" aria-label="Навигация по рабочему пространству">
        <a className="rail-logo" href="#network" title="Taymas" onClick={() => setWorkspaceView('network')}>
          T<span>↗</span>
        </a>
        <a className={`rail-link ${workspaceView === 'network' ? 'active' : ''}`} href="#network" aria-label="Карта связей" onClick={() => setWorkspaceView('network')}>
          <Icon name="network" size={21} />
        </a>
        <a className="rail-link" href="#priorities" aria-label="Очередь на проверку" onClick={() => { setFocusMode(false); setWorkspaceView('network') }}>
          <Icon name="list" size={21} />
        </a>
        <button
          className={`rail-link ${workspaceView === 'network' && inspectorTab === 'copilot' ? 'active' : ''}`}
          aria-label="Открыть AI Copilot"
          onClick={openCopilot}
        >
          <Icon name="spark" size={21} />
        </button>
        <a className="rail-link" href="#resilience" aria-label="Устойчивость сети" onClick={() => { setFocusMode(false); setWorkspaceView('network') }}>
          <Icon name="nodes" size={20} />
        </a>
        <button className={`rail-link rail-case ${workspaceView === 'case' ? 'active' : ''}`} aria-label="Открыть дело расследования" aria-pressed={workspaceView === 'case'} onClick={openCase}>
          <Icon name="folder" size={21} />{casebook.file.items.length > 0 && <span className="rail-case-count">{casebook.file.items.length}</span>}
        </button>
        <span className="rail-spacer" />
        <a className="rail-link" href="#methodology" aria-label="О данных и ограничениях" onClick={() => { setFocusMode(false); setWorkspaceView('network') }}>
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
          <DatasetUpload data={data} onDatasetReady={receiveDataset} onLegacy={loadLegacy} onInitialCheckDone={finishInitialCheck} />
          <div hidden={workspaceView !== 'network'}>
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
                <div className="workspace-actions">
                  <span className="local-note"><Icon name="shield" size={13} />Граф по локальной выгрузке</span>
                  <button className="workspace-expand" aria-pressed={focusMode} onClick={() => {
                    setFocusMode(!focusMode)
                    window.scrollTo({ top: 0, behavior: 'instant' })
                  }}><Icon name="expand" size={14} />{focusMode ? 'Вернуть обзор' : 'Режим фокуса'}</button>
                </div>
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
                  onClearEvidence={() => setCopilotAnswer(null)}
                  onClearContext={clearContext}
                  onCopilot={openCopilot}
                  onSearch={updateSearch}
                  onSelect={selectOnGraph}
                  onHover={setHoveredId}
                  onNeighbors={() => {
                    if (neighborsOnly || graph.focused) clearContext()
                    else if (selectedId) navigateNode(selectedId)
                  }}
                  onReset={reset}
                />
                <aside className="inspector panel" id="inspector">
                  <div className="inspector-tabs" role="tablist" aria-label="Панель исследования"
                    onKeyDown={event => {
                      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
                      event.preventDefault()
                      const next = event.key === 'Home' ? 'profile' : event.key === 'End' ? 'copilot' : inspectorTab === 'profile' ? 'copilot' : 'profile'
                      setInspectorTab(next)
                      document.getElementById(`${next}-tab`)?.focus()
                    }}>
                    <button
                      id="profile-tab" role="tab" aria-controls="profile-panel"
                      aria-selected={inspectorTab === 'profile'}
                      tabIndex={inspectorTab === 'profile' ? 0 : -1}
                      onClick={() => setInspectorTab('profile')}
                    >
                      <Icon name="list" size={15} />
                      Обзор клиента
                    </button>
                    <button
                      id="copilot-tab" role="tab" aria-controls="copilot-panel"
                      aria-selected={inspectorTab === 'copilot'}
                      tabIndex={inspectorTab === 'copilot' ? 0 : -1}
                      onClick={() => setInspectorTab('copilot')}
                    >
                      <Icon name="spark" size={15} />
                      AI Copilot
                      <span className="copilot-tab-badge" aria-hidden="true">{copilotAnswer ? '1' : '✦'}</span>
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
                        onSave={() => casebook.saveNode(selected.gid)}
                        canSave={Boolean(casebook.version)}
                      />
                    ) : (
                      <EmptyInspector search={filters.search} onStart={start} />
                    )}
                  </div>
                  <div className="inspector-scroll" id="copilot-panel" role="tabpanel" aria-labelledby="copilot-tab" hidden={inspectorTab !== 'copilot'}>
                    <CopilotSlot><CopilotPanel key={data.datasetId ?? data.source} data={data} selectedId={selectedId} onNavigate={focusNode} onAnswer={receiveAnswer} onSaveAnswer={casebook.saveAnswer} canSave={Boolean(casebook.version)} /></CopilotSlot>
                  </div>
                </aside>
              </section>
              <PriorityTable rows={data.topNodes} selectedId={selectedId} onSelect={focusNode} />
              <ResiliencePanel data={data} onSelect={focusNode} />
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
          </div>
          <div hidden={workspaceView !== 'case'}><CaseWorkspace controller={casebook} data={data} onNavigate={focusNode} onExplore={() => setWorkspaceView('network')} /></div>
        </main>
      </div>
      {casebook.notice && <div className="case-toast" role="status"><span>{casebook.notice}</span>
        {casebook.removed && <button className="text-button" onClick={casebook.undo}>Отменить удаление</button>}
        {workspaceView !== 'case' && <button className="text-button" onClick={openCase}>Открыть дело <Icon name="arrow" size={13} /></button>}
        <button className="icon-button" aria-label="Закрыть уведомление" onClick={casebook.clearNotice}><Icon name="close" size={14} /></button>
      </div>}
    </div>
  )
}
