"use client";
import { useEffect, useRef } from "react";
import { useRemote, ErrorBox, Loading, type Workspace } from "./campus-app";
import { text, type DataRecord } from "@/lib/api";

interface Preview {
  filename: string; format: string; truncated: boolean; warnings: string[];
  images: {page: number; data: string}[];
  tables: {name: string; columns: string[]; rows: DataRecord[]; total_rows: number}[];
  text: {page: number; text: string}[];
  blocks: {text: string; heading: boolean}[];
}

export function FileContentPreview({ w, sourceId }: {w: Workspace; sourceId: string}) {
  const {data,error,loading} = useRemote<Preview>(`/v1/admin/sources/${sourceId}/content-preview`,w);
  return <section aria-label="Original file content preview" className="section-gap">
    <h3>Original file preview</h3>
    <p className="subtle">A bounded view of the uploaded file, before processing or publication. Extracted corrections below do not change this original.</p>
    <ErrorBox message={error}/>{loading && <Loading/>}
    {data && <>
      {data.images.map((p) => /^data:image\/png;base64,/.test(p.data) && <figure key={p.page}>
        {/* Safe raster output from the bounded server renderer; no uploaded HTML. */}
        <img src={p.data} alt={`Original ${data.format === "pdf" ? "page" : "image"} ${p.page}`} style={{maxWidth:"100%",maxHeight:600,objectFit:"contain"}}/>
        <figcaption>Original {data.format === "pdf" ? `page ${p.page}` : "image"}</figcaption>
      </figure>)}
      {data.tables.map((table,i) => <div key={i} className="section-gap">
        <h4>{table.name}</h4><p className="subtle">Showing {table.rows.length} of {table.total_rows} rows. Original headers and values are preserved.</p>
        <div style={{overflow:"auto",maxHeight:420}} tabIndex={0} aria-label={`${table.name} worksheet rows`}>
          <table className="data-table"><caption className="sr-only">{table.name}</caption>
            <thead><tr>{table.columns.map((col,j)=><th scope="col" key={j}>{col}</th>)}</tr></thead>
            <tbody>{table.rows.map((row,j)=><tr key={j}>{table.columns.map((col,k)=><td key={k}>{text(row[col])}</td>)}</tr>)}</tbody>
          </table>
        </div>
      </div>)}
      {data.blocks.length ? data.blocks.map((b,i)=>b.heading ? <h4 key={i}>{b.text}</h4> : <p key={i} style={{whiteSpace:"pre-wrap"}}>{b.text}</p>) : data.text.map((p)=><details key={p.page} open={!data.images.length}>
        <summary>{data.images.length ? "Extracted text" : "Text / slide content"} · location {p.page}</summary><pre style={{whiteSpace:"pre-wrap",overflowWrap:"anywhere",maxHeight:350,overflow:"auto"}}>{p.text}</pre>
      </details>)}
      {data.warnings.map((warning,i)=><p className="subtle" key={i}>{warning}</p>)}
      {data.truncated && <p role="status">This preview is limited to keep large files responsive. The original is retained in full.</p>}
    </>}
  </section>;
}

export function ConfirmationDialog({title,onClose,children}: {title:string;onClose:()=>void;children:React.ReactNode}) {
  const ref = useRef<HTMLDialogElement>(null);
  const close = useRef(onClose);
  close.current=onClose;
  useEffect(()=>{
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog=ref.current;
    dialog?.showModal();
    return ()=>{dialog?.close();if(previous?.isConnected) previous.focus({preventScroll:true});};
  },[]);
  return <dialog ref={ref} aria-label={title} onCancel={(event)=>{event.preventDefault();close.current();}}
    onKeyDown={(event)=>{
      if(event.key!=="Tab")return;
      const dialog=ref.current;
      if(!dialog)return;
      const controls=Array.from(dialog.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'))
        .filter((element)=>element.getClientRects().length>0);
      if(!controls.length){event.preventDefault();dialog.focus();return;}
      const index=controls.indexOf(document.activeElement as HTMLElement);
      event.preventDefault();
      const next=index<0 ? (event.shiftKey?controls.length-1:0) : (index+(event.shiftKey?-1:1)+controls.length)%controls.length;
      controls[next].focus();
    }} tabIndex={-1}
    className="campus-confirmation-dialog">
    <h2>{title}</h2>{children}
  </dialog>;
}
