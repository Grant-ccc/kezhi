import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { BookOpen, ChevronLeft, CircleAlert, Download, FileText, FolderOpen, Library, LoaderCircle, LockKeyhole, Pencil, Plus, RefreshCw, Search, Trash2, Upload, X } from 'lucide-react'
import { api, type ChunkList, type Material, type MaterialList, type User, type Workspace } from './api'
import { RetrievalPanel } from './retrieval'

const errorMessage = (error: unknown) => error instanceof Error ? error.message : '操作失败，请稍后重试'
const byteSize = (size: number) => size < 1024 ? `${size} B` : size < 1024 * 1024 ? `${(size/1024).toFixed(1)} KB` : `${(size/1024/1024).toFixed(1)} MB`
function materialStatus(doc: Material) {
  const stage = doc.index_stage === 'EMBEDDING' ? '向量化' : '解析'
  return doc.status === 'READY' ? '可检索' : doc.status === 'PARSED' ? '已解析 · 待向量化' : doc.status === 'FAILED' ? '处理失败' : doc.index_status === 'PENDING' ? (doc.attempt_count ? '等待自动重试' : `等待${stage}`) : `正在${stage} · 第 ${doc.attempt_count} 次`
}

function Modal({ title, children, onClose }: { title:string; children:ReactNode; onClose:()=>void }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => { ref.current?.showModal(); const dialog = ref.current; return () => dialog?.close() }, [])
  return <dialog className="dialog material-dialog" ref={ref} onCancel={e=>{e.preventDefault();onClose()}}>
    <div className="dialog-title"><h2>{title}</h2><button className="icon-button" aria-label="关闭资料对话框" onClick={onClose}><X size={20}/></button></div>
    {children}
  </dialog>
}

function UploadDialog({ user, courseId, existing, onClose, onSaved }: { user:User;courseId:number;existing?:Material;onClose:()=>void;onSaved:()=>void }) {
  const [title,setTitle] = useState(existing?.title ?? '')
  const [file,setFile] = useState<File|null>(null)
  const [error,setError] = useState('')
  const [busy,setBusy] = useState(false)
  const requestKey=useRef(crypto.randomUUID())
  function changeFile(value:File|undefined) {
    setFile(value ?? null); requestKey.current=crypto.randomUUID(); setError('')
    if(value && !title) setTitle(value.name.replace(/\.[^.]+$/,''))
  }
  async function submit(event:FormEvent) {
    event.preventDefault(); if(!file || busy) return
    if(file.size>10*1024*1024) {setError('每份资料最多 10 MiB，请拆分后上传');return}
    setBusy(true);setError('')
    const form=new FormData()
    form.set('title',title.trim());form.set('file',file);form.set('request_key',requestKey.current)
    if(existing) form.set('expected_version',String(existing.content_version))
    try {
      await api(existing ? `/documents/${existing.id}/file` : `/courses/${courseId}/documents`,{method:existing?'PUT':'POST',body:form})
      onSaved()
    } catch(error) {setError(errorMessage(error))} finally {setBusy(false)}
  }
  return <Modal title={existing?'替换资料文件':user.role==='TEACHER'?'上传教学资料':'上传私人笔记'} onClose={()=>{if(!busy)onClose()}}>
    <p className="dialog-copy">{user.role==='TEACHER'?'课程公共资料仅对任课教师和有效选课学生开放。':'笔记仅本人可见，任课教师与其他同学均不可访问。'}</p>
    <form onSubmit={submit}>
      <label>资料标题<input required maxLength={255} value={title} onChange={e=>{setTitle(e.target.value);requestKey.current=crypto.randomUUID()}}/></label>
      <label className="file-drop"><Upload size={27}/><strong>{file?.name ?? '选择一份课程资料'}</strong><span>文本型 PDF、UTF-8 TXT 或 Markdown · 最大 10 MiB</span><input type="file" accept=".pdf,.txt,.md" aria-label="选择资料文件" required onChange={e=>changeFile(e.target.files?.[0])}/></label>
      <p className="material-hint">文件保存后可以下载和管理；检索索引与问答将在后续阶段开放。</p>
      {existing&&<p className="material-hint">替换后原版本将退出检索，新文件会生成新的正文版本。</p>}
      {error&&<p role="alert" className="error"><CircleAlert size={16}/>{error}</p>}
      <div className="dialog-actions"><button type="button" className="button secondary" disabled={busy} onClick={onClose}>取消</button><button className="button primary" disabled={busy||!file}>{busy?<LoaderCircle size={16} className="spin"/>:<Upload size={16}/>} {busy?'正在保存':'保存资料'}</button></div>
    </form>
  </Modal>
}

