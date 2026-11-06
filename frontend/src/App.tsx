import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import './App.css'

type UserSession = { access_token: string; token_type: string; expires_in: number }
type Dependent = { name: string; cpf: string; relationship: string; date_of_birth: string }
type EmploymentPeriod = { employer: string; position: string; start_date: string; end_date: string }
type ContributionPeriod = { institution: string; regime: string; start_date: string; end_date: string; months: string }
type ValidationCase = {
  id: string
  insured_id: string
  insured_name: string
  status: 'pending' | 'approved' | 'rejected'
  issues: string[]
  created_at: string
  reviewed_at: string | null
  reviewed_by: string | null
}
type DashboardData = {
  updated_at: string
  goal: number | null
  registered_today: number
  total_insured: number
  registrations_by_day: { day: string; count: number }[]
  validation: { pending: number; approved: number; rejected: number; total: number }
  progress_percent: number | null
  snapshots: { created_at: string; total_insured: number; pending: number }[]
}
type InsuredSummary = { id: string; full_name: string; cpf: string; registration_number: string | null }
type Notice = { tone: 'success' | 'error' | 'info'; text: string }
type Tab = 'dashboard' | 'register' | 'pending'
type ApiError = { detail?: string | { msg?: string }[] }

const emptyDependent: Dependent = { name: '', cpf: '', relationship: '', date_of_birth: '' }
const emptyEmployment: EmploymentPeriod = { employer: '', position: '', start_date: '', end_date: '' }
const emptyContribution: ContributionPeriod = { institution: '', regime: '', start_date: '', end_date: '', months: '' }

async function apiRequest<T>(
  path: string,
  token?: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const response = await fetch(`/api${path}`, { ...init, headers })
  const body: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = (body as ApiError | null)?.detail
    const message = typeof detail === 'string'
      ? detail
      : Array.isArray(detail)
        ? detail.map((item) => item.msg).filter(Boolean).join(', ')
        : `A API respondeu com erro HTTP ${response.status}.`
    throw new Error(message)
  }
  return body as T
}

function formatDate(value: string) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString('pt-BR')
}

function makeLastSevenDays(registrations: DashboardData['registrations_by_day']) {
  const values = new Map(registrations.map((item) => [item.day, item.count]))
  return Array.from({ length: 7 }, (_, index) => {
    const date = new Date()
    date.setDate(date.getDate() - (6 - index))
    const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
    return {
      label: date.toLocaleDateString('pt-BR', { weekday: 'short' }).replace('.', ''),
      count: values.get(key) ?? 0,
    }
  })
}

