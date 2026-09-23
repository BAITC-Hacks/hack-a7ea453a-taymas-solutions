import type { EdgeRecord, GraphData, NodeRecord } from '../types'

export interface PlanFact {
  file: 'nodes_roles.csv' | 'edge_table.csv'
  column: string
  gid?: string
  src?: string
  dst?: string
  value: string | number | boolean
}

export interface DataRequest {
  id: string
  gid: string
  kind: 'missing_outgoing' | 'missing_incoming' | 'same_day_order'
  title: string
  observed: string
  limitation: string
  hypothesis: string
  request: {
    direction: 'incoming' | 'outgoing' | 'both'
    period: { scope: 'source_period' } | { scope: 'day'; date: string }
    fields: string[]
    purpose: string
  }
  supports: string
  weakens: string
  sources: PlanFact[]
}

export interface DataRequestPlan {
  schema_version: 1
  gid: string
  status: 'ready' | 'insufficient_data' | 'unknown_gid'
  message: string
  items: DataRequest[]
  coverage: string[]
}

// This is a plan based on the loaded snapshot, never a request to an external system.
const coverage = [
  'Выводы относятся к исходной выгрузке: исходящий обход до 4-го колена и порог 5 000 KZT.',
  'Для порядка внутри дня проверяются только подтверждённые даты first_date/last_date. Остальные дни требуют отдельных транзакций.',
  'Отсутствие рекомендации не означает полноту данных или отсутствие риска.',
]
const fields = ['Плательщик', 'Получатель', 'Сумма и валюта', 'Дата операции', 'Идентификатор операции (если доступен)']
const yes = (value: unknown) => value === true || value === 1 ||
  (typeof value === 'string' && ['true', '1', 'yes', 'да'].includes(value.trim().toLowerCase()))
const compare = (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0

function nodeFact(node: NodeRecord, column: string): PlanFact {
  return { file: 'nodes_roles.csv', gid: node.gid, column, value: node[column] }
}

function day(value: unknown): value is string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const date = new Date(`${value}T00:00:00Z`)
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value
}

/** Each endpoint date proves an event occurred that day. Overlapping ranges do not. */
function eventDays(edges: EdgeRecord[]): Map<string, PlanFact> {
  const result = new Map<string, PlanFact>()
  const ordered = [...edges].sort((a, b) => compare(a.src, b.src) || compare(a.dst, b.dst))
  for (const edge of ordered) {
    if (!day(edge.first_date) || !day(edge.last_date) || edge.first_date > edge.last_date) continue
    for (const column of ['first_date', 'last_date']) {
      const date = edge[column] as string
      if (!result.has(date)) result.set(date, {
        file: 'edge_table.csv', src: edge.src, dst: edge.dst, column, value: date,
      })
    }
  }
  return result
}

