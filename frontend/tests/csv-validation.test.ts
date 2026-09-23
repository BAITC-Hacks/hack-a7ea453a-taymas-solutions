import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { loadData } from '../src/data'
import { numberValue, parseCsv, parseCsvTable } from '../src/csv'

const A = '100000003684369100', B = '100000003684369101', C = '100000003684369102'
type FileName = 'nodes_roles.csv' | 'edge_table.csv' | 'clusters.csv' | 'top_nodes.csv'
type Tables = Record<FileName, Record<string, string>[]>
function tables(): Tables {
  return {
    'nodes_roles.csv': [
      { gid: A, role: 'coordinator', role_score: '.9', cluster_id: '1', priority_score: '.8', evidence: '3 плательщика' },
      { gid: B, role: 'transit', role_score: '.7', cluster_id: '1', priority_score: '.7', evidence: '2 перевода' },
      { gid: C, role: 'peripheral', role_score: '0', cluster_id: '2', priority_score: '.3', evidence: '1 связь' },
    ],
    'edge_table.csv': [{ src: A, dst: B, sum_kzt: '10', n_tx: '2' }, { src: B, dst: C, sum_kzt: '20', n_tx: '1' }],
    'clusters.csv': [
      { cluster_id: '1', n_nodes: '2', n_seed: '0', sum_kzt_internal: '10', top_gids: `${A};${B}`, hypothesis: 'Гипотеза о транзите' },
      { cluster_id: '2', n_nodes: '1', n_seed: '0', sum_kzt_internal: '0', top_gids: C, hypothesis: 'Гипотеза о периферии' },
    ],
    'top_nodes.csv': [
      { rank: '1', gid: A, role: 'coordinator', priority_score: '.8', why: '3 плательщика' },
      { rank: '2', gid: B, role: 'transit', priority_score: '.7', why: '2 перевода' },
      { rank: '3', gid: C, role: 'peripheral', priority_score: '.3', why: '1 связь' },
    ],
  }
}
function csv(rows: Record<string, string>[]): string {
  const columns = Object.keys(rows[0])
  const escape = (value = '') => /[",\n\r]/.test(value) ? `"${value.replaceAll('"', '""')}"` : value
  return [columns.join(','), ...rows.map(row => columns.map(column => escape(row[column])).join(','))].join('\n') + '\n'
}
function mockCsv(files: Record<string, string>) {
  vi.stubGlobal('fetch', vi.fn((url: string) => Promise.resolve(new Response(files[url.split('/').pop()!] ?? '', { status: 200 }))))
}
function mockTables(source: Tables) { mockCsv(Object.fromEntries(Object.entries(source).map(([name, rows]) => [name, csv(rows)]))) }
afterEach(() => vi.unstubAllGlobals())

describe('strict CSV fields', () => {
  it('accepts a minimal contract and preserves distinct int64 IDs without Number coercion', async () => {
    mockTables(tables())
    const data = await loadData()
    expect(data.nodes.map(node => node.gid)).toEqual([A, B, C])
    expect(data.edges[0]).toMatchObject({ src: A, dst: B, sum_kzt: 10, n_tx: 2 })
    expect(data.nodes[0].in_kzt).toBe(0) // optional, absent in this minimal schema
  })

  it.each<[FileName, string, string]>([
    ['nodes_roles.csv', 'role', 'organizer'], ['nodes_roles.csv', 'role', ''],
    ['nodes_roles.csv', 'role_score', '2'], ['nodes_roles.csv', 'role_score', '-.1'],
    ['nodes_roles.csv', 'priority_score', 'NaN'], ['nodes_roles.csv', 'priority_score', ''],
    ['nodes_roles.csv', 'priority_score', 'Infinity'], ['nodes_roles.csv', 'priority_score', '1e400'],
    ['nodes_roles.csv', 'cluster_id', '1.5'], ['nodes_roles.csv', 'cluster_id', '-1'],
    ['nodes_roles.csv', 'gid', '1e17'], ['nodes_roles.csv', 'gid', '9223372036854775808'],
    ['nodes_roles.csv', 'evidence', '   '], ['nodes_roles.csv', 'evidence', 'a'.repeat(201)],
    ['edge_table.csv', 'sum_kzt', 'abc'], ['edge_table.csv', 'sum_kzt', ''],
    ['edge_table.csv', 'sum_kzt', '-3'], ['edge_table.csv', 'sum_kzt', '0'],
    ['edge_table.csv', 'n_tx', '1.5'], ['edge_table.csv', 'n_tx', '0'], ['edge_table.csv', 'n_tx', '9007199254740992'],
    ['clusters.csv', 'n_nodes', '1.5'], ['clusters.csv', 'n_seed', '-1'],
    ['clusters.csv', 'sum_kzt_internal', 'NaN'], ['clusters.csv', 'hypothesis', ''],
    ['top_nodes.csv', 'rank', '1.5'], ['top_nodes.csv', 'priority_score', '2'], ['top_nodes.csv', 'why', ''],
  ])('rejects invalid %s %s=%s with the file, physical row and field', async (file, field, value) => {
    const source = tables(); source[file][0][field] = value; mockTables(source)
    await expect(loadData()).rejects.toThrow(`${file}: строка 2, поле «${field}»`)
  })

  it.each(['in_kzt', 'depth', 'in_deg', 'is_seed'])('does not treat a blank provided optional %s as zero or false', async field => {
    const source = tables(); source['nodes_roles.csv'].forEach(row => { row[field] = '' }); mockTables(source)
    await expect(loadData()).rejects.toThrow(`поле «${field}»`)
  })

  it('preserves nullable undefined metrics, validates supplied values, and accepts legitimate pass-through above one', async () => {
    const source = tables()
    source['nodes_roles.csv'].forEach(row => Object.assign(row, { top_payer_share: '', top_receiver_share: '', fast_out_share: '', pass_through: '', pass_through_reliable: '', median_lag_days: '' }))
    source['nodes_roles.csv'][0].pass_through = '1.2'; mockTables(source)
    const data = await loadData()
    expect(data.nodes[0].pass_through).toBe(1.2)
    expect(data.nodes[1].median_lag_days).toBe('')
    source['nodes_roles.csv'][0].fast_out_share = 'NaN'; mockTables(source)
    await expect(loadData()).rejects.toThrow('поле «fast_out_share»')
  })

  it('permits a header-only edge file for an isolated-node graph', async () => {
    const source = tables()
    source['clusters.csv'].forEach(row => { row.sum_kzt_internal = '0' })
    mockCsv({ ...Object.fromEntries(Object.entries(source).map(([name, rows]) => [name, csv(rows)])), 'edge_table.csv': 'src,dst,sum_kzt,n_tx\n' })
    expect((await loadData()).edges).toEqual([])
  })
})

describe('cross-file consistency', () => {
  it('rejects a duplicate gid before indexing it', async () => {
    const source = tables(); source['nodes_roles.csv'][1].gid = A; mockTables(source)
    await expect(loadData()).rejects.toThrow('nodes_roles.csv: строка 3, поле «gid»: идентификатор')
  })
  it.each(['src', 'dst'])('reports an unknown edge %s instead of silently dropping the edge', async field => {
    const source = tables(); source['edge_table.csv'][0][field] = '999'; mockTables(source)
    await expect(loadData()).rejects.toThrow(`edge_table.csv: строка 2, поле «${field}»: узел 999 отсутствует`)
  })
  it('rejects repeated directed edges but accepts reciprocal edges', async () => {
    const source = tables(); source['edge_table.csv'].push({ ...source['edge_table.csv'][0] }); mockTables(source)
    await expect(loadData()).rejects.toThrow('поле «src/dst»: направленная пара повторяется')
    source['edge_table.csv'][2] = { src: B, dst: A, sum_kzt: '5', n_tx: '1' }; source['clusters.csv'][0].sum_kzt_internal = '15'; mockTables(source)
    expect((await loadData()).edges).toHaveLength(3)
  })
  it.each<[FileName, string, string]>([
    ['nodes_roles.csv', 'cluster_id', '999'],
    ['clusters.csv', 'n_nodes', '3'], ['clusters.csv', 'n_seed', '3'], ['clusters.csv', 'sum_kzt_internal', '11'],
    ['clusters.csv', 'top_gids', C], ['clusters.csv', 'top_gids', '999'], ['clusters.csv', 'top_gids', `${A};${A}`],
    ['top_nodes.csv', 'gid', '999'], ['top_nodes.csv', 'rank', '2'], ['top_nodes.csv', 'role', 'transit'], ['top_nodes.csv', 'priority_score', '.9'],
  ])('rejects inconsistent %s %s', async (file, field, value) => {
    const source = tables(); source[file][0][field] = value; mockTables(source)
    await expect(loadData()).rejects.toThrow(`${file}: строка 2, поле «${field}»`)
  })
  it('checks seed totals when seed flags are provided, without requiring this optional column', async () => {
    const source = tables(); source['nodes_roles.csv'].forEach(row => { row.is_seed = 'false' }); source['nodes_roles.csv'][0].is_seed = 'true'; mockTables(source)
    await expect(loadData()).rejects.toThrow('поле «n_seed»')
    source['clusters.csv'][0].n_seed = '1'; mockTables(source)
    expect((await loadData()).nodes[0].is_seed).toBe(true)
  })
  it('rejects duplicate cluster IDs and duplicate top gids', async () => {
    const source = tables(); source['clusters.csv'][1].cluster_id = '1'; mockTables(source)
    await expect(loadData()).rejects.toThrow('номер кластера повторяется')
    const other = tables(); other['top_nodes.csv'][1].gid = A; mockTables(other)
    await expect(loadData()).rejects.toThrow('узел повторяется в топ-листе')
  })
  it('rejects a top list out of score order even with consistent node scores', async () => {
    const source = tables(), rows = source['top_nodes.csv']; [rows[0], rows[1]] = [rows[1], rows[0]]
    rows.forEach((row, index) => { row.rank = String(index + 1) }); mockTables(source)
    await expect(loadData()).rejects.toThrow('по убыванию приоритета')
  })
  it('allows cent rounding of a cluster amount', async () => {
    const source = tables(); source['edge_table.csv'][0].sum_kzt = '10.0049'; mockTables(source)
    expect((await loadData()).clusters[0].sum_kzt_internal).toBe(10)
  })
  it('rejects overflow when individually finite edge amounts are added', async () => {
    const source = tables()
    source['edge_table.csv'][0].sum_kzt = '1e308'
    source['edge_table.csv'].push({ src: B, dst: A, sum_kzt: '1e308', n_tx: '1' })
    source['clusters.csv'][0].sum_kzt_internal = '1e308'; mockTables(source)
    await expect(loadData()).rejects.toThrow('суммирование рёбер превышает')
  })
})

describe('CSV syntax', () => {
  it('accepts BOM, CRLF, escaped quotes and quoted newlines with accurate physical line numbers', () => {
    const parsed = parseCsvTable('\uFEFFgid,evidence\r\n1,"line 1\nline ""2"""\r\n2,done\r\n')
    expect(parsed.rows[0].evidence).toBe('line 1\nline "2"')
    expect(parsed.lines).toEqual([2, 4])
  })
  it.each(['gid,gid\n1,2\n', 'gid,\n1,2\n', 'gid,evidence\n1\n', 'gid,evidence\n1,2,3\n', 'gid,evidence\n1,"unfinished\n', 'gid,evidence\n1,"done"oops\n'])('rejects ambiguous or malformed CSV %s', input => {
    expect(() => parseCsv(input)).toThrow()
  })
  it.each(['', ' ', 'null', 'NaN', 'Infinity', '0x10', '1e400'])('never coerces invalid numeric input %s to zero', value => {
    expect(() => numberValue(value)).toThrow()
  })
  it('reports malformed CSV with its filename and physical line', async () => {
    const source = tables(); mockTables(source)
    const valid = Object.fromEntries(Object.entries(source).map(([name, rows]) => [name, csv(rows)]))
    mockCsv({ ...valid, 'nodes_roles.csv': valid['nodes_roles.csv'].replace('3 плательщика', '"3 плательщика') })
    await expect(loadData()).rejects.toThrow('nodes_roles.csv: строка 2: не закрыта кавычка')
  })
})

describe('repository output compatibility', () => {
  const names: FileName[] = ['nodes_roles.csv', 'clusters.csv', 'top_nodes.csv', 'edge_table.csv']
  it('loads the checked-in demo fixture', async () => {
    mockCsv(Object.fromEntries(names.map(name => [name, readFileSync(resolve('public/fixtures', name), 'utf8')])))
    const data = await loadData('/fixtures')
    expect(data.nodes).toHaveLength(4); expect(data.edges).toHaveLength(4)
  })
  const realOut = process.env.MONEY_GRAPH_TEST_OUT
  it.skipIf(!realOut)('loads all real pipeline outputs including nullable analytical metrics', async () => {
    mockCsv(Object.fromEntries(names.map(name => [name, readFileSync(resolve(realOut!, name), 'utf8')])))
    const data = await loadData('/out')
    expect(data.nodes).toHaveLength(2248); expect(data.edges).toHaveLength(3119)
    expect(data.topNodes.length).toBeGreaterThanOrEqual(20)
  })
})
