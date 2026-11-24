/// <reference types="vite/client" />
import React, { createContext, useContext, useEffect, useRef, useState } from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom'
import { BookOpen, BookMarked, Search, Plus, Library, FileText, History, LogOut, ChevronRight, Check, X, Users, Settings2, LayoutGrid, LoaderCircle, MessageSquare, LockKeyhole, GraduationCap, Sparkles, CircleAlert, ChevronLeft } from 'lucide-react'
import { api, ApiError, type Course, type CourseList, type CoursePayload, type User, type Workspace } from './api'
import { CourseWorkspace, MaterialsPage } from './materials'
import './styles.css'

type AuthState = { user: User | null; refresh: () => Promise<void>; setUser: (user: User | null) => void }
const Auth = createContext<AuthState>(null!)
const message = (error: unknown) => error instanceof Error ? error.message : '操作失败，请稍后重试'

function App() {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [serviceError, setServiceError] = useState('')
  async function refresh() {
    try { setUser(await api<User>('/auth/me')); setServiceError('') }
    catch (error) {
      setUser(null)
      if (!(error instanceof ApiError && error.status === 401)) setServiceError(message(error))
    } finally { setLoading(false) }
  }
  useEffect(() => { void refresh() }, [])
  if (loading) return <div className="loading-page"><LoaderCircle className="spin" />正在打开你的学习空间</div>
  return <Auth.Provider value={{ user, setUser, refresh }}><Routes>
    <Route path="/login" element={user ? <Navigate to="/courses" replace /> : <Login initialError={serviceError} />} />
    <Route path="/*" element={user ? <Shell /> : <Navigate to="/login" replace />} />
  </Routes></Auth.Provider>
}

function Logo() { return <span className="brand"><span className="brand-icon"><BookOpen size={23} strokeWidth={1.6} /></span><span>课知<small>COURSE NOTES</small></span></span> }

function Login({ initialError }: { initialError: string }) {
  const { setUser } = useContext(Auth)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(initialError)
  const [busy, setBusy] = useState(false)
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('')
    try { setUser(await api<User>('/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) })) }
    catch (error) { setError(message(error)) } finally { setBusy(false) }
  }
  return <div className="login-page"><section className="login-story"><Logo /><div><p className="eyebrow">让学习有据可循</p><h1>每一次提问，<br />都离理解更近。</h1><p className="story-copy">把老师的课程资料与自己的学习笔记，<br />放进同一个专注的学习空间。</p><div className="story-rule" /><span className="story-caption">课程 · 资料 · 思考 · 理解</span></div><small>课知 / 课程学习助手</small></section><main className="login-form-area"><div className="login-form"><span className="label-tag">你的学习空间</span><h2>欢迎回来</h2><p className="muted">登录后，继续探索你的课程。</p><form onSubmit={submit}><label>账号<input autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} placeholder="输入学生或教师账号" required maxLength={64} /></label><label>密码<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} placeholder="输入登录密码" required maxLength={256} /></label>{error && <p role="alert" className="error"><CircleAlert size={16} />{error}</p>}<button className="button primary login-submit" disabled={busy}>{busy ? <LoaderCircle size={17} className="spin" /> : <BookOpen size={17} />}{busy ? '正在登录' : '进入学习空间'}</button></form><p className="login-note"><LockKeyhole size={15} />课程权限与个人资料彼此独立，笔记仅本人可见。</p></div></main></div>
}

