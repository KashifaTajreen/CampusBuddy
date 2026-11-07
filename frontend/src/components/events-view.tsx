"use client";
import {useState} from 'react';
import {type Workspace,useRemote,ErrorBox,Loading} from './campus-app';
import {type DataRecord} from '@/lib/api';
import {RecordList,Empty} from './result-cards';

export function EventsView({w}:{w:Workspace}){
  const [status,setStatus]=useState('all');const [category,setCategory]=useState('');
  const [query,setQuery]=useState('');const [limit,setLimit]=useState(20);
  const data=useRemote<DataRecord[]>(`/v1/events?status=${status}&category=${encodeURIComponent(category)}&query=${encodeURIComponent(query)}`,w);
  const all=useRemote<DataRecord[]>('/v1/events?status=all',w);
  const categories=Array.from(new Set((all.data||[]).map(r=>String(r.category||r.kind||'Other')))).sort();
  const groups=[['upcoming','Upcoming events'],['ongoing','Ongoing / today'],['past','Past events'],['undated','Date not announced']];
  return <>
    <div className="page-heading"><div><h1>Events</h1><p>Official campus activities in your authorized scope.</p></div></div>
    <div className="filter-grid"><div><label htmlFor="event-status">Event status</label><select id="event-status" value={status} onChange={e=>{setStatus(e.target.value);setLimit(20);}}><option value="all">All published events</option>{groups.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></div>
      <div><label htmlFor="event-category-filter">Event category</label><select id="event-category-filter" value={category} onChange={e=>setCategory(e.target.value)}><option value="">All categories</option>{categories.map(c=><option key={c} value={c}>{c}</option>)}</select></div>
      <div><label htmlFor="event-search">Search events</label><input id="event-search" value={query} onChange={e=>setQuery(e.target.value)} placeholder="Title, category or organizer"/></div>
    </div>
    <ErrorBox message={data.error}/>{data.loading?<Loading/>:!data.data?.length?<Empty message="No matching published events" hint="Drafts are private to administrators. Try another category or status."/>:groups.map(([state,label])=>{
      const rows=data.data!.filter(r=>r.event_status===state);return rows.length?<section key={state} className="section-gap" aria-label={label}><h2>{label}</h2><RecordList records={rows} kind="events" onSource={w.download} limit={limit}/>{rows.length>limit&&<button onClick={()=>setLimit(limit+20)}>Show more events</button>}</section>:null;
    })}
  </>;
}
