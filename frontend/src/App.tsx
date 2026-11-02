import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Activity, AlertCircle, ArrowLeft, ArrowUpRight, Check, CheckCircle2, ChevronRight, CircleHelp, Clock3, FileCheck2, FileText, FolderOpen, GraduationCap, Info, LoaderCircle, LockKeyhole, Menu, MoreHorizontal, Plus, RefreshCw, Search, Settings2, ShieldCheck, Sparkles, Trash2, UploadCloud, X } from 'lucide-react';

type AnyRecord = Record<string, any>;
type Doc = AnyRecord & { id: string; filename?: string; original_filename?: string; name?: string; status?: string; document_type?: string; created_at?: string; findings?: AnyRecord[] };
const API = '/api';
const API_TOKEN = import.meta.env.VITE_DOCVERIFY_API_TOKEN as string | undefined;
async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const headers = new Headers(options?.headers);
  if (API_TOKEN) headers.set('Authorization', `Bearer ${API_TOKEN}`);
  const response = await fetch(`${API}${path}`, { ...options, headers });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try { const body = await response.json(); message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body); } catch { /* use status message */ }
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
const idOf = (doc: Doc) => String(doc.id ?? doc.document_id ?? '');
const titleOf = (doc: Doc) => doc.original_filename ?? doc.filename ?? doc.name ?? 'Untitled document';
const statusOf = (doc: Doc) => String(doc.status ?? doc.verification_status ?? doc.processing_status ?? 'uploaded').toLowerCase();
const nice = (v: unknown) => String(v ?? '').replaceAll('_', ' ').replace(/\b\w/g, s => s.toUpperCase());
const dateOf = (value?: string) => value ? new Date(value).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : 'Date unavailable';
const arr = (value: unknown): AnyRecord[] => Array.isArray(value) ? value : [];

