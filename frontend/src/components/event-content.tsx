"use client";
import {type DataRecord,prettyDate,text} from "@/lib/api";

function safeLink(value:unknown){
  try{const url=new URL(String(value));return url.protocol==='https:'&&!url.username&&!url.password?url.href:null;}catch{return null;}
}
export function EventContent({record:r}:{record:DataRecord}){
  const date=r.start_date||r.start;
  const registration=safeLink(r.registration_url);
  const fields=[['Organizer',r.organizer||r.organizing_club||r.hosting_department],['Venue',r.location],
    ['Registration deadline',r.registration_deadline?prettyDate(r.registration_deadline):null]];
  return <>
    <div className="record-kicker"><span>{date?prettyDate(date):'Date not announced'}</span><span>{text(r.category||r.kind||'Other')}</span></div>
    <h3>{text(r.title)}</h3>
    {r.event_status&&<p className="subtle">{r.event_status==='undated'?'Date not announced':text(r.event_status)}</p>}
    {r.description&&<p>{text(r.description)}</p>}
    {r.start_time&&<p>Time: {text(r.start_time).slice(0,5)}{r.end_time?`–${text(r.end_time).slice(0,5)}`:''}</p>}
    {fields.filter(([,value])=>value!=null&&value!=='').map(([label,value])=><p key={String(label)}><strong>{String(label)}:</strong> {text(value)}</p>)}
    {r.registration_fee!=null&&<p><strong>Registration fee:</strong> {r.fee_currency==='INR'?'₹':r.fee_currency?`${text(r.fee_currency)} `:''}{text(r.registration_fee)}{!r.fee_currency?' (currency not specified)':''}</p>}
    {r.registration_status==='closed'&&<p role="status">Registration deadline has passed.</p>}
    {registration&&<a className="text-link" href={registration} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">{r.registration_status==='closed'?'Registration details (deadline passed)':'Registration link'}</a>}
    <details><summary>Event details and source</summary>
      {!!r.full_description&&<p style={{whiteSpace:'pre-wrap'}}>{text(r.full_description)}</p>}
      {['eligibility','team_size','registration_instructions','faculty_coordinators','student_coordinators','contacts','prizes','certificates','agenda','rules','materials','additional_instructions','mode','building','campus','submission_deadline'].filter(key=>r[key]!=null&&r[key]!=='').map(key=><p key={key} style={{whiteSpace:'pre-wrap'}}><strong>{key.replaceAll('_',' ')}:</strong> {text(r[key])}</p>)}
      {safeLink(r.meeting_url)&&<p><a href={safeLink(r.meeting_url)!} target="_blank" rel="noopener noreferrer">Meeting link</a></p>}
      {safeLink(r.qr_url)&&<p><a href={safeLink(r.qr_url)!} target="_blank" rel="noopener noreferrer">Reviewed QR destination</a></p>}
      {Array.isArray(r.external_links)&&r.external_links.map((link,i)=>safeLink(link)?<p key={i}><a href={safeLink(link)!} target="_blank" rel="noopener noreferrer">Related link {i+1}</a></p>:null)}
      {Array.isArray(r.important_dates)&&r.important_dates.map((raw,i)=>{const item=raw as {label:string;on:string};return <p key={i}><strong>{item.label}:</strong> {prettyDate(item.on)}</p>;})}
      <p className="subtle">Source: {text(r.source_filename)}</p>
    </details>
  </>;
}
