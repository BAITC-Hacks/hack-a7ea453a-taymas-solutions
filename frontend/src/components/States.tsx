import { Icon } from './Icon'
export function LoadingState() {
  return (
    <div className="state-card panel" role="status">
      <div className="spinner" />
      <p className="eyebrow">СОБИРАЕМ КАРТИНУ</p>
      <h2>Загружаем сеть переводов</h2>
      <p>Клиенты, связи, кластеры и приоритеты из локальной выгрузки.</p>
    </div>
  )
}
export function ErrorState({
  error,
  onRetry,
  onDemo,
}: {
  error: string
  onRetry: () => void
  onDemo: () => void
}) {
  return (
    <div className="state-card panel" role="alert">
      <Icon name="info" size={32} />
      <p className="eyebrow">ДАННЫЕ НЕДОСТУПНЫ</p>
      <h2>
        {error.includes('колонки')
          ? 'Проверьте схему выгрузки'
          : error.includes('пустой')
            ? 'Выгрузка пуста'
            : 'Не удалось открыть выгрузку'}
      </h2>
      <p>{error}</p>
      <code>python -m money_graph --data data --out out</code>
      <div className="button-group">
        <button className="primary-button" onClick={onRetry}>
          Повторить загрузку
        </button>
        <button className="secondary-button" onClick={onDemo}>
          Открыть демо-набор
        </button>
      </div>
    </div>
  )
}
