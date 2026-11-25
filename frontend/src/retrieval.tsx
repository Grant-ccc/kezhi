import { useEffect, useRef, useState, type FormEvent } from 'react'
import { BookOpen, Download, FileSearch, LoaderCircle, LockKeyhole, Search } from 'lucide-react'
import { api, type SearchResult } from './api'

const failureMessage = (code:string|null) => code==='RETRIEVAL_TIMEOUT'?'本次检索已超时，请重新发起检索。':code==='EMBEDDING_RATE_LIMITED'?'模型服务暂时繁忙，请稍后重新检索。':'本次检索未能完成，请稍后重新检索。'

export function RetrievalPanel({courseId}:{courseId:number}) {
  const [question,setQuestion]=useState('')
  const [result,setResult]=useState<SearchResult|null>(null)
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const [selected,setSelected]=useState<number|null>(null)
  const identity=useRef<{question:string;key:string}|null>(null)
  const generation=useRef(0)
  useEffect(()=>()=>{generation.current++},[])
  async function retrieve(fresh=false) {
    const text=question.trim()
    if(!text||busy)return
    const ticket=++generation.current
    if(fresh||identity.current?.question!==text)identity.current={question:text,key:crypto.randomUUID()}
    const request=identity.current!
    setBusy(true);setError('');setResult(null);setSelected(null)
    try {
      let data=await api<SearchResult>(`/courses/${courseId}/search`,{method:'POST',body:JSON.stringify({question:text,request_key:request.key})})
      const started=Date.now()
      while(ticket===generation.current) {
        setResult(data)
        if(data.status!=='PROCESSING')break
        if(Date.now()-started>90000)throw new Error('检索仍在处理中，请点击“查看结果”继续查询状态。')
        await new Promise(resolve=>window.setTimeout(resolve,1500))
        if(ticket!==generation.current)return
        data=await api<SearchResult>(`/searches/${data.id}`)
      }
    } catch(error) {
      if(ticket===generation.current){setResult(null);setError(error instanceof Error?error.message:'检索失败，请稍后再试')}
    } finally {if(ticket===generation.current)setBusy(false)}
  }
  function submit(event:FormEvent){event.preventDefault();void retrieve()}
  const active=result?.items.find(item=>item.chunk_id===selected)??result?.items[0]
  return <>
    <section className="question-pane retrieval-pane">
      <div className="question-header"><FileSearch size={18}/><span>资料检索</span><span className="muted">回答功能准备中</span></div>
      <div className="retrieval-body" aria-live="polite">
        {busy&&<p className="retrieval-progress" role="status"><LoaderCircle size={17} className="spin"/>正在查找你有权访问的资料…</p>}
        {error&&<p role="alert" className="error-banner">{error}</p>}
        {!result&&!busy&&!error&&<div className="question-empty"><span className="question-symbol"><Search size={29}/></span><h2>先找到资料，<br/>再理解问题。</h2><p>输入课程问题，查找老师的讲义与自己的笔记。<br/>这里只展示原文片段，尚不生成答案。</p></div>}
        {result?.status==='FAILED'&&<p role="alert" className="error-banner">{failureMessage(result.error_code)}</p>}
        {result?.status==='SUCCEEDED'&&<><p className="retrieval-notice">{result.notice}</p>{result.items.length>0&&<div className="retrieval-results">{result.items.map(item=><button type="button" key={item.chunk_id} aria-pressed={active?.chunk_id===item.chunk_id} className={`retrieval-hit ${active?.chunk_id===item.chunk_id?'selected':''}`} onClick={()=>setSelected(item.chunk_id)}><span className="retrieval-rank">{String(item.rank).padStart(2,'0')}</span><div><strong>{item.title}</strong><small>{item.kind==='NOTE'?'我的私人笔记':'课程公共资料'} · 版本 {item.content_version}{item.page_number?` · 第 ${item.page_number} 页`:''}</small><p>{item.content.slice(0,160)}{item.content.length>160?'…':''}</p></div></button>)}</div>}</>}
      </div>
      <form className="question-input retrieval-form" onSubmit={submit}>
        <label htmlFor={`course-search-${courseId}`}>你的课程问题</label>
        <textarea id={`course-search-${courseId}`} aria-label="课程检索问题" placeholder="例如：为什么转账需要放在同一个事务中？" maxLength={600} disabled={busy} value={question} onChange={e=>{setQuestion(e.target.value);identity.current=null;setResult(null);setError('')}}/>
        <div className="retrieval-controls"><small>{question.length}/600</small>{result&&result.status!=='PROCESSING'&&<button type="button" className="button secondary" disabled={busy} onClick={()=>void retrieve(true)}>重新检索</button>}<button className="button primary" disabled={busy||!question.trim()}>{busy?<LoaderCircle size={15} className="spin"/>:<Search size={15}/>} {identity.current?'查看结果':'查找资料'}</button></div>
        <span><LockKeyhole size={13}/>仅检索当前可访问的已向量化资料</span>
      </form>
    </section>
    <section className="citations-pane retrieval-source"><h2><BookOpen size={18}/>片段原文</h2>{active?<><p className="retrieval-source-title">{active.title}</p><p className="material-hint">片段 {active.chunk_index+1}{active.section_title?` · ${active.section_title}`:''}</p><pre className="retrieval-source-text">{active.content}</pre><a className="material-action" href={`/api/v1/documents/${active.document_id}/download`}><Download size={14}/>下载原资料</a></>:<div className="small-empty"><BookOpen size={30}/><p>让结果可以核对</p><small>检索后选择一个片段，在这里查看原文。</small></div>}</section>
  </>
}
