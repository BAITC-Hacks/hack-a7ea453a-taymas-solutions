import { useEffect, useRef, useState, type FormEvent } from 'react'
import type { GraphData } from '../types'
import DataRequestPanel from '../investigation/DataRequestPanel'
import { askCopilot, copilotStatus, MAX_QUESTION, type Availability, type Claim, type CopilotAnswer } from './api'
import './copilot.css'

interface Props {
  data: GraphData; selectedId?: string; onNavigate: (gid: string) => void
  onAnswer: (answer: CopilotAnswer | null) => void
}
const prompts = ['Почему этот узел в топе?', 'Кто общий сборщик?', 'Какой следующий шаг проверки?']
const number = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 6 })
const labels: Record<string, string> = { role: 'Роль', priority_score: 'Приоритет', role_score: 'Уверенность в роли',
  in_kzt: 'Вход', out_kzt: 'Выход', in_tx: 'Входящих переводов', out_tx: 'Исходящих переводов', n_payers: 'Плательщики',
  n_receivers: 'Получатели', sum_kzt: 'Сумма перевода', n_tx: 'Переводов', depth: 'Колено', is_seed: 'Seed',
  contrib_collect: 'Вклад сбора', contrib_fanout: 'Вклад рассылки', contrib_flow: 'Вклад потока', contrib_seed: 'Вклад связи с seed',
  contrib_bridge: 'Вклад связи кластеров', contrib_volume: 'Вклад объёма', boundary_factor: 'Поправка границы' }
const reasons: Record<string, string> = { timeout: 'AI не ответил вовремя.', quota: 'Лимит AI временно исчерпан.',
  no_api_key: 'AI не настроен.', no_model: 'AI не настроен.', auth: 'AI временно недоступен.' }

