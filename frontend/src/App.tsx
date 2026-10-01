import Knowledge from './Knowledge'
import './knowledge.css'
import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import Markdown from 'react-markdown'
import { LanguageProvider, LanguageSelect, useLanguage } from './i18n'
import { api, streamAnswer } from './api'
import type { BrowserLocation, Conversation, Message, Source, User } from './api'

function Icon({name, size = 20}: {name: string; size?: number}) {
  const paths: Record<string, React.ReactNode> = {
    spark: <><path d="m12 3 2.7 6.3L21 12l-6.3 2.7L12 21l-2.7-6.3L3 12l6.3-2.7Z"/><path d="m20 2 .5 1.5L22 4l-1.5.5L20 6l-.5-1.5L18 4l1.5-.5Z"/></>,
    plus: <path d="M12 5v14M5 12h14"/>, chat: <path d="M20 11a8 8 0 0 1-8 8H5l-3 3V11a9 9 0 0 1 18 0Z"/>,
    send: <><path d="m5 11 7-7 7 7M12 4v16"/></>, trash: <><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/></>,
    logout: <><path d="M9 4H4v16h5M9 12h12m-5-5 5 5-5 5"/></>, book: <><path d="M12 5v16M12 5C8 2 4 3 2 4v15c4-2 7-1 10 2 3-3 6-4 10-2V4c-2-1-6-2-10 1Z"/></>,
    menu: <path d="M4 6h16M4 12h16M4 18h16"/>, stop: <rect x="6" y="6" width="12" height="12" rx="2"/>,
  }
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.spark}</svg>
}

function Login({onLogin}: {onLogin: (user: User) => void}) {
  const {t} = useLanguage()
  const [register, setRegister] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    setBusy(true); setError('')
    try { onLogin(await api<User>('/auth/' + (register ? 'register' : 'login'), 'POST', {username: data.get('username'), password: data.get('password')})) }
    catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }
  return <div className="auth-page"><div className="auth-language"><LanguageSelect disabled={busy}/></div>
    <section className="auth-story"><div className="brand"><span className="brand-icon"><Icon name="spark"/></span>{t('智扫通')}<span className="brand-label">AI ASSISTANT</span></div>
      <div className="story-content"><span className="eyebrow">{t('让每一次清洁，更简单')}</span><h1>{t('你的清洁问题，')}<br/>{t('交给智能助手。')}</h1><p>{t('从产品选购到日常维护，连接专业知识，')}<br/>{t('找到适合你的答案。')}</p><div className="story-card"><Icon name="chat"/><span>{t('记住每一段对话')}<br/><small>{t('随时回来，接着聊。')}</small></span></div></div>
      <small>{t('智扫通 · 扫地机器人智能客服')}</small></section>
    <section className="auth-form"><div><div className="mobile-brand">{t('✧ 智扫通')}</div><h2>{register ? t('创建你的账号') : t('欢迎回来')}</h2><p>{register ? t('开启你的专属智能对话空间') : t('登录后继续你的对话与探索')}</p>
      <form onSubmit={submit}><label>{t('用户名')}<input name="username" required minLength={3} maxLength={32} autoComplete="username" placeholder={t('3–32 位文字、数字或下划线')}/></label>
        <label>{t('密码')}<input name="password" type="password" required minLength={8} maxLength={128} autoComplete={register ? 'new-password' : 'current-password'} placeholder={t('至少 8 位密码')}/></label>
        {error && <div role="alert" className="error">{t(error)}</div>}<button className="primary" disabled={busy}>{busy ? t('正在处理…') : register ? t('注册并开始对话') : t('登录')}<span>→</span></button>
      </form><p className="switch-auth">{register ? t('已有账号？') : t('还没有账号？')}<button disabled={busy} onClick={() => {setRegister(!register); setError('')}}>{register ? t('前往登录') : t('立即注册')}</button></p></div></section>
  </div>
}

function Sources({sources}: {sources: Source[]}) {
  const {t} = useLanguage()
  if (!sources.length) return null
  return <details className="sources"><summary><Icon name="book" size={15}/> {t('参考资料')} · {sources.length} {t('个片段')}</summary><div>{sources.map((source, i) => <article key={i}><b>{source.title}</b>{source.page != null && <small> · {t('页面索引')} {source.page}</small>}<p>{source.preview}</p></article>)}</div></details>
}

