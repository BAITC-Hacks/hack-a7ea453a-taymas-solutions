import { useState } from 'react'
import type { EdgeRecord, NodeRecord } from '../types'
import { ROLE_COLORS } from '../types'
import { isBoundaryNode, nodeNeighbors, summarizeNodeFlows } from '../filters'
import { evidenceParts, money, number, ROLE_LABELS } from '../presentation'
import { Icon } from './Icon'

export function Evidence({ text }: { text: string }) {
  return (
    <>
      {evidenceParts(text).map((part, i) =>
        part.numeric ? <strong key={i}>{part.text}</strong> : <span key={i}>{part.text}</span>,
      )}
    </>
  )
}
export function NodeInspector({
  node,
  edges,
  rank,
  onSelect,
  onClose,
  onSave,
  canSave,
}: {
  node: NodeRecord
  edges: EdgeRecord[]
  rank?: number
  onSelect: (gid: string) => void
  onClose: () => void
  onSave?: () => void
  canSave?: boolean
}) {
  const { incoming, outgoing } = nodeNeighbors(node.gid, edges)
  const flows = summarizeNodeFlows(node.gid, edges)
  return (
    <div className="node-inspector" role="region" aria-label={`Карточка узла ${node.gid}`}>
      <div className="inspector-header">
        <div>
          <p className="eyebrow">
            ДОСЬЕ КЛИЕНТА {rank && <span className="rank-tag">#{rank} в очереди</span>}
          </p>
          <h2 className="gid">{node.gid}</h2>
        </div>
        <button className="icon-button" onClick={onClose} aria-label="Закрыть карточку">
          <Icon name="close" size={16} />
        </button>
      </div>
      {onSave && <button className="case-save-action" onClick={onSave} disabled={!canSave}><Icon name="folder" size={15} />Добавить узел в дело</button>}
      <div
        className="node-tags"
        aria-label={`Роль: ${node.role}; seed: ${node.is_seed ? 'да' : 'нет'}`}
      >
        <span className="role-badge" style={{ color: ROLE_COLORS[node.role] }}>
          <i />
          {ROLE_LABELS[node.role] ?? node.role}
        </span>
        {node.is_seed ? (
          <span className="seed-badge">SEED</span>
        ) : (
          <span className="subtle-tag">Не seed</span>
        )}
        <span className="subtle-tag">{node.depth}-е колено</span>
        <span className="subtle-tag">Кластер {node.cluster_id}</span>
      </div>
      {isBoundaryNode(node) && (
        <div className="boundary-warning" role="note">
          <Icon name="info" size={19} />
          <div>
            <strong>Граница наблюдения · 4-е колено</strong>
            <p>
              Исходящие переводы не наблюдаются из-за границы обхода. Это не значит, что деньги
              осели. Для продолжения нужна выписка.
            </p>
          </div>
        </div>
      )}
      <section className="explanation">
        <div className="section-label">
          <span className="step-number">01</span>
          <h3>Почему эта роль</h3>
          <span className="role-score" title="Уверенность в роли · role_score">
            {node.role_score.toFixed(2)}
            <small> role score</small>
          </span>
        </div>
        <p className="evidence-text">
          <Evidence text={node.evidence} />
        </p>
        <span className="source-caption">Источник: nodes_roles.csv · evidence</span>
      </section>
      <section className="explanation priority-explanation">
        <div className="section-label">
          <span className="step-number">02</span>
          <h3>Почему в приоритете</h3>
        </div>
        <div className="priority-meter">
          <strong>{node.priority_score.toFixed(3)}</strong>
          <div>
            <span>Приоритет проверки</span>
            <div className="meter-track">
              <i style={{ width: `${Math.max(0, Math.min(1, node.priority_score)) * 100}%` }} />
            </div>
          </div>
          <small>/ 1.000</small>
        </div>
        <p className="score-note">Место в очереди на проверку, не вероятность вины.</p>
        <ul className="reasons">
          {node.priority_why
            .split(';')
            .filter((p) => p.trim())
            .map((reason, i) => (
              <li key={i}>
                <Evidence text={reason.trim()} />
              </li>
            ))}
        </ul>
      </section>
      <section className="flow-section">
        <div className="section-label">
          <span className="step-number">03</span>
          <h3>Денежные потоки</h3>
        </div>
        <div className="flow-summary" aria-label="Агрегаты денежных потоков">
          {(['incoming', 'outgoing'] as const).map((direction) => (
            <div key={direction}>
              <span>
                <Icon name={direction === 'incoming' ? 'down' : 'up'} size={14} />
                {direction === 'incoming' ? 'Получено' : 'Отправлено'}
              </span>
              <strong>
                {money(flows[direction].sum_kzt)} <small>KZT</small>
              </strong>
              <p>
                {number(flows[direction].n_tx)} переводов · {flows[direction].counterpart_count}{' '}
                {direction === 'incoming' ? 'отправителей' : 'получателей'}
              </p>
            </div>
          ))}
        </div>
        <FlowList
          key={`${node.gid}-in`}
          title="Получает от"
          rows={incoming.map((e) => ({ gid: e.src, sum: e.sum_kzt, n: e.n_tx }))}
          onSelect={onSelect}
        />
        <FlowList
          key={`${node.gid}-out`}
          title="Отправляет"
          rows={outgoing.map((e) => ({ gid: e.dst, sum: e.sum_kzt, n: e.n_tx }))}
          onSelect={onSelect}
          boundary={isBoundaryNode(node)}
        />
      </section>
    </div>
  )
}
function FlowList({
  title,
  rows,
  onSelect,
  boundary,
}: {
  title: string
  rows: { gid: string; sum: number; n: number }[]
  onSelect: (gid: string) => void
  boundary?: boolean
}) {
  const [expanded, setExpanded] = useState(false)
  return (
    <div className="flow-list">
      <div className="flow-list-heading">
        <h4>{title}</h4>
        <span>{rows.length} связей</span>
      </div>
      {rows.length ? (
        (expanded ? rows : rows.slice(0, 4)).map((row) => (
          <button
            className="flow-row"
            key={row.gid}
            onClick={() => onSelect(row.gid)}
            aria-label={`Открыть узел ${row.gid}`}
          >
            <span className="gid">
              {row.gid}
              <small>{row.n} переводов</small>
            </span>
            <span>
              {money(row.sum)} <small>KZT</small>
              <Icon name="chevron" size={12} />
            </span>
          </button>
        ))
      ) : (
        <p className="flow-empty">
          {boundary ? 'Исходящие за пределами наблюдения' : 'Связей в выгрузке нет'}
        </p>
      )}
      {rows.length > 4 && (
        <button className="text-button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
          {expanded ? 'Свернуть' : `Показать все ${rows.length} связей`}
        </button>
      )}
    </div>
  )
}
export function EmptyInspector({ search, onStart }: { search: string; onStart: () => void }) {
  return (
    <div className="inspector-empty">
      <span className="empty-orbit">
        <Icon name={search ? 'search' : 'nodes'} size={32} />
      </span>
      <p className="eyebrow">ОТ СВЯЗИ К ОБЪЯСНЕНИЮ</p>
      <h3>{search ? 'Точный GID не найден' : 'У каждого узла своя история'}</h3>
      <p>
        {search
          ? 'На графе могут быть совпадения по фрагменту. Выберите узел или введите полный GID. Если результатов нет, проверьте фильтры.'
          : 'Выберите клиента на графе или в очереди. Здесь появятся его роль, основания приоритета и денежные потоки.'}
      </p>
      <button className="secondary-button" onClick={onStart}>
        Открыть первый приоритет
        <Icon name="arrow" size={16} />
      </button>
    </div>
  )
}