export default function CopilotPanel({ data, selectedId, onNavigate, onAnswer }: Props) {
  const [question, setQuestion] = useState('')
  const [context, setContext] = useState<string[]>([])
  const [gidInput, setGidInput] = useState('')
  const [availability, setAvailability] = useState<Availability | null>(null)
  const [useAI, setUseAI] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [answer, setAnswer] = useState<CopilotAnswer | null>(null)
  const [asked, setAsked] = useState('')
  const request = useRef<AbortController | null>(null)
  const textarea = useRef<HTMLTextAreaElement>(null)
  const resultPanel = useRef<HTMLDivElement>(null)
  const demo = data.source !== '/out'

  useEffect(() => {
    const controller = new AbortController()
    void copilotStatus(controller.signal).then(setAvailability).catch(() => { if (!controller.signal.aborted) setAvailability({ ready: false, nvidia_available: false }) })
    return () => { controller.abort(); request.current?.abort() }
  }, [])

  useEffect(() => {
    if (answer || error) resultPanel.current?.scrollIntoView?.({ block: 'nearest' })
  }, [answer, error])

  function addGid(gid: string) {
    if (!data.nodes.some(n => n.gid === gid)) { setError('Этот GID не найден в текущей выгрузке.'); return }
    if (context.includes(gid)) return
    if (context.length === 5) { setError('Для одного вопроса можно выбрать до пяти узлов.'); return }
    setContext([...context, gid]); setGidInput(''); setError('')
  }
  function suggest(prompt: string) {
    setQuestion(prompt); setError('')
    if (prompt !== prompts[1] && !context.length) {
      const gid = selectedId ?? (prompt === prompts[0] ? data.topNodes[0]?.gid : undefined)
      if (gid) setContext([gid])
    }
    textarea.current?.focus()
  }
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (loading || !question.trim() || question.length > MAX_QUESTION || demo) return
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    setLoading(true); setError(''); setAnswer(null); onAnswer(null); setAsked(question.trim())
    try {
      const result = await askCopilot({ question: question.trim(), selected_gids: context, use_nvidia: useAI }, data, controller.signal)
      if (controller.signal.aborted) return
      setAvailability(current => ({ ready: true, nvidia_available: current?.nvidia_available ?? false }))
      setAnswer(result); onAnswer(result)
    } catch (caught) {
      if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : 'Не удалось получить ответ.')
    } finally { if (!controller.signal.aborted) setLoading(false) }
  }
  function cancel() { request.current?.abort(); setLoading(false); setAsked('') }
  const link = (gid: string) => <button type="button" className="copilot-gid" onClick={() => onNavigate(gid)} aria-label={`Открыть узел ${gid}`}>{gid}<span aria-hidden="true">↗</span></button>

  return <section className="copilot" aria-label="Помощник по расследованию">
    <div className="copilot-heading"><div className="copilot-symbol" aria-hidden="true">✳</div><div><p className="eyebrow">INVESTIGATION COPILOT</p><h2>От сигнала к проверке</h2></div></div>
    <p className="copilot-intro">Задайте вопрос о графе. В ответе — наблюдаемые факты, источники и следующий шаг.</p>
    <div className="copilot-mode"><span className={availability?.ready ? 'connected' : ''}><i />{availability === null ? 'Подключаем помощника…' : availability.ready ? 'Локальный анализ готов' : 'Нет связи с помощником'}</span>
      <label title="AI включается только для отправленного вопроса"><input type="checkbox" checked={useAI} disabled={!availability?.nvidia_available || loading} onChange={e => setUseAI(e.target.checked)} />{availability?.nvidia_available ? 'Использовать AI' : 'AI не настроен'}</label></div>
    {demo && <p className="copilot-notice">Помощник работает с полной локальной выгрузкой. Для демонстрационного графа запросы отключены, чтобы не смешивать данные.</p>}
    <div className="copilot-prompts">{prompts.map((prompt, index) => <button type="button" key={prompt} onClick={() => suggest(prompt)} disabled={loading || demo}><span>0{index + 1}</span>{prompt}<b aria-hidden="true">↗</b></button>)}</div>
    <form onSubmit={submit} className="copilot-form">
      <div className="copilot-context-heading"><label htmlFor="copilot-gid">Узлы для проверки</label><span>{context.length} / 5</span></div>
      <div className="copilot-chips">{context.map(gid => <span key={gid}>{gid}<button type="button" disabled={loading} onClick={() => setContext(context.filter(g => g !== gid))} aria-label={`Убрать узел ${gid}`}>×</button></span>)}{!context.length && <small>Без выбора — вопрос обо всей очереди проверки</small>}</div>
      <div className="copilot-add"><input id="copilot-gid" value={gidInput} onChange={e => setGidInput(e.target.value)} placeholder="Введите точный GID" inputMode="numeric" maxLength={19} disabled={loading || demo} /><button type="button" disabled={!gidInput.trim() || loading || demo} onClick={() => addGid(gidInput.trim())} aria-label="Добавить GID">+</button></div>
      {selectedId && !context.includes(selectedId) && <button type="button" className="copilot-add-selected" onClick={() => addGid(selectedId)} disabled={loading || demo}>+ Выбранный на графе · …{selectedId.slice(-6)}</button>}
      <label className="copilot-question-label" htmlFor="copilot-question">Вопрос аналитика</label>
      <textarea ref={textarea} id="copilot-question" value={question} maxLength={MAX_QUESTION} disabled={loading || demo} onChange={e => setQuestion(e.target.value)} placeholder="Например: почему этот узел стоит проверить первым?" rows={3} aria-describedby="copilot-question-hint" />
      <div className="copilot-send-row"><span id="copilot-question-hint">{question.length} / {MAX_QUESTION}</span>{loading ? <button type="button" className="copilot-cancel" onClick={cancel}>Отменить</button> : <button type="submit" disabled={!question.trim() || demo} className="copilot-send">Разобрать вопрос <span aria-hidden="true">→</span></button>}</div>
    </form>
    <DataRequestPanel data={data} gid={selectedId ?? context[0] ?? answer?.candidates[0]?.gid} onNavigate={onNavigate} />
    <div ref={resultPanel} className="copilot-result" aria-live="polite" aria-busy={loading}>
      {loading && <div className="copilot-loading" role="status"><span className="spinner" /><div><strong>Проверяем связи и факты</strong><p>Граф остаётся доступным</p></div></div>}
      {error && <div role="alert" className="copilot-error"><strong>Ответ не показан</strong><p>{error}</p><span>Можно уточнить вопрос и отправить его ещё раз.</span></div>}
      {!answer && !loading && !error && <div className="copilot-empty"><span aria-hidden="true">⌕</span><p>Каждый вывод можно проверить</p><small>Выберите подсказку или задайте свой вопрос.<br />Суммы, связи и роли будут со ссылками на выгрузку.</small></div>}
      {answer && <>
        <div className="copilot-answer-heading"><span className="copilot-answer-label">{answer.status === 'empty' ? 'Совпадений не найдено' : 'Материалы проверки'}</span><span className="copilot-provider">{answer.provider === 'nvidia' ? 'AI + проверка фактов' : 'Локальный ответ'}</span></div>
        <p className="copilot-asked">{asked}</p>
        {answer.fallback_reason && <p className="copilot-notice">{reasons[answer.fallback_reason] ?? 'AI временно недоступен.'} Показан проверенный локальный ответ.</p>}
        <p className="copilot-summary">{answer.summary}</p>
        {answer.candidates.length > 0 && <div className="copilot-candidates">{answer.candidates.map((candidate, index) => <div className="copilot-candidate" key={candidate.gid}><span className="copilot-position">{String(index + 1).padStart(2, '0')}</span><div>{link(candidate.gid)}<small>{candidate.role}</small></div><strong title="priority_score">{number.format(candidate.priority_score)}</strong></div>)}</div>}
        <div className="copilot-next"><p className="eyebrow">СЛЕДУЮЩИЙ ШАГ</p>{answer.next_steps.map(step => <p key={step}>{step}</p>)}</div>
        {answer.warnings.some(warning => warning.code === 'depth4_outflow_unobserved') && <p className="copilot-notice">В ответе есть узлы на границе обхода: их исходящие переводы не наблюдаются. Отсутствие оттока не доказывает оседание денег.</p>}
        <details className="copilot-facts" open><summary>Факты и источники <span>{answer.claims.length}</span></summary>
          {answer.evidence.slice(0, 5).map(({ claim_index }) => <Fact key={claim_index} claim={answer.claims[claim_index]} link={link} />)}
          {answer.claims.length > 5 && <details className="copilot-all-facts"><summary>Все подтверждённые факты</summary>{answer.claims.map((claim, index) => <Fact key={index} claim={claim} link={link} />)}</details>}
        </details>
        <details className="copilot-warnings"><summary>Границы данных · {answer.warnings.length}</summary>{answer.warnings.map((warning, i) => <p key={i}>{warning.gid && link(warning.gid)}{warning.message}</p>)}</details>
        <p className="copilot-audit" title="Проверены значения и источники claims; summary соответствует их шаблону. Истинность гипотез этим не подтверждается.">Факты сверены с выгрузкой · {answer.tool_calls.length} вызовов инструментов</p>
      </>}
    </div>
    <p className="copilot-disclaimer">Гипотезы для проверки, не выводы о виновности.</p>
  </section>
}

function Fact({ claim, link }: { claim: Claim; link: (gid: string) => React.ReactNode }) {
  const value = typeof claim.value === 'number' ? number.format(claim.value) : typeof claim.value === 'boolean' ? claim.value ? 'Да' : 'Нет' : claim.value
  return <div className="copilot-fact"><div className="copilot-fact-nodes">{claim.kind === 'node' ? link(claim.gid!) : <>{link(claim.src!)}<span>→</span>{link(claim.dst!)}</>}</div>
    <p>{labels[claim.field] ?? claim.field}<strong>{value}{claim.field.includes('kzt') ? ' KZT' : ''}</strong></p>
    <small title={`${claim.source.file} · ${claim.source.column}`}>{claim.source.file} · {claim.source.column}</small></div>
}
