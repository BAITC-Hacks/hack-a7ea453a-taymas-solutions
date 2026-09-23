import { useState } from 'react'
import GraphCanvas, { type GraphCommand } from '../GraphCanvas'
import type { EdgeRecord, NodeRecord } from '../types'
import { ROLE_LABELS, number, money } from '../presentation'
import { summarizeNodeFlows } from '../filters'
import { Icon } from './Icon'

type Props = {
  graph: { nodes: NodeRecord[]; edges: EdgeRecord[]; total: number; focused: boolean; contextId?: string; extraCount: number; outsideFilterCount: number; outsideLimitCount: number }
  selected?: NodeRecord
  hovered?: NodeRecord
  search: string
  neighborsOnly: boolean
  focus?: { gid: string; sequence: number }
  highlights: { gids: string[]; edges: string[] }
  hasAnswer: boolean
  onClearEvidence: () => void
  onClearContext: () => void
  onCopilot: () => void
  onSearch: (value: string) => void
  onSelect: (gid: string) => void
  onHover: (gid?: string) => void
  onNeighbors: () => void
  onReset: () => void
}
export function GraphPanel({
  graph,
  selected,
  hovered,
  search,
  neighborsOnly,
  focus,
  highlights,
  hasAnswer,
  onClearEvidence,
  onClearContext,
  onCopilot,
  onSearch,
  onSelect,
  onHover,
  onNeighbors,
  onReset,
}: Props) {
  const [colorMode, setColorMode] = useState<'role' | 'cluster'>('role')
  const [layout, setLayout] = useState<'flow' | 'network'>('flow')
  const canShowFlow = Boolean(graph.contextId && graph.nodes.length <= 65)
  const showFlow = canShowFlow && layout === 'flow'
  const flows = graph.contextId ? summarizeNodeFlows(graph.contextId, graph.edges) : undefined
  const counterpartCount = new Set(graph.edges.flatMap(edge =>
    edge.src === graph.contextId ? [edge.dst] : edge.dst === graph.contextId ? [edge.src] : []
  ).filter(gid => gid !== graph.contextId)).size
  const [command, setCommand] = useState<GraphCommand>({ action: 'fit', sequence: 0 })
  const run = (action: GraphCommand['action']) =>
    setCommand((c) => ({ action, sequence: c.sequence + 1 }))
  return (
    <section className="graph-panel panel" aria-labelledby="graph-heading">
      <div className="graph-toolbar">
        <div>
          <span className="live-square" />
          <h2 id="graph-heading">Карта денежных связей</h2>
          <span className="graph-count">{graph.nodes.length}</span>
        </div>
        <div className="segment-control" aria-label="Цвет узлов">
          <button aria-pressed={colorMode === 'role'} onClick={() => setColorMode('role')}>
            Роли
          </button>
          <button aria-pressed={colorMode === 'cluster'} onClick={() => setColorMode('cluster')}>
            Кластеры
          </button>
        </div>
      </div>
      {hasAnswer && (
        <div className="evidence-strip" role="status">
          <Icon name="spark" size={14} />
          <button onClick={onCopilot} className="evidence-label">
            {`Факты Copilot · узлы: ${highlights.gids.length}`}
            {graph.extraCount > 0 && <small>+{graph.extraCount} вне фильтров и лимита</small>}
          </button>
          <button className="icon-button" onClick={onClearEvidence} aria-label="Снять подсветку Copilot">
            <Icon name="close" size={14} />
          </button>
        </div>
      )}
      <div className="graph-searchbar">
        <label className="search-wrap">
          <Icon name="search" size={17} />
          <input
            aria-label="Поиск по GID"
            value={search}
            onChange={(e) => onSearch(e.target.value)}
            placeholder="Найти клиента по GID…"
            autoComplete="off"
            spellCheck={false}
          />
          {search && (
            <button
              className="icon-button"
              onClick={() => onSearch('')}
              aria-label="Очистить поиск"
            >
              <Icon name="close" size={14} />
            </button>
          )}
        </label>
        <button
          className={`neighbor-button ${neighborsOnly || graph.focused ? 'active' : ''}`}
          onClick={onNeighbors}
          disabled={!selected}
          aria-pressed={neighborsOnly || graph.focused}
        >
          <Icon name="nodes" size={16} />
          Соседи
        </button>
      </div>
      {graph.focused && <div className="graph-viewbar navigation-context" role="status">
        <span>Полное окружение клиента
          {graph.outsideFilterCount > 0 && ` · ${graph.outsideFilterCount} вне фильтров`}
          {graph.outsideLimitCount > 0 && ` · ${graph.outsideLimitCount} сверх лимита`}
        </span>
        <button className="text-button" onClick={onClearContext}>Снять временное окружение</button>
      </div>}
      {canShowFlow && <div className="graph-viewbar">
        <div className="segment-control" aria-label="Раскладка графа">
          <button aria-pressed={layout === 'flow'} onClick={() => setLayout('flow')}>Потоки</button>
          <button aria-pressed={layout === 'network'} onClick={() => setLayout('network')}>Сеть</button>
        </div>
        <span title={graph.contextId}>Клиент <span className="gid">…{graph.contextId?.slice(-6)}</span></span>
      </div>}
      <div className="graph-wrap">
        {showFlow && flows ? <div className="flow-guide" aria-label="Суммы связей на графе">
          <div><span>ВХОДЯЩИЕ</span><strong>{money(flows.incoming.sum_kzt)} <small>KZT</small></strong></div>
          <span className="flow-guide-arrow">→</span>
          <div><span>НА ГРАФЕ</span><strong>{counterpartCount} <small>контрагентов*</small></strong></div>
          <span className="flow-guide-arrow">→</span>
          <div><span>ИСХОДЯЩИЕ</span><strong>{money(flows.outgoing.sum_kzt)} <small>KZT</small></strong></div>
        </div> : <div className="graph-context">
          <span className="eyebrow">
            {graph.focused ? 'ОКРЕСТНОСТЬ КЛИЕНТА' : 'ТРАНЗАКЦИОННАЯ СЕТЬ'}
          </span>
          <span>Размер узла = приоритет</span>
        </div>}
        <GraphCanvas
          nodes={graph.nodes}
          edges={graph.edges}
          selectedId={selected?.gid}
          focusId={focus?.gid}
          focusSequence={focus?.sequence}
          highlightedGids={highlights.gids}
          highlightedEdges={highlights.edges}
          layoutFocusId={showFlow ? graph.contextId : undefined}
          colorMode={colorMode}
          command={command}
          onSelect={onSelect}
          onHover={onHover}
        />
        {!!hovered && (
          <div className="hover-card">
            <strong className="gid">{hovered.gid}</strong>
            <span>
              {ROLE_LABELS[hovered.role] ?? hovered.role} · приоритет{' '}
              {hovered.priority_score.toFixed(3)}
            </span>
          </div>
        )}
        {!graph.nodes.length && (
          <div className="empty-graph" role="status">
            <Icon name="search" size={30} />
            <h3>{search ? `GID «${search}» не найден` : 'Нет узлов по этим фильтрам'}</h3>
            <p>Проверьте значение или сбросьте ограничения.</p>
            <button className="secondary-button" onClick={onReset}>
              Сбросить фильтры
            </button>
          </div>
        )}
        {!showFlow && <div className="graph-watermark">
          T / M<span>FOLLOW THE FLOW</span>
        </div>}
        <div className="graph-controls">
          <button onClick={() => run('in')} aria-label="Увеличить масштаб">
            +
          </button>
          <button onClick={() => run('out')} aria-label="Уменьшить масштаб">
            −
          </button>
          <button onClick={() => run('fit')} aria-label="Показать весь граф">
            <Icon name="expand" size={17} />
          </button>
        </div>
        <div className="canvas-legend">
          <span>
            <i className="legend-arrow">→</i>Направление перевода
          </span>
          <span>
            <i className="seed-ring" />
            Seed
          </span>
          {colorMode === 'cluster' && <span>Цвет = кластер</span>}
          {showFlow && <span>* В пределах вида</span>}
        </div>
      </div>
      <div className="graph-footer">
        <span>
          <i className="status-dot live" />
          <strong>{number(graph.nodes.length)}</strong> узлов{graph.extraCount ? ` · +${graph.extraCount} из ответа` : ` из ${number(graph.total)}`} ·{' '}
          {number(graph.edges.length)} связей
        </span>
        <span>Прокрутка — масштаб · перетаскивание — обзор</span>
      </div>
    </section>
  )
}
