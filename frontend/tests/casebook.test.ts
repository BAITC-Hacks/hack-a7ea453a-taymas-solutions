import { describe, expect, it } from 'vitest'
import { addItem, captureAnswer, captureNode, fingerprintData, MAX_CASE_BYTES, newCase, parseCase,
  serializeCase, snapshotStatus, type CaseFile } from '../src/casebook/model'
import { renderReport } from '../src/casebook/report'
import { A, B, brief, data } from './copilot-fixture'

async function fixture() {
  const version = await fingerprintData(data)
  let file = newCase()
  file = addItem(file, captureNode(data, version, A))
  file = addItem(file, captureAnswer(data, version, brief, 'Почему этот узел?'))
  file = addItem(file, captureAnswer(data, version, brief, 'Что проверить дальше?'))
  return { file, version }
}

describe('Case file schema and evidence', () => {
  it('round-trips a node and two answers without losing long gids, edges, notes or source versions', async () => {
    const { file, version } = await fixture()
    const linked = { ...brief, gids: [A, B], claims: [{ kind: 'edge' as const, src: A, dst: B, field: 'sum_kzt', value: 5000,
      source: { file: 'edge_table.csv' as const, src: A, dst: B, column: 'sum_kzt' } }] }
    file.items.push(captureAnswer(data, version, linked, 'Связь между узлами?'))
    file.notes = 'Комментарий аналитика\nВторая строка.'
    file.hypothesis = 'Нужно проверить источник средств.'
    const restored = parseCase(serializeCase(file))
    expect(restored).toEqual(file)
    expect(restored.items.map(i => i.gids[0])).toEqual([A, A, A, A])
    expect(restored.items[3].claims[0].source).toEqual(linked.claims[0].source)
    expect(restored.items.every(i => snapshotStatus(i, data, version) === 'current')).toBe(true)
  })

  it('hashes every table, independent of URL and row/property order', async () => {
    const first = await fingerprintData(data)
    const moved = { ...data, source: '/another-place', nodes: [...data.nodes].reverse().map(n => Object.fromEntries(Object.entries(n).reverse())) }
    expect(await fingerprintData(moved as typeof data)).toEqual(first)
    for (const table of ['nodes', 'edges', 'clusters', 'topNodes'] as const) {
      const altered = structuredClone(data)
      if (!altered[table].length) altered.clusters.push({ cluster_id: 1, n_nodes: 2, n_seed: 0, sum_kzt_internal: 5000, top_gids: A, hypothesis: 'x' })
      else Object.assign(altered[table][0], { changed_column: 'new' })
      expect((await fingerprintData(altered)).id).not.toBe(first.id)
    }
  })

  it('marks other data stale; same fingerprint still requires checking every fact', async () => {
    const { file, version } = await fixture()
    const item = file.items[0]
    expect(snapshotStatus(item, null, null)).toBe('unavailable')
    expect(snapshotStatus(item, data, { ...version, id: `sha256:${'0'.repeat(64)}` })).toBe('stale')
    const tampered = structuredClone(item)
    tampered.claims.find(c => c.field === 'in_kzt')!.value = 999999
    expect(snapshotStatus(tampered, data, version)).toBe('unverified')
    const imported = parseCase(serializeCase({ ...file, items: [tampered] }))
    expect(snapshotStatus(imported.items[0], data, version)).toBe('unverified')
  })

  it('does not capture failed or altered Copilot evidence', async () => {
    const version = await fingerprintData(data)
    expect(() => captureAnswer(data, version, { ...brief, verification: 'failed' }, 'q')).toThrow('проверку')
    const wrong = structuredClone(brief)
    wrong.claims[0].value = 777
    expect(() => captureAnswer(data, version, wrong, 'q')).toThrow('не совпадают')
  })

  it('never exports arbitrary payload fields or API configuration', async () => {
    const version = await fingerprintData(data)
    const raw = { ...structuredClone(brief), api_key: 'SECRET-NVIDIA-KEY', headers: { Authorization: 'SECRET-NVIDIA-KEY' } }
    const item = captureAnswer(data, version, raw, 'q')
    const file = addItem(newCase(), item)
    expect(serializeCase(file)).not.toContain('SECRET-NVIDIA-KEY')
    expect(serializeCase(file)).not.toContain('api_key')
    raw.claims[0] = { ...brief.claims[0], value: 1 }
    expect(item.claims[0].value).toBe(15000)
  })

  it('deduplicates the same snapshot, but keeps changed questions and datasets', async () => {
    const { file, version } = await fixture()
    expect(addItem(file, captureNode(data, version, A))).toBe(file)
    expect(addItem(file, captureAnswer(data, version, brief, 'Почему этот узел?'))).toBe(file)
    expect(addItem(file, captureNode(data, { ...version, id: `sha256:${'0'.repeat(64)}` }, A)).items).toHaveLength(4)
    expect(addItem(file, captureAnswer(data, version, brief, 'Другой вопрос'))).not.toBe(file)
  })

  it('retains depth, seed and incomplete inflow caveats and PAN-54 next steps', async () => {
    const boundary = structuredClone(data)
    boundary.nodes[0] = { ...boundary.nodes[0], depth: 4, is_seed: true, boundary_depth4: true, external_inflow_suspected: true }
    const version = await fingerprintData(boundary)
    const item = captureNode(boundary, version, A)
    expect(item.warnings.map(w => w.code)).toContain('depth4_outflow_unobserved')
    expect(item.warnings.map(w => w.code)).toContain('missing_incoming')
    expect(item.next_steps.join(' ')).toContain('полную входящую выписку')
    expect(item.next_steps.join(' ')).toContain('полную исходящую выписку')
    expect(snapshotStatus(item, boundary, version)).toBe('current')
  })

  const corruptions: [string, (file: CaseFile) => unknown][] = [
    ['schema', f => ({ ...f, schema_version: 2 })],
    ['numeric gid', f => { (f.items[0].gids as unknown[]) = [9007199254741009]; return f }],
    ['missing source', f => { delete (f.items[0].claims[0] as Partial<typeof f.items[0]['claims'][0]>).source; return f }],
    ['wrong source column', f => { f.items[0].claims[0].source.column = 'different'; return f }],
    ['wrong source gid', f => { f.items[0].claims[0].source.gid = B; return f }],
    ['invalid fingerprint', f => { f.items[0].dataset.id = 'current'; return f }],
    ['extra secret', f => ({ ...f, api_key: 'secret' })],
    ['nested secret', f => { Object.assign(f.items[0], { api_key: 'secret' }); return f }],
    ['missing notes', f => { delete (f as Partial<CaseFile>).notes; return f }],
    ['duplicate material', f => { f.items.push(f.items[0]); return f }],
    ['date', f => ({ ...f, created_at: 'yesterday' })],
    ['prototype', f => JSON.parse(JSON.stringify(f).replace('"notes":', '"__proto__":{"polluted":true},"notes":'))],
  ]
  it.each(corruptions)('rejects %s without touching the original', async (_, corrupt) => {
    const { file } = await fixture()
    const before = serializeCase(file)
    expect(() => parseCase(JSON.stringify(corrupt(structuredClone(file))))).toThrow('Файл дела:')
    expect(serializeCase(file)).toBe(before)
  })
  it('rejects corrupt JSON and oversized text', () => {
    expect(() => parseCase('{broken')).toThrow('JSON')
    expect(() => parseCase('x'.repeat(MAX_CASE_BYTES + 1))).toThrow('4 МБ')
  })

  it('prints full identifiers, sources, version, amounts and multiline text without executable markup', async () => {
    const { file, version } = await fixture()
    const hostile = '<script>alert("x")</script><img src=x onerror=alert(1)> & "quote"'
    file.notes = `${hostile}\n${'Длинная заметка. '.repeat(400)}КОНЕЦ ТЕКСТА`
    file.title = hostile
    file.items[0].hypothesis = hostile
    const html = renderReport(file, data, version)
    expect(html).not.toContain('<script>')
    expect(html).not.toContain('<img')
    expect(html).toContain('&lt;script&gt;')
    expect(html).toContain('КОНЕЦ ТЕКСТА')
    expect(html).toContain(A)
    expect(html).toContain(version.id)
    expect(html).toContain('nodes_roles.csv')
    expect(html).toContain('KZT')
    expect(html).toContain('@media print')
    expect(html).toContain('white-space:pre-wrap')
    expect(html).not.toContain('text-overflow:ellipsis')
    expect(renderReport(file, null, null)).toContain('сохранённые значения не выдаются за текущие факты')
  })
})