function App() {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [types, setTypes] = useState<AnyRecord[]>([]);
  const [health, setHealth] = useState<'loading'|'online'|'offline'>('loading');
  const [page, setPage] = useState<'overview'|'documents'|'review'|'settings'>('overview');
  const [selected, setSelected] = useState<string|null>(null);
  const [report, setReport] = useState<AnyRecord|null>(null);
  const [loadingReport, setLoadingReport] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [query, setQuery] = useState('');
  const [demoConsent, setDemoConsent] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      const [healthResult, listResult, typeResult] = await Promise.allSettled([
        request<AnyRecord>('/health'), request<AnyRecord>('/documents'), request<AnyRecord>('/document-types')
      ]);
      if (healthResult.status === 'fulfilled') setHealth('online'); else setHealth('offline');
      if (listResult.status === 'fulfilled') {
        const result = listResult.value;
        setDocs((Array.isArray(result) ? result : result.documents ?? result.items ?? []).filter(Boolean));
      } else setError('Could not load your document list. Check that the local service is running and try again.');
      if (typeResult.status === 'fulfilled') {
        const result = typeResult.value;
        setTypes(Array.isArray(result) ? result : result.document_types ?? result.types ?? []);
      }
    } catch { setHealth('offline'); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => { if (!notice) return; const t = window.setTimeout(() => setNotice(''), 4500); return () => clearTimeout(t); }, [notice]);

  const activeDoc = docs.find(d => idOf(d) === selected) ?? null;
  const filteredDocs = useMemo(() => docs.filter(d => `${titleOf(d)} ${d.document_type ?? ''} ${d.status ?? ''}`.toLowerCase().includes(query.toLowerCase())), [docs, query]);
  const reviewDocs = docs.filter(d => ['needs_review','review_required','pending_review','pending'].includes(statusOf(d)));
  const chooseDoc = async (id: string) => {
    setSelected(id); setReport(null); setLoadingReport(true); setError(''); setDemoConsent(false);
    try { setReport(await request<AnyRecord>(`/documents/${encodeURIComponent(id)}/report`)); }
    catch (e) { const message = e instanceof Error ? e.message : 'Could not load this report.'; if (!message.toLowerCase().includes('verification has not been run')) setError(message); }
    finally { setLoadingReport(false); }
  };
  const upload = async (file?: File) => {
    if (!file) return;
    setError(''); setBusy(true);
    try {
      const form = new FormData(); form.append('file', file);
      const added = await request<Doc>('/documents', { method: 'POST', body: form });
      const id = idOf(added);
      await refresh(); setPage('documents');
      if (id) await chooseDoc(id);
      else setNotice('Upload received. Refreshing the document list…');
      setNotice('Document added. Review the extracted details before using the report.');
    } catch (e) { setError(e instanceof Error ? e.message : 'Upload failed. Please try again.'); }
    finally { setBusy(false); if (inputRef.current) inputRef.current.value = ''; }
  };
  const verify = async () => {
    if (!selected || !demoConsent) return;
    setBusy(true); setError('');
    try {
      await request(`/documents/${encodeURIComponent(selected)}/verify`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ demo_consent: true }) });
      try { setReport(await request<AnyRecord>(`/documents/${encodeURIComponent(selected)}/report`)); } catch { /* a reviewer may record a decision before verification exists */ }
      await refresh(); setNotice('Demo issuer check completed. Its result is simulated.');
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not run the issuer check.'); }
    finally { setBusy(false); }
  };
  const submitReview = async (decision: string, note: string, corrections: AnyRecord) => {
    if (!selected) return;
    setBusy(true); setError('');
    try {
      await request(`/documents/${encodeURIComponent(selected)}/review`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ decision, note, corrections }) });
      setReport(await request<AnyRecord>(`/documents/${encodeURIComponent(selected)}/report`));
      await refresh(); setNotice('Your review decision was recorded.');
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not record the review.'); }
    finally { setBusy(false); }
  };
  const removeDoc = async (id: string) => {
    if (!window.confirm('Delete this document and its local case data? This cannot be undone.')) return;
    setBusy(true); setError('');
    try { await request(`/documents/${encodeURIComponent(id)}`, { method: 'DELETE' }); setSelected(null); setReport(null); await refresh(); setNotice('Document deleted.'); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not delete this document.'); }
    finally { setBusy(false); }
  };

  const nav = <><button className={`nav-link ${page === 'overview' ? 'active' : ''}`} onClick={() => { setPage('overview'); setSelected(null); }}><Activity size={17}/>Overview</button><button className={`nav-link ${page === 'documents' ? 'active' : ''}`} onClick={() => { setPage('documents'); setSelected(null); }}><FolderOpen size={17}/>Documents<span className="nav-count">{docs.length}</span></button><button className={`nav-link ${page === 'review' ? 'active' : ''}`} onClick={() => { setPage('review'); setSelected(null); }}><FileCheck2 size={17}/>Review queue{reviewDocs.length > 0 && <span className="nav-count amber-count">{reviewDocs.length}</span>}</button><button className={`nav-link ${page === 'settings' ? 'active' : ''}`} onClick={() => { setPage('settings'); setSelected(null); }}><Settings2 size={17}/>Settings</button></>;

  return <div className="app-shell">
    <aside className={`sidebar ${mobileNav ? 'mobile-open' : ''}`}>
      <div className="brand"><div className="brand-mark"><ShieldCheck size={19}/></div><div><strong>docverify<span>ai</span></strong><small>DOCUMENT WORKSPACE</small></div><button className="icon-button mobile-close" aria-label="Close navigation" onClick={() => setMobileNav(false)}><X size={18}/></button></div>
      <div className="workspace-label">WORKSPACE</div><nav aria-label="Main navigation">{nav}</nav>
      <div className="sidebar-spacer"/><div className="privacy-card"><div className="privacy-icon"><LockKeyhole size={16}/></div><div><strong>Private by design</strong><p>Files stay on this device and are never shared publicly.</p></div></div>
      <div className="sidebar-bottom"><div className={`service-state ${health}`}><span className="state-dot"/>{health === 'loading' ? 'Connecting to local service' : health === 'online' ? 'Local service connected' : 'Local service unavailable'}</div><div className="profile"><div className="avatar">R</div><div className="profile-meta"><strong>Reviewer workspace</strong><span>Local session</span></div><MoreHorizontal size={17} className="muted"/></div></div>
    </aside>
    {mobileNav && <button className="nav-scrim" onClick={() => setMobileNav(false)} aria-label="Close navigation"/>}
    <main className="main-area">
      <header className="topbar"><button className="icon-button menu-button" aria-label="Open navigation" onClick={() => setMobileNav(true)}><Menu size={19}/></button><div className="crumb">{selected ? 'Document report' : page === 'overview' ? 'Your workspace' : page === 'documents' ? 'Your documents' : page === 'review' ? 'Ready for your review' : 'Settings'}</div><div className="top-actions"><span className="local-pill"><span className="state-dot"/>Saved on this device</span><button className="icon-button help-button" aria-label="Help" title="Help and limitations" onClick={() => { setPage('settings'); setSelected(null); setReport(null); }}><CircleHelp size={18}/></button></div></header>
      <div className="page-content">
        {error && <div className="alert error-alert" role="alert"><AlertCircle size={17}/><span>{error}</span><button className="alert-close" onClick={() => setError('')} aria-label="Dismiss error"><X size={16}/></button></div>}
        {notice && <div className="alert success-alert" role="status"><CheckCircle2 size={17}/><span>{notice}</span><button className="alert-close" onClick={() => setNotice('')} aria-label="Dismiss message"><X size={16}/></button></div>}
        {selected && activeDoc ? <ReportView key={selected} doc={activeDoc} report={report} loading={loadingReport} demoConsent={demoConsent} setDemoConsent={setDemoConsent} busy={busy} onBack={() => { setSelected(null); setReport(null); }} onVerify={verify} onDelete={() => removeDoc(selected)} onReview={submitReview} onRefresh={() => void chooseDoc(selected)}/> : page === 'overview' ? <Overview docs={docs} reviewDocs={reviewDocs} busy={busy} inputRef={inputRef} onUpload={upload} onOpen={chooseDoc} onBrowse={() => { setPage('documents'); setSelected(null); }}/>: page === 'documents' ? <Documents docs={filteredDocs} query={query} setQuery={setQuery} busy={busy} onOpen={chooseDoc} onUpload={() => inputRef.current?.click()} onDelete={removeDoc}/> : page === 'review' ? <ReviewQueue docs={reviewDocs} onOpen={chooseDoc} onUpload={() => inputRef.current?.click()}/> : <SettingsPage types={types} health={health}/>}
        <input ref={inputRef} type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,application/pdf,image/png,image/jpeg,image/webp" className="visually-hidden" onChange={e => void upload(e.target.files?.[0])} aria-label="Choose a document to upload"/>
      </div>
    </main>
  </div>;
}

