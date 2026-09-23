import type { GraphData } from '../types'
import { number } from '../presentation'
import { Icon, type IconName } from './Icon'

export function Header({ data, onStart, onCopilot }: { data: GraphData | null; onStart: () => void; onCopilot: () => void }) {
  const stats: { label: string; value: number; detail: string; icon: IconName }[] = data
    ? [
        { label: 'Клиентов в сети', value: data.nodes.length, detail: 'узлы графа', icon: 'nodes' },
        {
          label: 'Направленных связей',
          value: data.edges.length,
          detail: 'денежные потоки',
          icon: 'network',
        },
        {
          label: 'Кластеров',
          value: data.clusters.length,
          detail: 'структура сети',
          icon: 'expand',
        },
        {
          label: 'Seed-клиентов',
          value: data.nodes.filter((n) => n.is_seed).length,
          detail: 'точки начала обхода',
          icon: 'shield',
        },
      ]
    : []
  return (
    <>
      <header className="topbar">
        <a className="brand" href="#network" aria-label="Taymas — граф денег">
          <span className="brand-mark">
            <Icon name="network" size={22} />
          </span>
          <strong>
            TAYMAS<span> / </span>
            <small>ГРАФ ДЕНЕГ</small>
          </strong>
        </a>
        <div className="header-right">
          <span className="header-tag">AML WORKSPACE</span>
          <span className="header-status">
            <i className={data ? 'status-dot live' : 'status-dot'} />
            {data?.source === '/fixtures'
              ? 'Демо-набор'
              : data
                ? 'Локальные данные'
                : 'Загрузка данных'}
          </span>
          <span className="avatar">TM</span>
        </div>
      </header>
      <section className="page-heading">
        <div>
          <p className="eyebrow">
            FINANCIAL INTELLIGENCE <span>/</span> ОБЗОР СЕТИ
          </p>
          <h1>
            За переводами — <span>структура.</span>
          </h1>
          <p className="page-description">
            Связи, роли и доказательства. От первого сигнала до объяснимой гипотезы.
          </p>
        </div>
        <div className="hero-actions">
          <button className="primary-button" onClick={onCopilot} disabled={!data}>
            <Icon name="spark" size={17} /> Обсудить с Copilot
          </button>
          <button className="text-button" onClick={onStart} disabled={!data?.topNodes.length}>
            К первому приоритету <Icon name="arrow" size={14} />
          </button>
        </div>
      </section>
      {!!stats.length && (
        <section className="stats-strip" aria-label="Сводка по всей выгрузке">
          {stats.map((stat, index) => (
            <div className="stat" key={stat.label}>
              <div className="stat-label">
                <Icon name={stat.icon} size={16} />
                {stat.label}
              </div>
              <div className="stat-value">
                <strong className={index === 3 ? 'gold-text' : ''}>{number(stat.value)}</strong>
                <span>{stat.detail}</span>
              </div>
            </div>
          ))}
          <div className="scope-note">
            <span className="scope-orbit">
              <Icon name="shield" size={23} />
            </span>
            <div>
              <strong>4 шага от seed</strong>
              <span>
                Выводы ограничены
                <br />
                границами выгрузки
              </span>
            </div>
          </div>
        </section>
      )}
    </>
  )
}
