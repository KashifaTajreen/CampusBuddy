"use client";
import {useEffect,useState} from 'react';
import {api,type Job,type DataRecord,type SourceInfo} from '@/lib/api';
import {type Workspace,useRemote,ErrorBox} from './campus-app';
import {Button} from './ui/button';
import {FileContentPreview,ConfirmationDialog} from './file-preview';

const groups:[string,string[]][]=[
 ['Date and time (optional)',['start_date','end_date','start_time','end_time','registration_deadline','submission_deadline']],
 ['Location and attendance (optional)',['location','building','campus','mode','meeting_url']],
 ['Registration (optional)',['registration_url','qr_url','registration_fee','fee_currency','registration_required','eligibility','team_size','registration_instructions']],
 ['Organizers and contacts (optional)',['organizer','hosting_department','organizing_club','faculty_coordinators','student_coordinators','contacts']],
 ['Agenda, rules and other details (optional)',['prizes','certificates','agenda','rules','materials','additional_instructions']],
];
const value=(raw:unknown)=>raw==null?'':String(raw);
const label=(field:string)=>field.replaceAll('_',' ').replace(/^./,letter=>letter.toUpperCase());

export function EventsImport({w,jobs,onKindChange}:{w:Workspace;jobs:Job[];onKindChange:(kind:string)=>void}){
 const [file,setFile]=useState<File|null>(null);const [category,setCategory]=useState('');
 const [department,setDepartment]=useState(['department_admin','section_admin'].includes(w.profile.role)?w.profile.department:'All');
 const [replacement,setReplacement]=useState('');const [demo,setDemo]=useState(false);
 const [job,setJob]=useState<Job|null>(null);const [events,setEvents]=useState<DataRecord[]>([]);
 const [pages,setPages]=useState<{page:number;text:string}[]>([]);
 const [reviewed,setReviewed]=useState(false);const [dirty,setDirty]=useState(false);
 const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [notice,setNotice]=useState('');
 const [deleting,setDeleting]=useState(false);
 const sources=useRemote<SourceInfo[]>('/v1/admin/files?limit=100',w);
 function choose(next:Job){setJob(next);setEvents(next.preview.event_rows||[]);setPages(next.preview.event_pages||[]);
  setReviewed(next.preview.ocr_reviewed||false);setDirty(false);setError('');}
 useEffect(()=>{
  if(!job||!['queued','processing','extract_queued'].includes(job.status))return;
  let active=true,fetching=false;
  const timer=setInterval(async()=>{if(fetching)return;fetching=true;try{
   const rows=await api<Job[]>('/v1/ingestion',w.session,w.tenant);const next=rows.find(j=>j.id===job.id);
   if(active&&next){if(!['queued','processing','extract_queued'].includes(next.status)){choose(next);w.refresh();}else setJob(next);}
  }catch(e){if(active)setError((e as Error).message);}finally{fetching=false;}},2000);
  return()=>{active=false;clearInterval(timer);};
 },[job?.id,job?.status,w.session,w.tenant]);
 function edit(index:number,field:string,raw:string){
  const next:unknown=field==='registration_fee'?(raw===''?null:Number(raw)):field==='registration_required'?(raw===''?null:raw==='true'):raw||null;
  setEvents(rows=>rows.map((row,i)=>i===index?{...row,[field]:next}:row));setDirty(true);setReviewed(false);
 }
 async function upload(event:React.FormEvent){
  event.preventDefault();if(!file)return;setBusy(true);setError('');setNotice('');
  try{const body=new FormData();body.append('file',file);body.append('options',JSON.stringify({kind:'events',event_workflow:true,event_category:category||null,
    department,semester:w.profile.role==='section_admin'?w.profile.semester:null,section:w.profile.role==='section_admin'?w.profile.section:'All',
    replaces_source_id:replacement||null,demo}));
   choose(await api<Job>('/v1/ingestion/preview',w.session,w.tenant,{method:'POST',body}));w.refresh();
  }catch(e){setError((e as Error).message);}finally{setBusy(false);}
 }
 async function validate(){
  if(!job)return;setBusy(true);setError('');setNotice('');
  try{choose(await api<Job>(`/v1/ingestion/${job.id}/event-review`,w.session,w.tenant,{method:'POST',body:JSON.stringify({events,pages,ocr_reviewed:reviewed})}));
   setNotice('Review saved. Valid content is indexed privately in the draft; publication remains a separate action.');w.refresh();
  }catch(e){setError((e as Error).message);}finally{setBusy(false);}
 }
 async function publish(){
  if(!job)return;setBusy(true);setError('');setNotice('');
  try{choose(await api<Job>(`/v1/ingestion/${job.id}/publish-event`,w.session,w.tenant,{method:'POST'}));w.refresh();}
  catch(e){setError((e as Error).message);}finally{setBusy(false);}
 }
 const working=!!job&&['queued','processing','extract_queued'].includes(job.status);
 const published=job?.source_status==='published';
 const canPublish=!!job&&!working&&!busy&&!dirty&&!published&&job.preview.event_reviewed&&job.preview.event_index_ready&&!job.preview.error_count
  &&!!job.preview.valid_count&&(!job.preview.ocr||reviewed);
 function field(index:number,name:string){
  const raw=events[index][name];const id=`event-${index}-${name}`;
  const multiline=['description','full_description','eligibility','registration_instructions','agenda','rules','materials','additional_instructions'].includes(name);
  return <div className="filter-field" key={name}><label htmlFor={id}>{label(name)}</label>{name==='mode'?<select id={id} value={value(raw)} disabled={published} onChange={e=>edit(index,name,e.target.value)}><option value="">Not Available</option><option value="online">Online</option><option value="offline">Offline</option><option value="hybrid">Hybrid</option></select>
   :name==='registration_required'?<select id={id} value={raw==null?'':String(raw)} disabled={published} onChange={e=>edit(index,name,e.target.value)}><option value="">Not Available</option><option value="true">Required</option><option value="false">Not required</option></select>
   :multiline?<textarea id={id} rows={name==='full_description'?7:3} value={value(raw)} readOnly={published} placeholder="Not Available" onChange={e=>edit(index,name,e.target.value)}/>
   :<input id={id} type={name.endsWith('_date')?'date':name.endsWith('_time')?'time':name==='registration_fee'?'number':'text'} min={name==='registration_fee'?0:undefined} step={name==='registration_fee'?'0.01':undefined} value={name.endsWith('_time')?value(raw).slice(0,5):value(raw)} readOnly={published} placeholder={name.includes('deadline')?'YYYY-MM-DD or YYYY-MM-DDTHH:MM':'Not Available'} onChange={e=>edit(index,name,e.target.value)}/>}</div>;
 }
 return <div>
  <div className="filter-field"><label htmlFor="events-import-kind">What are you importing?</label><select id="events-import-kind" value="events" onChange={e=>onKindChange(e.target.value)}><option value="events">Events</option><option value="timetable">Timetable</option><option value="document">General Official Document</option><option value="faculty">Faculty directory</option><option value="calendar">Academic calendar</option><option value="exams">Examination schedule / deadlines</option>{['institution_admin','super_admin'].includes(w.profile.role)&&<option value="menu">Canteen menu</option>}</select></div>
  <h2>Import an official campus event</h2><ol aria-label="Event import steps" style={{display:'flex',gap:24,flexWrap:'wrap',margin:'20px 0'}}><li>1. Upload Event</li><li>2. Preview, edit and validate</li><li>3. Publish Event</li></ol>
  <ErrorBox message={error}/>{notice&&<p role="status">{notice}</p>}
  <section className="panel" aria-label="Upload Event"><h3>1. Upload Event</h3>
   <p>Supported: JPG, JPEG, PNG, WEBP, PDF (digital or scanned), DOCX, XLSX, CSV and TXT.</p>
   <details open={!job}><summary>Recommended Event Information</summary><p><strong>Essential:</strong> a meaningful title and usable event content. <strong>Admin supplied:</strong> authorized department/campus-wide scope. <strong>Optional:</strong> category, dates/times, venue, registration deadline/link/fee, eligibility, team size, organizers, prizes and rules.</p><p>No spreadsheet is needed for posters or circulars. Missing dates remain Date not announced. Unknown details are never invented.</p></details>
   <form onSubmit={upload}><div className="filter-grid">
    <div><label htmlFor="event-upload-category">Event category (optional)</label><input id="event-upload-category" list="event-category-suggestions" value={category} onChange={e=>setCategory(e.target.value)} placeholder="Any category; defaults to Other"/><datalist id="event-category-suggestions">{['Technical','Cultural','Sports','Training','Club activities','Entrepreneurship','Academic','Other'].map(c=><option key={c} value={c}/>)}</datalist></div>
    <div><label htmlFor="event-upload-department">Publication department (All = campus-wide)</label><input id="event-upload-department" value={department} onChange={e=>setDepartment(e.target.value.trim())} readOnly={['department_admin','section_admin'].includes(w.profile.role)} required/></div>
    <div><label htmlFor="event-upload-file">Event poster or document</label><input id="event-upload-file" type="file" accept=".jpg,.jpeg,.png,.webp,.pdf,.docx,.xlsx,.csv,.txt" onChange={e=>setFile(e.target.files?.[0]||null)} required/></div>
   </div><details><summary>Demo Data or explicit replacement</summary><label><input type="checkbox" checked={demo} onChange={e=>setDemo(e.target.checked)}/>This event is Demo Data</label><label htmlFor="event-replacement">Replace a published event source (optional)</label><select id="event-replacement" value={replacement} onChange={e=>setReplacement(e.target.value)}><option value="">Separate event; no replacement</option>{sources.data?.filter(s=>s.kind==='events'&&s.status==='published'&&s.department===department).map(s=><option key={s.id} value={s.id}>{s.filename}</option>)}</select><p>The previous source stays published until replacement succeeds.</p></details>
   <Button type="submit" disabled={!file||busy||working}>{busy?'Reading event…':'Upload Event'}</Button></form>
  </section>
  {job&&<section className="panel section-gap" aria-label="Event preview"><h3>2. Preview, Edit and Validate</h3><p><strong>{job.filename}</strong> · {job.department} · {job.source_status}{job.demo?' · Demo Data':''}</p>
   {job.error&&<ErrorBox message={job.error}/>}
   {working?<p role="status">{job.status}: {!job.preview.event_reviewed?'Extracting event text; nothing is being published.':!job.preview.event_index_ready?'Indexing the reviewed draft privately; this does not approve or publish it.':'Completing your explicitly requested event publication.'} Original preview alone does not establish OCR success.</p>:<>
    <p role="status">{job.preview.valid_count||0} events ready · {job.preview.error_count||0} validation issue(s){job.preview.event_index_ready?` · ${job.preview.indexed_chunks} E5 passages indexed`:''}</p>
    {!events.length&&<div role="alert"><p>{job.preview.event_workflow?'No editable event data was returned. Retry text extraction from the preserved original.':'This older draft used structured-table validation. Extract it as an event to recover its text and metadata.'}</p></div>}
    {!published&&job.source_status==='draft'&&!job.preview.event_reviewed&&<Button variant="outline" disabled={busy} onClick={async()=>{setBusy(true);setError('');try{choose(await api<Job>(`/v1/ingestion/${job.id}/extract-event`,w.session,w.tenant,{method:'POST'}));w.refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}}>{events.length?'Retry text extraction':'Extract event text'}</Button>}
    {!!job.preview.errors?.length&&<div role="alert">{job.preview.errors.slice(0,4).map((e,i)=><p key={i}>Event {e.row}: {e.message}</p>)}</div>}
    {!!job.preview.warnings?.length&&<details><summary>Extraction warnings ({job.preview.warnings.length})</summary>{job.preview.warnings.map((warning,i)=><p key={i}>{warning}</p>)}</details>}
    <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit, minmax(min(100%, 320px), 1fr))',gap:20}}>
     <div><FileContentPreview w={w} sourceId={job.source_id}/></div><div>{events.map((event,index)=><details key={index} open={index===0}><summary>Event {index+1}: {value(event.title)||'Title needs review'}</summary>
      {field(index,'title')}{field(index,'category')}{field(index,'description')}
      {groups.map(([name,fields])=><details key={name}><summary>{name}</summary><div className="filter-grid">{fields.map(f=>field(index,f))}</div></details>)}
      <details><summary>Important dates and related links (optional)</summary>
       <div className="filter-field"><label htmlFor={`event-links-${index}`}>Related HTTPS links (one per line)</label><textarea id={`event-links-${index}`} value={Array.isArray(event.external_links)?event.external_links.join('\n'):''} readOnly={published} onChange={e=>{setEvents(rows=>rows.map((row,i)=>i===index?{...row,external_links:e.target.value.split('\n').map(s=>s.trim()).filter(Boolean)}:row));setDirty(true);}}/></div>
       {(Array.isArray(event.important_dates)?event.important_dates:[]).map((raw,j)=>{const milestone=raw as {label:string;on:string};return <div key={j} className="filter-grid">{(['label','on'] as const).map(key=><div key={key}><label htmlFor={`milestone-${index}-${j}-${key}`}>{key==='label'?'Milestone':'Milestone date / local time'}</label><input id={`milestone-${index}-${j}-${key}`} value={milestone[key]||''} readOnly={published} placeholder={key==='on'?'YYYY-MM-DD or YYYY-MM-DDTHH:MM':''} onChange={e=>{setEvents(rows=>rows.map((row,i)=>i===index?{...row,important_dates:(row.important_dates as typeof milestone[]).map((v,k)=>k===j?{...v,[key]:e.target.value}:v)}:row));setDirty(true);}}/></div>)}{!published&&<Button variant="ghost" onClick={()=>{setEvents(rows=>rows.map((row,i)=>i===index?{...row,important_dates:(row.important_dates as unknown[]).filter((_,k)=>k!==j)}:row));setDirty(true);}}>Remove milestone</Button>}</div>;})}
       {!published&&<Button variant="outline" onClick={()=>{setEvents(rows=>rows.map((row,i)=>i===index?{...row,important_dates:[...(Array.isArray(row.important_dates)?row.important_dates:[]),{label:'',on:''}]}:row));setDirty(true);}}>Add important date</Button>}
      </details>
      {events.length>1&&field(index,'full_description')}
     </details>)}</div>
    </div>
    <details><summary>Full extracted text — review or correct</summary>{pages.map((page,index)=><div className="filter-field" key={page.page}><label htmlFor={`event-page-${page.page}`}>Reviewed text · Page {page.page}</label><textarea id={`event-page-${page.page}`} rows={12} value={page.text} readOnly={published} onChange={e=>{setPages(values=>values.map((p,i)=>i===index?{...p,text:e.target.value}:p));setDirty(true);setReviewed(false);}}/></div>)}</details>
    {job.preview.ocr&&<><p className="subtle">{job.preview.qr_supported?'QR decoding was attempted; review any decoded URL.':'Local QR decoding is unavailable; enter a verified URL manually if needed. No QR link is invented or opened automatically.'}</p><label><input type="checkbox" checked={reviewed} disabled={published} onChange={e=>{setReviewed(e.target.checked);setDirty(true);}}/>I reviewed the OCR text and event details against the original, leaving genuinely unknown fields blank.</label></>}
    {!published&&<Button disabled={busy||!events.length||!pages.some(p=>p.text.trim())||events.some(e=>!value(e.title).trim())} variant="outline" onClick={validate}>Validate Event</Button>}
    {!published&&dirty&&<Button variant="outline" disabled={busy||!events.length} onClick={async()=>{if(!job)return;setBusy(true);setError('');try{choose(await api<Job>(`/v1/ingestion/${job.id}/event-draft`,w.session,w.tenant,{method:'POST',body:JSON.stringify({events,pages,ocr_reviewed:false})}));setNotice('Draft saved without validation, indexing or publication.');w.refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}}>Save event draft</Button>}
    {!published&&events.some(e=>!value(e.title).trim())&&<p role="status">Enter the exact event title from the original poster to enable Validate Event. OCR could not reliably read the decorative title.</p>}
    {!published&&events.length>0&&!pages.some(p=>p.text.trim())&&<p role="alert">OCR returned no usable text. Retry text extraction or enter a transcription in Full extracted text before validation.</p>}
    {!published&&job.status==='failed'&&job.preview.event_reviewed&&<p role="alert">Draft indexing failed. Your reviewed text is saved. Click Validate Event to retry indexing; nothing has been published.</p>}
    {dirty&&<p role="status">Validate your edits before publication.</p>}
   </>}
  </section>}
  {job&&<section className="panel section-gap" aria-label="Publish Event"><h3>3. Publish Event</h3>{published?<p className="success" role="status">Published {job.preview.published_count??job.preview.valid_count} event(s). Structured details and reviewed E5 evidence are available to authorized students.</p>:<><p>You can review and publish your own event. No second administrator is required. Indexing must succeed before publication.</p><Button disabled={!canPublish} onClick={publish}>Publish Event</Button>{!canPublish&&!working&&<p className="subtle">{dirty?'Validate your edits first.':job.preview.error_count?'Correct the required title/content or invalid supplied fields.':job.preview.ocr&&!reviewed?'Review and acknowledge the OCR text above.':job.preview.event_reviewed&&!job.preview.event_index_ready?'Draft indexing must succeed. Click Validate Event to retry.':'Click Validate Event to save and index the reviewed fields and text.'}</p>}</>}</section>}
  {job?.source_status==='draft'&&<Button variant="outline" disabled={busy} onClick={()=>setDeleting(true)}>Delete event draft</Button>}
  {deleting&&job&&<ConfirmationDialog title="Delete event draft" onClose={()=>setDeleting(false)}><p>Delete {job.filename} and its unpublished extraction? Other sources remain unchanged.</p><Button variant="outline" disabled={busy} onClick={()=>setDeleting(false)}>Cancel deletion</Button><Button disabled={busy} onClick={async()=>{setBusy(true);try{await api(`/v1/admin/sources/${job.source_id}/file`,w.session,w.tenant,{method:'POST',body:JSON.stringify({action:'delete_draft',confirmation:`DELETE ${job.source_id}`})});setJob(null);setDeleting(false);w.refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}}>Delete this event draft</Button></ConfirmationDialog>}
  <details className="section-gap"><summary>Recent event imports</summary>{jobs.filter(j=>j.kind==='events'&&(j.preview.event_workflow||j.preview.schema_mismatch)).slice(0,20).map(j=><div className="job-row" key={j.id}><span>{j.filename} · {j.status} · {j.source_status}</span><Button variant="ghost" onClick={()=>choose(j)}>Review event import</Button></div>)}</details>
 </div>;
}