function Overview({docs,reviewDocs,busy,inputRef,onUpload,onOpen,onBrowse}: {docs:Doc[];reviewDocs:Doc[];busy:boolean;inputRef:React.RefObject<HTMLInputElement|null>;onUpload:(file?:File)=>void;onOpen:(id:string)=>void;onBrowse:()=>void}) {
  return <>
    <div className="welcome-row"><div><div className="eyebrow">DOCUMENT REVIEW</div><h1>Check a student document in 3 steps.</h1><p className="page-subtitle">1. Upload a marksheet or certificate   2. See the result   3. Save your decision.</p></div><button className="primary-button" onClick={() => inputRef.current?.click()} disabled={busy}><Plus size={17}/>{busy ? 'Uploading…' : 'Add a document'}</button></div>
    <section className="hero-panel" onDragOver={e=>{e.preventDefault();e.currentTarget.classList.add('drag-active');}} onDragLeave={e=>e.currentTarget.classList.remove('drag-active')} onDrop={e=>{e.preventDefault();e.currentTarget.classList.remove('drag-active');onUpload(e.dataTransfer.files?.[0]);}}><div className="hero-copy"><div className="hero-kicker"><Sparkles size={14}/> A little help with the paperwork</div><h2>Upload a document to get a clear result.</h2><p>You will see: Looks consistent, Needs a closer look, or Could not read, with the reasons.</p><label className="upload-button" htmlFor="hero-file"><UploadCloud size={17}/><span>Choose a document</span><small>PDF or image · stays on this device</small></label><input id="hero-file" type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,application/pdf,image/png,image/jpeg,image/webp" className="visually-hidden" onChange={e => onUpload(e.target.files?.[0])}/><div className="hero-feature-strip"><span><ShieldCheck size={13}/> Stays on this device</span><span><FileCheck2 size={13}/> See supporting evidence</span><span><CheckCircle2 size={13}/> You make the decision</span></div></div><div className="hero-art" aria-hidden="true"><div className="orb orb-one"/><div className="orb orb-two"/><div className="paper-card"><div className="paper-top"><div className="paper-emblem"><GraduationCap size={19}/></div><span>ACADEMIC RECORD</span><MoreHorizontal size={16}/></div><div className="paper-title-line"/><div className="paper-label">Student name</div><div className="paper-text-line w80"/><div className="paper-grid"><div><div className="paper-label">Institution</div><div className="paper-text-line"/></div><div><div className="paper-label">Year</div><div className="paper-text-line w60"/></div></div><div className="paper-bottom"><span className="mini-check"><Check size={12}/></span><span>Evidence-linked fields</span></div></div><div className="evidence-tag"><span className="tag-dot"/> Review with care</div></div></section>
    <div className="section-heading-row"><div><h2 className="section-title">Your workspace</h2><p className="section-note">A live view of documents saved in this local workspace.</p></div><button className="text-button" onClick={onBrowse}>View all documents <ArrowUpRight size={15}/></button></div>
    <div className="summary-grid"><SummaryCard icon={<FileText size={17}/>} label="Documents" value={String(docs.length)} note={docs.length === 1 ? '1 document in this workspace' : `${docs.length} documents in this workspace`} tone="blue"/><SummaryCard icon={<Clock3 size={17}/>} label="Awaiting review" value={String(reviewDocs.length)} note="Based on current document statuses" tone="amber"/><SummaryCard icon={<ShieldCheck size={17}/>} label="Service status" value="Local" note="Documents are processed on this device" tone="green"/></div>
    <section className="recent-section"><div className="section-heading-row compact"><div><h2 className="section-title">Recently added</h2><p className="section-note">Open a document to see its current report.</p></div><button className="text-button" onClick={onBrowse}>All documents <ChevronRight size={15}/></button></div>{docs.length ? <div className="document-list">{docs.slice(0,4).map(doc => <DocumentRow key={idOf(doc)} doc={doc} onClick={() => onOpen(idOf(doc))}/>)}</div> : <div className="empty-state compact-empty"><div className="empty-icon"><FolderOpen size={21}/></div><strong>No documents yet</strong><p>Add a document to start a review. Your list will reflect records returned by the local service.</p><button className="secondary-button" onClick={() => inputRef.current?.click()} disabled={busy}><Plus size={16}/>Add first document</button></div>}</section>
    <div className="principle-note"><Info size={16}/><p><strong>A careful distinction:</strong> automated checks can surface issues and evidence. They do not by themselves prove a document is genuine or fraudulent.</p></div>
  </>;
}
function SummaryCard({icon,label,value,note,tone}:{icon:React.ReactNode;label:string;value:string;note:string;tone:string}) { return <article className="summary-card"><div className={`summary-icon ${tone}`}>{icon}</div><span className="summary-label">{label}</span><strong className="summary-value">{value}</strong><span className="summary-note">{note}</span></article>; }