function RenameDialog({ doc,onClose,onSaved }:{doc:Material;onClose:()=>void;onSaved:()=>void}) {
  const [title,setTitle]=useState(doc.title)
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  async function submit(e:FormEvent) {e.preventDefault();setBusy(true);setError('');try{await api(`/documents/${doc.id}`,{method:'PATCH',body:JSON.stringify({title})});onSaved()}catch(error){setError(errorMessage(error))}finally{setBusy(false)}}
  return <Modal title="修改资料标题" onClose={()=>{if(!busy)onClose()}}><form onSubmit={submit}><label>资料标题<input value={title} required maxLength={255} onChange={e=>setTitle(e.target.value)}/></label>{error&&<p role="alert" className="error">{error}</p>}<div className="dialog-actions"><button type="button" className="button secondary" disabled={busy} onClick={onClose}>取消</button><button className="button primary" disabled={busy}>保存标题</button></div></form></Modal>
}

function ChunksDialog({doc,onClose}:{doc:Material;onClose:()=>void}) {
  const [page,setPage]=useState(1)
  const [data,setData]=useState<ChunkList|null>(null)
  const [error,setError]=useState('')
  useEffect(()=>{let cancelled=false;setData(null);setError('');api<ChunkList>(`/documents/${doc.id}/chunks?page=${page}`).then(value=>{if(!cancelled)setData(value)}).catch(error=>{if(!cancelled)setError(errorMessage(error))});return()=>{cancelled=true}},[doc.id,page])
  return <Modal title="查看正文分块" onClose={onClose}>
    <p className="dialog-copy">{doc.title} · 版本 {data?.content_version ?? doc.content_version}</p>
    <p className="material-hint">{data?.searchable?'当前版本已完成向量化，可供授权检索使用。课程问答尚未开放。':'当前是正文预处理结果；完成向量化后才能用于检索。'}</p>
    {error&&<p role="alert" className="error">{error}</p>}
    {!data&&!error&&<p className="material-hint">正在读取分块…</p>}
    {data&&<><p className="material-hint">共 {data.total} 个片段</p><div className="chunks-preview">{data.items.map(chunk=><section key={chunk.chunk_index} className="chunk-preview"><p>片段 {chunk.chunk_index+1}{chunk.page_number?` · 第 ${chunk.page_number} 页`:''}{chunk.section_title?` · ${chunk.section_title}`:''}</p><pre>{chunk.content}</pre></section>)}</div>{data.total>data.page_size&&<div className="pagination"><button className="button secondary" disabled={page===1} onClick={()=>setPage(page-1)}>上一页</button><span>{page}</span><button className="button secondary" disabled={page*data.page_size>=data.total} onClick={()=>setPage(page+1)}>下一页</button></div>}</>}
  </Modal>
}

