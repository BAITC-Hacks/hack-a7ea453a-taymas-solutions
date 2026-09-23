import type { GraphData } from '../types'
import { SCOPE, snapshotStatus, STATUS_LABELS, type CaseFile, type DatasetVersion } from './model'

export const FACT_LABELS: Record<string, string> = {
  role: 'Роль по модели', role_score: 'Role score', priority_score: 'Приоритет проверки', depth: 'Колено', is_seed: 'Seed',
  in_kzt: 'Входящий объём', out_kzt: 'Исходящий объём', n_payers: 'Плательщиков', n_receivers: 'Получателей',
  in_tx: 'Входящих переводов', out_tx: 'Исходящих переводов', n_seed_payers: 'Плательщиков seed', n_seed_receivers: 'Получателей seed',
  sum_kzt: 'Сумма связи', n_tx: 'Переводов', boundary_factor: 'Поправка границы',
  contrib_collect: 'Вклад сбора', contrib_fanout: 'Вклад рассылки', contrib_flow: 'Вклад потока',
  contrib_seed: 'Вклад связи с seed', contrib_bridge: 'Вклад моста', contrib_volume: 'Вклад объёма',
}
export function factValue(value: string | number | boolean, field: string): string {
  const text = typeof value === 'number' ? new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 6 }).format(value)
    : typeof value === 'boolean' ? value ? 'Да' : 'Нет' : value
  return text + (field.includes('kzt') ? ' KZT' : '')
}
export function formatDate(value: string): string {
  return new Date(value).toLocaleString('ru-RU', { dateStyle: 'medium', timeStyle: 'short' })
}
export function escapeHtml(value: unknown): string {
  return String(value).replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]!)
}

/** Standalone, script-free report. Every dynamic value is escaped, including notes and source fields. */
export function renderReport(file: CaseFile, data: GraphData | null, version: DatasetVersion | null): string {
  const e = escapeHtml
  const paragraphs = (values: string[]) => values.map(v => `<li>${e(v)}</li>`).join('')
  return `<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>${e(file.title || 'Дело расследования')} — Taymas</title><style>
*{box-sizing:border-box}body{color:#172c2a;background:#edf1ed;font:14px/1.6 Arial,sans-serif;margin:0;padding:36px}
main{background:white;max-width:950px;margin:auto;padding:48px;border-top:5px solid #347764}h1{font-size:32px;line-height:1.2}h2{font-size:20px}h3{font-size:14px;margin:22px 0 8px}
.brand{font-weight:bold;letter-spacing:.2em;color:#347764}small,.meta{color:#546561}.meta{font-size:12px}.text{white-space:pre-wrap;overflow-wrap:anywhere}.mono{font-family:monospace;overflow-wrap:anywhere}
.block{padding:18px;background:#f4f7f3;margin:18px 0}.note{border-left:3px solid #8573a5}.hypothesis{border-left:3px solid #bf9952}.warning{color:#775416;background:#fcf6e9;padding:12px}
article{border-top:1px solid #ccd8d2;margin-top:30px;padding-top:16px}table{width:100%;border-collapse:collapse;table-layout:fixed}th{text-align:left;background:#f0f5f1}td,th{padding:10px 8px;border-bottom:1px solid #e0e7e2;vertical-align:top;overflow-wrap:anywhere}td small{display:block}tr{break-inside:avoid}li{white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0}footer{border-top:1px solid #ccd8d2;margin-top:32px;padding-top:14px}
@page{size:A4;margin:16mm}@media print{body{background:white;padding:0;font-size:10pt}main{max-width:none;padding:0;border:0}.print-hint{display:none}h1,h2,h3{break-after:avoid}a{color:inherit}}@media(max-width:600px){body{padding:10px}main{padding:20px}}
</style></head><body><main>
<div class="brand">TAYMAS / ДЕЛО РАССЛЕДОВАНИЯ</div>
<p class="print-hint meta">Печатная справка · Печать браузера → «Сохранить как PDF». Данные остаются локально.</p>
<h1 class="text">${e(file.title || 'Без названия')}</h1><p class="meta">Создано ${e(formatDate(file.created_at))} · Изменено ${e(formatDate(file.updated_at))}<br>Дело <span class="mono">${e(file.id)}</span> · схема ${file.schema_version}</p>
<p class="warning">${e(SCOPE)}</p>
<section class="block hypothesis"><h2>Рабочая гипотеза аналитика</h2><p class="meta">Не подтверждённый факт</p><p class="text">${e(file.hypothesis || 'Не сформулирована.')}</p></section>
<section class="block note"><h2>Заметки аналитика</h2><p class="meta">Личный комментарий, не результат проверки CSV</p><p class="text">${e(file.notes || 'Заметок пока нет.')}</p></section>
<h2>Материалы · ${file.items.length}</h2>
${file.items.map((item, index) => {
    const status = snapshotStatus(item, data, version)
    return `<article><p class="meta">${String(index + 1).padStart(2, '0')} / ${item.kind === 'node' ? 'Узел' : 'Ответ Copilot'} · ${e(formatDate(item.created_at))}</p>
<h2 class="text">${e(item.title)}</h2><p class="${status === 'current' ? 'meta' : 'warning'}">${e(STATUS_LABELS[status])}${status === 'current' ? ' · проверены только структурированные факты' : ' · сохранённые значения не выдаются за текущие факты'}</p>
<p class="meta">Версия данных: <span class="mono">${e(item.dataset.id)}</span><br>${item.dataset.node_count} узлов · ${item.dataset.edge_count} связей</p>
<p class="mono">GID: ${item.gids.map(e).join(' · ')}</p>
<h3>${status === 'current' ? 'Факты и источники' : 'Сохранённые значения и источники'}</h3><table><thead><tr><th>Объект / поле</th><th>Значение</th><th>Источник</th></tr></thead><tbody>
${item.claims.map(c => `<tr><td><span class="mono">${e(c.kind === 'node' ? c.gid : `${c.src} → ${c.dst}`)}</span><small>${e(FACT_LABELS[c.field] ?? c.field)}</small></td><td class="text">${e(factValue(c.value, c.field))}</td><td class="mono">${e(c.source.file)}<small>${e(c.source.column)}</small></td></tr>`).join('')}</tbody></table>
<section class="block hypothesis"><h3>Гипотеза / объяснение из материала</h3><p class="meta">Свободный текст; сверка числовых фактов не подтверждает смысл гипотезы.</p><p class="text">${e(item.hypothesis)}</p></section>
<h3>Ограничения</h3><ul>${paragraphs([SCOPE, ...item.warnings.map(w => (w.gid ? `${w.gid}: ` : '') + w.message)])}</ul>
<h3>Следующие шаги</h3>${item.next_steps.length ? `<ol>${paragraphs(item.next_steps)}</ol>` : '<p>Следующий шаг не указан.</p>'}</article>`
  }).join('')}
<footer class="meta">Локальный снимок материалов Taymas. Источники относятся к версии, указанной у каждого материала. Справка не является заключением о виновности.</footer>
</main></body></html>`
}

export function downloadFile(name: string, contents: string, type: string): void {
  const url = URL.createObjectURL(new Blob([contents], { type }))
  const link = document.createElement('a')
  link.href = url; link.download = name; link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