const suggestions = [
  {icon: 'spark', title: '挑选合适的机器人', text: '小户型应该怎样选择扫地机器人？'},
  {icon: 'book', title: '了解维护与保养', text: '扫地机器人的滤网和滚刷应该多久清理一次？'},
  {icon: 'chat', title: '排查使用中的问题', text: '扫地机器人经常找不到充电座，应该怎样排查？'},
]
const toolLabels: Record<string,string> = {rag_summarize: '检索知识库', get_weather: '查询天气', get_user_location: '确认所在城市', get_user_id: '确认用户信息', get_current_month: '获取月份', fetch_external_data: '查询使用记录', fill_context_for_report: '准备使用报告'}

function Chat({user, onLogout, onKnowledge}: {user: User; onLogout: () => void; onKnowledge: () => void}) {
  const {t} = useLanguage()
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [active, setActive] = useState<string | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [progress, setProgress] = useState('')
  const [mobileOpen, setMobileOpen] = useState(false)
  const [location, setLocation] = useState<BrowserLocation>()
  const [locating, setLocating] = useState(false)
  const locationVersion = useRef(0)
  const controller = useRef<AbortController | null>(null)
  const bottom = useRef<HTMLDivElement>(null)
  const selection = useRef(0)
  const sending = useRef(false)
  const mounted = useRef(true)

  async function refresh() { setConversations(await api<Conversation[]>('/conversations')) }
  useEffect(() => {
    mounted.current = true
    refresh().catch(err => setError(err.message))
    return () => {mounted.current = false; controller.current?.abort()}
  }, [])
  useEffect(() => { bottom.current?.scrollIntoView({behavior: 'smooth'}) }, [messages, progress])
  useEffect(() => {
    if (!location) return
    const timer = window.setTimeout(() => setLocation(undefined), Math.max(0, (location.captured_at + 300) * 1000 - Date.now()))
    return () => window.clearTimeout(timer)
  }, [location])

  function shareLocation() {
    if (!navigator.geolocation || !window.isSecureContext) {
      setError(t('当前浏览器无法获取位置，请使用 HTTPS 或 localhost，或直接输入城市和国家。'))
      return
    }
    const version = ++locationVersion.current
    setLocating(true); setError('')
    navigator.geolocation.getCurrentPosition(position => {
      if (!mounted.current || version !== locationVersion.current) return
      setLocation({latitude: Number(position.coords.latitude.toFixed(4)), longitude: Number(position.coords.longitude.toFixed(4)), captured_at: position.timestamp / 1000})
      setLocating(false)
    }, failure => {
      if (!mounted.current || version !== locationVersion.current) return
      setLocating(false); setLocation(undefined)
      setError(failure.code === 1 ? t('未获得位置授权，你仍可直接输入城市和国家查询天气。') : failure.code === 3 ? t('获取位置超时，请重试或直接输入城市和国家。') : t('暂时无法获取设备位置，请重试或直接输入城市和国家。'))
    }, {enableHighAccuracy: false, timeout: 10000, maximumAge: 60000})
  }

  function clearLocation() {
    locationVersion.current++; setLocation(undefined); setLocating(false)
  }

  async function selectConversation(id: string) {
    if (sending.current) return
    const version = ++selection.current
    setLoading(true); setError(''); setMobileOpen(false)
    try {
      const data = await api<Conversation & {messages: Message[]}>(`/conversations/${id}`)
      if (version !== selection.current) return
      setActive(id); setMessages(data.messages); setProgress(''); setInput('')
    } catch (err) { if (version === selection.current) setError((err as Error).message) }
    finally { if (version === selection.current) setLoading(false) }
  }
  function newConversation() {
    if (sending.current) return
    selection.current++; setLoading(false); setActive(null); setMessages([]); setError(''); setInput(''); setProgress(''); setMobileOpen(false)
  }
  async function removeConversation(id: string) {
    if (busy || !window.confirm(t('确定删除这段对话及全部历史消息吗？'))) return
    try { await api(`/conversations/${id}`, 'DELETE'); if (id === active) newConversation(); await refresh() }
    catch (err) { setError((err as Error).message) }
  }
  async function send(content = input) {
    content = content.trim()
    if (!content || sending.current || loading) return
    sending.current = true
    setBusy(true); setError(''); setProgress(t('正在准备回答…'))
    controller.current = new AbortController()
    const answerId = crypto.randomUUID()
    let id = active
    let appended = false
    try {
      if (!id) { const conversation = await api<Conversation>('/conversations', 'POST'); id = conversation.id; setActive(id) }
      setInput('')
      setMessages(previous => [...previous, {id: crypto.randomUUID(), role: 'user', content, status: 'complete', sources: []}, {id: answerId, role: 'assistant', content: '', status: 'streaming', sources: []}])
      appended = true
      await streamAnswer(id, content, controller.current.signal, event => {
        if (event.type === 'token') { setProgress(''); setMessages(previous => previous.map(message => message.id === answerId ? {...message, content: message.content + event.content} : message)) }
        if (event.type === 'sources') setMessages(previous => previous.map(message => message.id === answerId ? {...message, sources: event.sources || []} : message))
        if (event.type === 'tool') setProgress(`${t(toolLabels[event.name || ''] || '工具')}: ${t(event.message || '正在处理…')}`)
        if (event.type === 'warning') setError(event.message || '')
        if (event.type === 'done') setMessages(previous => previous.map(message => message.id === answerId ? {...message, status: 'complete'} : message))
      }, location && Date.now() / 1000 - location.captured_at <= 300 ? location : undefined)
    } catch (err) {
      const aborted = (err as Error).name === 'AbortError'
      if (!aborted) setError((err as Error).message)
      if (appended) setMessages(previous => previous.map(message => message.id === answerId ? {...message, status: aborted ? 'interrupted' : 'error'} : message))
      if (!appended) setInput(content)
    } finally {
      sending.current = false
      if (mounted.current) {setBusy(false); setProgress(''); refresh().catch(err => setError(err.message))}
    }
  }

  return <div className="workspace">
    {mobileOpen && <button className="overlay" aria-label={t('关闭侧栏')} onClick={() => setMobileOpen(false)}/>}
    <aside className={'sidebar ' + (mobileOpen ? 'open' : '')}>
      <div className="brand"><span className="brand-icon"><Icon name="spark"/></span>{t('智扫通')}<span className="badge">AI</span></div>
      <button className="new-chat" disabled={busy} onClick={newConversation}><Icon name="plus"/>{t('开启新对话')}<span>＋</span></button>
      <div className="section-label">{t('历史对话')}<span>{conversations.length}</span></div>
      <nav aria-label={t('历史对话')}>{!conversations.length && <p className="empty-history">{t('你的对话会保存在这里')}</p>}{conversations.map(item => <div className={'history-row ' + (active === item.id ? 'selected' : '')} key={item.id}>
        <button disabled={busy} onClick={() => selectConversation(item.id)} title={item.title}><Icon name="chat" size={17}/><span>{item.title === '新对话' ? t('新对话') : item.title}</span></button>
        <button className="delete" disabled={busy} aria-label={t('删除对话：') + item.title} onClick={() => removeConversation(item.id)}><Icon name="trash" size={15}/></button>
      </div>)}</nav>
      <button className="knowledge-nav" disabled={busy} onClick={onKnowledge}><Icon name="book"/>{t('知识库管理')}</button><div className="sidebar-note"><Icon name="book"/><div>{t('专业知识，随时查阅')}<small>{t('选购 · 使用 · 维护 · 故障排查')}</small></div></div>
      <div className="user-panel"><span className="avatar">{user.username[0].toUpperCase()}</span><div><b>{user.username}</b><small>{t('个人对话空间')}</small></div><button className="icon-button" aria-label={t('退出登录')} disabled={busy} onClick={async () => {try {await api('/auth/logout', 'POST'); onLogout()} catch(err) {setError((err as Error).message)}}}><Icon name="logout" size={18}/></button></div>
    </aside>
    <main className="chat-main"><header><div className="header-title"><button className="icon-button mobile-menu" aria-label={t('打开历史对话')} onClick={() => setMobileOpen(true)}><Icon name="menu"/></button><b>{active ? conversations.find(item => item.id === active)?.title || t('对话') : t('智能对话')}</b><span className="header-divider"/><span>{t('你的清洁好帮手')}</span></div><div className="header-actions"><span className="assistant-tag"><i/>{t('AI 智能助手')}</span><LanguageSelect disabled={busy}/></div></header>
      <div className="scroll-area" aria-busy={loading}>
        {loading ? <div className="loading">{t('正在加载历史对话…')}</div> : !messages.length ? <section className="welcome"><div className="welcome-icon"><Icon name="spark" size={36}/></div><span className="eyebrow">HI, {user.username.toUpperCase()}</span><h1>{t('今天，有什么可以帮你？')}</h1><p>{t('关于扫地机器人的疑问，从这里开始。')}</p><div className="suggestions">{suggestions.map(item => <button key={item.title} onClick={() => send(t(item.text))} disabled={busy}><Icon name={item.icon}/><b>{t(item.title)}</b><span>{t(item.text)}</span><em>↗</em></button>)}</div><div className="welcome-foot"><span/>{t('从专业知识库中寻找答案，陪你解决实际问题')}</div></section> : <div className="messages">{messages.map(message => <article key={message.id} className={'message ' + message.role}><span className={'message-avatar ' + message.role}>{message.role === 'assistant' ? <Icon name="spark" size={19}/> : user.username[0]}</span><div className="message-body"><div className="message-name">{message.role === 'assistant' ? t('智扫通') : t('你')}{message.role === 'assistant' && <span>AI</span>}</div><div className="markdown">{message.content ? <Markdown>{message.content}</Markdown> : message.status === 'streaming' && busy ? <span className="dots">{t('正在思考')}<span>•••</span></span> : <span className="muted">{t('未生成回答')}</span>}</div><Sources sources={message.sources || []}/>{['error','interrupted'].includes(message.status) && <small className="message-status">{message.status === 'error' ? t('生成失败，可以重新发送问题') : t('已停止生成')}</small>}{message.status === 'streaming' && !busy && <small className="message-status">{t('上次生成尚未完成，请稍后重新打开对话')}</small>}</div></article>)}<div ref={bottom}/></div>}
      </div>
      <div className="composer-area"><div className="location-controls"><button type="button" disabled={busy || locating} onClick={shareLocation}>{locating ? t('正在获取位置…') : location ? t('更新当前位置') : t('使用当前位置')}</button><span role="status">{location ? t('位置已共享，5 分钟内用于所在地与天气查询') : t('可选，用于查询所在地与天气')}</span>{(location || locating) && <button type="button" onClick={clearLocation}>{locating ? t('取消定位') : t('取消共享')}</button>}</div>{progress && <div className="progress" role="status"><span className="pulse"/>{progress}</div>}{error && <div className="error chat-error" role="alert">{t(error)}<button aria-label={t('关闭提示')} onClick={() => setError('')}>×</button></div>}<form className="composer" onSubmit={event => {event.preventDefault(); send()}}><textarea value={input} onChange={event => setInput(event.target.value)} placeholder={t('发送消息，聊聊你的清洁问题…')} aria-label={t('消息内容')} maxLength={12000} rows={2} disabled={loading} onKeyDown={event => {if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {event.preventDefault(); send()}}}/><div className="composer-bottom"><span><Icon name="book" size={15}/>{t('知识库问答')}<i>·</i>{t('Shift + Enter 换行')}</span>{busy ? <button type="button" className="send-button" aria-label={t('停止生成')} onClick={() => controller.current?.abort()}><Icon name="stop"/></button> : <button className="send-button" aria-label={t('发送消息')} disabled={!input.trim() || loading}><Icon name="send"/></button>}</div></form><p className="disclaimer">{t('AI 生成的内容仅供参考，请结合产品说明书核实。')}</p></div>
    </main>
  </div>
}

function AppContent() {
  const [page, setPage] = useState('chat')
  const {t} = useLanguage()
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    api<User>('/auth/me').then(setUser).catch(() => setUser(null)).finally(() => setLoading(false))
    const expired = () => setUser(null)
    window.addEventListener('auth-expired', expired)
    return () => window.removeEventListener('auth-expired', expired)
  }, [])
  if (loading) return <div className="loading fullscreen">{t('正在打开智扫通…')}</div>
  return user ? <><div hidden={page !== 'chat'}><Chat key={user.id} user={user} onLogout={() => {setUser(null); setPage('chat')}} onKnowledge={() => setPage('knowledge')}/></div>{page === 'knowledge' && <Knowledge onBack={() => setPage('chat')}/>}</> : <Login onLogin={setUser}/>
}

export default function App() { return <LanguageProvider><AppContent/></LanguageProvider> }