export function DocumentCollection({user,courseId,compact=false}:{user:User;courseId?:number;compact?:boolean}) {
  const [result,setResult]=useState<MaterialList>({items:[],total:0,page:1,page_size:24})
  const [page,setPage]=useState(1)
  const [query,setQuery]=useState('')
  const [search,setSearch]=useState('')
  const [kind,setKind]=useState<'ALL'|'TEACHING'|'NOTE'>('ALL')
  const [loading,setLoading]=useState(true)
  const [error,setError]=useState('')
  const [notice,setNotice]=useState('')
  const [upload,setUpload]=useState<true|Material|null>(null)
  const [rename,setRename]=useState<Material|null>(null)
  const [deleting,setDeleting]=useState<Material|null>(null)
  const [busy,setBusy]=useState(false)
  const [preview,setPreview]=useState<{title:string;content:string}|null>(null)
  const [chunks,setChunks]=useState<Material|null>(null)
  const [retrying,setRetrying]=useState<number|null>(null)
  const serial=useRef(0)
  async function load(quiet=false) {
    const ticket=++serial.current;if(!quiet)setLoading(true);setError('')
    try {const data=await api<MaterialList>(`/documents?page=${page}&q=${encodeURIComponent(search)}${courseId?`&course_id=${courseId}`:''}${kind==='ALL'?'':`&kind=${kind}`}`);if(ticket===serial.current)setResult(data)}catch(error){if(ticket===serial.current)setError(errorMessage(error))}finally{if(ticket===serial.current)setLoading(false)}
  }
  useEffect(()=>{setPage(1);setKind('ALL')},[courseId])
  useEffect(()=>{void load();return()=>{serial.current++}},[courseId,page,search,kind])
  useEffect(()=>{if(!result.items.some(doc=>doc.status==='PROCESSING'))return;const timer=setInterval(()=>{void load(true)},3000);return()=>clearInterval(timer)},[result.items,courseId,page,search,kind])
  async function saved() {setUpload(null);setRename(null);setNotice('资料已保存');await load()}
  async function remove() {
    if(!deleting)return;setBusy(true);setError('')
    try{await api(`/documents/${deleting.id}`,{method:'DELETE'});setDeleting(null);setNotice('资料已删除，访问权限已撤销');await load()}catch(error){setError(errorMessage(error));setDeleting(null)}finally{setBusy(false)}
  }
  async function openPreview(doc:Material) {
    setError('')
    try{setPreview(await api<{title:string;content:string}>(`/documents/${doc.id}/preview`))}catch(error){setError(errorMessage(error))}
  }
  async function retry(doc:Material) {
    setRetrying(doc.id);setError('')
    try{await api(`/documents/${doc.id}/retry`,{method:'POST',body:JSON.stringify({expected_version:doc.content_version,expected_retry_round:doc.retry_round})});setNotice('已重新加入处理队列');await load()}catch(error){setError(errorMessage(error))}finally{setRetrying(null)}
  }
  const visible=result.items
  return <div className={compact?'document-collection compact':'document-collection'}>
    <div className="material-toolbar"><span className="material-count">{result.total} 份资料</span><button className="icon-button" aria-label="刷新资料列表" onClick={()=>load()}><RefreshCw size={16}/></button>{courseId&&<button className="button primary" onClick={()=>setUpload(true)}><Plus size={16}/>{compact?'上传':user.role==='TEACHER'?'上传教学资料':'上传私人笔记'}</button>}</div>
    {courseId&&user.role==='STUDENT'&&<div className="material-filters"><button className={kind==='ALL'?'active':''} onClick={()=>{setPage(1);setKind('ALL')}}>全部</button><button className={kind==='TEACHING'?'active':''} onClick={()=>{setPage(1);setKind('TEACHING')}}>教师资料</button><button className={kind==='NOTE'?'active':''} onClick={()=>{setPage(1);setKind('NOTE')}}>我的笔记</button></div>}
    {!compact&&<form className="search material-search" onSubmit={e=>{e.preventDefault();setPage(1);setSearch(query)}}><Search size={17}/><input aria-label="搜索资料" value={query} placeholder="按资料标题搜索" onChange={e=>setQuery(e.target.value)}/><button type="submit">搜索</button></form>}
    {notice&&<p role="status" className="notice">{notice}<button className="icon-button" aria-label="关闭资料提示" onClick={()=>setNotice('')}><X size={15}/></button></p>}
    {error&&<p role="alert" className="error-banner"><CircleAlert size={17}/>{error}</p>}
    {loading?<div className="loading-block"><LoaderCircle className="spin"/>正在读取资料</div>:visible.length===0?<div className="small-empty"><FolderOpen size={32}/><p>{kind==='TEACHING'?'还没有教师资料':kind==='NOTE'?'还没有私人笔记':'这里还没有资料'}</p><small>{courseId?'上传第一份资料，开始积累你的课程知识。':'在课程工作台上传后，你的资料会出现在这里。'}</small>{!courseId&&<Link className="button secondary" to="/courses">去课程中心</Link>}</div>:<div className="material-list">{visible.map(doc=><article className="material-card" key={doc.id}><div className={`file-emblem ${doc.kind==='NOTE'?'private':''}`}><FileText size={23}/></div><div className="material-body"><div className="material-heading"><h3>{doc.title}</h3><span className={`material-kind ${doc.kind==='NOTE'?'private':''}`}>{doc.kind==='NOTE'?<><LockKeyhole size={12}/>仅本人</>:'课程公共'}</span></div><p>{doc.course_name} · {doc.original_filename}</p><div className="material-meta"><span>{byteSize(doc.size_bytes)}</span><span>版本 {doc.content_version}</span><span>{materialStatus(doc)}</span>{doc.chunk_count>0&&<span>{doc.chunk_count} 个片段</span>}</div>{doc.processing_error&&<p className="processing-error">{doc.processing_error}</p>}<div className="material-actions"><a className="material-action" href={`/api/v1/documents/${doc.id}/download`}><Download size={14}/>下载</a>{doc.media_type!=='application/pdf'&&<button className="material-action" onClick={()=>openPreview(doc)}><BookOpen size={14}/>查看正文</button>}{(doc.status==='PARSED'||doc.status==='READY')&&<button className="material-action" onClick={()=>setChunks(doc)}><Library size={14}/>查看分块</button>}{doc.can_retry&&<button className="material-action" disabled={retrying===doc.id} onClick={()=>retry(doc)}><RefreshCw size={14}/>重试处理</button>}{doc.can_manage&&<><button className="material-action" onClick={()=>setRename(doc)}><Pencil size={14}/>改名</button>{doc.can_replace&&<button className="material-action" onClick={()=>setUpload(doc)}><RefreshCw size={14}/>替换</button>}<button className="material-action delete" onClick={()=>setDeleting(doc)}><Trash2 size={14}/>删除</button></>}</div></div></article>)}</div>}
    {result.total>result.page_size&&<div className="pagination"><button className="button secondary" disabled={page===1} onClick={()=>setPage(page-1)}>上一页</button><span>{page}</span><button className="button secondary" disabled={page*result.page_size>=result.total} onClick={()=>setPage(page+1)}>下一页</button></div>}
    <p className="material-hint"><LockKeyhole size={14}/>文件访问由服务端校验。课程问答尚未开放。</p>
    {upload&&<UploadDialog user={user} courseId={upload===true?courseId!:upload.course_id} existing={upload===true?undefined:upload} onClose={()=>setUpload(null)} onSaved={saved}/>}
    {rename&&<RenameDialog doc={rename} onClose={()=>setRename(null)} onSaved={saved}/>}
    {deleting&&<Modal title="删除这份资料？" onClose={()=>{if(!busy)setDeleting(null)}}><p className="dialog-copy">「{deleting.title}」将从资料列表移除，文件下载与后续检索访问会立即停止。</p><div className="dialog-actions"><button className="button secondary" disabled={busy} onClick={()=>setDeleting(null)}>保留资料</button><button className="button danger" disabled={busy} onClick={remove}>确认删除</button></div></Modal>}
    {preview&&<Modal title={preview.title} onClose={()=>setPreview(null)}><pre className="document-preview">{preview.content}</pre></Modal>}
    {chunks&&<ChunksDialog doc={chunks} onClose={()=>setChunks(null)}/>}
  </div>
}

