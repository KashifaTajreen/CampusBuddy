"use client";
import { useEffect, useState } from "react";
import { Upload, RefreshCw, Check, FileText, Code2, Copy } from "lucide-react";
import {
  type Workspace,
  useRemote,
  ErrorBox,
  Loading,
} from "@/components/campus-app";
import {
  api,
  text,
  prettyDate,
  type Job,
  type DataRecord,
  type SourceInfo,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { DemoBadge, Empty } from "@/components/result-cards";
import { FileContentPreview, ConfirmationDialog } from "./file-preview";
import {TimetableImport} from "./timetable-import";
import {EventsImport} from './events-import';

interface SystemInfo {
  verification?: {
    backend: {
      passed: number;
      failures: number;
      errors: number;
      skipped: number;
    };
    browser: { passed: number; failed: number };
    executed_at: string;
  } | null;
  counts: Record<string, number>;
  traces: {
    id: string;
    route: string;
    tools: { tool: string; latency_ms: number; success: boolean }[];
    latency_ms: number;
    success: boolean;
    result_count: number;
    created_at: string;
  }[];
  sources: SourceInfo[];
  audit: {
    action: string;
    details: Record<string, unknown>;
    created_at: string;
  }[];
  models: Record<string, string>;
  tools: string[];
}

export function AdminView({
  w,
  developer,
}: {
  w: Workspace;
  developer: boolean;
}) {
  const [kind, setKind] = useState(w.profile.role === "canteen_manager" ? "menu" : "timetable");
  const [eventKind, setEventKind] = useState("");
  const [logicalKey, setLogicalKey] = useState("");
  const [scopeSemester, setScopeSemester] = useState(w.profile.role === "section_admin" ? String(w.profile.semester) : "");
  const [scopeSection, setScopeSection] = useState(w.profile.role === "section_admin" ? w.profile.section : "All");
  const [file, setFile] = useState<File | null>(null);
  const [department, setDepartment] = useState(
    ["department_admin", "section_admin"].includes(w.profile.role) ? w.profile.department : "All",
  );
  const [title, setTitle] = useState("");
  const [editedPages,setEditedPages] = useState<{page:number;text:string}[]>([]);
  const [category, setCategory] = useState("Notice");
  const [topic, setTopic] = useState("");
  const [claim, setClaim] = useState("");
  const [demo, setDemo] = useState(false);
  const [replacement, setReplacement] = useState("");
  const [reviewSource, setReviewSource] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [editedRows, setEditedRows] = useState("");
  const [correctionsPending,setCorrectionsPending]=useState(false);
  const [ocrReviewed, setOcrReviewed] = useState(false);
  const [proposedKind, setProposedKind] = useState("");
  const [reextractRequested, setReextractRequested] = useState(false);
  const [termStart, setTermStart] = useState("");
  const [termEnd, setTermEnd] = useState("");
  const [menuDate,setMenuDate]=useState("");
  const [overlapReviewed,setOverlapReviewed]=useState(false);
  const [draftTarget,setDraftTarget]=useState<{id:string;filename:string}|null>(null);
  const [ocrLanguage, setOcrLanguage] = useState("eng");
  const [menuLocation, setMenuLocation] = useState("");
  const [menuDiet, setMenuDiet] = useState("");
  const [fileTarget, setFileTarget] = useState<SourceInfo | null>(null);
  const [fileOperation, setFileOperation] = useState<"delete" | "restore" | "purge" | "rollback" | "reindex">("delete");
  const [fileFailure, setFileFailure] = useState("");
  const [notice, setNotice] = useState("");
  const [fileConfirmation, setFileConfirmation] = useState("");
  const [rollbackTarget, setRollbackTarget] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState("imports");
  const [sourceOffset, setSourceOffset] = useState(0);
  useEffect(()=>setFileFailure(""),[fileTarget?.id,fileOperation]);
  const system = useRemote<SystemInfo>("/v1/admin/system", w);
  const sourcePage = useRemote<SourceInfo[]>(`/v1/admin/files?limit=50&offset=${sourceOffset}`, w);
  const canteenSources = useRemote<SourceInfo[]>(w.profile.role === "canteen_manager" ? "/v1/admin/canteen-sources" : null, w);
  const jobs = useRemote<Job[]>("/v1/ingestion", w);
  useEffect(() => {
    setKind(w.profile.role === "canteen_manager" ? "menu" : "timetable");
    setDepartment(["section_admin","department_admin"].includes(w.profile.role) ? w.profile.department : "All");
    setScopeSemester(w.profile.role === "section_admin" ? String(w.profile.semester) : "");
    setScopeSection(w.profile.role === "section_admin" ? w.profile.section : "All");
    setJob(null); setMapping({}); setEditedRows(""); setFileTarget(null); setReviewSource("");
    setError(""); setReplacement("");
    setSourceOffset(0);
  }, [w.tenant, w.profile.user_id, w.profile.role, w.profile.department, w.profile.semester, w.profile.section]);
  useEffect(() => {
    if (!job || !["queued", "processing"].includes(job.status)) return;
    let active = true;
    let fetching = false;
    const timer = setInterval(() => {
      if(fetching)return;
      fetching=true;
      api<Job[]>("/v1/ingestion", w.session, w.tenant)
        .then((rows) => {
          const next = rows.find((j) => j.id === job.id);
          if (active && next) {
            if (!["queued", "processing"].includes(next.status)) { selectJob(next); w.refresh(); }
            else setJob(next);
          }
        })
        .catch((e) => {
          if (active) setError(e.message);
        }).finally(()=>{fetching=false;});
    }, 2500);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [job?.id, job?.status, w.session, w.tenant]);
  function selectJob(next: Job) {
    if(next.kind)setKind(next.kind);
    setEditedPages(next.preview.editable_pages || []);
    setProposedKind(""); setReextractRequested(false);
    setTermStart(String(next.preview.defaults?.effective_from || ""));
    setTermEnd(String(next.preview.defaults?.effective_to || ""));
    setMenuDate(next.preview.defaults?.menu_date || "");
    setOverlapReviewed(next.preview.overlap_reviewed || false);
    setCorrectionsPending(false);
    setJob(next);
    setMapping(next.preview.mapping || {});
    setEditedRows(next.preview.editable_rows ? JSON.stringify(next.preview.editable_rows, null, 2) : "");
    setOcrReviewed(next.preview.ocr_reviewed || false);
  }
  async function loadPublishedMenu() {
    if (!replacement) return;
    setBusy(true); setError("");
    try {
      const result = await api<{records: DataRecord[]}>(`/v1/admin/canteen-sources/${replacement}/rows`, w.session, w.tenant);
      setFile(new File([JSON.stringify(result.records, null, 2)], `menu-version-${Date.now()}.json`, {type:"application/json"}));
      setJob(null); setEditedRows(""); setOcrReviewed(false);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function preview(e: React.FormEvent) {
    e.preventDefault();
    if (!file) {
      setError("Choose a file first.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      form.append("file", file);
      form.append(
        "options",
        JSON.stringify({
          kind,
          department: kind === "menu" ? "All" : department,
          semester: kind === "menu" ? null : scopeSemester ? Number(scopeSemester) : null,
          section: kind === "menu" || !scopeSemester ? "All" : scopeSection,
          event_kind: kind === "events" && eventKind ? eventKind : null,
          logical_key: logicalKey,
          title,
          category,
          topic,
          claim,
          demo,
          ocr_languages: ocrLanguage,
          menu_location: menuLocation,
          menu_vegetarian: menuDiet === "" ? null : menuDiet === "true",
          menu_date:menuDate || null,
          effective_from:termStart || null,effective_to:termEnd || null,
          replaces_source_id: replacement || undefined,
        }),
      );
      const next = await api<Job>(
        "/v1/ingestion/preview",
        w.session,
        w.tenant,
        { method: "POST", body: form },
      );
      selectJob(next);
      w.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function action(action: "mapping" | "confirm" | "retry" | "reextract" | "reset" | "demo" | "document") {
    if (!job) return;
    setBusy(true);
    setError("");
    try {
      const next = await api<Job>(
        `/v1/ingestion/${job.id}/${action==="document" ? "as-document" : ["reextract","reset","demo"].includes(action) ? "mapping" : action}`,
        w.session,
        w.tenant,
        {
          method: "POST",
          body: action === "reextract" ? JSON.stringify({mapping:{},reextract:true}) : ["mapping","reset","demo"].includes(action) ? JSON.stringify({ mapping:action === "reset" ? {} : mapping,reset_mapping:action === "reset",pages:job.kind==="document"&&editedPages.length?editedPages:undefined,rows: editedRows ? JSON.parse(editedRows) : undefined, ocr_reviewed: ocrReviewed,overlap_reviewed:overlapReviewed,
            defaults:{effective_from:termStart || null,effective_to:termEnd || null,menu_date:menuDate || null,menu_location:menuLocation,menu_vegetarian:menuDiet===""?null:menuDiet==="true",...(action==="demo"?{demo:true}:{})} }) : undefined,
        },
      );
      selectJob(next);
      w.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      {draftTarget && <ConfirmationDialog title="Delete draft" onClose={()=>{if(!busy)setDraftTarget(null);}}><p>Delete draft {draftTarget.filename}? Its original file and unpublished content will be removed. Other uploads and published sources are preserved.</p><Button variant="outline" disabled={busy} onClick={()=>setDraftTarget(null)}>Cancel draft deletion</Button><Button disabled={busy} onClick={async()=>{setBusy(true);setError("");try {await api(`/v1/admin/sources/${draftTarget.id}/file`,w.session,w.tenant,{method:"POST",body:JSON.stringify({action:"delete_draft",confirmation:`DELETE ${draftTarget.id}`})});if(job?.source_id===draftTarget.id)setJob(null);setDraftTarget(null);setNotice("Draft deleted. Other uploads were preserved.");w.refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}}>Delete this draft</Button></ConfirmationDialog>}
      <div className="page-heading">
        <div>
          <div className="eyebrow">
            {developer
              ? "OBSERVABILITY & INTEROPERABILITY"
              : "KEEP YOUR CAMPUS CONNECTED"}
          </div>
          <h1>{developer ? "System Intelligence" : "Admin Console"}</h1>
          <p>
            {developer
              ? "Actual service activity, source freshness and approved tools."
              : "Turn institutional files into answers students can trust."}
          </p>
        </div>
        <Button variant="outline" onClick={w.refresh}>
          <RefreshCw size={14} />
          Refresh
        </Button>
      </div>
      <ErrorBox message={error || system.error || jobs.error || sourcePage.error} />
      {notice && <div role="status" className="success" style={{position:"fixed",bottom:20,right:20,zIndex:50,maxWidth:"90vw"}}>{notice}<Button variant="ghost" onClick={()=>setNotice("")}>Dismiss</Button></div>}
      {developer ? (
        system.loading ? (
          <Loading />
        ) : (
          system.data && (
            <>
              <div className="stat-grid">
                {Object.entries(system.data.counts).map(([key, value]) => (
                  <div className="stat" key={key}>
                    <strong>{value}</strong>
                    <span>{key.replace("_", " ")} records</span>
                  </div>
                ))}
              </div>
              <section className="panel">

                <h2>Query inspection</h2>
                <p className="subtle">
                  Your authenticated activity in this institution. No question
                  text is written to application logs.
                </p>
                {system.data.traces.length ? (
                  system.data.traces.map((t) => (
                    <details className="more-results" key={t.id}>
                      <summary>
                        {t.route} · {t.latency_ms.toFixed(1)} ms ·{" "}
                        {t.result_count} result(s) ·{" "}
                        {t.success ? "Success" : "Failed"}
                      </summary>
                      <div className="data-table-wrap">
                        <table>
                          <thead>
                            <tr>
                              <th>Tool</th>
                              <th>Latency</th>
                              <th>Status</th>
                            </tr>
                          </thead>
                          <tbody>
                            {t.tools.map((tool, i) => (
                              <tr key={i}>
                                <td>{tool.tool}</td>
                                <td>{tool.latency_ms} ms</td>
                                <td>{tool.success ? "Success" : "Failed"}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </details>
                  ))
                ) : (
                  <Empty
                    message="No queries yet"
                    hint="Ask CampusOS a question, then refresh this panel."
                  />
                )}
              </section>
              <section className="panel section-gap">
                <h2>
                  <Code2
                    size={17}
                    style={{ display: "inline", marginRight: 7 }}
                  />
                  MCP tools
                </h2>
                <p className="subtle" style={{ marginTop: 10 }}>
                  These tools share the same tenant-aware services as the API.
                  Stdio requires your access token and tenant ID.
                </p>
                <div className="data-table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Approved read-only tool</th>
                      </tr>
                    </thead>
                    <tbody>
                      {system.data.tools.map((t) => (
                        <tr key={t}>
                          <td>{t}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="subtle">
                  Synthesis: {system.data.models.synthesis} · Embeddings:{" "}
                  {system.data.models.embeddings}
                </p>
                {system.data.verification ? (
                  <div className="success">
                    <strong>Executed verification</strong>
                    <p>
                      Backend: {system.data.verification.backend.passed} passed
                      · {system.data.verification.backend.skipped} skipped.
                      Browser: {system.data.verification.browser.passed} passed
                      · {system.data.verification.browser.failed} failed.
                    </p>
                    <p>
                      Report:{" "}
                      {new Date(
                        system.data.verification.executed_at,
                      ).toLocaleString("en-IN")}
                      . Cloud integrations require separate live verification.
                    </p>
                  </div>
                ) : (
                  <p className="subtle">
                    No executed verification report is installed. Run
                    scripts/verify.py to create one.
                  </p>
                )}
              </section>
            </>
          )
        )
      ) : (
        <>
          <div className="tabs">
            <button
              aria-pressed={tab === "imports"}
              onClick={() => setTab("imports")}
            >
              Import data
            </button>
            <button
              aria-pressed={tab === "sources"}
              onClick={() => setTab("sources")}
            >
              Sources & freshness
            </button>
            {["institution_admin"].includes(
              w.profile.role,
            ) && (
              <button
                aria-pressed={tab === "members"}
                onClick={() => setTab("members")}
              >
                People & roles
              </button>
            )}
            {["institution_admin", "department_admin", "section_admin"].includes(
              w.profile.role,
            ) && (
              <button
                aria-pressed={tab === "section_codes"}
                onClick={() => setTab("section_codes")}
              >
                Section Join Codes
              </button>
            )}
            <button
              aria-pressed={tab === "audit"}
              onClick={() => setTab("audit")}
            >
              Audit log
            </button>
          </div>
          {tab === "imports" ? (
            <>
              {kind === "timetable" ? <TimetableImport w={w} jobs={jobs.data||[]} onKindChange={setKind}/> : kind==='events'&&!['exam','deadline'].includes(eventKind) ? <EventsImport w={w} jobs={jobs.data||[]} onKindChange={next=>{setEventKind(next==='exams'?'exam':'');setKind(next==='exams'?'events':next);}}/> : <>
              <section className="upload-zone">
                <h2>
                  <Upload
                    size={18}
                    style={{ display: "inline", marginRight: 8 }}
                  />
                  Bring your campus information together
                </h2>
                <p>
                  1. Choose a file · 2. Review and map columns · 3. Confirm your
                  import
                </p>
                <details open><summary><strong>Recommended Document Columns</strong></summary>
                  <p className="subtle">{kind==="timetable" ? "Essential: day, start_time, end_time and course_name (or subject). Department, semester and section must be in each row or supplied in the upload settings. Optional: course_code, faculty, room, effective_from, effective_to. Blank dates mean weekly recurrence. No batch field is required." : kind==="menu" ? "Essential: name and numeric price. Optional: vegetarian, category, location, served_on. Blank served_on means a regular menu; Daily and weekday availability are supported. Department defaults to the selected campus scope. Unknown diet is never treated as vegetarian." : "Use the exact field names shown after preview. Keep unknown optional values blank; the original is retained for review."}</p>
                  {["timetable","menu"].includes(kind) && <><code style={{display:"block",overflowWrap:"anywhere"}}>{kind==="timetable" ? "department,semester,section,day,start_time,end_time,course_code,course_name,faculty,room" : "name,price,vegetarian,category,location,served_on"}</code><Button type="button" variant="outline" onClick={async()=>{try{await navigator.clipboard.writeText(kind==="timetable" ? "department,semester,section,day,start_time,end_time,course_code,course_name,faculty,room" : "name,price,vegetarian,category,location,served_on");setNotice("Recommended columns copied.");}catch{setError("Clipboard unavailable. Select and copy the column names above.");}}}>Copy Columns</Button></>}
                </details>
                <form onSubmit={preview}>
                  <div className="upload-grid">
                    <div>
                      <label htmlFor="import-type">
                        What are you importing?
                      </label>
                      <select
                        id="import-type"
                        value={kind}
                        onChange={(e) => {
                          setKind(e.target.value);
                          setReplacement("");
                          setJob(null);
                        }}
                      >
                        {w.profile.role !== "canteen_manager" && <>
                        <option value="timetable">Class timetable</option>
                        <option value="faculty">Faculty directory</option>
                        <option value="events">Events and examinations</option>
                        <option value="calendar">Academic calendar and holidays</option>
                        <option value="document">General official document / notice / circular</option>
                        <option value="exceptions">
                          Date-specific timetable changes
                        </option>
                        </>}
                        {["institution_admin", "canteen_manager"].includes(w.profile.role) && <option value="menu">Canteen menu</option>}
                      </select>
                    </div>
                    <div>
                      <label htmlFor="import-department">Department</label>
                      <input
                        id="import-department"
                        value={kind === "menu" ? "All" : department}
                        disabled={["department_admin", "section_admin", "canteen_manager"].includes(w.profile.role) || kind === "menu"}
                        onChange={(e) => {
                          setDepartment(e.target.value);
                          setReplacement("");
                        }}
                        placeholder="All or your department code"
                      />
                    </div>
                    {kind !== "menu" && <>
                      <div><label htmlFor="scope-semester">Source semester (optional)</label><input id="scope-semester" type="number" min={1} max={12} value={scopeSemester} disabled={w.profile.role === "section_admin"} onChange={(e) => {setScopeSemester(e.target.value); setReplacement("");}} /></div>
                      <div><label htmlFor="scope-section">Source section</label><input id="scope-section" value={scopeSection} disabled={w.profile.role === "section_admin" || !scopeSemester} maxLength={20} onChange={(e) => {setScopeSection(e.target.value); setReplacement("");}} /></div>
                    </>}
                    {kind === "timetable" && <fieldset><legend>Optional upload dates</legend><label htmlFor="upload-effective-from">Effective from (optional)</label><input id="upload-effective-from" type="date" value={termStart} onChange={e=>setTermStart(e.target.value)}/><label htmlFor="upload-effective-to">Effective to (optional)</label><input id="upload-effective-to" type="date" value={termEnd} onChange={e=>setTermEnd(e.target.value)}/><p className="subtle">Blank dates mean a recurring weekly timetable. Explicit row dates override these upload defaults.</p></fieldset>}
                    {kind === "menu" && <div><label htmlFor="upload-menu-date">Menu date (optional daily special)</label><input id="upload-menu-date" type="date" value={menuDate} onChange={e=>setMenuDate(e.target.value)}/><p className="subtle">Leave blank for regular menus. Daily and weekday availability in the file is preserved.</p></div>}
                    {kind === "events" && <div><label htmlFor="event-kind">Event workflow</label><select id="event-kind" value={eventKind} onChange={(e) => setEventKind(e.target.value)}><option value="">Use kinds from file</option><option value="exam">Exam timetable</option><option value="event">Campus events</option><option value="deadline">Important deadlines</option></select><p className="subtle">Exam rows may use date, subject, start_time, end_time and venue columns.</p></div>}
                    <div>
                      <label htmlFor="replace-source">
                        Replace a published source (optional)
                      </label>
                      <select
                        id="replace-source"
                        value={replacement}
                        onChange={(e) => setReplacement(e.target.value)}
                      >
                        <option value="">
                          New source / incremental import
                        </option>
                        {((w.profile.role === "canteen_manager" ? canteenSources.data : system.data?.sources) ?? [])
                          .filter(
                            (s) =>
                              s.status === "published" &&
                              s.kind === kind &&
                              s.department === (kind === "menu" ? "All" : department) &&
                              (s.semester ?? null) === (kind === "menu" || !scopeSemester ? null : Number(scopeSemester)) &&
                              (s.section ?? "All") === (kind === "menu" || !scopeSemester ? "All" : scopeSection),
                          )
                          .map((s) => (
                            <option key={s.id} value={s.id}>
                              {s.filename} · {s.id.slice(0,8)}
                            </option>
                          ))}
                      </select>
                      <p className="subtle">
                        The published version stays available until its
                        replacement is approved and published.
                      </p>
                      {kind === "menu" && replacement && <Button type="button" variant="outline" disabled={busy} onClick={loadPublishedMenu}>Load published menu for editing</Button>}
                      {file && <p className="subtle">Selected file: {file.name}. Preview it to edit rows and validate.</p>}
                    </div>
                    <details><summary>Advanced: logical version key</summary><label htmlFor="logical-key">Logical schedule or circular key (optional)</label><input id="logical-key" maxLength={100} value={logicalKey} onChange={(e) => setLogicalKey(e.target.value)} placeholder="For example: 2026-sem3-internal-1" /></details>
                    <div>
                      <label htmlFor="import-file">
                        Source file (up to 15 MB)
                      </label>
                      <input
                        id="import-file"
                        type="file"
                        accept=".pdf,.csv,.xlsx,.json,.docx,.pptx,.txt,.md,.png,.jpg,.jpeg,.webp"
                        onChange={(e) => setFile(e.target.files?.[0] || null)}
                      />
                    </div>
                    <div><label htmlFor="ocr-language">OCR language packs</label><select id="ocr-language" value={ocrLanguage} onChange={(e) => setOcrLanguage(e.target.value)}><option value="eng">English</option><option value="eng+hin">Hindi + English</option><option value="eng+kan">Kannada + English</option></select></div>
                    {kind === "menu" && <>
                      <div><label htmlFor="menu-location">Menu location (if verified)</label><input id="menu-location" value={menuLocation} onChange={(e) => setMenuLocation(e.target.value)} /><p className="subtle">Leave blank when the source does not specify a location.</p></div>
                      <div><label htmlFor="menu-diet">Verified menu diet default</label><select id="menu-diet" value={menuDiet} onChange={(e) => setMenuDiet(e.target.value)}><option value="">Unknown (leave unspecified)</option><option value="true">Vegetarian</option><option value="false">Non-vegetarian</option></select></div>
                    </>}
                    <div>
                      <label
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 9,
                          marginTop: 26,
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={demo}
                          onChange={(e) => setDemo(e.target.checked)}
                        />
                        This file contains synthetic Demo Data
                      </label>
                    </div>
                    {kind === "document" && (
                      <>
                        <div>
                          <label htmlFor="doc-title">Document title</label>
                          <input
                            id="doc-title"
                            value={title}
                            onChange={(e) => setTitle(e.target.value)}
                            maxLength={300}
                          />
                        </div>
                        <div>
                          <label htmlFor="doc-category">Category</label>
                          <input
                            id="doc-category"
                            value={category}
                            onChange={(e) => setCategory(e.target.value)}
                          />
                        </div>
                        <details><summary>Advanced: recorded claim and conflict topic</summary><div>
                          <label htmlFor="doc-topic">
                            Claim topic (optional, for conflict checks)
                          </label>
                          <input
                            id="doc-topic"
                            value={topic}
                            onChange={(e) => setTopic(e.target.value)}
                            placeholder="internal-assessment-date"
                          />
                        </div>
                        <div>
                          <label htmlFor="doc-claim">
                            Recorded claim (optional)
                          </label>
                          <input
                            id="doc-claim"
                            value={claim}
                            onChange={(e) => setClaim(e.target.value)}
                            placeholder="2026-11-03"
                          />
                        </div>
                        </details>
                      </>
                    )}
                  </div>
                  <Button
                    type="submit"
                    style={{ marginTop: 20 }}
                    disabled={busy || !file}
                  >
                    <FileText size={15} />
                    {busy ? "Reading your file…" : "Preview file"}
                  </Button>
                </form>
              </section>
              {job && (
                <section className="panel">
                  <div className="section-header">
                    <h2>Review your import</h2>
                    <span className="demo-badge">{job.status}</span>
                  </div>
                  {job.preview.message && <p>{job.preview.message}</p>}
                  {job.filename && <p>{job.filename} · {job.kind} · {job.department}{job.semester ? ` · Semester ${job.semester} · Section ${job.section}` : ""}</p>}
                  {job.demo && <DemoBadge/>}
                  <ol aria-label="Import progress" style={{display:"flex",gap:16,flexWrap:"wrap"}}>{["Draft","Processing","Needs review","Approved","Published"].map((step)=><li key={step} aria-current={step === (job.source_status === "published" ? "Published" : job.source_status === "approved" ? "Approved" : job.source_status === "pending_review" ? "Needs review" : ["queued","processing"].includes(job.status) ? "Processing" : "Draft") ? "step" : undefined}>{step}</li>)}</ol>
                  {job.preview.valid_count!==undefined && <p role="status">{job.preview.valid_count} valid · {job.preview.skipped_count || 0} skipped · {job.preview.error_count || 0} validation issue(s)</p>}
                  {!!job.preview.skipped_count && <details><summary>Why rows were skipped ({job.preview.skipped_count})</summary>{job.preview.skipped_rows?.map((r,i)=><p key={i}>{r.sheet} · row {r.row}: {r.reason}</p>)}</details>}
                  {job.preview.requires_demo && !job.demo && <div role="alert"><p>This workbook explicitly contains generated sample/test information. It cannot be confirmed or published as real campus data.</p><Button disabled={busy} onClick={()=>action("demo")}>Mark this draft Demo Data</Button></div>}
                  {!!job.preview.overlap_warnings?.length && <div role="alert">{job.preview.overlap_warnings.map((warning,i)=><p key={i}>{warning}</p>)}<label><input type="checkbox" checked={overlapReviewed} onChange={e=>{setOverlapReviewed(e.target.checked);setCorrectionsPending(true);}}/>I reviewed these overlapping entries and want to retain them.</label></div>}
                  {job.preview.needs_ocr && <Button variant="outline" disabled={busy} onClick={()=>action("mapping")}>Retry extraction after OCR is configured</Button>}
                  <FileContentPreview key={job.source_id} w={w} sourceId={job.source_id}/>
                  {job.preview.schema_mismatch && <div role="alert">
                    <p>This is not a matching structured table. General official document preserves arbitrary sheets and text.</p>
                    <p>Your selected import type is preserved. A suggestion only changes it after you explicitly confirm.</p>
                    {job.kind!=="document" && job.source_status==="draft" && <Button disabled={busy} onClick={()=>action("document")}>Import original as General Official Document</Button>}
                    <Button variant="outline" onClick={()=>setProposedKind(job.preview.suggested_kind || "document")}>Choose suggested import type</Button>
                    {proposedKind && <div role="group" aria-label="Confirm suggested import type"><p>Suggested type: {proposedKind}. This starts a new preview; the original draft is retained.</p><Button variant="outline" onClick={()=>setProposedKind("")}>Keep selected type</Button><Button onClick={()=>{setKind(proposedKind);setJob(null);setMapping({});setEditedRows("");setReplacement("");setProposedKind("");}}>Use {proposedKind} for next preview</Button></div>}
                  </div>}
                  {job.kind==="document" && !!editedPages.length && <section aria-label="Extracted document text">
                    <h3>Extracted document text</h3>
                    <p>Review the readable text below. Corrections are optional and do not change the original file. Page identifiers are retained for citations.</p>
                    {editedPages.map((page,index)=><div key={page.page}><label htmlFor={`document-text-${page.page}`}>Extracted text · Page {page.page}</label><textarea id={`document-text-${page.page}`} rows={10} value={page.text} readOnly={job.status!=="preview"} onChange={e=>{setEditedPages(pages=>pages.map((p,i)=>i===index?{...p,text:e.target.value}:p));setCorrectionsPending(true);setOcrReviewed(false);}}/></div>)}
                    {job.status==="preview" && <Button variant="outline" disabled={busy} onClick={()=>action("mapping")}>Validate document text</Button>}
                  </section>}
                  <p>
                    Publication: <strong>{job.source_status}</strong>
                  </p>
                  <p className="subtle">You can review, approve and publish your own import. No second administrator is required.</p>
                  {job.kind==="document" && job.status==="preview" && job.preview.ocr && <p role="status">OCR text is ready. Verify it against the original, validate OCR review, then confirm processing to create the vector index. Publishing is a separate action you can complete yourself.</p>}
                  {["preview","failed"].includes(job.status) && job.source_status === "draft" && <div className="section-gap">
                    <Button variant="outline" disabled={busy} onClick={()=>setReextractRequested(true)}>Re-extract original file</Button>
                    {reextractRequested && <div role="group" aria-label="Confirm re-extraction"><p>Re-extraction clears saved corrections for this draft. The original file and published information are preserved.</p><Button variant="outline" onClick={()=>setReextractRequested(false)}>Cancel re-extraction</Button><Button disabled={busy} onClick={()=>action("reextract")}>Discard corrections and re-extract</Button></div>}
                  </div>}
                  {job.status === "completed" && (
                    <div className="success" role="status">
                      <Check
                        size={15}
                        style={{ display: "inline", marginRight: 7 }}
                      />
                      {job.preview.imported_count ??
                        job.preview.valid_count ??
                        "Your"}{" "}
                      records processed.{" "}
                      {job.source_status === "published"
                        ? "Published information is available to authorized users."
                        : "Not published. Review this source, then explicitly approve and publish it."}
                      {job.source_status !== "published" && (
                        <Button
                          variant="ghost"
                          onClick={() => {
                            setReviewSource(job.source_id);
                            setTab("sources");
                          }}
                        >
                          Open publication review
                        </Button>
                      )}
                    </div>
                  )}
                  {["queued", "processing"].includes(job.status) && (
                    <div className="loading" role="status">
                      Your file is {job.status}. This page will update
                      automatically. Keep the backend worker running.
                    </div>
                  )}
                  {job.error && <ErrorBox message={job.error} />}{" "}
                  {job.preview.format && <p className="subtle">Detected format: {job.preview.format} · Import type: {job.preview.detected_type}</p>}
                  {job.preview.warnings?.map((warning, index) => <p className="subtle" key={index}>{warning}</p>)}
                  {job.preview.duplicate_check && <div role="status"><p>Background duplicate check completed.</p>{job.preview.duplicate_check.warnings.map((warning, i) => <p key={i}>{warning}</p>)}{job.preview.duplicate_check.blocking && <p>Upload an explicitly selected replacement before publication.</p>}</div>}
                  {job.status === "preview" && editedRows && (
                    <div className="filter-field">
                      {job.preview.detected_type === "timetable" && <p className="subtle">Upload dates above are optional defaults. Blank row dates inherit them; explicit row dates are preserved.</p>}
                      {job.preview.course_mapping_text && <details><summary>Course and faculty legend from the original — verify mapping</summary><pre style={{whiteSpace:"pre-wrap"}}>{job.preview.course_mapping_text}</pre><p>Keep uncertain faculty IDs blank. Faculty names and room values may remain unspecified; supplied faculty IDs must match the directory.</p></details>}
                      <EditableRows value={editedRows} onChange={(value)=>{setEditedRows(value);setOcrReviewed(false);setCorrectionsPending(true);}}/>
                    <details>
                      <summary>Advanced: raw extracted rows and JSON corrections</summary>
                      <label htmlFor="editable-import-rows">Extracted rows (editable JSON)</label>
                      <textarea id="editable-import-rows" rows={10} value={editedRows} onChange={(e) => { setEditedRows(e.target.value); setOcrReviewed(false);setCorrectionsPending(true); }} />
                      <p className="subtle">Correct extracted values and validate mapping again before confirmation.</p>
                    </details>
                    </div>
                  )}
                  {job.preview.ocr && <div className="section-gap">
                    <p>OCR requires manual verification. Recognition confidence is not calibrated.</p>
                    <Button variant="outline" onClick={() => w.download(job.source_id)}>Verify original file</Button>
                    {job.preview.ocr_issues?.map((issue, index) => <p className="subtle" key={index}>{issue.page ? `Page ${issue.page} · ` : ""}{issue.message}</p>)}
                    <label><input type="checkbox" checked={ocrReviewed} onChange={(e) => setOcrReviewed(e.target.checked)} /> I verified extracted values against the original and left genuinely unknown optional fields blank.</label>
                    {!job.preview.fields?.length && <Button variant="outline" disabled={busy} onClick={() => action("mapping")}>Validate OCR review</Button>}
                  </div>}
                  {!!job.preview.fields?.length && job.status === "preview" && (
                    <details open={!!job.preview.error_count}>
                      <summary>Correct structured field mapping</summary>
                      <p className="subtle">
                        Match your spreadsheet columns to the fields below.
                        Required fields have an asterisk.
                      </p>
                      <div className="mapping-grid">
                        {job.preview.fields.filter(f=>f.name!=="lab_batch").map((f) => (
                          <div key={f.name}>
                            <label htmlFor={`map-${f.name}`}>
                              {f.name.replaceAll("_", " ")}
                              {f.required ? " *" : ""}
                            </label>
                            <select
                              id={`map-${f.name}`}
                              value={mapping[f.name] || ""}
                              onChange={(e) => {
                                setOcrReviewed(false);
                                setCorrectionsPending(true);
                                setMapping((m) => ({
                                  ...m,
                                  [f.name]: e.target.value,
                                }));
                              }}
                            >
                              <option value="">
                                {f.required
                                  ? "Choose column"
                                  : "Use default / not supplied"}
                              </option>
                              {job.preview.columns?.filter(col=>!["effective_from","effective_to","served_on"].includes(f.name) || !["day","weekday","day of week"].includes(col.toLowerCase().replaceAll("_"," "))).map((col) => (
                                <option value={col} key={col}>
                                  {col}
                                </option>
                              ))}
                            </select>
                          </div>
                        ))}
                      </div>
                    </details>
                  )}
                  {!!job.preview.fields?.length && job.status === "preview" && <Button
                        variant="outline"
                        onClick={() => action("mapping")}
                        disabled={busy}
                      >
                        Validate changes
                      </Button>}
                  {!!job.preview.fields?.length && job.status === "preview" && <Button variant="outline" disabled={busy} onClick={()=>action("reset")}>Reset mapping from exact headers</Button>}
                  {correctionsPending && <p role="status">Validate your changes before confirming processing.</p>}
                  {!!job.preview.error_count && (
                    <>
                      <ErrorBox
                        message={`${job.preview.error_count} record(s) need attention. Correct the file or mapping, then validate again.`}
                      />
                      <ul
                        className="subtle"
                        style={{ margin: "15px 0", paddingLeft: 20 }}
                      >
                        {job.preview.error_groups ? job.preview.error_groups.map((group,i)=><li key={i}>{group.count} affected row(s), including {group.rows.join(", ")}: {group.message}</li>) : job.preview.errors?.slice(0, 12).map((e, i) => (
                          <li key={i}>
                            Row {e.row}: {e.message}
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
                  {job.preview.records && job.preview.records.length > 0 && (
                    <PreviewTable records={job.preview.records} />
                  )}
                  <div style={{ display: "flex", gap: 12, marginTop: 18 }}>
                    {job.status === "preview" && (
                      <Button
                        onClick={() => action("confirm")}
                        disabled={busy || !!job.preview.error_count || correctionsPending || (!!job.preview.overlap_warnings?.length && !job.preview.overlap_reviewed) || (!!job.preview.ocr && !job.preview.ocr_reviewed)}
                      >
                        <Check size={15} />
                        Confirm import for processing
                        {job.preview.valid_count !== undefined
                          ? ` · ${job.preview.valid_count} records`
                          : ""}
                      </Button>
                    )}
                    {job.status === "failed" && (
                      <Button variant="outline" onClick={() => action("retry")}>
                        Preview and retry
                      </Button>
                    )}
                  </div>
                </section>
              )}
              <section className="panel section-gap">
                <h2>Recent imports</h2>
                {jobs.data?.length ? (
                  jobs.data.map((j) => (
                    <div className="job-row" key={j.id}>
                      <div>
                        <strong style={{ fontSize: 13 }}>
                          {j.filename || j.id.slice(0,8)} · {j.status} · {j.source_status} ·{" "}
                          {prettyDate(j.created_at)}
                        </strong>
                        <p>
                          {j.error ||
                            `${j.preview.valid_count ?? j.preview.imported_count ?? "Document"} records · ${j.attempts} processing attempt(s)`}
                        </p>
                      </div>
                      <Button variant="ghost" onClick={() => selectJob(j)}>
                        Review
                      </Button>
                      {j.source_status==="draft" && <Button variant="outline" disabled={busy} onClick={()=>setDraftTarget({id:j.source_id,filename:j.filename || j.id.slice(0,8)})}>Delete Draft</Button>}
                    </div>
                  ))
                ) : (
                  <Empty
                    message="No imports yet"
                    hint="Choose a source above to start."
                  />
                )}
              </section>
              </>}
            </>
          ) : tab === "sources" ? (
            <section className="panel">
              <h2>Sources and publication</h2>
              {fileTarget && (
                <ConfirmationDialog title="File lifecycle confirmation" onClose={()=>{if(!busy)setFileTarget(null);}}>
                <section className="publication-review" aria-label="File lifecycle confirmation">
                  <h3>{fileOperation}: {fileTarget.filename}</h3>
                  <p className="subtle">Upload {fileTarget.id.slice(0,8)} · {fileTarget.kind} · {fileTarget.department} · {fileTarget.status}</p>
                  <ErrorBox message={fileFailure}/>
                  <p>{fileOperation === "reindex" ? "Rebuild embeddings from approved evidence. Publication and original content remain unchanged. Monitor progress under Recent imports." : fileOperation === "purge" ? "Permanent deletion removes the original and extracted content. This cannot be undone." : fileOperation === "restore" || fileOperation === "rollback" ? "This returns the file to draft. Reprocess it, review and explicitly publish it again." : "Delete this uploaded or published file from student access? Its passages will be removed from search and active jobs cancelled. The original remains recoverable until you choose Permanently delete. Other uploads are preserved."}</p>
                  {fileOperation === "purge" && <div className="filter-field"><label htmlFor="purge-confirmation">Type DELETE {fileTarget.id}</label><input id="purge-confirmation" value={fileConfirmation} onChange={(e) => setFileConfirmation(e.target.value)} /></div>}
                  {fileOperation === "rollback" && <div className="filter-field"><label htmlFor="rollback-target">Currently published version to replace</label><select id="rollback-target" value={rollbackTarget} onChange={(e) => setRollbackTarget(e.target.value)}><option value="">Choose current version</option>{system.data?.sources.filter((s) => s.status === "published" && s.kind === fileTarget.kind && s.department === fileTarget.department).map((s) => <option key={s.id} value={s.id}>{s.filename}</option>)}</select></div>}
                  <Button variant="outline" onClick={() => setFileTarget(null)} disabled={busy}>Cancel file action</Button>
                  <Button disabled={busy || (fileOperation === "purge" && fileConfirmation !== `DELETE ${fileTarget.id}`) || (fileOperation === "rollback" && !rollbackTarget)} onClick={async () => {
                    setBusy(true); setError("");setFileFailure("");
                    try {
                      if(fileOperation === "reindex") {
                        await api<Job>(`/v1/sources/${fileTarget.id}/reindex`,w.session,w.tenant,{method:"POST"});
                      } else {
                        await api(`/v1/admin/sources/${fileTarget.id}/file`, w.session, w.tenant, { method: "POST", body: JSON.stringify({ action: fileOperation, confirmation: fileConfirmation, replaces_source_id: rollbackTarget || null }) });
                      }
                      setNotice(`${fileTarget.filename} (${fileTarget.id.slice(0,8)}): ${fileOperation === "reindex" ? "indexing queued" : fileOperation+" completed"}.`);
                      setFileTarget(null); setReviewSource(""); setJob(null); w.refresh();
                    } catch (e) { setFileFailure((e as Error).message); w.refresh(); }
                    finally { setBusy(false); }
                  }}>Confirm {fileOperation}</Button>
                </section>
                </ConfirmationDialog>
              )}
              <p className="subtle">
                Processing does not publish shared information. An authorized uploader
                may review and explicitly publish their source. Structured originals are
                restricted to institution administrators.
              </p>
              {reviewSource && (
                <ConfirmationDialog title="Review source publication" onClose={()=>setReviewSource("")}>
                <Button variant="outline" onClick={()=>setReviewSource("")}>Close publication review</Button>
                <PublicationReview
                  key={`${w.tenant}:${reviewSource}`}
                  w={w}
                  sourceId={reviewSource}
                />
                </ConfirmationDialog>
              )}
              {sourcePage.data?.filter(s=>s.file_state!=="purged").map((s) => (
                <div className="job-row" key={s.id} data-source-id={s.id}>
                  <div>
                    <strong style={{ fontSize: 13 }}>{s.filename}</strong>
                    <p>
                      {s.kind} · {s.department} · <strong>{s.status}</strong> ·{" "}
                      {new Date(s.created_at).toLocaleString("en-IN")}
                      {s.demo && (
                        <>
                          {" "}
                          · <DemoBadge />
                        </>
                      )}
                    </p>
                    <p className="subtle">Upload {s.id.slice(0,8)} · {s.semester ? `Semester ${s.semester}, section ${s.section}` : "All sections"}
                      {s.version ? ` · Document version ${s.version}` : ""}
                      {s.replaces_source_id ? ` · Replaces upload ${s.replaces_source_id.slice(0,8)}` : " · Separate upload"}
                      {s.replaced_by ? ` · Superseded by ${s.replaced_by.slice(0,8)}` : ""}
                    </p>
                    {s.file_state && s.file_state !== "active" && <p><strong>{s.file_state === "purged" ? "Permanently removed; cannot be restored" : s.file_state === "purge_pending" ? "Permanent removal pending; retry cleanup" : "Soft-deleted; restore returns it to draft"}</strong> · Publication history: {s.publication_status}</p>}
                  </div>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    {(s.file_state === "active" || !s.file_state) && <Button variant="outline" disabled={busy} onClick={() => { setFileOperation("delete"); setFileTarget(s); setFileConfirmation(""); }}>{s.kind==="document" ? "Delete document" : "Delete file"}</Button>}
                    {s.status==="draft" && (s.file_state==="active" || !s.file_state) && <Button variant="outline" disabled={busy} onClick={()=>setDraftTarget({id:s.id,filename:s.filename})}>Delete Draft</Button>}
                    {s.file_state === "deleted" && <Button variant="outline" disabled={busy} onClick={() => { setFileOperation("restore"); setFileTarget(s); }}>Restore as draft</Button>}
                    {s.status === "replaced" && s.file_state === "active" && <Button variant="outline" disabled={busy} onClick={() => { setFileOperation("rollback"); setFileTarget(s); setRollbackTarget(""); }}>Roll back as draft</Button>}
                    {["deleted", "purge_pending"].includes(s.file_state || "") && <Button variant="outline" disabled={busy} onClick={() => { setFileOperation("purge"); setFileTarget(s); setFileConfirmation(""); }}>Permanently delete</Button>}
                    {s.file_state !== "purged" && <Button
                      variant="outline"
                      onClick={() => setReviewSource(s.id)}
                    >
                      Review publication
                    </Button>}
                    <Button
                      variant="outline"
                      onClick={() => w.download(s.id)}
                      disabled={
                        !s.original_download_allowed ||
                        ["revoked", "rejected", "replaced", "deleted", "purge_pending", "purged"].includes(s.status)
                      }
                    >
                      View original
                    </Button>
                    {s.kind === "document" && s.status === "published" && (
                      <Button
                        variant="ghost"
                        onClick={()=>{setFileOperation("reindex");setFileTarget(s);setFileFailure("");}}
                      >
                        Re-index
                      </Button>
                    )}
                  </div>
                </div>
              ))}
              <div style={{ display: "flex", gap: 8 }}>
                <Button variant="outline" disabled={busy || sourcePage.loading || sourceOffset === 0} onClick={() => setSourceOffset((offset) => Math.max(0, offset - 50))}>Previous files</Button>
                <Button variant="outline" disabled={busy || sourcePage.loading || (sourcePage.data?.length || 0) < 50} onClick={() => setSourceOffset((offset) => offset + 50)}>More files</Button>
              </div>
            </section>
          ) : tab === "members" ? (
            <Members w={w} />
          ) : tab === "section_codes" ? (
            <SectionCodes w={w} />
          ) : (
            <section className="panel">
              <h2>Your administrative actions</h2>
              {system.data?.audit.length ? (
                system.data.audit.map((a, i) => (
                  <div className="job-row" key={i}>
                    <div>
                      <strong style={{ fontSize: 13 }}>{a.action}</strong>
                      <p>{new Date(a.created_at).toLocaleString("en-IN")}</p>
                      <p>
                        {Object.entries(a.details)
                          .map(([k, v]) => `${k}: ${v}`)
                          .join(" · ")}
                      </p>
                    </div>
                  </div>
                ))
              ) : (
                <Empty message="No administrative actions recorded" />
              )}
            </section>
          )}
        </>
      )}
    </>
  );
}

function PublicationReview({
  w,
  sourceId,
}: {
  w: Workspace;
  sourceId: string;
}) {
  const { data, error, loading } = useRemote<
    SourceInfo & {
      validation: Job["preview"];
      job_status: string | null;
      job_error: string | null;
    }
  >(`/v1/admin/sources/${sourceId}`, w);
  const [reason, setReason] = useState("");
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState(false);
  async function change(action: SourceInfo["actions"][number]) {
    setBusy(true);
    setFailure("");
    try {
      await api(
        `/v1/admin/sources/${sourceId}/${action}`,
        w.session,
        w.tenant,
        {
          method: "POST",
          body: JSON.stringify({ reason }),
        },
      );
      setReason("");
      w.refresh();
    } catch (e) {
      setFailure(e instanceof Error ? e.message : "Publication action failed.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      className="panel section-gap"
      aria-label="Source publication review"
    >
      <ErrorBox message={error || failure} />
      {loading ? (
        <Loading />
      ) : (
        data && (
          <>
            <h3>{data.filename}</h3>
            <p className="subtle">Upload {data.id.slice(0,8)} · {data.department} {data.semester ? `· semester ${data.semester}, section ${data.section}` : "· all sections"}</p>
            {data.student_records && <p role="status">Student-specific evidence: students can query only rows linked to their verified USN. The whole original remains restricted to authorized administrators.</p>}
            {data.file_state === "active" && <FileContentPreview w={w} sourceId={data.id}/>}
            <p>
              Publication: <strong>{data.status}</strong> · Processing:{" "}
              {data.job_status || "Legacy import"}
            </p>
            <p className="subtle">
              {data.validation.valid_count ??
                data.validation.imported_count ??
                0}{" "}
              validated records/passages · {data.validation.error_count ?? 0}{" "}
              validation errors.
            </p>
            {data.review_note && <p>{data.review_note}</p>}
            <ErrorBox message={data.job_error || ""} />
            {data.validation.duplicate_check && <div role="status">Background duplicate check completed.{data.validation.duplicate_check.warnings.map((warning, i) => <p key={i}>{warning}</p>)}{data.validation.duplicate_check.blocking && <p>Publication is blocked until an explicit replacement is reviewed.</p>}</div>}
            {!!data.validation.records?.length && (
              <PreviewTable records={data.validation.records} />
            )}
            {data.validation.passages?.map((p, i) => (
              <p key={i} className="passage">
                Page {p.page}: {p.text}
              </p>
            ))}
            <p className="subtle">
              The preview shows up to ten normalized records or passages. Review
              the permitted original for the complete file.
            </p>
            {data.status === "pending_review" &&
              !data.actions.includes("approve") && (
                <p>
                  A different authorized administrator must approve this upload.
                </p>
              )}
            {!!data.actions.length && (
              <>
                <label htmlFor="publication-reason">
                  Review note (required for rejection or revocation)
                </label>
                <textarea
                  id="publication-reason"
                  maxLength={500}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
                <div className="quick-actions">
                  {data.actions.map((action) => (
                    <Button
                      key={action}
                      variant={action === "publish" ? "default" : "outline"}
                      disabled={
                        busy ||
                        (["reject", "revoke"].includes(action) &&
                          !reason.trim())
                      }
                      onClick={() => void change(action)}
                    >
                      {action === "approve"
                        ? "Approve source"
                        : action === "publish"
                          ? "Publish source"
                          : action === "reject"
                            ? "Reject source"
                            : "Revoke source"}
                    </Button>
                  ))}
                </div>
              </>
            )}
          </>
        )
      )}
    </section>
  );
}

function PreviewTable({ records }: { records: DataRecord[] }) {
  const fields = Object.keys(records[0] || {});
  return (
    <div className="data-table-wrap">
      <table>
        <caption className="sr-only">Normalized import preview</caption>
        <thead>
          <tr>
            {fields.map((f) => (
              <th key={f}>{f.replaceAll("_", " ")}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {records.map((r, i) => (
            <tr key={i}>
              {fields.map((f) => (
                <td key={f}>
                  {Array.isArray(r[f])
                    ? (r[f] as unknown[]).join(", ")
                    : text(r[f])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EditableRows({value,onChange}:{value:string;onChange:(value:string)=>void}) {
  const [offset,setOffset]=useState(0);
  let rows: DataRecord[];
  try { rows=JSON.parse(value);if(!Array.isArray(rows))throw new Error(); }
  catch {return <p role="alert">Raw corrections are not a valid row list. Fix them in Advanced before validation.</p>;}
  const columns=Object.keys(rows[0] || {}).filter(col=>!["batch","lab_batch"].includes(col) && !col.startsWith("__"));
  return <div className="section-gap"><h3>Correct extracted cells</h3>
    <p className="subtle">Edit values, then validate the field mapping. The original file is retained.</p>
    <div style={{overflow:"auto",maxHeight:360}}><table className="data-table"><caption className="sr-only">Editable extracted information</caption>
      <thead><tr>{columns.map((col)=><th key={col} scope="col">{col}</th>)}</tr></thead>
      <tbody>{rows.slice(offset,offset+10).map((row,i)=><tr key={i}>{columns.map((col)=><td key={col}><input aria-label={`Row ${offset+i+2} ${col}`} value={text(row[col])}
        onChange={(event)=>{const next=rows.map((r,j)=>j===offset+i ? {...r,[col]:event.target.value}:r);onChange(JSON.stringify(next,null,2));}}/></td>)}</tr>)}</tbody>
    </table></div>
    <p className="subtle">Rows {offset+1}–{Math.min(rows.length,offset+10)} of {rows.length}</p>
    <Button variant="outline" disabled={!offset} onClick={()=>setOffset(Math.max(0,offset-10))}>Previous rows</Button>
    <Button variant="outline" disabled={offset+10>=rows.length} onClick={()=>setOffset(offset+10)}>More rows</Button>
  </div>;
}

function Members({ w }: { w: Workspace }) {
  const { data, error } = useRemote<DataRecord[]>("/v1/admin/memberships", w);
  const [message, setMessage] = useState("");
  const [fail, setFail] = useState("");
  const [busy, setBusy] = useState(false);
  async function save(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setFail("");
    setMessage("");
    const values = Object.fromEntries(new FormData(e.currentTarget));
    try {
      await api("/v1/admin/memberships", w.session, w.tenant, {
        method: "POST",
        body: JSON.stringify({ ...values, semester: Number(values.semester) }),
      });
      setMessage("Membership saved. Access is checked on every request.");
      w.refresh();
    } catch (e) {
      setFail((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel">
      <h2>Institution memberships</h2>
      <p className="subtle" style={{ margin: "10px 0 20px" }}>
        Create the user in Supabase Auth first, then assign their institution
        access here.
      </p>
      <ErrorBox message={error || fail} />
      {message && (
        <div className="success" role="status">
          {message}
        </div>
      )}
      <form onSubmit={save}>
        <div className="upload-grid">
          <div><label htmlFor="student-number">Verified USN (optional)</label><input id="student-number" name="student_number" maxLength={10} placeholder="1RV25CI077"/><p className="subtle">Verify against institutional records. Students cannot change this identifier in chat or preferences.</p></div>
          {[
            ["user_id", "Supabase user ID"],
            ["name", "Preferred name"],
            ["department", "Department"],
            ["section", "Section"],
          ].map(([name, label]) => (
            <div key={name}>
              <label htmlFor={name}>{label}</label>
              <input
                name={name}
                id={name}
                required
                maxLength={name === "name" ? 120 : 60}
              />
            </div>
          ))}
          <div>
            <label htmlFor="member-role">Role</label>
            <select id="member-role" name="role">
              <option value="student">Student</option>
              <option value="department_admin">Department Admin</option>
              <option value="section_admin">Section Admin</option>
              <option value="canteen_manager">Canteen Manager</option>
              <option value="institution_admin">
                College Admin
              </option>
            </select>
          </div>
          <div>
            <label htmlFor="semester">Semester</label>
            <input
              id="semester"
              name="semester"
              type="number"
              min={1}
              max={12}
              defaultValue={3}
            />
          </div>
        </div>
        <Button type="submit" style={{ marginTop: 20 }} disabled={busy}>
          Save membership
        </Button>
      </form>
      {data && <PreviewTable records={data} />}
    </section>
  );
}


interface SectionCodeItem {
  id: string;
  department: string;
  semester: number;
  section: string;
  expires_at: string;
  revoked: boolean;
  is_active: boolean;
  created_at: string;
}

function SectionCodes({ w }: { w: Workspace }) {
  const [department, setDepartment] = useState(
    ["department_admin", "section_admin"].includes(w.profile.role) ? w.profile.department : "AIML"
  );
  const [semester, setSemester] = useState(
    w.profile.role === "section_admin" ? String(w.profile.semester) : "3"
  );
  const [section, setSection] = useState(
    w.profile.role === "section_admin" ? w.profile.section : "A"
  );
  const [expiryDays, setExpiryDays] = useState("7");
  const [newCode, setNewCode] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const { data, loading, error: listError } = useRemote<SectionCodeItem[]>("/v1/admin/section-codes", w);

  async function generate(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setNewCode(null);
    setCopied(false);
    try {
      const res = await api<{ code: string; expires_at: string }>(
        "/v1/admin/section-codes",
        w.session,
        w.tenant,
        {
          method: "POST",
          body: JSON.stringify({
            department,
            semester: parseInt(semester, 10),
            section: section.toUpperCase(),
            expiry_days: parseInt(expiryDays, 10),
          }),
        }
      );
      setNewCode(res.code);
      w.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to generate section code.");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: string) {
    if (!confirm("Revoke this section code? Students will no longer be able to use it.")) return;
    try {
      await api("/v1/admin/section-codes/revoke", w.session, w.tenant, {
        method: "POST",
        body: JSON.stringify({ code_id: id }),
      });
      w.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to revoke code.");
    }
  }

  function copyCode() {
    if (!newCode) return;
    navigator.clipboard.writeText(newCode);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <section className="panel">
      <h2>Section Join Codes</h2>
      <p className="subtle">
        Generate and rotate secure, time-bounded section join codes for students. Existing active codes for the selected section will be revoked automatically upon rotation.
      </p>
      <ErrorBox message={error || listError} />

      <form onSubmit={generate} className="upload-grid" style={{ marginTop: 18, marginBottom: 24 }}>
        <div className="filter-field">
          <label htmlFor="sc-dept">Department</label>
          <select
            id="sc-dept"
            value={department}
            disabled={["department_admin", "section_admin"].includes(w.profile.role)}
            onChange={(e) => setDepartment(e.target.value)}
          >
            {["AIML", "CSE", "ECE", "ISE", "MECH", "CIVIL", "EEE", "BT"].map((d) => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
        </div>
        <div className="filter-field">
          <label htmlFor="sc-sem">Semester</label>
          <select
            id="sc-sem"
            value={semester}
            disabled={w.profile.role === "section_admin"}
            onChange={(e) => setSemester(e.target.value)}
          >
            {[1, 2, 3, 4, 5, 6, 7, 8].map((s) => (
              <option key={s} value={s}>Semester {s}</option>
            ))}
          </select>
        </div>
        <div className="filter-field">
          <label htmlFor="sc-sec">Section</label>
          <select
            id="sc-sec"
            value={section}
            disabled={w.profile.role === "section_admin"}
            onChange={(e) => setSection(e.target.value)}
          >
            {["A", "B", "C", "D"].map((s) => (
              <option key={s} value={s}>Section {s}</option>
            ))}
          </select>
        </div>
        <div className="filter-field">
          <label htmlFor="sc-exp">Validity</label>
          <select
            id="sc-exp"
            value={expiryDays}
            onChange={(e) => setExpiryDays(e.target.value)}
          >
            <option value="3">3 days</option>
            <option value="7">7 days (recommended)</option>
            <option value="14">14 days</option>
            <option value="30">30 days</option>
          </select>
        </div>
        <div style={{ display: "flex", alignItems: "flex-end" }}>
          <Button type="submit" disabled={busy} style={{ width: "100%" }}>
            {busy ? "Generating…" : "Generate Join Code"}
          </Button>
        </div>
      </form>

      {newCode && (
        <div className="code-display-box" role="status">
          <div>
            <div style={{ fontSize: 12, fontWeight: 700, textTransform: "uppercase", letterSpacing: "1px" }}>
              New Active Code · Share with {department} Sem {semester} Section {section}
            </div>
            <code>{newCode}</code>
          </div>
          <Button type="button" variant="outline" onClick={copyCode}>
            {copied ? <Check size={16} /> : <Copy size={16} />}
            {copied ? "Copied!" : "Copy Code"}
          </Button>
        </div>
      )}

      <h3 style={{ marginTop: 24, marginBottom: 12 }}>Active & Historical Codes</h3>
      {loading ? (
        <Loading />
      ) : data?.length ? (
        <div className="table-wrap">
          <table className="sources-table">
            <thead>
              <tr>
                <th>Scope</th>
                <th>Status</th>
                <th>Expires</th>
                <th>Created</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.map((c) => (
                <tr key={c.id}>
                  <td>
                    <strong>{c.department}</strong> · Sem {c.semester} · Sec {c.section}
                  </td>
                  <td>
                    {c.is_active ? (
                      <span className="badge badge-success">Active</span>
                    ) : c.revoked ? (
                      <span className="badge badge-muted">Revoked</span>
                    ) : (
                      <span className="badge badge-warn">Expired</span>
                    )}
                  </td>
                  <td>{prettyDate(c.expires_at)}</td>
                  <td>{prettyDate(c.created_at)}</td>
                  <td>
                    {c.is_active && (
                      <Button
                        type="button"
                        variant="ghost"
                        onClick={() => revoke(c.id)}
                      >
                        Revoke
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty message="No section join codes generated yet. Generate a code above to allow students to join their section." />
      )}
    </section>
  );
}