function DocumentRow({doc,onClick}:{doc:Doc;onClick:()=>void}) {
  const status = statusOf(doc);
  return <button className="document-row" onClick={onClick}><div className="doc-file-icon"><FileText size={18}/></div><div className="doc-primary"><strong title={titleOf(doc)}>{titleOf(doc)}</strong><span>{nice(doc.document_type || 'Document')} <i>·</i> {dateOf(doc.created_at ?? doc.uploaded_at)}</span></div>{doc.verdict ? <VerdictBadge verdict={doc.verdict} title={doc.verdict_title}/> : <StatusPill status={status}/>}<ChevronRight className="row-arrow" size={17}/></button>;
}
function VerdictBadge({verdict,title}:{verdict:string;title?:string}) {
  const cls = verdict === 'automated_checks_passed' ? 'green' : verdict === 'needs_review' || verdict === 'partial_extraction' ? 'amber' : 'red';
  return <span className={`status-pill ${cls}`}><span className="pill-dot"/>{title ?? nice(verdict)}</span>;
}
function ResultCard({summary,doc}:{summary:AnyRecord|undefined;doc:Doc}) {
  const sm = summary ?? doc.summary;
  if (!sm) return null;
  const v = String(sm.verdict ?? 'could_not_read');
  const Icon = v === 'automated_checks_passed' ? CheckCircle2 : v === 'needs_review' || v === 'partial_extraction' ? AlertCircle : CircleHelp;
  const details = arr(sm.key_details); const reasons: string[] = sm.reasons ?? [];
  const statuses = sm.statuses ?? {}; const extraction = statuses.extraction ?? {};
  const statusRows = [
    ['Extraction', extraction.label ?? 'Status unavailable', extraction.method ? `Method: ${nice(extraction.method)}` : ''],
    ['Consistency checks', statuses.consistency_checks?.label ?? 'Not assessed', ''],
    ['Review signals', statuses.review_signals?.label ?? 'Not assessed', ''],
    ['Suspicious classification', statuses.suspicious_findings?.label ?? 'Not classified by this system', ''],
    ['Issuer verification', statuses.issuer_verification?.label ?? 'Not officially verified', ''],
  ];
  return <section className={`result-card result-${v}`} aria-live="polite">
    <div className="result-top"><div className="result-icon"><Icon size={30}/></div><div><div className="result-label">REPORT STATUS</div><h2 className="result-title">{sm.title}</h2><p className="result-headline">{sm.headline}</p></div></div>
    <div className="report-status-grid">{statusRows.map(([label,value,sub])=><div className="report-status-item" key={label}><span>{label}</span><strong>{value}</strong>{sub && <small>{sub}</small>}</div>)}</div>
    {details.length > 0 && <div className="result-details">{details.map((d,i)=><div className="result-detail" key={i}><span>{d.label}</span><strong>{d.value}</strong></div>)}</div>}
    {extraction.missing_fields_status === 'not_assessed' ? <p className="report-missing"><strong>Required fields not assessed:</strong> {extraction.missing_fields_label ?? 'Document family unknown.'}</p> : extraction.missing_fields?.length > 0 ? <p className="report-missing"><strong>Missing or uncertain:</strong> {extraction.missing_fields.map((field:string)=>nice(field)).join(', ')}</p> : extraction.missing_fields_status === 'assessed' ? <p className="report-complete-fields"><strong>Missing required fields:</strong> None identified by this parser.</p> : null}
    {reasons.length > 0 && <div className="result-block"><h3>Please check</h3><ul>{reasons.map((r,i)=><li key={i}>{r}</li>)}</ul></div>}
    <div className="result-next"><strong>Next step:</strong> {sm.next_step}</div>
    <p className="result-disclaimer"><Info size={14}/> {sm.authenticity_statement ?? sm.disclaimer}</p>
  </section>;
}
function StatusPill({status}:{status:string}) {
  let cls = 'neutral'; let label = nice(status || 'Uploaded');
  if (/complete|done|processed/.test(status)) cls='green';
  if (/review|pending/.test(status)) cls='amber';
  if (/error|fail|rejected/.test(status)) cls='red';
  if (status === 'simulation_only') label='Demo only';
  if (status === 'uploaded') label='Uploaded';
  return <span className={`status-pill ${cls}`}><span className="pill-dot"/>{label}</span>;
}
function Documents({docs,query,setQuery,busy,onOpen,onUpload,onDelete}:{docs:Doc[];query:string;setQuery:(s:string)=>void;busy:boolean;onOpen:(id:string)=>void;onUpload:()=>void;onDelete:(id:string)=>void}) {
  return <><div className="page-title-row"><div><div className="eyebrow">LOCAL CASES</div><h1>Documents</h1><p className="page-subtitle">Browse and manage documents stored in this workspace.</p></div><button className="primary-button" onClick={onUpload} disabled={busy}><Plus size={17}/>Add a document</button></div><section className="content-panel"><div className="list-toolbar"><div className="search-box"><Search size={17}/><input aria-label="Search documents" placeholder="Search by filename or type" value={query} onChange={e=>setQuery(e.target.value)}/>{query&&<button aria-label="Clear search" onClick={()=>setQuery('')}><X size={15}/></button>}</div><span className="result-count">{docs.length} {docs.length===1?'document':'documents'}</span></div>{docs.length ? <div className="document-list full-list">{docs.map(doc=><div className="list-row-wrap" key={idOf(doc)}><DocumentRow doc={doc} onClick={()=>onOpen(idOf(doc))}/><button className="row-delete" aria-label={`Delete ${titleOf(doc)}`} title="Delete document" onClick={()=>onDelete(idOf(doc))}><Trash2 size={16}/></button></div>)}</div> : <div className="empty-state"><div className="empty-icon"><Search size={20}/></div><strong>{query ? 'No matching documents' : 'No documents in this workspace'}</strong><p>{query ? 'Try another filename or document type.' : 'Uploaded documents returned by the local service will appear here.'}</p>{query ? <button className="secondary-button" onClick={()=>setQuery('')}>Clear search</button> : <button className="secondary-button" onClick={onUpload} disabled={busy}><Plus size={16}/>Add a document</button>}</div>}</section><div className="privacy-footnote"><LockKeyhole size={15}/> Documents are stored by the local service. Use Delete to remove a document and its case data.</div></>;
}
function ReviewQueue({docs,onOpen,onUpload}:{docs:Doc[];onOpen:(id:string)=>void;onUpload:()=>void}) {
  return <><div className="page-title-row"><div><div className="eyebrow">HUMAN OVERSIGHT</div><h1>Review queue</h1><p className="page-subtitle">Cases with a status indicating that a reviewer may need to look closer.</p></div></div><section className="content-panel">{docs.length ? <><div className="queue-intro"><div className="summary-icon amber"><Clock3 size={17}/></div><div><strong>{docs.length} {docs.length===1?'case':'cases'} to review</strong><p>Review each case's evidence and record your decision separately from automated checks.</p></div></div><div className="document-list full-list">{docs.map(doc=><DocumentRow key={idOf(doc)} doc={doc} onClick={()=>onOpen(idOf(doc))}/>)}</div></> : <div className="empty-state"><div className="empty-icon calm"><CheckCircle2 size={21}/></div><strong>No cases are currently marked for review</strong><p>This queue is derived from statuses returned by the local service. New uploads and report updates may change it.</p><button className="secondary-button" onClick={onUpload}><Plus size={16}/>Add a document</button></div>}</section><div className="principle-note"><Info size={16}/><p>Reviewers can record a decision and note. That record remains distinct from extraction, policy and issuer findings.</p></div></>;
}
function SettingsPage({types,health}:{types:AnyRecord[];health:string}) {
  return <><div className="page-title-row"><div><div className="eyebrow">WORKSPACE DETAILS</div><h1>Settings &amp; help</h1><p className="page-subtitle">Understand what this local demonstration can and cannot tell you.</p></div></div><div className="settings-grid"><section className="content-panel settings-panel"><div className="settings-title"><div className="summary-icon blue"><Activity size={17}/></div><div><h2>Local service</h2><p>Connection status for the document processing API.</p></div></div><div className="settings-line"><span>API connection</span><StatusPill status={health==='online'?'connected':health==='loading'?'connecting':'unavailable'}/></div><div className="settings-line"><span>Document storage</span><strong>Local service storage</strong></div><div className="settings-line"><span>External processing</span><strong>Not configured</strong></div></section><section className="content-panel settings-panel"><div className="settings-title"><div className="summary-icon green"><FileText size={17}/></div><div><h2>Document types</h2><p>Types reported by the connected service.</p></div></div>{types.length ? <div className="type-list">{types.map((type,i)=>{const label=typeof type==='string'?type:type.label??type.name??type.id??type.type??`Type ${i+1}`;return <span key={`${label}-${i}`} className="type-chip">{nice(label)}</span>})}</div> : <p className="settings-empty">No document types were returned by the service.</p>}</section><section className="content-panel settings-panel wide-settings"><div className="settings-title"><div className="summary-icon amber"><Info size={17}/></div><div><h2>How to read results</h2><p>Each source of information answers a different question.</p></div></div><div className="help-list"><div><span>01</span><p><strong>Extracted fields</strong> show what the system could read, along with confidence and evidence where available.</p></div><div><span>02</span><p><strong>Consistency and anomaly findings</strong> can surface details worth checking. They are signals, not proof of wrongdoing.</p></div><div><span>03</span><p><strong>Issuer lookup</strong> in this demo uses a simulated provider only when you explicitly opt in. It is not a real issuer confirmation.</p></div><div><span>04</span><p><strong>Reviewer decisions</strong> are recorded separately and should be based on the evidence you inspect.</p></div></div></section></div></>;
}

function ReportView({doc,report,loading,demoConsent,setDemoConsent,busy,onBack,onVerify,onDelete,onReview,onRefresh}:{doc:Doc;report:AnyRecord|null;loading:boolean;demoConsent:boolean;setDemoConsent:(v:boolean)=>void;busy:boolean;onBack:()=>void;onVerify:()=>void;onDelete:()=>void;onReview:(decision:string,note:string,corrections:AnyRecord)=>void;onRefresh:()=>void}) {
  const data = report ?? doc;
  const extractionData = data.extraction ?? doc.extraction ?? {};
  const fields = arr(data.fields ?? data.extracted_fields ?? extractionData.fields ?? extractionData.extracted_fields);
  const subjects = arr(extractionData.subjects);
  const findings = [...arr(data.findings ?? data.checks ?? data.consistency_findings ?? data.consistency_checks ?? doc.findings), ...arr(data.anomaly_signals)];
  const policy = data.policy_assessment ?? data.policy ?? {};
  const issuer = data.issuer_verification ?? data.issuer_result ?? data.verification ?? {};
  const policyItems = [...arr(policy.requirements ?? policy.findings ?? policy.evidence), ...arr(policy.citations).map(c => ({ title: c.source_title ?? 'Policy source', category: 'Citation', detail: c.passage, source_url: c.source_url, evidence: [`${c.issuer ?? 'Source'} · Page ${c.page ?? 'not provided'}`] }))];
  const reviewHistory = arr(data.review_history ?? data.reviews ?? data.audit_history);
  const [note,setNote] = useState(''); const [decision,setDecision] = useState('needs_more_information');
  const [corrections,setCorrections] = useState<AnyRecord>({});
  useEffect(()=>{const initial:AnyRecord={}; fields.forEach(f=>{if(f.name)initial[f.name]=f.value??''});Object.assign(initial,reviewHistory.at(-1)?.corrections??{});setCorrections(initial);},[doc.id, report]);
  const status = statusOf(doc);
  const isSimulated = Boolean(issuer.simulated ?? issuer.is_simulated ?? issuer.provider?.toLowerCase?.().includes('simulat') ?? issuer.provider_name?.toLowerCase?.().includes('simulat')) || String(issuer.provider ?? issuer.provider_name ?? '').toLowerCase().includes('simulat');
  const items = findings;
  return <>
    <div className="report-back"><button className="back-button" onClick={onBack}><ArrowLeft size={16}/>Back to documents</button><div className="report-actions"><button className="icon-button" aria-label="Refresh report" title="Refresh report" onClick={onRefresh} disabled={busy}><RefreshCw size={16}/></button><button className="icon-button danger-hover" aria-label="Delete document" title="Delete document" onClick={onDelete} disabled={busy}><Trash2 size={16}/></button></div></div>
    <div className="report-heading"><div className="doc-file-icon large"><FileText size={20}/></div><div className="report-title"><div className="eyebrow">DOCUMENT REPORT</div><h1>{titleOf(doc)}</h1><p>{nice(doc.document_type ?? doc.extraction?.document_type ?? 'Document')} <i>·</i> Added {dateOf(doc.created_at ?? doc.uploaded_at)}</p></div>{(data.summary ?? doc.summary) ? <VerdictBadge verdict={(data.summary ?? doc.summary).verdict} title={(data.summary ?? doc.summary).title}/> : <StatusPill status={status}/>}</div>
    {loading ? <div className="loading-panel"><LoaderCircle className="spin" size={22}/><div><strong>Loading the latest report</strong><p>Fetching the evidence returned for this document.</p></div></div> : <>
      <ResultCard summary={data.summary} doc={doc}/>
      <div className="report-grid"><div className="report-main">
        <details className="evidence-details" open><summary>Detailed evidence (extracted fields, checks, policy)</summary>
        <section className="content-panel report-section"><div className="panel-heading"><div><div className="section-kicker">01 / EXTRACTED INFORMATION</div><h2>Details we could read</h2></div><span className="subtle-tag">{fields.length} {fields.length===1?'field':'fields'}</span></div><div className="extraction-method">Extraction method: <strong>{nice(extractionData.extraction_method ?? data.summary?.extraction_method ?? 'Not available')}</strong></div>{fields.length ? <div className="field-table">{fields.map((field,i)=><FieldRow key={`${field.name??'field'}-${i}`} field={field} value={corrections[field.name]??field.value??''} onChange={value=>setCorrections((prev:AnyRecord)=>({...prev,[field.name]:value}))}/>)}</div> : <InlineEmpty title="No extracted fields were returned" detail="This may be an unsupported file or the text could not be read. You can still record a review note."/>}{subjects.length > 0 && <div className="subject-table-wrap"><div className="subject-table-heading"><strong>Subject-wise marks</strong><span>{subjects.length} rows extracted</span></div><table className="subject-table"><thead><tr><th>Code</th><th>Subject</th><th>Theory</th><th>Internal / practical</th><th>Total</th><th>Total in words</th><th>Grade</th></tr></thead><tbody>{subjects.map((row:AnyRecord,i:number)=><tr key={`${row.subject_code??row.subject}-${i}`}><td>{row.subject_code??'—'}</td><td>{row.subject??'—'}</td><td>{row.theory_marks??'—'}</td><td>{row.internal_marks??'—'}</td><td>{row.total_marks??row.marks_obtained??'—'}</td><td>{row.total_in_words??'—'}</td><td>{row.grade??'—'}</td></tr>)}</tbody></table><p className="table-note">Only printed values are shown. A maximum or grand total is not inferred when it is absent from the extracted text.</p></div>}</section>
        <section className="content-panel report-section"><div className="panel-heading"><div><div className="section-kicker">02 / CONSISTENCY &amp; ANOMALY SIGNALS</div><h2>Things to check</h2></div><span className="subtle-tag">{items.length} {items.length===1?'finding':'findings'}</span></div>{items.length ? <div className="finding-list">{items.map((item,i)=><Finding key={i} item={item}/>)}</div> : <InlineEmpty title="No findings were returned" detail="A lack of reported findings is not proof that a document is genuine."/>}</section>
        <section className="content-panel report-section"><div className="panel-heading"><div><div className="section-kicker">03 / POLICY CHECK</div><h2>Requirements and guidance</h2></div><span className="subtle-tag">{policy.status ? nice(policy.status) : 'Evidence-based'}</span></div>{policy.summary&&<p className="policy-summary">{policy.summary}</p>}{policyItems.length ? <div className="finding-list">{policyItems.map((item,i)=><Finding key={i} item={item}/>)}</div> : !policy.summary&&<InlineEmpty title="No applicable policy evidence was returned" detail="This check can only describe requirements supported by the available policy sources."/>}{policy.corpus_disclaimer&&<p className="policy-disclaimer">{policy.corpus_disclaimer}</p>}</section>
        </details>
        <section className="content-panel report-section"><div className="panel-heading"><div><div className="section-kicker">04 / HUMAN REVIEW</div><h2>Record a reviewer decision</h2></div></div><p className="section-note review-explainer">Your decision is recorded separately from automated findings. Review the evidence before choosing an outcome.</p><label className="form-label" htmlFor="review-decision">Review outcome</label><select id="review-decision" className="form-control" value={decision} onChange={e=>setDecision(e.target.value)}><option value="needs_more_information">Needs more information</option><option value="approved_for_next_step">Approved for next step</option><option value="not_approved">Not approved</option></select><label className="form-label" htmlFor="review-note">Reviewer note <span>(optional)</span></label><textarea id="review-note" className="form-control note-input" placeholder="Describe what you checked or what needs follow-up…" value={note} onChange={e=>setNote(e.target.value)} rows={3}/><button className="primary-button" disabled={busy} onClick={()=>onReview(decision,note,corrections)}><Check size={16}/>{busy?'Saving…':'Save review decision'}</button>{reviewHistory.length>0&&<div className="audit-history"><h3>Previous review activity</h3>{reviewHistory.map((r,i)=><div className="audit-item" key={i}><span className="audit-dot"/><div><strong>{nice(r.decision??r.outcome??'Review')}</strong><p>{r.note||'No note recorded'} <i>·</i> {dateOf(r.created_at??r.reviewed_at)}</p></div></div>)}</div>}</section>
      </div><aside className="report-aside">
        <section className="content-panel aside-card preview-card"><div className="aside-top"><div className="summary-icon green"><FileText size={16}/></div><span className="section-kicker">SOURCE DOCUMENT</span></div>{String(doc.media_type ?? '').startsWith('image/') ? <img className="document-preview-image" src={`/api/documents/${encodeURIComponent(doc.id)}/content`} alt={`Preview of ${titleOf(doc)}`}/> : doc.media_type === 'application/pdf' ? <iframe className="document-preview-pdf" src={`/api/documents/${encodeURIComponent(doc.id)}/content`} title={`Preview of ${titleOf(doc)}`}/> : <p className="aside-small">Preview is unavailable for this file type.</p>}<a className="preview-link" href={`/api/documents/${encodeURIComponent(doc.id)}/content`} target="_blank" rel="noreferrer">Open original document <ArrowUpRight size={14}/></a></section>
        <section className="content-panel aside-card issuer-card"><div className="aside-top"><div className="summary-icon blue"><ShieldCheck size={17}/></div><span className="section-kicker">ISSUER CHECK</span></div><h2>{data.summary?.statuses?.issuer_verification?.label ?? (isSimulated ? 'Simulated lookup only' : 'Not officially verified')}</h2><p>{issuer.summary ?? issuer.message ?? issuer.explanation ?? 'Authenticity remains unverified unless an authoritative issuer confirms it.'}</p>{isSimulated && <div className="simulated-label"><Sparkles size={14}/><span>SIMULATED DEMO RESULT</span></div>}{issuer.provider||issuer.provider_name ? <div className="provider-line"><span>Provider</span><strong>{issuer.provider??issuer.provider_name}</strong></div> : null}<label className="consent-row"><input type="checkbox" checked={demoConsent} onChange={e=>setDemoConsent(e.target.checked)}/><span>I understand this uses simulated data and is not a real issuer check.</span></label><button className="secondary-button full-width" onClick={onVerify} disabled={!demoConsent||busy}>{busy?<LoaderCircle size={16} className="spin"/>:<RefreshCw size={15}/>}Run demo issuer check (optional)</button><p className="consent-hint">Requires explicit opt-in. Results are for demonstration only.</p></section>
        <section className="content-panel aside-card"><div className="aside-top"><div className="summary-icon amber"><Info size={17}/></div><span className="section-kicker">KEEP IN MIND</span></div><p className="aside-callout">A scan can look consistent and still be unverified. A mismatch may need context before anyone reaches a conclusion.</p><div className="aside-divider"/><p className="aside-small">Automated signals are decision support. A human reviewer remains responsible for the final assessment.</p></section>
        <section className="content-panel aside-card"><div className="aside-top"><div className="summary-icon green"><LockKeyhole size={16}/></div><span className="section-kicker">DOCUMENT PRIVACY</span></div><p className="aside-small">This local workspace serves the original file only through the local document API. No public file URL is created.</p><div className="aside-divider"/><button className="delete-link" onClick={onDelete}><Trash2 size={15}/>Delete document and case data</button></section>
      </aside></div>
    </>}
  </>;
}
function FieldRow({field,value,onChange}:{field:AnyRecord;value:unknown;onChange:(s:string)=>void}) {
  const confidence = Number(field.confidence);
  const fieldLabel = field.name === 'issuing_authority' && field.issuer_confident === false ? 'Issuer not confidently identified' : nice(field.name??field.label??'Field');
  return <div className="field-row"><div className="field-label"><strong>{fieldLabel}</strong>{field.source_page!=null&&<span>Page {field.source_page}</span>}</div><div className="field-value-wrap"><input className="field-value" aria-label={`Correct ${nice(field.name??field.label??'field')}`} value={String(value??'')} onChange={e=>onChange(e.target.value)}/>{Number.isFinite(confidence)&&<span className="confidence-tag">{confidence<=1?Math.round(confidence*100):Math.round(confidence)}% confidence</span>}</div>{field.original_value != null && field.original_value !== field.value && <div className="evidence-text"><span>Original extracted value</span> “{field.original_value}”</div>}{field.evidence_text&&<div className="evidence-text"><span>Source evidence</span> “{field.evidence_text}”</div>}</div>;
}
function Finding({item}:{item:AnyRecord}) {
  const sev=String(item.severity??item.level??'info').toLowerCase(); const title=item.summary??item.title??item.message??item.name??'Finding'; const evidence=arr(item.evidence??item.evidence_references??item.sources);
  const warm = sev.includes('high')||sev.includes('error')||sev.includes('critical')||sev.includes('review')||sev.includes('warning')||sev.includes('medium');
  return <div className={`finding-item ${warm?'finding-warm':''}`}><div className="finding-icon">{warm?<AlertCircle size={16}/>:<Info size={16}/>}</div><div className="finding-body"><div className="finding-title-row"><strong>{title}</strong><span className="finding-category">{nice(item.category??item.severity??'Note')}</span></div>{(item.recommendation||item.action||item.detail)&&<p>{item.recommendation??item.action??item.detail}</p>}{evidence.length>0&&<div className="evidence-list">{evidence.map((e,i)=><span key={i}>{typeof e==='string'?e:e.text??e.source??e.title??JSON.stringify(e)}</span>)}</div>}{item.source_url&&<a className="source-link" href={item.source_url} target="_blank" rel="noreferrer">Open source <ArrowUpRight size={12}/></a>}</div></div>;
}
function InlineEmpty({title,detail}:{title:string;detail:string}) {return <div className="inline-empty"><div className="inline-empty-icon"><Info size={16}/></div><div><strong>{title}</strong><p>{detail}</p></div></div>;}

export default App;