/** Pure function. Uses the PAN-50 flag; does not duplicate its threshold or change scores. */
export function buildDataRequestPlan(data: GraphData, gid: string): DataRequestPlan {
  const base = { schema_version: 1 as const, gid, coverage: [...coverage] }
  const node = data.nodes.find(n => n.gid === gid)
  if (!node) return { ...base, status: 'unknown_gid', message: 'Узел не найден в текущей выгрузке.', items: [] }
  const items: DataRequest[] = []
  const add = (item: Omit<DataRequest, 'id' | 'gid'>) => items.push({ ...item, id: `${gid}:${item.kind}`, gid })

  if (node.depth === 4 || yes(node.boundary_depth4)) {
    add({
      kind: 'missing_outgoing', title: 'Проверить продолжение потока',
      observed: node.depth === 4 ? 'Узел находится на 4-м колене обхода.' : 'Узел помечен как граница наблюдения исходящих.',
      limitation: 'Исходящие за границей обхода не наблюдаются. Отсутствие видимого оттока не доказывает оседание.',
      hypothesis: 'Гипотеза для проверки: часть полученных средств осталась на узле в рассматриваемый период.',
      request: { direction: 'outgoing', period: { scope: 'source_period' }, fields: [...fields],
        purpose: 'Запросить полную исходящую выписку за период выгрузки, включая операции ниже порога и вне наблюдаемого графа, если доступно.' },
      supports: 'Полная выписка без последующих исходящих поддержит гипотезу об отсутствии дальнейших переводов за этот период; для остатка нужны данные о балансе.',
      weakens: 'Последующие исходящие покажут продолжение потока и ослабят гипотезу об оседании.',
      sources: [nodeFact(node, node.depth === 4 ? 'depth' : 'boundary_depth4')],
    })
  }

  const incomingSources: PlanFact[] = []
  const reasons: string[] = []
  if (yes(node.is_seed)) {
    incomingSources.push(nodeFact(node, 'is_seed'))
    reasons.push('Узел входит в исходный набор seed.')
  } else if (yes(node.in_underestimated)) {
    incomingSources.push(nodeFact(node, 'in_underestimated'))
    reasons.push('Выгрузка помечает входящие как неполные.')
  }
  if (yes(node.external_inflow_suspected)) {
    incomingSources.push(nodeFact(node, 'external_inflow_suspected'))
    reasons.push('Правило пайплайна отметило возможный источник средств вне выгрузки.')
    for (const column of ['in_kzt', 'out_kzt']) {
      if (typeof node[column] === 'number' && Number.isFinite(node[column])) incomingSources.push(nodeFact(node, column))
    }
  }
  if (incomingSources.length) {
    add({
      kind: 'missing_incoming', title: 'Уточнить источник средств', observed: reasons.join(' '),
      limitation: 'Видимый вход не описывает все источники средств. Превышение оттока также может покрываться остатком до начала периода.',
      hypothesis: 'Гипотеза для проверки: часть средств поступила от контрагентов вне наблюдаемого графа.',
      request: { direction: 'incoming', period: { scope: 'source_period' }, fields: [...fields, 'Входящий остаток на начало периода (если доступен)'],
        purpose: 'Запросить полную входящую выписку за период выгрузки и начальный остаток, если доступны; сопоставить с видимым входом.' },
      supports: 'Дополнительные поступления от отсутствующих в графе контрагентов поддержат гипотезу о неполном охвате входящих.',
      weakens: 'Совпадение полной выписки с видимым входом ослабит эту гипотезу; начальный остаток может объяснить превышение оттока.',
      sources: incomingSources,
    })
  }

  const incoming = eventDays(data.edges.filter(e => e.dst === gid && e.src !== gid))
  const outgoing = eventDays(data.edges.filter(e => e.src === gid && e.dst !== gid))
  const date = [...incoming.keys()].filter(d => outgoing.has(d)).sort(compare)[0]
  if (date) {
    add({
      kind: 'same_day_order', title: 'Установить порядок операций внутри дня',
      observed: `На дату ${date} подтверждены входящий и исходящий переводы по двум направленным связям.`,
      limitation: 'Даты не содержат времени. Неизвестно, какой из этих переводов произошёл раньше.',
      hypothesis: 'Гипотеза для проверки: наблюдаемый исходящий перевод произошёл после входящего в тот же день.',
      request: { direction: 'both', period: { scope: 'day', date }, fields: [...fields, 'Точное время исполнения и часовой пояс (если доступны)'],
        purpose: 'Запросить время входящих и исходящих операций по указанным связям за этот день, если первичная система его хранит.' },
      supports: 'Входящий раньше исходящего поддержит временную совместимость цепочки, но не докажет передачу тех же денег.',
      weakens: 'Исходящий раньше входящего исключит этот порядок для выбранной пары операций. При одинаковом времени порядок может остаться неизвестным.',
      sources: [incoming.get(date)!, outgoing.get(date)!],
    })
  }

  return { ...base, items, status: items.length ? 'ready' : 'insufficient_data',
    message: items.length ? 'План проверки по наблюдаемым ограничениям.' : 'Недостаточно данных для рекомендации по этим правилам.' }
}