function App() {
  const [token, setToken] = useState<string | null>(null)
  const [operator, setOperator] = useState('')
  const [login, setLogin] = useState({ username: '', password: '' })
  const [activeTab, setActiveTab] = useState<Tab>('dashboard')
  const [notice, setNotice] = useState<Notice | null>(null)
  const [busy, setBusy] = useState(false)
  const [apiOnline, setApiOnline] = useState<boolean | null>(null)
  const [dashboard, setDashboard] = useState<DashboardData | null>(null)
  const [pendingCases, setPendingCases] = useState<ValidationCase[]>([])
  const [pendingLoading, setPendingLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [reviewNote, setReviewNote] = useState<Record<string, string>>({})
  const [fullScreenError, setFullScreenError] = useState('')
  const [insured, setInsured] = useState({
    full_name: '',
    cpf: '',
    date_of_birth: '',
    email: '',
    phone: '',
    address: '',
    municipality: '',
    registration_number: '',
    position: '',
    department: '',
    admission_date: '',
    employment_status: 'active',
    contribution_years: '',
    contribution_months: '',
  })
  const [dependents, setDependents] = useState<Dependent[]>([])
  const [employmentHistory, setEmploymentHistory] = useState<EmploymentPeriod[]>([])
  const [contributionHistory, setContributionHistory] = useState<ContributionPeriod[]>([])
  const [lastRegistered, setLastRegistered] = useState<InsuredSummary | null>(null)

  const loadDashboard = useCallback(async (quiet = false) => {
    if (!token) return
    try {
      const data = await apiRequest<DashboardData>('/analytics/dashboard', token)
      setDashboard(data)
      setApiOnline(true)
    } catch (error) {
      setApiOnline(false)
      if (!quiet) {
        setNotice({
          tone: 'error',
          text: error instanceof Error ? error.message : 'Não foi possível carregar as métricas.',
        })
      }
    }
  }, [token])

  const loadPending = useCallback(async () => {
    if (!token) return
    setPendingLoading(true)
    try {
      const result = await apiRequest<{ items: ValidationCase[]; total: number }>('/pending?case_status=pending', token)
      setPendingCases(result.items)
      setApiOnline(true)
    } catch (error) {
      setNotice({
        tone: 'error',
        text: error instanceof Error ? error.message : 'Não foi possível carregar as pendências.',
      })
    } finally {
      setPendingLoading(false)
    }
  }, [token])

  useEffect(() => {
    if (!token) return
    const initial = window.setTimeout(() => void loadDashboard(true), 0)
    const refresh = window.setInterval(() => void loadDashboard(true), 15_000)
    return () => {
      window.clearTimeout(initial)
      window.clearInterval(refresh)
    }
  }, [token, loadDashboard])

  useEffect(() => {
    if (!token || activeTab !== 'pending') return
    const initial = window.setTimeout(() => void loadPending(), 0)
    return () => window.clearTimeout(initial)
  }, [token, activeTab, loadPending])

  async function submitLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setNotice(null)
    try {
      const session = await apiRequest<UserSession>('/login', undefined, {
        method: 'POST',
        body: JSON.stringify(login),
      })
      setToken(session.access_token)
      setOperator(login.username)
      setLogin({ username: '', password: '' })
      setApiOnline(true)
      setNotice({ tone: 'success', text: 'Sessão autenticada. Bem-vindo ao Recenseamento RPPS.' })
    } catch (error) {
      setNotice({
        tone: 'error',
        text: error instanceof Error ? error.message : 'Não foi possível autenticar.',
      })
    } finally {
      setBusy(false)
    }
  }

  async function submitInsured(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!token) return
    setBusy(true)
    setNotice(null)
    const payload = {
      ...insured,
      contribution_years: insured.contribution_years === '' ? null : Number(insured.contribution_years),
      contribution_months: insured.contribution_months === '' ? null : Number(insured.contribution_months),
      dependents: dependents.filter((item) => item.name.trim()).map((item) => ({
        ...item,
        cpf: item.cpf || null,
        relationship: item.relationship || null,
        date_of_birth: item.date_of_birth || null,
      })),
      employment_history: employmentHistory.filter((item) => item.employer.trim()).map((item) => ({
        ...item,
        position: item.position || null,
        end_date: item.end_date || null,
      })),
      contribution_history: contributionHistory.filter((item) => item.institution.trim() && item.regime.trim()).map((item) => ({
        ...item,
        months: item.months === '' ? null : Number(item.months),
        end_date: item.end_date || null,
      })),
    }
    try {
      const result = await apiRequest<{ insured: InsuredSummary; validation_case: ValidationCase }>('/insured', token, {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      setLastRegistered(result.insured)
      setNotice({
        tone: result.validation_case.status === 'pending' ? 'info' : 'success',
        text: result.validation_case.issues.length
          ? `Ficha salva. ${result.validation_case.issues.length} divergência(s) enviada(s) para validação.`
          : 'Ficha salva e validada sem pendências.',
      })
      setInsured({
        full_name: '', cpf: '', date_of_birth: '', email: '', phone: '', address: '',
        municipality: '', registration_number: '', position: '', department: '',
        admission_date: '', employment_status: 'active', contribution_years: '', contribution_months: '',
      })
      setDependents([])
      setEmploymentHistory([])
      setContributionHistory([])
      void loadDashboard(true)
    } catch (error) {
      setNotice({
        tone: 'error',
        text: error instanceof Error ? error.message : 'Não foi possível registrar o segurado.',
      })
    } finally {
      setBusy(false)
    }
  }

  async function decideCase(caseId: string, decision: 'approve' | 'reject') {
    if (!token) return
    setBusy(true)
    setNotice(null)
    try {
      const result = await apiRequest<ValidationCase>(`/pending/${caseId}`, token, {
        method: 'PATCH',
        body: JSON.stringify({
          decision,
          note: reviewNote[caseId] || undefined,
        }),
      })
      setPendingCases((current) => current.filter((item) => item.id !== caseId))
      setNotice({
        tone: 'success',
        text: `Ficha de ${result.insured_name} ${decision === 'approve' ? 'aprovada' : 'rejeitada'}.`,
      })
      void loadDashboard(true)
    } catch (error) {
      setNotice({
        tone: 'error',
        text: error instanceof Error ? error.message : 'Não foi possível concluir a análise.',
      })
    } finally {
      setBusy(false)
    }
  }

  async function toggleFullscreen() {
    setFullScreenError('')
    try {
      if (document.fullscreenElement) await document.exitFullscreen()
      else await document.documentElement.requestFullscreen()
    } catch {
      setFullScreenError('Tela cheia indisponível neste navegador.')
    }
  }

  function signOut() {
    setToken(null)
    setOperator('')
    setDashboard(null)
    setPendingCases([])
    setActiveTab('dashboard')
    setNotice({ tone: 'info', text: 'Sessão encerrada.' })
  }

  const chartDays = useMemo(
    () => makeLastSevenDays(dashboard?.registrations_by_day ?? []),
    [dashboard?.registrations_by_day],
  )
  const maxDaily = Math.max(1, ...chartDays.map((day) => day.count))
  const filteredCases = useMemo(() => {
    const term = search.trim().toLocaleLowerCase('pt-BR')
    if (!term) return pendingCases
    return pendingCases.filter((item) =>
      item.insured_name.toLocaleLowerCase('pt-BR').includes(term)
      || item.insured_id.toLocaleLowerCase('pt-BR').includes(term)
      || item.issues.some((issue) => issue.toLocaleLowerCase('pt-BR').includes(term)),
    )
  }, [pendingCases, search])

  if (!token) {
    return (
      <main className="login-screen">
        <div className="login-orbit orbit-one" />
        <div className="login-orbit orbit-two" />
        <section className="login-card">
          <div className="login-brand">
            <div className="brand-logo">RP</div>
            <div><p>PLATAFORMA PREVIDENCIÁRIA</p><strong>RPPS<span>+</span></strong></div>
          </div>
          <div className="login-intro">
            <span className="login-eyebrow"><i /> AMBIENTE DE RECENSEAMENTO</span>
            <h1>Gestão previdenciária com <em>clareza.</em></h1>
            <p>Acesse o painel de recenseamento, cadastro e validação de segurados.</p>
          </div>
          {notice && <div className={`login-notice notice-${notice.tone}`} role="status">{notice.text}</div>}
          <form className="login-form" onSubmit={submitLogin}>
            <label htmlFor="login-user">Usuário</label>
            <input id="login-user" autoComplete="username" value={login.username} onChange={(event) => setLogin({ ...login, username: event.target.value })} placeholder="Seu usuário" required />
            <label htmlFor="login-password">Senha</label>
            <input id="login-password" type="password" autoComplete="current-password" value={login.password} onChange={(event) => setLogin({ ...login, password: event.target.value })} placeholder="Sua senha" required />
            <button type="submit" className="primary-button login-submit" disabled={busy}>
              {busy ? 'Autenticando…' : 'Entrar na plataforma'} <span>→</span>
            </button>
          </form>
          <footer className="login-footer"><span>SESSÃO PROTEGIDA POR JWT</span><span>RECENSEAMENTO RPPS · DEMONSTRAÇÃO</span></footer>
        </section>
      </main>
    )
  }

  const pageTitle = activeTab === 'dashboard'
    ? 'Visão geral'
    : activeTab === 'register'
      ? 'Cadastro de segurado'
      : 'Validação de pendências'

  return (
    <div className="app-layout min-h-screen text-slate-100">
      <aside className="sidebar">
        <a className="sidebar-brand" href="#" onClick={(event) => { event.preventDefault(); setActiveTab('dashboard') }}>
          <span className="brand-logo">RP</span>
          <span><strong>RPPS<span>+</span></strong><small>GESTÃO PREVIDENCIÁRIA</small></span>
        </a>
        <div className="sidebar-label">MENU PRINCIPAL</div>
        <nav className="side-nav" aria-label="Navegação principal">
          <button className={activeTab === 'dashboard' ? 'nav-item active' : 'nav-item'} onClick={() => setActiveTab('dashboard')}>
            <span className="nav-icon">◫</span> Painel inicial
          </button>
          <button className={activeTab === 'register' ? 'nav-item active' : 'nav-item'} onClick={() => setActiveTab('register')}>
            <span className="nav-icon">＋</span> Recenseamento
          </button>
          <button className={activeTab === 'pending' ? 'nav-item active' : 'nav-item'} onClick={() => setActiveTab('pending')}>
            <span className="nav-icon">⌑</span> Validação
            {(dashboard?.validation.pending ?? 0) > 0 && <span className="nav-count">{dashboard?.validation.pending}</span>}
          </button>
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-system">
            <span className={apiOnline ? 'system-indicator' : 'system-indicator disconnected'} />
            <span><strong>{apiOnline ? 'Serviços operacionais' : 'Verificando serviços'}</strong><small>Gateway RPPS · API</small></span>
          </div>
          <button className="user-profile" onClick={signOut}>
            <span className="avatar">{operator.slice(0, 2).toUpperCase()}</span>
            <span className="profile-copy"><strong>{operator}</strong><small>Operador · Sair</small></span>
            <span className="profile-more">↗</span>
          </button>
        </div>
      </aside>

      <main className="main-area">
        <header className="page-header">
          <div><p className="breadcrumb">RPPS <span>/</span> OPERAÇÕES <span>/</span> <b>{pageTitle.toUpperCase()}</b></p><h1>{pageTitle}</h1></div>
          <div className="header-actions">
            <div className={apiOnline ? 'connection-pill connected' : 'connection-pill'}>
              <i />{apiOnline ? 'SISTEMA CONECTADO' : 'CONECTANDO'}
            </div>
            <button className="header-icon" type="button" aria-label="Atualizar painel" title="Atualizar dados" onClick={() => { void loadDashboard(); if (activeTab === 'pending') void loadPending() }}>↻</button>
            <button className="header-icon" type="button" aria-label="Alternar tela cheia" title="Tela cheia" onClick={() => void toggleFullscreen()}>⛶</button>
          </div>
        </header>

        {notice && <div className={`app-notice notice-${notice.tone}`} role="status"><span>{notice.text}</span><button aria-label="Fechar" onClick={() => setNotice(null)}>×</button></div>}

        {activeTab === 'dashboard' && (
          <section className="page-content dashboard-content">
            <div className="welcome-row">
              <div><h2>Acompanhamento do recenseamento</h2><p>Indicadores consolidados a partir dos cadastros e validações registrados.</p></div>
              <span className="updated-chip"><i /> ATUALIZAÇÃO AUTOMÁTICA · 15S</span>
            </div>
            <div className="stats-grid">
              <article className="stat-card"><div className="stat-head"><span className="stat-symbol symbol-blue">◉</span><span className="stat-period">ACUMULADO</span></div><p>Segurados recenseados</p><strong>{dashboard?.total_insured ?? '—'}</strong><small>Total de fichas registradas</small></article>
              <article className="stat-card"><div className="stat-head"><span className="stat-symbol symbol-violet">↗</span><span className="stat-period">HOJE</span></div><p>Novos recenseamentos</p><strong>{dashboard?.registered_today ?? '—'}</strong><small>Cadastros realizados hoje</small></article>
              <article className="stat-card"><div className="stat-head"><span className="stat-symbol symbol-amber">⌑</span><span className="stat-period">EM ANÁLISE</span></div><p>Pendências abertas</p><strong>{dashboard?.validation.pending ?? '—'}</strong><small>Fichas aguardando revisão</small></article>
              <article className="stat-card"><div className="stat-head"><span className="stat-symbol symbol-green">✓</span><span className="stat-period">CONCLUÍDAS</span></div><p>Fichas aprovadas</p><strong>{dashboard?.validation.approved ?? '—'}</strong><small>Validadas sem divergências ou aprovadas</small></article>
            </div>

            <div className="dashboard-panels">
              <article className="surface-card progress-panel">
                <div className="card-title-row"><div><p className="eyebrow-label">VOLUME CADASTRAL</p><h3>Progresso do recenseamento</h3></div><span className="live-tag"><i /> AO VIVO</span></div>
                <div className="progress-overview"><strong>{dashboard?.total_insured ?? '—'}</strong><span>segurados recenseados</span></div>
                <div className="progress-track"><span style={{ width: `${dashboard?.progress_percent ?? 0}%` }} /></div>
                <div className="progress-meta"><span>{dashboard?.goal ? `${dashboard.progress_percent}% da meta` : 'Meta não configurada'}</span><span>{dashboard?.goal ? `Meta ${dashboard.goal.toLocaleString('pt-BR')}` : 'Configure RPPS_RECENSUS_GOAL'}</span></div>
                <div className="chart-divider" />
                <div className="card-title-row chart-heading"><div><p className="eyebrow-label">ÚLTIMOS 7 DIAS</p><h3>Novas inscrições</h3></div><span className="chart-unit">FICHAS / DIA</span></div>
                <div className="bar-chart" role="img" aria-label="Gráfico de inscrições nos últimos sete dias">
                  {chartDays.map((day) => (
                    <div className="bar-column" key={day.label}>
                      <span className="bar-value">{day.count || ''}</span>
                      <div className="bar-track"><i style={{ height: `${Math.max(day.count ? 8 : 2, (day.count / maxDaily) * 100)}%` }} /></div>
                      <span className="bar-label">{day.label}</span>
                    </div>
                  ))}
                </div>
              </article>

              <article className="surface-card quality-panel">
                <div className="card-title-row"><div><p className="eyebrow-label">SITUAÇÃO DAS FICHAS</p><h3>Validação cadastral</h3></div><button className="text-link" onClick={() => setActiveTab('pending')}>Ver pendências <span>→</span></button></div>
                <div className="validation-total"><strong>{dashboard?.validation.total ?? '—'}</strong><span>fichas avaliadas</span></div>
                <div className="validation-stack" aria-label="Distribuição das fichas por situação">
                  <span className="stack-approved" style={{ flex: dashboard?.validation.approved || 0.001 }} />
                  <span className="stack-pending" style={{ flex: dashboard?.validation.pending || 0.001 }} />
                  <span className="stack-rejected" style={{ flex: dashboard?.validation.rejected || 0.001 }} />
                </div>
                <div className="legend-list">
                  <div><i className="legend-green" /><span>Aprovadas</span><strong>{dashboard?.validation.approved ?? '—'}</strong></div>
                  <div><i className="legend-amber" /><span>Pendentes de análise</span><strong>{dashboard?.validation.pending ?? '—'}</strong></div>
                  <div><i className="legend-red" /><span>Rejeitadas</span><strong>{dashboard?.validation.rejected ?? '—'}</strong></div>
                </div>
                <div className="review-callout"><span className="callout-icon">⌑</span><span><strong>Fila de validação</strong><small>Revise divergências e registre a decisão da ficha.</small></span><button aria-label="Abrir pendências" onClick={() => setActiveTab('pending')}>→</button></div>
              </article>
            </div>
            <div className="dashboard-footnote">Os indicadores refletem apenas dados persistidos nos serviços RPPS desta instância.</div>
          </section>
        )}

        {activeTab === 'register' && (
          <section className="page-content registry-content">
            <div className="welcome-row"><div><h2>Nova ficha cadastral</h2><p>Preencha os dados pessoais, funcionais e previdenciários do segurado.</p></div><span className="required-note"><i>*</i> CAMPOS OBRIGATÓRIOS</span></div>
            {lastRegistered && <div className="registered-banner"><span className="registered-check">✓</span><span><strong>{lastRegistered.full_name}</strong><small>Ficha registrada · Matrícula {lastRegistered.registration_number || 'não informada'}</small></span><code>{lastRegistered.id.slice(0, 8).toUpperCase()}</code></div>}
            <form className="insured-form surface-card" onSubmit={submitInsured}>
              <section className="form-section">
                <div className="form-section-title"><span>01</span><div><h3>Dados pessoais</h3><p>Identificação e canais de contato</p></div></div>
                <div className="form-grid">
                  <label className="field span-2"><span>Nome completo <i>*</i></span><input autoComplete="name" value={insured.full_name} onChange={(event) => setInsured({ ...insured, full_name: event.target.value })} required maxLength={160} placeholder="Nome completo do segurado" /></label>
                  <label className="field"><span>CPF <i>*</i></span><input value={insured.cpf} onChange={(event) => setInsured({ ...insured, cpf: event.target.value })} required maxLength={14} placeholder="000.000.000-00" /></label>
                  <label className="field"><span>Data de nascimento</span><input type="date" value={insured.date_of_birth} onChange={(event) => setInsured({ ...insured, date_of_birth: event.target.value })} /></label>
                  <label className="field"><span>E-mail</span><input type="email" autoComplete="email" value={insured.email} onChange={(event) => setInsured({ ...insured, email: event.target.value })} placeholder="segurado@exemplo.gov.br" /></label>
                  <label className="field"><span>Telefone</span><input autoComplete="tel" value={insured.phone} onChange={(event) => setInsured({ ...insured, phone: event.target.value })} placeholder="(00) 00000-0000" /></label>
                  <label className="field span-2"><span>Endereço</span><input autoComplete="street-address" value={insured.address} onChange={(event) => setInsured({ ...insured, address: event.target.value })} placeholder="Rua, número, complemento e bairro" /></label>
                  <label className="field"><span>Município</span><input value={insured.municipality} onChange={(event) => setInsured({ ...insured, municipality: event.target.value })} placeholder="Município / UF" /></label>
                </div>
              </section>

              <section className="form-section">
                <div className="form-section-title"><span>02</span><div><h3>Dados funcionais</h3><p>Vínculo atual no regime próprio</p></div></div>
                <div className="form-grid">
                  <label className="field"><span>Matrícula funcional</span><input value={insured.registration_number} onChange={(event) => setInsured({ ...insured, registration_number: event.target.value })} placeholder="Ex.: 000123" /></label>
                  <label className="field"><span>Cargo</span><input value={insured.position} onChange={(event) => setInsured({ ...insured, position: event.target.value })} placeholder="Cargo efetivo" /></label>
                  <label className="field"><span>Lotação / órgão</span><input value={insured.department} onChange={(event) => setInsured({ ...insured, department: event.target.value })} placeholder="Secretaria, órgão ou unidade" /></label>
                  <label className="field"><span>Data de admissão</span><input type="date" value={insured.admission_date} onChange={(event) => setInsured({ ...insured, admission_date: event.target.value })} /></label>
                  <label className="field"><span>Situação funcional</span><select value={insured.employment_status} onChange={(event) => setInsured({ ...insured, employment_status: event.target.value })}><option value="active">Ativo</option><option value="retired">Aposentado</option><option value="pensioner">Pensionista</option><option value="leave">Afastado</option></select></label>
                  <div className="field-help">Matrícula, cargo, lotação e admissão são conferidos pelo motor de validação.</div>
                </div>
              </section>

              <section className="form-section">
                <div className="form-section-title"><span>03</span><div><h3>Tempo previdenciário</h3><p>Tempo declarado e períodos contributivos</p></div></div>
                <div className="form-grid contribution-grid">
                  <label className="field"><span>Tempo de contribuição · anos</span><input type="number" min="0" max="70" value={insured.contribution_years} onChange={(event) => setInsured({ ...insured, contribution_years: event.target.value })} placeholder="0" /></label>
                  <label className="field"><span>Meses adicionais</span><input type="number" min="0" max="11" value={insured.contribution_months} onChange={(event) => setInsured({ ...insured, contribution_months: event.target.value })} placeholder="0" /></label>
                </div>
                <div className="nested-editor">
                  <div className="nested-heading"><span>Histórico previdenciário</span><button type="button" className="add-link" onClick={() => setContributionHistory([...contributionHistory, { ...emptyContribution }])}>＋ Adicionar período</button></div>
                  {contributionHistory.map((period, index) => (
                    <div className="repeat-grid contribution-repeat" key={`contribution-${index}`}>
                      <input aria-label="Instituição previdenciária" placeholder="Instituição" value={period.institution} onChange={(event) => setContributionHistory(contributionHistory.map((item, current) => current === index ? { ...item, institution: event.target.value } : item))} />
                      <input aria-label="Regime previdenciário" placeholder="Regime" value={period.regime} onChange={(event) => setContributionHistory(contributionHistory.map((item, current) => current === index ? { ...item, regime: event.target.value } : item))} />
                      <input aria-label="Início do período previdenciário" type="date" value={period.start_date} onChange={(event) => setContributionHistory(contributionHistory.map((item, current) => current === index ? { ...item, start_date: event.target.value } : item))} />
                      <input aria-label="Fim do período previdenciário" type="date" value={period.end_date} onChange={(event) => setContributionHistory(contributionHistory.map((item, current) => current === index ? { ...item, end_date: event.target.value } : item))} />
                      <button type="button" className="remove-button" aria-label="Remover período" onClick={() => setContributionHistory(contributionHistory.filter((_, current) => current !== index))}>×</button>
                    </div>
                  ))}
                </div>
              </section>

              <section className="form-section">
                <div className="form-section-title"><span>04</span><div><h3>Dependentes e histórico funcional</h3><p>Dados complementares do cadastro</p></div></div>
                <div className="nested-editor">
                  <div className="nested-heading"><span>Dependentes</span><button type="button" className="add-link" onClick={() => setDependents([...dependents, { ...emptyDependent }])}>＋ Adicionar dependente</button></div>
                  {dependents.map((dependent, index) => (
                    <div className="repeat-grid dependent-repeat" key={`dependent-${index}`}>
                      <input aria-label="Nome do dependente" placeholder="Nome" value={dependent.name} onChange={(event) => setDependents(dependents.map((item, current) => current === index ? { ...item, name: event.target.value } : item))} />
                      <input aria-label="CPF do dependente" placeholder="CPF" value={dependent.cpf} onChange={(event) => setDependents(dependents.map((item, current) => current === index ? { ...item, cpf: event.target.value } : item))} />
                      <input aria-label="Grau de parentesco" placeholder="Parentesco" value={dependent.relationship} onChange={(event) => setDependents(dependents.map((item, current) => current === index ? { ...item, relationship: event.target.value } : item))} />
                      <input aria-label="Nascimento do dependente" type="date" value={dependent.date_of_birth} onChange={(event) => setDependents(dependents.map((item, current) => current === index ? { ...item, date_of_birth: event.target.value } : item))} />
                      <button type="button" className="remove-button" aria-label="Remover dependente" onClick={() => setDependents(dependents.filter((_, current) => current !== index))}>×</button>
                    </div>
                  ))}
                </div>
                <div className="nested-editor employment-editor">
                  <div className="nested-heading"><span>Vínculos funcionais anteriores</span><button type="button" className="add-link" onClick={() => setEmploymentHistory([...employmentHistory, { ...emptyEmployment }])}>＋ Adicionar vínculo</button></div>
                  {employmentHistory.map((period, index) => (
                    <div className="repeat-grid employment-repeat" key={`employment-${index}`}>
                      <input aria-label="Empregador anterior" placeholder="Órgão / empregador" value={period.employer} onChange={(event) => setEmploymentHistory(employmentHistory.map((item, current) => current === index ? { ...item, employer: event.target.value } : item))} />
                      <input aria-label="Cargo anterior" placeholder="Cargo" value={period.position} onChange={(event) => setEmploymentHistory(employmentHistory.map((item, current) => current === index ? { ...item, position: event.target.value } : item))} />
                      <input aria-label="Início do vínculo" type="date" value={period.start_date} onChange={(event) => setEmploymentHistory(employmentHistory.map((item, current) => current === index ? { ...item, start_date: event.target.value } : item))} />
                      <input aria-label="Fim do vínculo" type="date" value={period.end_date} onChange={(event) => setEmploymentHistory(employmentHistory.map((item, current) => current === index ? { ...item, end_date: event.target.value } : item))} />
                      <button type="button" className="remove-button" aria-label="Remover vínculo" onClick={() => setEmploymentHistory(employmentHistory.filter((_, current) => current !== index))}>×</button>
                    </div>
                  ))}
                </div>
              </section>

              <div className="form-actions"><span>Os dados serão persistidos no RPPS Core e enviados para validação.</span><button className="primary-button" type="submit" disabled={busy}>{busy ? 'Salvando ficha…' : 'Registrar segurado'} <span>→</span></button></div>
            </form>
          </section>
        )}

        {activeTab === 'pending' && (
          <section className="page-content pending-content">
            <div className="welcome-row"><div><h2>Fila de validação cadastral</h2><p>Analise inconsistências, registre uma observação e decida cada ficha.</p></div><span className="pending-total"><strong>{pendingCases.length}</strong> AGUARDANDO</span></div>
            <div className="pending-toolbar surface-card">
              <div className="filter-search"><span>⌕</span><input aria-label="Buscar pendências" placeholder="Buscar por segurado, ficha ou divergência" value={search} onChange={(event) => setSearch(event.target.value)} /></div>
              <button className="secondary-button" onClick={() => void loadPending()} disabled={pendingLoading}>{pendingLoading ? 'Atualizando…' : '↻ Atualizar fila'}</button>
            </div>
            <div className="pending-list">
              {pendingLoading && pendingCases.length === 0 && <div className="empty-panel surface-card">Carregando fichas para análise…</div>}
              {!pendingLoading && filteredCases.length === 0 && <div className="empty-panel surface-card">{search ? 'Nenhuma ficha corresponde à busca.' : 'Fila limpa. Não há fichas aguardando validação.'}</div>}
              {filteredCases.map((item) => (
                <article className="case-card surface-card" key={item.id}>
                  <div className="case-main">
                    <div className="case-avatar">{item.insured_name.trim().split(/\s+/).slice(0, 2).map((part) => part[0]).join('').toUpperCase()}</div>
                    <div className="case-info"><div className="case-title-row"><h3>{item.insured_name}</h3><span className="case-status"><i /> PENDENTE</span></div><p>Ficha <code>{item.insured_id.slice(0, 12).toUpperCase()}</code><span>·</span> Aberta em {formatDate(item.created_at)}</p></div>
                  </div>
                  <div className="issue-list">
                    {item.issues.map((issue) => <span className="issue-chip" key={issue}><i />{issue}</span>)}
                  </div>
                  <div className="case-actions">
                    <input aria-label={`Observação da ficha de ${item.insured_name}`} placeholder="Observação da análise (opcional)" value={reviewNote[item.id] ?? ''} onChange={(event) => setReviewNote({ ...reviewNote, [item.id]: event.target.value })} />
                    <button className="reject-button" onClick={() => void decideCase(item.id, 'reject')} disabled={busy}>Rejeitar</button>
                    <button className="approve-button" onClick={() => void decideCase(item.id, 'approve')} disabled={busy}>Aprovar <span>✓</span></button>
                  </div>
                </article>
              ))}
            </div>
          </section>
        )}

        <footer className="main-footer"><span>RPPS+ <i>·</i> SERVIÇOS INDEPENDENTES</span><span>{fullScreenError && <b>{fullScreenError}</b>} DADOS PESSOAIS · AMBIENTE DE DEMONSTRAÇÃO</span></footer>
      </main>
    </div>
  )
}

export default App
