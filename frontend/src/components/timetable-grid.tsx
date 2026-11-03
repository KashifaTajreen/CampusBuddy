"use client";
import { type DataRecord, text } from "@/lib/api";
import { DemoBadge, Empty } from "@/components/result-cards";

const DAYS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];
const TITLES = /^(dr|prof|professor|mr|mrs|ms)\.?$/i;

function clock(value: unknown) {
  return String(value ?? "").slice(0, 5);
}
function initialsOf(name: string) {
  return name
    .split(/\s+/)
    .filter((part) => part && !TITLES.test(part))
    .map((part) => part[0].toUpperCase())
    .join("");
}
/** Short teacher labels. Staff codes replace initials that two people would share. */
function teacherLabels(records: DataRecord[]) {
  const names = new Map<string, string>();
  for (const r of records) {
    const code = text(r.faculty_code);
    if (!names.has(code) || r.faculty) names.set(code, text(r.faculty || code));
  }
  const counts = new Map<string, number>();
  for (const name of names.values()) {
    const initials = initialsOf(name);
    counts.set(initials, (counts.get(initials) ?? 0) + 1);
  }
  const labels = new Map<string, { label: string; name: string }>();
  for (const [code, name] of names) {
    const initials = initialsOf(name);
    labels.set(code, {
      label: initials && counts.get(initials) === 1 ? initials : code,
      name,
    });
  }
  return labels;
}

export function TimetableGrid({
  records,
  caption = "Weekly timetable",
  onEdit,
  onSource,
  emptyHint,
}: {
  records: DataRecord[];
  caption?: string;
  onEdit?: (record: DataRecord) => void;
  onSource?: (id: string) => void;
  emptyHint?: string;
}) {
  if (!records.length)
    return (
      <Empty
        message="No classes in this timetable."
        hint={
          emptyHint ??
          "Try another week, or ask an administrator to import the timetable."
        }
      />
    );
  const periods = [
    ...new Set(
      records.map((r) => `${clock(r.start_time)}-${clock(r.end_time)}`),
    ),
  ].sort();
  // Weekdays always show. Weekends show only when a class is scheduled on them.
  const days = DAYS.map((name, index) => ({ name, index })).filter(
    ({ index }) => index < 5 || records.some((r) => Number(r.day) === index),
  );
  const labels = teacherLabels(records);
  const sources = new Map<string, string>();
  for (const r of records)
    if (r.source_id)
      sources.set(r.source_id, text(r.source_filename || "Source file"));
  return (
    <div className="timetable-grid">
      {records.some((r) => r.demo) && (
        <div className="timetable-demo">
          <DemoBadge />
        </div>
      )}
      <div
        className="timetable-scroll"
        role="region"
        aria-label={`${caption}, scrollable`}
        tabIndex={0}
      >
        <table>
          <caption className="sr-only">{caption}</caption>
          <thead>
            <tr>
              <th scope="col" className="timetable-period">
                Time
              </th>
              {days.map(({ name }) => (
                <th scope="col" key={name}>
                  {name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {periods.map((period) => (
              <tr key={period}>
                <th scope="row" className="timetable-period">
                  {period.replace("-", " to ")}
                </th>
                {days.map(({ name, index }) => {
                  const cell = records.filter(
                    (r) =>
                      Number(r.day) === index &&
                      `${clock(r.start_time)}-${clock(r.end_time)}` === period,
                  );
                  return (
                    <td key={name}>
                      {cell.length === 0 && (
                        <span className="sr-only">No class</span>
                      )}
                      {cell.map((r, i) => {
                        const teacher = labels.get(text(r.faculty_code));
                        const body = (
                          <>
                            <strong>{text(r.subject)}</strong>
                            <span className="timetable-meta">
                              <abbr title={teacher?.name}>{teacher?.label}</abbr>
                              <span aria-hidden="true"> · </span>
                              <span>{text(r.room)}</span>
                            </span>
                          </>
                        );
                        return onEdit ? (
                          <button
                            type="button"
                            key={String(r.id ?? i)}
                            className="timetable-class"
                            onClick={() => onEdit(r)}
                            aria-label={`Edit ${text(r.subject)}, ${name}, ${period.replace("-", " to ")}`}
                          >
                            {body}
                          </button>
                        ) : (
                          <div
                            key={String(r.id ?? i)}
                            className="timetable-class"
                          >
                            {body}
                          </div>
                        );
                      })}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="timetable-legend">
        <h2>Teachers</h2>
        <dl>
          {[...labels.values()]
            .sort((a, b) => a.label.localeCompare(b.label))
            .map(({ label, name }) => (
              <div key={`${label}-${name}`}>
                <dt>{label}</dt>
                <dd>{name}</dd>
              </div>
            ))}
        </dl>
        {onSource && sources.size > 0 && (
          <div className="timetable-sources">
            <h2>Sources</h2>
            {[...sources].map(([id, filename]) => (
              <button type="button" key={id} onClick={() => onSource(id)}>
                {filename}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
