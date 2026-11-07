"""Events-only reviewed extraction and atomic structured + semantic indexing."""
import math
from sqlalchemy import select,delete
from pydantic import ValidationError
from fastapi import HTTPException

from backend.app.event_data import OfficialEventData,EventReviewRequest
from backend.app.event_extraction import suggest
from backend.app.models import Document,DocumentChunk,AuditLog
from backend.app.schemas import ImportOptions
from backend.app.parsing import parse_file
from backend.app.retrieval import chunks,embeddings


def event_embeddings(texts):
    vectors,model=embeddings(texts)
    if not model.startswith('onnx-e5-'):
        raise ValueError('Event indexing requires the configured local E5-small model. The reviewed draft is preserved; configure E5 and retry.')
    if len(vectors)!=len(texts) or any(len(v)!=384 or not all(math.isfinite(float(n)) for n in v) for v in vectors):
        raise ValueError('Event indexing failed: incompatible E5 embedding dimensions. The draft is preserved.')
    return vectors,model


class EventPipeline:
    def __init__(self,ingestion):
        self.i=ingestion
        self.c=ingestion.c

    def parsed(self,job,source,provided=None):
        return provided or job.staged_data.get('ocr_result') or parse_file(source.filename,
            self.i.store.get(source.storage_key),import_kind='events')

    def reviewed(self,parsed,options):
        pages=[(int(p),t) for p,t in parsed['pages']]
        if options.event_pages is not None:
            if {p.page for p in options.event_pages}!={p for p,_ in pages}:
                raise ValueError('Keep the original event page identifiers for citations.')
            pages=[(p.page,p.text) for p in options.event_pages]
        rows,warnings=suggest(pages,parsed.get('rows',[]),options.event_category)
        rows=options.event_rows if options.event_rows is not None else rows
        if parsed.get('qr_url') and len(rows)==1 and not rows[0].get('qr_url'):
            rows=[{**rows[0],'qr_url':parsed['qr_url']}]
        if len(rows)>100:
            raise ValueError('Split event lists larger than 100 events.')
        ready,errors,display=[],[],[]
        full='\n\n'.join(t for _,t in pages).strip()
        for index,raw in enumerate(rows):
            data=dict(raw)
            if data.get('department',options.department)!=options.department:
                raise HTTPException(403,'Every event and its reviewed text must use the selected authorized publication scope.')
            data.update(department=options.department,semester=options.semester,section=options.section)
            if len(rows)==1:
                data['full_description']=full or data.get('full_description')
            try:
                record=OfficialEventData.model_validate(data).model_dump(mode='json')
                self.c.actor.check_department_write(record['department'],'events',record['semester'],record['section'])
                ready.append(record)
                display.append(record)
            except ValidationError as exc:
                errors.append(dict(row=index+1,message='; '.join(f"{'.'.join(map(str,e['loc']))}: {e['msg']}" for e in exc.errors())))
                display.append(data)
        if not full:
            errors.append(dict(row=0,message='Enter the reviewed event text or a manual transcription before indexing.'))
        return pages,display,ready,errors,warnings

    def preview(self,job,source,options,parsed=None):
        parsed=self.parsed(job,source,parsed)
        pages,rows,ready,errors,warnings=self.reviewed(parsed,options)
        job.preview=dict(filename=source.filename,format=parsed['format'],event_workflow=True,
            message='Review the event details and full text. Optional dates, venue and registration information may remain unknown.',
            event_rows=rows,event_pages=[dict(page=p,text=t) for p,t in pages],event_reviewed=options.event_reviewed,
            valid_count=len(ready),error_count=len(errors),errors=errors[:20],warnings=list(dict.fromkeys(parsed.get('warnings',[])+warnings)),
            ocr=bool(parsed.get('needs_review')),ocr_reviewed=options.ocr_reviewed,
            qr_supported=parsed.get('qr_supported',False),qr_url=parsed.get('qr_url'),event_index_ready=False,
            fields=[],columns=[])
        job.status,job.error='preview',None
        self.c.db.flush()
        return job

    def save_review(self,job,body:EventReviewRequest,reviewed=True):
        source=self.c.management_source(job.source_id)
        self.c.actor.check_source_write(source)
        if source.kind!='events' or source.publication_status!='draft' or source.file_state!='active' or job.status not in ('preview','failed'):
            raise ValueError('Only a draft event preview can be edited.')
        options=ImportOptions.model_validate({**job.options,'event_workflow':True,'event_rows':body.events,
            'event_pages':body.pages,'ocr_reviewed':body.ocr_reviewed if reviewed else False,'event_reviewed':reviewed})
        job.options=options.model_dump(mode='json')
        self.c.db.add(AuditLog(tenant_id=self.c.actor.tenant_id,user_id=self.c.actor.user_id,
            action='event.reviewed' if reviewed else 'event.draft_saved',details=dict(job_id=job.id,event_count=len(body.events))))
        return self.preview(job,source,options)

    def process(self,job,source,options):
        if not options.event_reviewed:
            raise ValueError('Validate the reviewed event details before processing.')
        parsed=self.parsed(job,source)
        pages,_,rows,errors,_=self.reviewed(parsed,options)
        if errors or not rows:
            raise ValueError('Event validation failed. Correct the reviewed fields before retrying.')
        if parsed.get('needs_review') and not options.ocr_reviewed:
            raise ValueError('Review the OCR text against the original event poster before processing.')
        from backend.app.ingestion import natural_key
        parts=[]
        if len(rows)==1:
            parts=[(p,t,natural_key('events',rows[0])) for p,t in chunks(pages)]
        else:
            parts=[(p,t,None) for p,t in chunks(pages)]
            for row in rows:
                text=row.get('full_description') or '\n'.join(f'{key}: {value}' for key,value in row.items() if value is not None)
                text=row['title']+'\n'+text
                parts.extend((p,t,natural_key('events',row)) for p,t in chunks([(1,text)]))
        if not parts:
            raise ValueError('No reviewed text is available for event indexing.')
        try:
            vectors,model=event_embeddings([text for _,text,_ in parts])
        except Exception as exc:
            raise ValueError('Event indexing failed. Reviewed fields and text remain saved. Check local E5 configuration and retry publication.') from exc
        document=self.c.db.scalar(select(Document).where(Document.source_id==source.id,Document.tenant_id==source.tenant_id))
        if document is None:
            document=Document(tenant_id=source.tenant_id,source_id=source.id,title=options.title or source.filename,
                category='Campus Event',department=source.department,semester=source.semester,section=source.section,
                owner_id=None,published=str(self.c.local_now().date()))
            self.c.db.add(document)
            self.c.db.flush()
        else:
            self.c.db.execute(delete(DocumentChunk).where(DocumentChunk.document_id==document.id,DocumentChunk.tenant_id==source.tenant_id))
        for (page,text,key),vector in zip(parts,vectors):
            self.c.db.add(DocumentChunk(tenant_id=source.tenant_id,document_id=document.id,page=page,text=text,
                embedding=vector,embedding_model=model,student_numbers=[],row_data={'event_key':key,'event_evidence':True}))
        job.staged_data={**job.staged_data,'records':rows,'reviewed_event_pages':[dict(page=p,text=t) for p,t in pages]}
        job.preview={**job.preview,'event_index_ready':True,'embedding_model':model,'indexed_chunks':len(parts),
                     'valid_count':len(rows),'error_count':0}
        return len(rows)
