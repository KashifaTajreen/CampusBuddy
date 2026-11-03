"use client";
import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Plus } from "lucide-react";
import {
  type Workspace,
  useRemote,
  ErrorBox,
  Loading,
} from "@/components/campus-app";
import { api, campusDate, text, type DataRecord } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { TimetableGrid } from "@/components/timetable-grid";

const DAYS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];
const classSchema = z
  .object({
    subject: z.string().trim().min(1, "Enter a subject.").max(200),
    faculty_code: z.string().min(1, "Choose a teacher."),
    room: z.string().trim().min(1, "Enter a room.").max(100),
    day: z.string().regex(/^[0-6]$/, "Choose a day."),
    start_time: z.string().regex(/^\d{2}:\d{2}$/, "Enter a start time."),
    end_time: z.string().regex(/^\d{2}:\d{2}$/, "Enter an end time."),
    effective_from: z.string().min(1, "Choose a start date."),
    effective_to: z.string().min(1, "Choose an end date."),
  })
  .refine((v) => v.start_time < v.end_time, {
    path: ["end_time"],
    message: "End time must be after the start time.",
  })
  .refine((v) => v.effective_from <= v.effective_to, {
    path: ["effective_to"],
    message: "End date must not be before the start date.",
  });
type ClassValues = z.infer<typeof classSchema>;
type Editing = { record: DataRecord | null; defaults: ClassValues };

function addDays(day: string, count: number) {
  const value = new Date(day + "T12:00:00");
  value.setDate(value.getDate() + count);
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}

export function TimetableEditor({ w }: { w: Workspace }) {
  const [department, setDepartment] = useState(w.profile.department);
  const [semester, setSemester] = useState(w.profile.semester);
  const [section, setSection] = useState(w.profile.section);
  const [sectionDraft, setSectionDraft] = useState(w.profile.section);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [message, setMessage] = useState("");
  const params = new URLSearchParams({
    department,
    semester: String(semester),
    section,
  });
  const { data, error, loading } = useRemote<DataRecord[]>(
    "/v1/admin/timetable?" + params,
    w,
  );
  const directory = useRemote<DataRecord[]>(
    "/v1/faculty?" + new URLSearchParams({ query: department }),
    w,
  );
  // The unfiltered directory supplies the department list, which the server scopes per role.
  const allFaculty = useRemote<DataRecord[]>("/v1/faculty", w);
  const rows = data ?? [];

  // A form and message belong to one section, so changing section clears them.
  function changeScope(apply: () => void) {
    setEditing(null);
    setMessage("");
    apply();
  }
  function commitSection() {
    const next = sectionDraft.trim();
    if (!next) setSectionDraft(section);
    else if (next !== section) changeScope(() => setSection(next));
  }
  function addClass() {
    const today = campusDate(w.profile.timezone);
    const first = rows[0];
    setMessage("");
    setEditing({
      record: null,
      defaults: {
        subject: "",
        faculty_code: "",
        room: "",
        day: "0",
        start_time: "09:00",
        end_time: "10:00",
        // New classes follow the validity dates the section already uses.
        effective_from: first ? text(first.effective_from) : today,
        effective_to: first ? text(first.effective_to) : addDays(today, 180),
      },
    });
  }
  function editClass(record: DataRecord) {
    setMessage("");
    setEditing({
      record,
      defaults: {
        subject: text(record.subject),
        faculty_code: text(record.faculty_code),
        room: text(record.room),
        day: String(record.day),
        start_time: text(record.start_time).slice(0, 5),
        end_time: text(record.end_time).slice(0, 5),
        effective_from: text(record.effective_from),
        effective_to: text(record.effective_to),
      },
    });
  }
  function saved(note: string) {
    setEditing(null);
    setMessage(note);
    w.refresh();
  }
  const departments = [
    ...new Set([
      w.profile.department,
      department,
      ...(allFaculty.data ?? []).map((f) => text(f.department)),
    ]),
  ]
    .filter(Boolean)
    .sort();
  return (
    <section className="panel">
      <div className="panel-heading">
        <h2>Edit a section&apos;s timetable</h2>
        <Button type="button" onClick={addClass} disabled={loading || !!error}>
          <Plus size={16} />
          Add class
        </Button>
      </div>
      <div className="filters">
        <div className="filter-field small">
          <label htmlFor="timetable-department">Department</label>
          <select
            id="timetable-department"
            value={department}
            onChange={(e) =>
              changeScope(() => setDepartment(e.target.value))
            }
          >
            {departments.map((name) => (
              <option key={name}>{name}</option>
            ))}
          </select>
        </div>
        <div className="filter-field small">
          <label htmlFor="timetable-semester">Semester</label>
          <select
            id="timetable-semester"
            value={semester}
            onChange={(e) =>
              changeScope(() => setSemester(Number(e.target.value)))
            }
          >
            {Array.from({ length: 12 }, (_, i) => i + 1).map((n) => (
              <option key={n}>{n}</option>
            ))}
          </select>
        </div>
        <div className="filter-field small">
          <label htmlFor="timetable-section">Section</label>
          <input
            id="timetable-section"
            value={sectionDraft}
            maxLength={20}
            onChange={(e) => setSectionDraft(e.target.value)}
            onBlur={commitSection}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                commitSection();
              }
            }}
          />
        </div>
      </div>
      <ErrorBox message={error} />
      {message && (
        <div className="success" role="status">
          {message}
        </div>
      )}
      {editing && (
        <ClassForm
          key={String(editing.record?.id ?? "new")}
          w={w}
          scope={{ department, semester, section }}
          editing={editing}
          faculty={directory.data ?? []}
          facultyLoading={directory.loading}
          onCancel={() => setEditing(null)}
          onSaved={saved}
        />
      )}
      {loading ? (
        <Loading />
      ) : (
        !error && (
          <TimetableGrid
            records={rows}
            caption={`Timetable for ${department}, semester ${semester}, section ${section}`}
            onEdit={editClass}
            emptyHint="Add the first class, or import a timetable file."
          />
        )
      )}
    </section>
  );
}

