import { useState } from 'react'
import { ROLE_COLORS, type TopRecord } from '../types'
import { ROLE_LABELS } from '../presentation'
import { Icon } from './Icon'
import { Evidence } from './NodeInspector'

export function PriorityTable({
  rows,
  selectedId,
  onSelect,
}: {
  rows: TopRecord[]
  selectedId?: string
  onSelect: (gid: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const visibleRows = expanded ? rows : rows.slice(0, 5)
  return (
    <section className="priority-panel panel" id="priorities" aria-labelledby="priority-heading">
      <div className="priority-heading">
        <div>
          <p className="eyebrow">С ЧЕГО НАЧАТЬ</p>
          <h2 id="priority-heading">
            Очередь на проверку <span>{rows.length}</span>
          </h2>
        </div>
        <p>Структурные сигналы, которые требуют внимания</p>
        {rows.length > 5 && <button
          className="text-button"
          onClick={() => setExpanded(!expanded)}
          aria-expanded={expanded}
        >
          {expanded ? 'Свернуть' : `Все ${rows.length} клиентов`}
          <Icon name="arrow" size={16} />
        </button>}
      </div>
      <p className="priority-count" role="status">Показано {visibleRows.length} из {rows.length} клиентов</p>
      <div className="priority-table" role="table" aria-label="Клиенты по приоритету проверки">
        <div className="table-head" role="row">
          <span role="columnheader">№</span>
          <span role="columnheader">Клиент / GID</span>
          <span role="columnheader">Роль в сети</span>
          <span role="columnheader">Приоритет ↓</span>
          <span role="columnheader">Основание для проверки</span>
        </div>
        {visibleRows.map((item) => (
          <div
            role="row"
            key={item.gid}
            className={`table-row ${selectedId === item.gid ? 'selected' : ''}`}
            onClick={() => onSelect(item.gid)}
          >
            <span role="cell" className="rank">
              {String(item.rank).padStart(2, '0')}
            </span>
            <span role="cell">
              <button
                className="table-node gid"
                onClick={(event) => {
                  event.stopPropagation()
                  onSelect(item.gid)
                }}
                aria-label={`Открыть клиент ${item.gid}`}
              >
                <span>{item.gid}</span>
                <Icon name="arrow" size={14} />
              </button>
            </span>
            <span role="cell">
              <span
                className="role-badge"
                title={item.role}
                style={{ color: ROLE_COLORS[item.role] }}
              >
                <i />
                {ROLE_LABELS[item.role] ?? item.role}
              </span>
            </span>
            <span role="cell" className="table-score">
              <strong>{item.priority_score.toFixed(3)}</strong>
              <i>
                <b style={{ width: `${Math.max(0, Math.min(1, item.priority_score)) * 100}%` }} />
              </i>
            </span>
            <span role="cell" className="why">
              <Evidence text={item.priority_why || item.why} />
            </span>
          </div>
        ))}
      </div>
      {!rows.length && <p className="priority-count">Приоритетных клиентов в выгрузке нет.</p>}
      <div className="table-footer">
        <Icon name="info" size={14} />
        <span>
          Роль и приоритет — объяснимые гипотезы для проверки. Оценка не устанавливает виновность.
        </span>
        <span className="source-caption">top_nodes.csv</span>
      </div>
    </section>
  )
}