function Shell() {
  const { user, setUser } = useContext(Auth)
  const currentLocation = useLocation()
  const currentSection = currentLocation.pathname.startsWith("/materials") ? "资料与笔记" : "课程"
  const [logoutError, setLogoutError] = useState('')
  async function logout() {
    try { await api('/auth/logout', { method: 'POST' }); setUser(null) }
    catch (error) { setLogoutError(message(error)) }
  }
  return <div className="app-shell"><aside className="sidebar"><Link to="/courses" className="logo-link"><Logo /></Link><p className="nav-label">学习空间</p><nav><NavLink to="/courses" end><LayoutGrid size={19} />课程中心</NavLink><NavLink to="/courses?mine=1" className={({ isActive }) => isActive && currentLocation.search.includes('mine=1') ? 'selected-mine' : ''}><BookMarked size={19} />我的课程</NavLink><NavLink to="/materials"><FileText size={19} />资料与笔记</NavLink><span className="nav-disabled"><History size={19} />问答记录<small>待开放</small></span></nav><div className="sidebar-note"><BookOpen size={23} /><p>好的理解，<br />来自有依据的思考。</p><span>让每一份资料有所连接</span></div><div className="profile"><span className="avatar">{user!.display_name[0]}</span><span>{user!.display_name}<small>{user!.role === 'TEACHER' ? '教师空间' : '学生空间'}</small></span><button className="icon-button" aria-label="退出登录" title="退出登录" onClick={logout}><LogOut size={17} /></button></div>{logoutError && <p className="error">{logoutError}</p>}</aside><div className="main-column"><header className="topbar"><span className="breadcrumb">学习空间<ChevronRight size={14} /><strong>{currentSection}</strong></span><span className="edition">课程学习助手 <span>资料管理版</span></span></header><Routes><Route path="/courses" element={<Courses />} /><Route path="/courses/:courseId" element={<CourseWorkspace user={user!} />} /><Route path="/materials" element={<MaterialsPage user={user!} />} /><Route path="*" element={<Navigate to="/courses" replace />} /></Routes></div></div>
}

