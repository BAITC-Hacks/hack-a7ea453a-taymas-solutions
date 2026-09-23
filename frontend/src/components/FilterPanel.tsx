import type { ClusterRecord, FilterState, NodeRecord } from '../types'
import { ROLE_COLORS, ROLES } from '../types'
import { ROLE_LABELS, number } from '../presentation'
import { Icon } from './Icon'

type Props = {
  filters: FilterState
  clusters: ClusterRecord[]
  nodes: NodeRecord[]
  filteredCount: number
  onChange: <K extends keyof FilterState>(key: K, value: FilterState[K]) => void
  onReset: () => void
}
export function FilterPanel({ filters, clusters, nodes, filteredCount, onChange, onReset }: Props) {
  return (
    <aside className="filter-panel panel" aria-label="Фильтры сети">
      <div className="panel-title">
        <h2>
          <Icon name="tune" />
          Фильтры
        </h2>
        <button
          className="icon-button"
          title="Сбросить фильтры"
          aria-label="Сбросить фильтры"
          onClick={onReset}
        >
          <Icon name="reset" size={16} />
        </button>
      </div>
      <div className="filter-fields">
        <label className="field-label" htmlFor="role">
          Роль клиента
        </label>
        <select id="role" value={filters.role} onChange={(e) => onChange('role', e.target.value)}>
          <option value="all">Все роли</option>
          {ROLES.map((role) => (
            <option key={role} value={role}>
              {ROLE_LABELS[role]}
            </option>
          ))}
        </select>
        <label className="field-label" htmlFor="cluster">
          Кластер
        </label>
        <select
          id="cluster"
          value={filters.cluster}
          onChange={(e) => onChange('cluster', e.target.value)}
        >
          <option value="all">Все кластеры</option>
          {clusters.map((c) => (
            <option key={c.cluster_id} value={c.cluster_id}>
              #{c.cluster_id} · {c.n_nodes} узлов
            </option>
          ))}
        </select>
        <div className="filter-pair">
          <div>
            <label className="field-label" htmlFor="depth">
              Глубина
            </label>
            <select
              id="depth"
              value={filters.depth}
              onChange={(e) => onChange('depth', e.target.value)}
            >
              <option value="all">Все уровни</option>
              {[0, 1, 2, 3, 4].map((d) => (
                <option key={d} value={d}>
                  {d}-е колено
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="field-label" htmlFor="seed">
              Статус
            </label>
            <select
              id="seed"
              value={filters.seed}
              onChange={(e) => onChange('seed', e.target.value as FilterState['seed'])}
            >
              <option value="all">Все</option>
              <option value="seed">Seed</option>
              <option value="non-seed">Не seed</option>
            </select>
          </div>
        </div>
        <button
          className={`toggle-row ${filters.topOnly ? 'active' : ''}`}
          aria-pressed={filters.topOnly}
          onClick={() => onChange('topOnly', !filters.topOnly)}
        >
          <span>
            <strong>Верхние приоритеты</strong>
            <small>30 первых на проверку</small>
          </span>
          <span className="toggle">
            <i />
          </span>
        </button>
        <label className="field-label range-label" htmlFor="limit">
          Узлов на графе <b>{filters.limit}</b>
        </label>
        <input
          className="range"
          id="limit"
          type="range"
          min="30"
          max="500"
          step="10"
          value={filters.limit}
          onChange={(e) => onChange('limit', Number(e.target.value))}
        />
        <div className="range-ends">
          <span>30</span>
          <span>500</span>
        </div>
      </div>
      <div className="role-legend">
        <p className="eyebrow">
          РОЛИ В СЕТИ <span title="Число клиентов во всей выгрузке">{number(nodes.length)}</span>
        </p>
        {ROLES.map((role) => (
          <button
            key={role}
            className={`legend-item ${filters.role === role ? 'active' : ''}`}
            aria-pressed={filters.role === role}
            onClick={() => onChange('role', filters.role === role ? 'all' : role)}
            title={role}
          >
            <i style={{ background: ROLE_COLORS[role] }} />
            <span>{ROLE_LABELS[role]}</span>
            <small>{number(nodes.filter((n) => n.role === role).length)}</small>
          </button>
        ))}
        <div className="seed-legend">
          <i />
          Seed-клиент <span>кольцо</span>
        </div>
      </div>
      <div className="filter-result">
        <i className="status-dot live" />
        <span>
          По фильтрам: <strong>{number(filteredCount)}</strong>
        </span>
      </div>
    </aside>
  )
}
