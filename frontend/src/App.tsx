import { useState, type FormEvent } from 'react'
import './App.css'

type User = {
  id: number
  name: string
  email: string
}

type Order = {
  id: number
  user_id: number
  item_name: string
  amount: number
}

type Notice = {
  kind: 'success' | 'error'
  text: string
}

async function postJson<T>(path: string, payload: object): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })

  const body: unknown = await response.json()
  if (!response.ok) {
    const detail = (body as { detail?: unknown }).detail
    const message = typeof detail === 'string'
      ? detail
      : Array.isArray(detail)
        ? detail.map((item) => (item as { msg?: string }).msg).filter(Boolean).join(', ')
        : 'Não foi possível concluir a solicitação.'
    throw new Error(message)
  }

  return body as T
}

function App() {
  const [user, setUser] = useState({ name: '', email: '' })
  const [order, setOrder] = useState({ user_id: '', item_name: '', amount: '' })
  const [notice, setNotice] = useState<Notice | null>(null)
  const [createdUser, setCreatedUser] = useState<User | null>(null)
  const [createdOrder, setCreatedOrder] = useState<Order | null>(null)
  const [submitting, setSubmitting] = useState<'user' | 'order' | null>(null)

  async function submitUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSubmitting('user')
    setNotice(null)
    setCreatedOrder(null)

    try {
      const result = await postJson<User>('/users', user)
      setCreatedUser(result)
      setOrder((current) => ({ ...current, user_id: String(result.id) }))
      setNotice({ kind: 'success', text: `Usuário #${result.id} cadastrado com sucesso.` })
      setUser({ name: '', email: '' })
    } catch (error) {
      setNotice({
        kind: 'error',
        text: error instanceof Error ? error.message : 'Erro inesperado ao cadastrar usuário.',
      })
    } finally {
      setSubmitting(null)
    }
  }

  async function submitOrder(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSubmitting('order')
    setNotice(null)
    setCreatedUser(null)

    try {
      const result = await postJson<Order>('/orders', {
        user_id: Number(order.user_id),
        item_name: order.item_name,
        amount: Number(order.amount),
      })
      setCreatedOrder(result)
      setNotice({ kind: 'success', text: `Pedido #${result.id} criado com sucesso.` })
      setOrder((current) => ({ ...current, item_name: '', amount: '' }))
    } catch (error) {
      setNotice({
        kind: 'error',
        text: error instanceof Error ? error.message : 'Erro inesperado ao criar pedido.',
      })
    } finally {
      setSubmitting(null)
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Microstore início">
          <span className="brand-mark">m</span>
          <span>micro<span className="brand-light">store</span></span>
        </a>
        <div className="gateway-status">
          <span className="status-dot" />
          <span>API Gateway</span>
          <code>localhost:8000</code>
        </div>
      </header>

      <section className="intro">
        <div className="eyebrow"><span /> PAINEL DE OPERAÇÕES</div>
        <h1>Gerencie seus <span>pedidos</span><br />em um só lugar.</h1>
        <p>Cadastre usuários e crie pedidos pela API de microsserviços.</p>
      </section>

      {notice && (
        <div className={`notice ${notice.kind}`} role="status">
          <span className="notice-icon" aria-hidden="true">{notice.kind === 'success' ? '✓' : '!'}</span>
          <span>{notice.text}</span>
          <button type="button" aria-label="Fechar mensagem" onClick={() => setNotice(null)}>×</button>
        </div>
      )}

      <section className="work-grid" aria-label="Operações">
        <article className="panel">
          <div className="panel-heading">
            <div className="step-icon user-icon" aria-hidden="true">01</div>
            <div>
              <p className="panel-kicker">PRIMEIRO PASSO</p>
              <h2>Cadastrar usuário</h2>
            </div>
          </div>
          <p className="panel-description">Crie um usuário para associá-lo a um pedido.</p>

          <form onSubmit={submitUser}>
            <label htmlFor="user-name">Nome completo</label>
            <input
              id="user-name"
              name="name"
              autoComplete="name"
              placeholder="Ex.: Luiz Silva"
              value={user.name}
              onChange={(event) => setUser({ ...user, name: event.target.value })}
              required
            />

            <label htmlFor="user-email">E-mail</label>
            <input
              id="user-email"
              name="email"
              type="email"
              autoComplete="email"
              placeholder="luiz@exemplo.com"
              value={user.email}
              onChange={(event) => setUser({ ...user, email: event.target.value })}
              required
            />

            <button className="button button-secondary" type="submit" disabled={submitting !== null}>
              {submitting === 'user' ? 'Cadastrando…' : 'Cadastrar usuário'}
              <span aria-hidden="true">→</span>
            </button>
          </form>

          {createdUser && (
            <div className="result-card">
              <span className="result-label">USUÁRIO CRIADO</span>
              <strong>{createdUser.name}</strong>
              <span>{createdUser.email}</span>
              <span className="result-id">ID <b>{createdUser.id}</b></span>
            </div>
          )}
        </article>

        <article className="panel order-panel">
          <div className="panel-heading">
            <div className="step-icon order-icon" aria-hidden="true">02</div>
            <div>
              <p className="panel-kicker">NOVO PEDIDO</p>
              <h2>Registrar pedido</h2>
            </div>
          </div>
          <p className="panel-description">O serviço valida se o usuário existe antes de salvar.</p>

          <form onSubmit={submitOrder}>
            <label htmlFor="order-user">ID do usuário</label>
            <input
              id="order-user"
              name="user_id"
              type="number"
              min="1"
              placeholder="Ex.: 1"
              value={order.user_id}
              onChange={(event) => setOrder({ ...order, user_id: event.target.value })}
              required
            />

            <label htmlFor="order-item">Nome do produto</label>
            <input
              id="order-item"
              name="item_name"
              placeholder="Ex.: Teclado Mecânico"
              value={order.item_name}
              onChange={(event) => setOrder({ ...order, item_name: event.target.value })}
              required
            />

            <label htmlFor="order-amount">Valor <span className="label-hint">BRL</span></label>
            <div className="amount-input">
              <span>R$</span>
              <input
                id="order-amount"
                name="amount"
                type="number"
                min="0.01"
                step="0.01"
                placeholder="0,00"
                value={order.amount}
                onChange={(event) => setOrder({ ...order, amount: event.target.value })}
                required
              />
            </div>

            <button className="button button-primary" type="submit" disabled={submitting !== null}>
              {submitting === 'order' ? 'Criando pedido…' : 'Criar pedido'}
              <span aria-hidden="true">↗</span>
            </button>
          </form>

          {createdOrder && (
            <div className="result-card order-result">
              <span className="result-label">PEDIDO CRIADO</span>
              <strong>{createdOrder.item_name}</strong>
              <span>{new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(createdOrder.amount)}</span>
              <span className="result-id">PEDIDO <b>#{createdOrder.id}</b> · USUÁRIO <b>#{createdOrder.user_id}</b></span>
            </div>
          )}
        </article>
      </section>

      <footer>
        <span>MICROSERVIÇOS <i>·</i> FASTAPI + REACT</span>
        <span>Usuários <b>8001</b><i>·</i> Pedidos <b>8002</b></span>
      </footer>
    </main>
  )
}

export default App