function Courses() {
  const { user, refresh } = useContext(Auth)
  const location = useLocation()
  const navigate = useNavigate()
  const mine = new URLSearchParams(location.search).has('mine')
  const [result, setResult] = useState<CourseList>({ items: [], total: 0, page: 1, page_size: 24 })
  const [query, setQuery] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [pending, setPending] = useState<number | null>(null)
  const [modal, setModal] = useState<Course | 'new' | null>(null)
  const [withdrawCourse, setWithdrawCourse] = useState<Course | null>(null)
  const latestRequest = useRef(0)
  async function load() {
    const ticket = ++latestRequest.current
    setLoading(true); setError('')
    try {
      const data = await api<CourseList>(`/courses?q=${encodeURIComponent(search)}&mine=${mine}&page=${page}`)
      if (ticket === latestRequest.current) setResult(data)
    } catch (error) {
      if (ticket === latestRequest.current) setError(message(error))
      if (error instanceof ApiError && error.status === 401) await refresh()
    } finally { if (ticket === latestRequest.current) setLoading(false) }
  }
  useEffect(() => { setPage(1) }, [mine])
  useEffect(() => { void load(); return () => { latestRequest.current++ } }, [mine, search, page])
  async function enrollment(course: Course, withdraw = false) {
    setPending(course.id); setError(''); setNotice('')
    try {
      const data = await api<{ message: string }>(`/courses/${course.id}/enrollment`, { method: withdraw ? 'DELETE' : 'POST' })
      setNotice(data.message); setWithdrawCourse(null); await load()
    } catch (error) { setError(message(error)) } finally { setPending(null) }
  }
  return <main className="content"><section className="intro"><div><p className="eyebrow">你的课程，你的知识脉络</p><h1>把零散的知识，<br />连成自己的理解<span>。</span></h1><p className="intro-copy">从一门课程出发，连接教学资料、个人笔记与每一个好问题。</p><div className="intro-foot"><span className="intro-line" /><span>学有所问，问有所据</span></div></div><div className="book-art" aria-hidden="true"><div className="art-caption">A PLACE FOR<br />YOUR NEXT QUESTION.</div><div className="book book-one"><span>关系模型</span><i>RELATIONS</i></div><div className="book book-two"><span>事务与并发</span><i>TRANSACTIONS</i></div><div className="book book-three"><span>知识的连接</span><i>CONNECTIONS</i></div><div className="book-shadow" /></div></section><section className="course-section"><div className="section-heading"><div><p className="eyebrow">COURSE LIBRARY</p><h2>{mine ? '我的课程' : '发现课程'}<span className="count">{result.total}</span></h2></div>{user!.role === 'TEACHER' && <button className="button primary" onClick={() => setModal('new')}><Plus size={17} />创建课程</button>}</div><div className="course-toolbar"><div className="tabs" aria-label="课程范围"><button className={!mine ? 'active' : ''} onClick={() => navigate('/courses')}>全部课程</button><button className={mine ? 'active' : ''} onClick={() => navigate('/courses?mine=1')}>{user!.role === 'TEACHER' ? '我负责的' : '我已选修'}</button></div><form className="search" onSubmit={e => { e.preventDefault(); setPage(1); setSearch(query) }}><Search size={17} /><input aria-label="搜索课程" placeholder="搜索课程名称或编号" value={query} onChange={e => setQuery(e.target.value)} /><button type="submit">搜索</button></form></div>{notice && <div role="status" className="notice"><Check size={17} />{notice}<button className="icon-button" aria-label="关闭提示" onClick={() => setNotice('')}><X size={15} /></button></div>}{error && <div role="alert" className="error-banner"><CircleAlert size={18} />{error}<button onClick={load}>重试</button></div>}{loading ? <div className="loading-block"><LoaderCircle className="spin" />正在整理课程</div> : result.items.length === 0 ? <div className="empty-state"><Library size={34} /><h3>{search ? '没有找到匹配的课程' : '还没有课程'}</h3><p>{search ? '换一个名称或课程编号试试。' : mine ? '先去全部课程看看，找到你的下一段学习旅程。' : '教师创建课程后，会出现在这里。'}</p></div> : <div className="course-grid">{result.items.map((course, index) => <article className={`course-card theme-${index % 3}`} key={course.id}><div className="course-cover"><BookOpen size={30} strokeWidth={1.2} /><span>{course.code}</span><div className="cover-lines" /></div><div className="card-body"><div className="card-topline"><span className="teacher"><GraduationCap size={15} />{course.teachers || '授课教师待分配'}</span><span className={`status ${course.enrollment_status === 'OPEN' ? 'open' : ''}`}>{course.enrollment_status === 'OPEN' ? '开放选课' : '选课已关闭'}</span></div><h3>{course.name}</h3><p className="course-description">{course.description || '暂无课程介绍'}</p><div className="course-meta"><span><Users size={15} />{course.enrolled_count} / {course.capacity} 人</span><span><FileText size={15} />{course.document_count} 份公共资料</span></div><div className="capacity-track" aria-hidden="true"><span style={{ width: `${Math.min(100, course.enrolled_count / course.capacity * 100)}%` }} /></div><div className="card-actions">{course.can_manage || course.is_enrolled ? <><Link className="button secondary" to={`/courses/${course.id}`}><BookMarked size={16} />进入课程</Link>{course.can_manage ? <button className="icon-button manage" aria-label={`管理${course.name}`} title="管理课程" onClick={() => setModal(course)}><Settings2 size={18} /></button> : <button className="text-button" onClick={() => setWithdrawCourse(course)}>退课</button>}</> : user!.role === 'STUDENT' ? <button className="button secondary" disabled={pending === course.id || course.enrolled_count >= course.capacity || course.enrollment_status !== 'OPEN'} onClick={() => enrollment(course)}>{pending === course.id ? <LoaderCircle size={16} className="spin" /> : <Plus size={16} />}{course.enrolled_count >= course.capacity ? '名额已满' : course.enrollment_status !== 'OPEN' ? '暂未开放' : '选修课程'}</button> : <span className="muted card-readonly">其他教师负责的课程</span>}</div></div></article>)}</div>}{result.total > result.page_size && <div className="pagination"><button className="button secondary" disabled={page === 1} onClick={() => setPage(page - 1)}>上一页</button><span>第 {page} 页</span><button className="button secondary" disabled={page * result.page_size >= result.total} onClick={() => setPage(page + 1)}>下一页</button></div>}<div className="learning-note"><span className="note-icon"><LockKeyhole size={19} /></span><p><strong>属于你的思考，只对你可见。</strong><span>私人笔记仅本人可访问；课程公共资料仅对已选课学生与任课教师开放。</span></p></div></section>{modal && <CourseEditor course={modal} onClose={() => setModal(null)} onSaved={async text => { setModal(null); setNotice(text); await load() }} />}{withdrawCourse && <Dialog title="确认退出课程" onClose={() => setWithdrawCourse(null)}><p className="dialog-copy">退出「{withdrawCourse.name}」后，你将无法访问课程公共资料和课程问答。自己的笔记仍归你所有。</p><div className="dialog-actions"><button className="button secondary" onClick={() => setWithdrawCourse(null)}>继续学习</button><button className="button danger" disabled={pending !== null} onClick={() => enrollment(withdrawCourse, true)}>确认退课</button></div></Dialog>}</main>
}