function ClassForm({
  w,
  scope,
  editing,
  faculty,
  facultyLoading,
  onCancel,
  onSaved,
}: {
  w: Workspace;
  scope: { department: string; semester: number; section: string };
  editing: Editing;
  faculty: DataRecord[];
  facultyLoading: boolean;
  onCancel: () => void;
  onSaved: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [fail, setFail] = useState("");
  const [confirming, setConfirming] = useState(false);
  const mounted = useRef(true);
  const id = editing.record?.id;
  const {
    register,
    handleSubmit,
    setFocus,
    setValue,
    formState: { errors },
  } = useForm<ClassValues>({
    resolver: zodResolver(classSchema),
    defaultValues: editing.defaults,
  });
  useEffect(() => {
    mounted.current = true;
    setFocus("subject");
    return () => {
      mounted.current = false;
    };
  }, [setFocus]);
  // The select's options load after the form opens, so its value is set again once they arrive.
  useEffect(() => {
    setValue("faculty_code", editing.defaults.faculty_code);
  }, [faculty, editing.defaults.faculty_code, setValue]);

  const current = text(editing.defaults.faculty_code);
  const options = faculty.map((f) => ({
    code: text(f.code),
    name: text(f.name),
  }));
  // Keep a teacher from another department selectable when editing their class.
  if (current && !options.some((o) => o.code === current))
    options.unshift({
      code: current,
      name: text(editing.record?.faculty || current),
    });

  async function send(path: string, init: RequestInit, note: string) {
    setBusy(true);
    setFail("");
    try {
      await api(path, w.session, w.tenant, init);
      if (mounted.current) onSaved(note);
    } catch (e) {
      if (mounted.current)
        setFail(e instanceof Error ? e.message : "Could not save. Try again.");
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  function submit(values: ClassValues) {
    const body = JSON.stringify({
      ...scope,
      ...values,
      day: Number(values.day),
    });
    void (id
      ? send(
          `/v1/admin/timetable/${id}`,
          { method: "PATCH", body },
          `Saved ${values.subject}.`,
        )
      : send(
          "/v1/admin/timetable",
          { method: "POST", body },
          `Added ${values.subject}.`,
        ));
  }
  function field(
    name: keyof ClassValues,
    label: string,
    type = "text",
    maxLength?: number,
  ) {
    const fieldId = `class-${name}`;
    const problem = errors[name]?.message;
    return (
      <div>
        <label htmlFor={fieldId}>{label}</label>
        <input
          id={fieldId}
          type={type}
          maxLength={maxLength}
          aria-invalid={!!problem}
          aria-describedby={problem ? `${fieldId}-error` : undefined}
          {...register(name)}
        />
        {problem && (
          <p className="field-message" id={`${fieldId}-error`}>
            {problem}
          </p>
        )}
      </div>
    );
  }
  return (
    <form
      className="class-form"
      onSubmit={handleSubmit(submit)}
      aria-label={id ? "Edit class" : "Add class"}
      noValidate
    >
      <h3>{id ? `Edit ${editing.defaults.subject}` : "Add a class"}</h3>
      <ErrorBox message={fail} />
      <div className="upload-grid">
        {field("subject", "Subject", "text", 200)}
        <div>
          <label htmlFor="class-faculty_code">Teacher</label>
          <select
            id="class-faculty_code"
            aria-invalid={!!errors.faculty_code}
            aria-describedby={
              errors.faculty_code ? "class-faculty_code-error" : undefined
            }
            {...register("faculty_code")}
          >
            <option value="">
              {facultyLoading ? "Loading teachers..." : "Choose a teacher"}
            </option>
            {options.map((o) => (
              <option key={o.code} value={o.code}>
                {o.name}
              </option>
            ))}
          </select>
          {errors.faculty_code && (
            <p className="field-message" id="class-faculty_code-error">
              {errors.faculty_code.message}
            </p>
          )}
        </div>
        {field("room", "Room", "text", 100)}
        <div>
          <label htmlFor="class-day">Day</label>
          <select id="class-day" {...register("day")}>
            {DAYS.map((name, index) => (
              <option key={name} value={String(index)}>
                {name}
              </option>
            ))}
          </select>
        </div>
        {field("start_time", "Start time", "time")}
        {field("end_time", "End time", "time")}
        {field("effective_from", "Runs from", "date")}
        {field("effective_to", "Runs until", "date")}
      </div>
      <div className="class-form-actions">
        <Button type="submit" disabled={busy}>
          {busy ? "Saving..." : id ? "Save changes" : "Add class"}
        </Button>
        <Button
          type="button"
          variant="outline"
          onClick={onCancel}
          disabled={busy}
        >
          Cancel
        </Button>
        {id &&
          (confirming ? (
            <span className="class-form-confirm" role="group" aria-label="Confirm delete">
              <span>Delete this class for all students?</span>
              <Button
                type="button"
                className="class-delete"
                disabled={busy}
                onClick={() =>
                  void send(
                    `/v1/admin/timetable/${id}`,
                    { method: "DELETE" },
                    `Deleted ${editing.defaults.subject}.`,
                  )
                }
              >
                Yes, delete class
              </Button>
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={() => setConfirming(false)}
              >
                Keep class
              </Button>
            </span>
          ) : (
            <Button
              type="button"
              variant="ghost"
              disabled={busy}
              onClick={() => setConfirming(true)}
            >
              Delete class
            </Button>
          ))}
      </div>
    </form>
  );
}
