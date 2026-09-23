import { useMemo } from 'react'
import type { GraphData } from '../types'
import { buildDataRequestPlan, type PlanFact } from './dataRequestPlan'
import './dataRequestPlan.css'

interface Props { data: GraphData; gid?: string; onNavigate: (gid: string) => void }
const directions = { incoming: 'Входящие', outgoing: 'Исходящие', both: 'Входящие и исходящие' }
const value = (fact: PlanFact) => typeof fact.value === 'boolean' ? fact.value ? 'да' : 'нет' : String(fact.value)

export default function DataRequestPanel({ data, gid, onNavigate }: Props) {
  const plan = useMemo(() => gid ? buildDataRequestPlan(data, gid) : null, [data, gid])
  return <details className="data-request-plan">
    <summary>Каких данных не хватает? <span>{plan?.items.length || ''}</span></summary>
    <div className="data-request-body">
      <p className="data-request-intro">Локальный план проверки. Запросы не отправляются.</p>
      {!plan && <p>Выберите узел на графе или добавьте GID в контекст помощника.</p>}
      {plan && <>
        <p className="data-request-target">План для узла <button type="button" onClick={() => onNavigate(plan.gid)} disabled={plan.status === 'unknown_gid'}>{plan.gid}</button></p>
        {data.source !== '/out' && <p className="data-request-note">Демонстрационный набор: план относится только к показанным данным.</p>}
        <p>{plan.message}</p>
        {plan.items.map(item => <article key={item.id} className="data-request-item">
          <h3>{item.title}</h3>
          <p>{item.observed}</p>
          <p className="data-request-note">{item.limitation}</p>
          <p>{item.hypothesis}</p>
          <div className="data-request-action"><strong>Что запросить</strong><p>{item.request.purpose}</p>
            <p>{directions[item.request.direction]} · {item.request.period.scope === 'day' ? item.request.period.date : 'Полный период исходной выгрузки'}</p>
          </div>
          <details><summary>Поля, проверка и источники</summary>
            <p><strong>Поля:</strong> {item.request.fields.join('; ')}.</p>
            <p><strong>Поддержит:</strong> {item.supports}</p>
            <p><strong>Ослабит:</strong> {item.weakens}</p>
            <ul className="data-request-sources">{item.sources.map((source, i) => <li key={i}>
              <code>{source.file} · {source.column} = {value(source)}</code>
              <span>{source.gid ?? `${source.src} → ${source.dst}`}</span>
            </li>)}</ul>
          </details>
        </article>)}
        <details className="data-request-coverage"><summary>Границы плана</summary>{plan.coverage.map(text => <p key={text}>{text}</p>)}</details>
      </>}
    </div>
  </details>
}
