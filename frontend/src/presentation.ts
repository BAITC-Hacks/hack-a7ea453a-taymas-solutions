export const number = (value: number) =>
  new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(value)
export function money(value: number): string {
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1)} млн`
  if (Math.abs(value) >= 1000)
    return `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(value / 1000)} тыс.`
  return number(value)
}
export const ROLE_LABELS: Record<string, string> = {
  coordinator: 'Координатор',
  consolidator: 'Сборщик',
  distributor: 'Распределитель',
  transit: 'Транзит',
  terminal: 'Конечный получатель',
  peripheral: 'Периферия',
}
/** Split plain text, preserving every character; never interpret CSV text as HTML. */
export function evidenceParts(text: string) {
  return text
    .split(/(\d+(?:[.,]\d+)?(?:\s?%)?)/g)
    .filter(Boolean)
    .map((text) => ({ text, numeric: /^\d/.test(text) }))
}
export function clusterColor(clusterId: number) {
  return `hsl(${(((clusterId * 137.508) % 360) + 360) % 360}, 64%, 70%)`
}
