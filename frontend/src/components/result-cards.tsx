"use client";
import {EventContent} from './event-content';
import {
  MapPin,
  Clock3,
  UserRound,
  Leaf,
  ExternalLink,
  FileText,
  Check,
  TriangleAlert,
  Mail,
} from "lucide-react";
import { type DataRecord, prettyDate, text as rawText } from "@/lib/api";
import { Button } from "@/components/ui/button";

function text(value:unknown) { return rawText(value) || "Not Available"; }

export function DemoBadge() {
  return <span className="demo-badge">Demo Data</span>;
}
export function Empty({
  message = "No records yet.",
  hint = "Try another day or ask an administrator to import the relevant source.",
}: {
  message?: string;
  hint?: string;
}) {
  return (
    <div className="empty">
      <FileText size={24} aria-hidden="true" />
      <strong>{message}</strong>
      <p>{hint}</p>
    </div>
  );
}
function asRecord(value: unknown): DataRecord {
  return (value && typeof value === "object" ? value : {}) as DataRecord;
}

export function RecordCard({
  record: r,
  kind,
  onSource,
}: {
  record: DataRecord;
  kind: string;
  onSource?: (id: string) => void;
}) {
  const timeRange = (a: unknown, b: unknown) =>
    `${text(a).includes("T") ? text(a).split("T")[1].slice(0, 5) : text(a).slice(0, 5)} – ${text(b).includes("T") ? text(b).split("T")[1].slice(0, 5) : text(b).slice(0, 5)}`;
  const source = typeof r.source_id === "string" ? r.source_id : undefined;
  let content: React.ReactNode;
  if (kind === "calendar")
    content = <><div className="record-kicker">{prettyDate(r.start_on)} – {prettyDate(r.end_on)}</div><h3>{text(r.title)}</h3><p>{r.classes_cancelled ? "Classes cancelled by official calendar" : text(r.entry_kind)}</p></>;
  else if (kind === "availability")
    content=<><div className="record-kicker">{prettyDate(r.date)} · {text(r.time)}</div><h3>{r.status === "busy" ? "Recorded commitment" : r.status === "schedule_unknown" ? "Timetable coverage unavailable" : "No recorded conflict"}</h3>
      <p>{r.status === "schedule_unknown" ? "Missing data does not establish free time." : "Check the cited schedule and other personal commitments."}</p>
      {Array.isArray(r.commitments) && r.commitments.map((item,i)=><p key={i}>{text(asRecord(item).subject || asRecord(item).title)}</p>)}
    </>;
  else if (kind === "classes")
    content = (
      <>
        <div className="record-kicker">
          {prettyDate(r.date || r.start)}
          <span>{timeRange(r.start_time || r.start, r.end_time || r.end)}</span>
        </div>
        <h3>{text(r.subject)}</h3>
          <p className="subtle">Course code: {text(r.course_code)}</p>
        <div className="record-details">
          <span>
            <MapPin size={14} />
            {text(r.room)}
          </span>
          <span>
            <UserRound size={14} />
            {text(r.faculty || r.faculty_code)}
          </span>
        </div>
      </>
    );
  else if (kind === "food")
    content = (
      <>
        <div className="record-kicker">
          <span className={r.vegetarian === true ? "veg" : r.vegetarian === false ? "nonveg" : "subtle"}>
            <Leaf size={13} />
            {r.vegetarian === true ? "Vegetarian" : r.vegetarian === false ? "Non-vegetarian" : "Diet not specified"}
          </span>
          <strong className="price">₹{text(r.price)}</strong>
        </div>
        <h3>{text(r.name)}</h3>
        <p>
          {r.location ? text(r.location) : "Location not specified"} · {r.available ? "Available" : "Unavailable"}
        </p>
        <div className="nutrition">
          {r.protein !== null && r.protein !== undefined
            ? `${r.protein} g protein`
            : "Protein not supplied"}
          {r.calories !== null && r.calories !== undefined
            ? ` · ${r.calories} kcal`
            : ""}{" "}
          · {r.nutrition_verified ? "Verified" : "Estimated"}
        </div>
      </>
    );
  else if (kind === "faculty")
    content = (
      <>
        <div className="record-kicker">
          {text(r.department)} · {text(r.designation)}
        </div>
        <h3>{text(r.name)}</h3>
        <p>{text(r.subjects)}</p>
        <div className="record-details">
          <span>
            <MapPin size={14} />
            {text(r.office)}
          </span>
          <span>
            <Clock3 size={14} />
            {text(r.office_hours)}
          </span>
        </div>
        {!!r.contact && (
          <p className="official-contact">
            Official contact: {text(r.contact)}
          </p>
        )}
      </>
    );
  else if (kind === "events")
    content = <EventContent record={r}/>;
  else if (kind === "event_fit") {
    const e = asRecord(r.event);
    const clashes = Array.isArray(r.clashes) ? (r.clashes as DataRecord[]) : [];
    content = (
      <>
        <div className="record-kicker">{prettyDate(e.start)}</div>
        <h3>{text(e.title)}</h3>
        <p>
          {timeRange(e.start, e.end)} · {text(e.location)}
        </p>
        <p className={r.can_attend ? "status-good" : "status-warn"}>
          {r.can_attend ? <Check size={16} /> : <TriangleAlert size={16} />}
          {r.can_attend
            ? "You can attend without missing class"
            : "This overlaps with your timetable"}
        </p>
        {clashes.map((cl, i) => (
          <p key={i}>
            {text(cl.subject)} · {timeRange(cl.start, cl.end)} · {text(cl.room)}
          </p>
        ))}
      </>
    );
  } else if (kind === "meals") {
    const items = Array.isArray(r.items) ? (r.items as DataRecord[]) : [];
    content = (
      <>
        <div className="record-kicker">
          Meal combination<strong className="price">₹{text(r.total)}</strong>
        </div>
        <h3>{items.map((item) => text(item.name)).join(" + ")}</h3>
        <p>{items.map((item) => `${item.name}: ₹${item.price}`).join(" · ")}</p>
        <div className="nutrition">
          {text(r.protein)} g estimated protein ·{" "}
          {items.every((item) => item.vegetarian === true)
            ? "Vegetarian"
            : items.some((item) => item.vegetarian === false) ? "Contains non-vegetarian items" : "Diet not fully specified"}
        </div>
      </>
    );
  } else if (kind === "slots")
    content = (
      <>
        <div className="record-kicker">
          {prettyDate(r.start)}
          <span>{text(r.minutes)} min free</span>
        </div>
        <h3>{timeRange(r.start, r.end)}</h3>
        <p>Clear of your listed classes and visible activities.</p>
      </>
    );
  else if (kind === "conflicts") {
    const a = asRecord(r.first),
      b = asRecord(r.second);
    content = (
      <>
        <div className="record-kicker status-warn">
          <TriangleAlert size={14} />
          Schedule overlap · {prettyDate(a.start)}
        </div>
        <h3>{text(a.subject || a.title)}</h3>
        <p>{timeRange(a.start, a.end)}</p>
        <h3>{text(b.subject || b.title)}</h3>
        <p>{timeRange(b.start, b.end)}</p>
      </>
    );
  } else if (kind === "emails")
    content = (
      <>
        <div className="record-kicker">
          <Mail size={14} />
          {r.unread ? "Unread message" : "Message"}
          <span>{prettyDate(r.received)}</span>
        </div>
        <h3>{text(r.subject)}</h3>
        <p>From {text(r.sender)}</p>
        <p className="passage">{text(r.body)}</p>
        {Array.isArray(r.extracted_dates) && r.extracted_dates.length > 0 && (
          <p>Dates mentioned: {r.extracted_dates.join(", ")}</p>
        )}
      </>
    );
  else if (kind === "passages")
    content = (
      <>
        <div className="record-kicker">
          {r.sheet ? `Worksheet: ${text(r.sheet)} · Row ${text(r.row)}` : `Page ${text(r.page)}`}
          <span>{r.outdated ? "Archived / expired" : "Source passage"}</span>
        </div>
        <h3>{text(r.title)}</h3>
        <p className="passage">
          {r.suspicious
            ? "Instruction-like content withheld. Inspect the original source to review."
            : text(r.text)}
        </p>
        <p className="subtle">
          Published {prettyDate(r.published)} · {r.match_type === "exact_identifier" ? "Exact verified identifier match" : `Similarity ${text(r.score)} (ranking signal)`}
        </p>
      </>
    );
  else
    content = (
      <>
        <div className="record-kicker">
          {text(r.category || "Campus notice")}
          <span>{prettyDate(r.published)}</span>
        </div>
        <h3>{text(r.title)}</h3>
        {!!r.expires && <p>Expires {prettyDate(r.expires)}</p>}
        {!!r.supersedes_id && <p>Replaces an earlier version.</p>}
      </>
    );
  const nestedDemo = kind === "event_fit" && !!asRecord(r.event).demo;
  return (
    <article
      className={`record-card ${kind === "classes" ? "class-card" : ""}`}
    >
      {content}
      <footer>
        {(r.demo || nestedDemo) && <DemoBadge />}
        {source && onSource && r.original_download_allowed !== false && (
          <Button variant="ghost" size="sm" onClick={() => onSource(source)}>
            <FileText size={13} />
            View source
          </Button>
        )}
        {source && r.original_download_allowed === false && (
          <span className="subtle">
            Original restricted to institution administrators
          </span>
        )}
      </footer>
    </article>
  );
}

export function RecordList({
  records,
  kind,
  onSource,
  limit = 8,
}: {
  records: DataRecord[];
  kind: string;
  onSource?: (id: string) => void;
  limit?: number;
}) {
  if (!records.length) return <Empty />;
  return (
    <>
      <div className={`record-list ${kind === "food" ? "food-grid" : ""}`}>
        {records.slice(0, limit).map((r, i) => (
          <RecordCard
            key={text(r.id) || i}
            record={r}
            kind={kind}
            onSource={onSource}
          />
        ))}
      </div>
      {records.length > limit && (
        <details className="more-results">
          <summary>Show {records.length - limit} more results</summary>
          <div className="record-list">
            {records.slice(limit).map((r, i) => (
              <RecordCard
                key={text(r.id) || i}
                record={r}
                kind={kind}
                onSource={onSource}
              />
            ))}
          </div>
        </details>
      )}
    </>
  );
}