function Dialog({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => { ref.current?.showModal(); const dialog = ref.current; return () => dialog?.close() }, [])
  return <dialog ref={ref} className="dialog" onCancel={event => { event.preventDefault(); onClose() }}><div className="dialog-title"><h2>{title}</h2><button className="icon-button" aria-label="关闭对话框" onClick={onClose}><X size={20} /></button></div>{children}</dialog>
}

function CourseEditor({ course, onClose, onSaved }: { course: Course | 'new'; onClose: () => void; onSaved: (text: string) => Promise<void> }) {
  const isNew = course === 'new'
  const [payload, setPayload] = useState<CoursePayload>(isNew ? { code: '', name: '', description: '', capacity: 40, enrollment_status: 'OPEN' } : { name: course.name, description: course.description, capacity: course.capacity, enrollment_status: course.enrollment_status })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState(false)
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('')
    try { await api(isNew ? '/courses' : `/courses/${course.id}`, { method: isNew ? 'POST' : 'PUT', body: JSON.stringify(payload) }); await onSaved(isNew ? '课程已创建' : '课程已更新') }
    catch (error) { setError(message(error)) } finally { setBusy(false) }
  }
  async function remove() {
    if (isNew) return
    setBusy(true); setError('')
    try { await api(`/courses/${course.id}`, { method: 'DELETE' }); await onSaved('课程已删除') }
    catch (error) { setError(message(error)) } finally { setBusy(false) }
  }
  return <Dialog title={isNew ? '创建一门新课程' : '管理课程'} onClose={() => { if (!busy) onClose() }}><form onSubmit={submit}>{isNew && <label>开课编号<input value={payload.code} required maxLength={64} pattern="[A-Za-z0-9_-]+" placeholder="如 DB-2026-FALL" onChange={e => setPayload({ ...payload, code: e.target.value })} /></label>}<label>课程名称<input value={payload.name} required maxLength={120} onChange={e => setPayload({ ...payload, name: e.target.value })} /></label><label>课程介绍<textarea rows={3} value={payload.description} maxLength={4000} onChange={e => setPayload({ ...payload, description: e.target.value })} /></label><div className="form-row"><label>课程容量<input type="number" min={1} max={10000} required value={payload.capacity} onChange={e => setPayload({ ...payload, capacity: Number(e.target.value) })} /></label><label>选课状态<select value={payload.enrollment_status} onChange={e => setPayload({ ...payload, enrollment_status: e.target.value as 'OPEN' | 'CLOSED' })}><option value="OPEN">开放选课</option><option value="CLOSED">关闭选课</option></select></label></div>{error && <p role="alert" className="error">{error}</p>}<div className="dialog-actions">{!isNew && <button type="button" className="text-button delete" disabled={busy} onClick={() => deleteConfirm ? remove() : setDeleteConfirm(true)}>{deleteConfirm ? '再次点击确认删除' : '删除空课程'}</button>}<button type="button" className="button secondary" disabled={busy} onClick={onClose}>取消</button><button className="button primary" disabled={busy}>{busy ? '正在保存' : '保存课程'}</button></div></form></Dialog>
}

const root = ReactDOM.createRoot(document.getElementById('root')!)
root.render(<React.StrictMode><BrowserRouter><App /></BrowserRouter></React.StrictMode>)

if (import.meta.hot) import.meta.hot.dispose(() => root.unmount())
