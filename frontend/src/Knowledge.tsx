import { useEffect, useRef, useState } from 'react'
import { api, checkResponse } from './api'
import { LanguageSelect, requestLanguage, useLanguage } from './i18n'
import type { Language } from './i18n'

type Entry = {name:string; size:number; updated:number; revision:string; kind:string; chunks:number}
type Detail = Entry & {content:string}
type Listing = {can_manage:boolean; documents:Entry[]}

export default function Knowledge({onBack}:{onBack:()=>void}) {
  const {language}=useLanguage()
  const tr=(zh:string,en:string)=>language==='en'?en:zh
  const [corpus,setCorpus]=useState<Language>(language)
  const [documents,setDocuments]=useState<Entry[]>([])
  const [canManage,setCanManage]=useState(false)
  const [selected,setSelected]=useState<Detail|null>(null)
  const [text,setText]=useState('')
  const [search,setSearch]=useState('')
  const [busy,setBusy]=useState(false)
  const [loading,setLoading]=useState(true)
  const [error,setError]=useState('')
  const [notice,setNotice]=useState('')
  const version=useRef(0)
  const fileInput=useRef<HTMLInputElement>(null)
  const dirty=selected!==null && text!==selected.content
  const base=`/knowledge/${corpus}`
  const detailPath=(entry:Entry)=>`${base}/document?name=${encodeURIComponent(entry.name)}`
  const discard=()=>!dirty || window.confirm(tr('有未保存的修改，确定放弃吗？','Discard your unsaved changes?'))

  useEffect(()=>{setCorpus(language)},[language])
  useEffect(()=>{
    const current=++version.current
    setLoading(true);setSelected(null);setText('');setSearch('');setError('');setNotice('')
    api<Listing>(`/knowledge/${corpus}/documents`).then(result=>{
      if(current===version.current){setDocuments(result.documents);setCanManage(result.can_manage)}
    }).catch(err=>{if(current===version.current){setDocuments([]);setCanManage(false);setError(err.message)}})
      .finally(()=>{if(current===version.current)setLoading(false)})
    return ()=>{version.current++}
  },[corpus])
  useEffect(()=>{
    if(!dirty)return
    const warn=(event:BeforeUnloadEvent)=>{event.preventDefault();event.returnValue=''}
    window.addEventListener('beforeunload',warn)
    return ()=>window.removeEventListener('beforeunload',warn)
  },[dirty])

  async function open(entry:Entry) {
    if(!discard())return
    setBusy(true);setError('');setNotice('')
    try{const data=await api<Detail>(detailPath(entry));setSelected(data);setText(data.content)}
    catch(err){setError((err as Error).message)}finally{setBusy(false)}
  }
  async function mutate(operation:()=>Promise<Detail|undefined>,success:string) {
    setBusy(true);setError('');setNotice('')
    try{
      const result=await operation()
      setSelected(result||null);setText(result?.content||'');setNotice(success)
      const listing=await api<Listing>(`${base}/documents`)
      setDocuments(listing.documents);setCanManage(listing.can_manage)
    }catch(err){setError((err as Error).message)}finally{setBusy(false)}
  }
  function save() {
    if(!selected)return
    mutate(()=>api<Detail>(detailPath(selected),'PUT',{content:text,revision:selected.revision}),tr('已保存，检索索引已更新。','Saved. The search index is up to date.'))
  }
  function remove() {
    if(!selected || !window.confirm(tr(`确定删除「${selected.name}」及其检索内容吗？系统会保留恢复备份。`,`Delete “${selected.name}” and remove it from search? A recovery backup will be retained.`)))return
    mutate(()=>api<undefined>(detailPath(selected)+`&revision=${selected.revision}`,'DELETE'),tr('文档已删除，检索内容已移除。','Document deleted and removed from search.'))
  }
  async function upload(file?:File) {
    if(fileInput.current)fileInput.current.value=''
    if(!file || !discard())return
    if(!/\.(txt|pdf)$/i.test(file.name) || file.size>10*1024*1024 || file.size===0){
      setError(tr('请选择非空的 TXT 或 PDF 文件，最大 10 MB。','Choose a nonempty TXT or PDF file, up to 10 MB.'));return
    }
    await mutate(async()=>{
      const response=await checkResponse(await fetch(`/api${base}/documents?name=${encodeURIComponent(file.name)}`,{method:'POST',credentials:'include',headers:{'Content-Type':'application/octet-stream','Accept-Language':requestLanguage()},body:file}))
      return response.json()
    },tr('上传完成，文档已加入检索。','Uploaded and added to search.'))
  }
  const visible=documents.filter(doc=>doc.name.toLowerCase().includes(search.toLowerCase()))
  return <div className="knowledge-page">
    <header className="knowledge-header"><div><button className="kb-back" disabled={busy} onClick={()=>{if(discard())onBack()}}>← {tr('返回对话','Back to chat')}</button><b>SweepWise <span>/ {tr('知识库','Knowledge')}</span></b></div><LanguageSelect disabled={busy||dirty}/></header>
    <main className="knowledge-content"><div className="knowledge-intro"><div><span className="eyebrow">{tr('知识库管理','KNOWLEDGE LIBRARY')}</span><h1>{tr('让答案，始终有据可查。','Keep every answer grounded.')}</h1><p>{tr('查看和维护产品资料，更新后自动用于后续问答。','Manage product documents. Updates are available to subsequent conversations.')}</p></div>
      <div className="kb-upload"><input ref={fileInput} id="knowledge-upload" type="file" accept=".txt,.pdf" disabled={!canManage||busy||loading} onChange={event=>upload(event.target.files?.[0])}/><button className="primary" disabled={!canManage||busy||loading} onClick={()=>fileInput.current?.click()}>＋ {tr('上传文档','Upload document')}</button><small>TXT / PDF · {tr('最大 10 MB','Up to 10 MB')}</small></div></div>
      <div className="kb-toolbar"><div className="kb-tabs" aria-label={tr('选择知识库','Select knowledge library')}>{(['zh','en'] as const).map(value=><button key={value} aria-pressed={corpus===value} disabled={busy} onClick={()=>{if(value!==corpus&&discard())setCorpus(value)}}>{value==='zh'?tr('中文知识库','Chinese library'):tr('英文知识库','English library')}</button>)}</div><span>{loading?tr('加载中…','Loading…'):`${documents.length} ${tr('份文档','documents')} · ${canManage?tr('管理员','Administrator'):tr('只读','Read only')}`}</span></div>
      {!loading&&!canManage&&<div className="kb-info">{tr('当前账号可查看资料。编辑、删除和上传需要管理员权限。','You can view documents. Editing, deleting and uploading require administrator access.')}</div>}
      {error&&<div role="alert" className="error">{error}</div>}{notice&&<div role="status" className="kb-success">✓ {notice}</div>}{busy&&<div className="kb-progress" role="status">{tr('正在处理，请稍候… 保存和上传时会同步更新检索索引。','Working… Saving and uploading also update the search index.')}</div>}
      <div className="kb-grid"><section className="kb-library" aria-label={tr('文档列表','Document list')}><div className="kb-list-head"><h2>{tr('全部文档','All documents')}</h2><input aria-label={tr('搜索文档','Search documents')} placeholder={tr('按文件名搜索…','Search by filename…')} value={search} onChange={event=>setSearch(event.target.value)}/></div><div className="kb-file-list">{loading?<p className="kb-empty">{tr('正在读取知识库…','Loading your library…')}</p>:!visible.length?<p className="kb-empty">{tr('暂无文档，上传一份资料开始使用。','No documents yet. Upload a document to get started.')}</p>:visible.map(doc=><button className={'kb-file '+(selected?.name===doc.name?'selected':'')} key={doc.name} disabled={busy} onClick={()=>open(doc)}><span className="kb-file-type">{doc.kind.toUpperCase()}</span><span><b>{doc.name}</b><small>{(doc.size/1024).toFixed(1)} KB · {doc.chunks} {tr('个检索片段','search passages')}</small></span><span className={'kb-dot '+(!doc.chunks?'pending':'')} title={doc.chunks?tr('已索引','Indexed'):tr('尚未索引','Not indexed')}/></button>)}</div></section>
        <section className="kb-editor" aria-label={tr('文档编辑器','Document editor')}>{selected?<><div className="kb-editor-head"><div><span className="eyebrow">{tr('文档内容','DOCUMENT CONTENT')}</span><h2>{selected.name}</h2><small>{dirty?tr('有未保存的修改','Unsaved changes'):tr('内容已保存','Saved')} · {new Date(selected.updated*1000).toLocaleString(language==='en'?'en-AU':'zh-CN')}</small></div>{canManage&&<button className="kb-delete" disabled={busy} onClick={remove}>{tr('删除文档','Delete document')}</button>}</div>
          {selected.kind==='pdf'&&<div className="kb-info">{tr('这里显示 PDF 提取的文字。编辑后保存为同名 TXT 并替换原检索内容；原 PDF 会保留备份。','This is text extracted from the PDF. Saving edits converts it to a TXT document and replaces its search content. The original PDF is backed up.')}</div>}
          <label className="kb-editor-label" htmlFor="knowledge-content">{tr('编辑内容','Document text')}</label><textarea id="knowledge-content" spellCheck={false} value={text} onChange={event=>setText(event.target.value)} readOnly={!canManage||busy} maxLength={1000000}/><div className="kb-editor-footer"><small>{text.length.toLocaleString()} {tr('字符','characters')}</small>{canManage&&<div><button className="kb-reset" disabled={busy||!dirty} onClick={()=>{if(discard())setText(selected.content)}}>{tr('放弃修改','Discard changes')}</button><button className="primary" disabled={busy||!dirty||!text.trim()} onClick={save}>{tr('保存并更新知识库','Save and update library')}</button></div>}</div></>:<div className="kb-placeholder"><span>▤</span><h2>{tr('选择一份资料','Select a document')}</h2><p>{tr('从左侧打开文档，查看内容或进行编辑。','Open a document from the list to view or edit its content.')}</p></div>}</section>
      </div><p className="kb-footnote">{tr('中文与英文资料分别维护，修改一侧不会自动翻译或改动另一侧。','Chinese and English libraries are maintained separately. Changes are not automatically translated into the other library.')}</p>
    </main></div>
}
