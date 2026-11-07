"use client";

import {useEffect,useState} from "react";
import {Copy,Upload,Check} from "lucide-react";
import {api,type DataRecord,type Job,type SourceInfo} from "@/lib/api";
import {type Workspace,useRemote,ErrorBox} from "./campus-app";
import {Button} from "./ui/button";
import {FileContentPreview} from "./file-preview";

const COLUMNS="department, semester, section, day, start_time, end_time, course_code, course_name, faculty, room";
const FIELDS=["day","start_time","end_time","subject","course_code","faculty_name","room"];
const LABELS:Record<string,string>={day:"Day",start_time:"Start time",end_time:"End time",subject:"Course name",course_code:"Course code",faculty_name:"Faculty",room:"Room"};
const string=(value:unknown)=>value==null?"":String(value);

export function TimetableImport({w,jobs,onKindChange}:{w:Workspace;jobs:Job[];onKindChange:(kind:string)=>void}){
  const [file,setFile]=useState<File|null>(null);
  const [department,setDepartment]=useState(w.profile.department==="All"?"":w.profile.department);
  const [semester,setSemester]=useState(String(w.profile.semester||3));
  const [section,setSection]=useState(w.profile.section==="All"?"":w.profile.section);
  const [from,setFrom]=useState("");const [to,setTo]=useState("");
  const [replacement,setReplacement]=useState("");
  const [demo,setDemo]=useState(false);
  const [job,setJob]=useState<Job|null>(null);
  const [rows,setRows]=useState<DataRecord[]>([]);
  const [mapping,setMapping]=useState<Record<string,string>>({});
  const [dirty,setDirty]=useState(false);const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");const [notice,setNotice]=useState("");
  const [ocrReviewed,setOcrReviewed]=useState(false);
  const [parallelReviewed,setParallelReviewed]=useState(false);
  const [readyOnly,setReadyOnly]=useState(false);
  const sources=useRemote<SourceInfo[]>("/v1/admin/files?limit=100",w);
  const published=sources.data?.filter(s=>s.kind==="timetable"&&s.status==="published"&&s.department===department&&s.semester===Number(semester)&&s.section===section)||[];
  function choose(next:Job){
    setJob(next);setRows(next.preview.editable_rows||[]);setMapping(next.preview.mapping||{});
    setDepartment(next.department||'');setSemester(String(next.semester||''));setSection(next.section||'');
    setFrom(next.preview.defaults?.effective_from||'');setTo(next.preview.defaults?.effective_to||'');
    setOcrReviewed(next.preview.ocr_reviewed||false);setParallelReviewed(next.preview.overlap_reviewed||false);
    setReadyOnly(next.preview.ready_only||false);setDirty(false);setError("");
  }
  useEffect(()=>{
    if(!job||!["queued","processing","extract_queued"].includes(job.status))return;
    let active=true,fetching=false;
    const timer=setInterval(async()=>{
      if(fetching)return;fetching=true;
      try{
        const list=await api<Job[]>("/v1/ingestion",w.session,w.tenant);
        const next=list.find(j=>j.id===job.id);
        if(active&&next){
          if(!["queued","processing","extract_queued"].includes(next.status)){choose(next);w.refresh();}
          else setJob(next);
        }
      }catch(e){if(active)setError((e as Error).message);}finally{fetching=false;}
    },2000);
    return()=>{active=false;clearInterval(timer);};
  },[job?.id,job?.status,w.session,w.tenant]);
  async function upload(event:React.FormEvent){
    event.preventDefault();if(!file)return;setBusy(true);setError("");setNotice("");
    try{
      const form=new FormData();form.append("file",file);
      form.append("options",JSON.stringify({kind:"timetable",department,semester:Number(semester),section,
        effective_from:from||null,effective_to:to||null,replaces_source_id:replacement||null,demo}));
      choose(await api<Job>("/v1/ingestion/preview",w.session,w.tenant,{method:"POST",body:form}));w.refresh();
    }catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  async function validate(){
    if(!job)return;setBusy(true);setError("");setNotice("");
    try{
      const next=await api<Job>(`/v1/ingestion/${job.id}/mapping`,w.session,w.tenant,{method:"POST",body:JSON.stringify({
        mapping,rows:rows.length?rows:undefined,ocr_reviewed:ocrReviewed,overlap_reviewed:parallelReviewed,
        timetable_ready_only:readyOnly,defaults:{effective_from:from||null,effective_to:to||null}})});
      choose(next);setNotice("Corrections saved and revalidated. The original file is unchanged.");w.refresh();
    }catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  async function publish(){
    if(!job)return;setBusy(true);setError("");setNotice("");
    try{choose(await api<Job>(`/v1/ingestion/${job.id}/publish-timetable`,w.session,w.tenant,{method:"POST",body:JSON.stringify({
      ocr_reviewed:ocrReviewed,overlap_reviewed:parallelReviewed,ready_only:readyOnly})}));w.refresh();}
    catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  function downloadHeld(full=false){
    if(!job)return;
    const held=full?rows:(job.preview.review_rows||[]).map(i=>rows[i]).filter(Boolean);
    const columns=Array.from(new Set(held.flatMap(r=>Object.keys(r)))).filter(k=>!k.startsWith("__")&&!["batch","lab_batch"].includes(k));
    const quote=(v:unknown)=>{const value=string(v);return '"'+(/^[=+@\t\r-]/.test(value)?"'":"")+value.replaceAll('"','""')+'"';};
    const csv=[columns.map(quote).join(","),...held.map(r=>columns.map(k=>quote(r[k])).join(","))].join("\r\n");
    const url=URL.createObjectURL(new Blob([csv],{type:"text/csv;charset=utf-8"}));
    const link=document.createElement("a");link.href=url;link.download=full?"timetable-full-correction.csv":"timetable-rows-needing-review.csv";link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  const working=!!job&&["queued","processing","extract_queued"].includes(job.status);
  const ready=job?.preview.valid_count||0;const review=job?.preview.review_count||0;
  const publishedNow=job?.source_status==="published";
  const canPublish=!!job&&!busy&&!working&&!dirty&&!publishedNow&&ready>0&&(!review||readyOnly)
    &&(!job.preview.ocr||ocrReviewed)&&(!job.preview.overlap_warnings?.length||parallelReviewed)
    &&(!job.preview.error_count||(review>0&&readyOnly));
  return <div>
    <div className="filter-field"><label htmlFor="focused-import-kind">What are you importing?</label><select id="focused-import-kind" value="timetable" onChange={e=>onKindChange(e.target.value)}>
      <option value="timetable">Timetable</option>{['institution_admin','super_admin'].includes(w.profile.role)&&<option value="menu">Canteen menu</option>}<option value="document">General Official Document</option><option value="faculty">Faculty directory</option><option value="events">Events</option><option value="calendar">Academic calendar</option></select></div>
    <h2>Import a timetable</h2>
    <ol aria-label="Timetable import steps" style={{display:"flex",gap:24,flexWrap:"wrap",margin:"20px 0"}}>
      <li aria-current={!job?"step":undefined}>1. Upload</li><li aria-current={job&&!publishedNow?"step":undefined}>2. Preview and validate</li><li aria-current={publishedNow?"step":undefined}>3. Publish</li>
    </ol>
    <ErrorBox message={error}/>{notice&&<p role="status">{notice}</p>}
    <section className="panel" aria-label="Upload timetable">
      <h3>1. Select and upload</h3><p>Supported: PDF (digital or scanned), XLSX, CSV, JPG/JPEG and PNG.</p>
      <details open={!job}><summary>Recommended timetable columns</summary><p style={{overflowWrap:"anywhere"}}>{COLUMNS}</p>
        <p><strong>Essential:</strong> day, start_time, end_time and course_name. <strong>Admin supplied when absent:</strong> department, semester and section. <strong>Optional:</strong> course_code, faculty, room, effective_from and effective_to. No batch field is required.</p>
        <Button type="button" variant="outline" onClick={async()=>{try{await navigator.clipboard.writeText(COLUMNS);setNotice("Timetable columns copied.");}catch{setError("Select and copy the displayed column names.");}}}><Copy size={15}/>Copy Columns</Button>
      </details>
      <form onSubmit={upload}>
        <div className="filter-grid">
          <div><label htmlFor="tt-department">Timetable department</label><input id="tt-department" value={department} disabled={['department_admin','section_admin'].includes(w.profile.role)} onChange={e=>setDepartment(e.target.value.trim())} required maxLength={60}/></div>
          <div><label htmlFor="tt-semester">Timetable semester</label><input id="tt-semester" type="number" min={1} max={12} value={semester} disabled={w.profile.role==='section_admin'} onChange={e=>setSemester(e.target.value)} required/></div>
          <div><label htmlFor="tt-section">Timetable section</label><input id="tt-section" value={section} disabled={w.profile.role==='section_admin'} onChange={e=>setSection(e.target.value.trim())} required maxLength={20}/></div>
          <div><label htmlFor="tt-file">Timetable file</label><input id="tt-file" type="file" accept=".pdf,.xlsx,.csv,.jpg,.jpeg,.png" onChange={e=>setFile(e.target.files?.[0]||null)} required/></div>
        </div>
        <details><summary>Optional dates and safe replacement</summary><div className="filter-grid">
          <div><label htmlFor="tt-from">Effective from (optional)</label><input id="tt-from" type="date" value={from} onChange={e=>{setFrom(e.target.value);if(job)setDirty(true);}}/></div>
          <div><label htmlFor="tt-to">Effective to (optional)</label><input id="tt-to" type="date" value={to} onChange={e=>{setTo(e.target.value);if(job)setDirty(true);}}/></div>
          <div><label htmlFor="tt-replacement">Replace published timetable (optional)</label><select id="tt-replacement" value={replacement} onChange={e=>setReplacement(e.target.value)}><option value="">Separate upload; do not replace</option>{published.map(s=><option key={s.id} value={s.id}>{s.filename}</option>)}</select><p>The selected schedule stays published until the replacement succeeds.</p></div>
        </div><label><input type="checkbox" checked={demo} onChange={e=>setDemo(e.target.checked)}/>This timetable is Demo Data</label></details>
        <Button type="submit" disabled={!file||busy||working}><Upload size={15}/>{busy?"Reading timetable…":"Upload and Extract"}</Button>
      </form>
    </section>
    {job&&<section className="panel section-gap" aria-label="Timetable preview and validation">
      <h3>2. Preview and validate</h3><p><strong>{job.filename}</strong> · {job.department} · Semester {job.semester} · Section {job.section}</p>
      {working?<p role="status">{job.status === "processing"?"Processing":"Queued"} · the background worker is extracting or publishing this timetable. This updates automatically.</p>:<>
        <p role="status"><strong>{ready} classes ready, {review} need review</strong>{job.preview.skipped_count?` · ${job.preview.skipped_count} empty/break rows skipped`:""}</p>
        {job.error&&<ErrorBox message={job.error}/>}
        {job.preview.schema_mismatch&&<ErrorBox message="A timetable grid or scheduling columns could not be read. Check the original, correct the mapping below, or upload a clearer timetable. The import type remains Timetable."/>}
        <details><summary>View original file and extraction notes</summary><FileContentPreview w={w} sourceId={job.source_id}/>{job.preview.course_mapping_text&&<pre style={{whiteSpace:"pre-wrap"}}>{job.preview.course_mapping_text}</pre>}</details>
        {!publishedNow&&<Button variant="outline" disabled={busy} onClick={async()=>{
          if(dirty&&!window.confirm('Re-extract the preserved original? Unsaved preview edits will be replaced.'))return;
          setBusy(true);setError('');try{choose(await api<Job>(`/v1/ingestion/${job.id}/mapping`,w.session,w.tenant,{method:'POST',body:JSON.stringify({mapping:{},reextract:true})}));w.refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}
        }}>Re-extract original timetable</Button>}
        {!!rows.length&&<div style={{overflow:"auto",maxHeight:520}}><table className="data-table"><caption>Extracted classes — edit before publication</caption>
          <thead><tr><th>Row / status</th>{FIELDS.map(f=><th key={f}>{LABELS[f]}</th>)}</tr></thead>
          <tbody>{rows.map((row,index)=>{
            const problems=job.preview.review_errors?.filter(e=>e.row===Number(row.__source_row||index+2))||[];
            const rawDay=row[mapping.day||'day'];const weekdays=['monday','tuesday','wednesday','thursday','friday','saturday','sunday'];
            const weekday=weekdays.indexOf(string(rawDay).toLowerCase());
            const parallel=job.preview.parallel_records?.some(p=>p.day===(weekday>=0?weekday:Number(rawDay))&&p.start_time.slice(0,5)===string(row[mapping.start_time||'start_time']).slice(0,5)&&p.subject===string(row[mapping.subject||'subject']).trim());
            return <tr key={index}><td>{index+2} · {problems.length?"Needs review":parallel?"Parallel — review":"Ready"}{problems.length>0&&<p className="subtle">{problems.map(e=>e.message).join("; ")}</p>}{row.__source_cell!=null&&<details><summary>Source cell</summary>{string(row.__source_cell)}</details>}</td>
              {FIELDS.map(f=>{const col=mapping[f]||f;const update=(value:string)=>{
                setRows(values=>values.map((r,i)=>i===index?{...r,[col]:value}:r));
                setMapping(values=>({...values,[f]:col}));setDirty(true);setOcrReviewed(false);
              };const days=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
              const day=days.findIndex(d=>d.toLowerCase()===string(row[col]).toLowerCase());
              return <td key={f}>{f==='day'?<select aria-label={`Timetable row ${index+2} Day`} value={day>=0?String(day):string(row[col])} disabled={publishedNow} onChange={e=>update(e.target.value)}><option value="">Select day</option>{days.map((name,value)=><option key={name} value={value}>{name}</option>)}</select>:<input aria-label={`Timetable row ${index+2} ${LABELS[f]}`} type={f.endsWith('_time')?'time':'text'} value={f.endsWith('_time')?string(row[col]).slice(0,5):string(row[col])} placeholder="Not Available" readOnly={publishedNow} onChange={e=>update(e.target.value)}/>}</td>;})}</tr>;
          })}</tbody></table></div>}
        {!publishedNow&&<Button variant="outline" disabled={busy||rows.length>=1000} onClick={()=>{
          setRows(previous=>[...previous,Object.fromEntries(FIELDS.map(f=>[mapping[f]||f,""]))]);
          setMapping(previous=>({...Object.fromEntries(FIELDS.map(f=>[f,f])),...previous}));setDirty(true);setOcrReviewed(false);
        }}>Add class row</Button>}
        <details open={!!job.preview.schema_mismatch}><summary>Advanced field mapping</summary><div className="filter-grid">{job.preview.fields?.filter(f=>f.name!=="lab_batch").map(f=><div key={f.name}><label htmlFor={`tt-map-${f.name}`}>{f.name}{f.required?" *":""}</label><select id={`tt-map-${f.name}`} value={mapping[f.name]||""} disabled={publishedNow} onChange={e=>{setMapping(m=>({...m,[f.name]:e.target.value}));setDirty(true);setOcrReviewed(false);}}><option value="">Admin default / not supplied</option>{job.preview.columns?.filter(c=>!["effective_from","effective_to"].includes(f.name)||!["day","weekday"].includes(c.toLowerCase())).map(c=><option key={c} value={c}>{c}</option>)}</select></div>)}</div></details>
        {review>0&&<p>Incomplete rows remain saved in this import. They are never published. Correct them above or explicitly publish only the ready classes.</p>}
        {!!job.preview.overlap_warnings?.length&&<label><input type="checkbox" checked={parallelReviewed} disabled={publishedNow} onChange={e=>setParallelReviewed(e.target.checked)}/>I reviewed the parallel/overlapping sessions shown in the original. Preserve them without assigning students to a batch.</label>}
        {job.preview.ocr&&<label><input type="checkbox" checked={ocrReviewed} disabled={publishedNow} onChange={e=>setOcrReviewed(e.target.checked)}/>I compared all extracted classes, codes, timings and venues with the original timetable.</label>}
        {review>0&&!publishedNow&&<label><input type="checkbox" checked={readyOnly} onChange={e=>setReadyOnly(e.target.checked)}/>Publish only the {ready} ready classes; retain {review} incomplete rows for later correction.</label>}
        {!publishedNow&&<Button variant="outline" disabled={busy||!rows.length} onClick={validate}>Validate timetable</Button>}
        {dirty&&<p role="status">Save your edits with Validate timetable before publishing.</p>}
        {review>0&&<Button variant="outline" onClick={()=>downloadHeld()}>Download rows needing review (CSV)</Button>}
        {(review>0||publishedNow)&&<Button variant="outline" onClick={()=>downloadHeld(true)}>Download full timetable for correction (CSV)</Button>}
      </>}
    </section>}
    {job&&<section className="panel section-gap" aria-label="Publish timetable"><h3>3. Publish</h3>
      {publishedNow?<p className="success" role="status">Published {job.preview.published_count??job.preview.imported_count??ready} class records. Authorized students can now query this timetable.</p>:<><p>The same authorized admin can publish. No second reviewer is required. Only validated, explicitly reviewed classes become visible.</p>
        <Button disabled={!canPublish} onClick={publish}><Check size={16}/>Publish Timetable</Button>
        {!canPublish&&!working&&<p className="subtle">{dirty?"Validate your saved edits first.":!ready?"No publishable classes yet. Correct the extracted scheduling fields.":job.preview.ocr&&!ocrReviewed?"Compare the OCR text with the original and acknowledge the review above.":job.preview.overlap_warnings?.length&&!parallelReviewed?"Review and acknowledge the parallel sessions above.":review&&!readyOnly?"Correct incomplete rows or explicitly choose to publish only ready classes.":"Complete the timetable validation before publication."}</p>}
      </>}
    </section>}
    <details className="section-gap"><summary>Recent timetable imports</summary>{jobs.filter(j=>j.kind==="timetable").slice(0,15).map(j=><div className="job-row" key={j.id}><span>{j.filename} · {j.status} · {j.source_status}</span><Button variant="ghost" onClick={()=>choose(j)}>Open timetable</Button></div>)}</details>
  </div>;
}