export function MaterialsPage({user}:{user:User}) {
  return <main className="content materials-page"><div className="materials-page-title"><p className="eyebrow">MY MATERIALS</p><h1>让每一份资料，<br/>成为理解的起点。</h1><p>{user.role==='STUDENT'?'这里只展示你自己的笔记。退课后，仍可查看、下载、改名和删除已有笔记。':'在这里管理你上传的课程公共资料，也可以进入课程工作台查看课程内资料。'}</p></div><DocumentCollection user={user}/></main>
}

export function CourseWorkspace({user}:{user:User}) {
  const {courseId}=useParams()
  const [data,setData]=useState<Workspace|null>(null)
  const [error,setError]=useState('')
  useEffect(()=>{let cancelled=false;setData(null);setError('');api<Workspace>(`/courses/${courseId}/workspace`).then(value=>{if(!cancelled)setData(value)}).catch(error=>{if(!cancelled)setError(errorMessage(error))});return()=>{cancelled=true}},[courseId])
  if(error)return <main className="content"><Link className="back-link" to="/courses"><ChevronLeft size={17}/>返回课程中心</Link><div role="alert" className="error-banner">{error}</div></main>
  if(!data)return <div className="loading-block"><LoaderCircle className="spin"/>正在打开课程</div>
  return <main className="content workspace-content"><Link className="back-link" to="/courses"><ChevronLeft size={17}/>返回课程中心</Link><div className="workspace-title"><div><p className="eyebrow">{data.course.code}</p><h1>{data.course.name}</h1></div><span className="label-tag">课程工作台</span></div><div className="workspace materials-workspace"><section className="materials-pane"><h2><Library size={18}/>课程资料</h2><DocumentCollection user={user} courseId={Number(courseId)} compact/></section><RetrievalPanel key={courseId} courseId={Number(courseId)}/></div></main>
}
