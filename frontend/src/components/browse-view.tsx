"use client";
import { useState } from "react";
import { Clock3, CalendarDays } from "lucide-react";
import {
  type Workspace,
  type Page,
  useRemote,
  ErrorBox,
  Loading,
} from "@/components/campus-app";
import { type DataRecord, campusDate, prettyDate } from "@/lib/api";
import { RecordList, Empty, RecordCard } from "@/components/result-cards";
import { TimetableGrid } from "@/components/timetable-grid";
import { Button } from "@/components/ui/button";

const descriptions: Partial<Record<Page, string>> = {
  Timetable: "Know where to be, and what comes next.",
  Canteen: "Good food. A budget that works for you.",
  Notices: "The campus updates that matter to you.",
  Events: "Find your next idea, connection, or opportunity.",
  Faculty: "Find the right person, without the runaround.",
  Calendar: "A little more room for everything you do.",
};

export function BrowseView({ w, page }: { w: Workspace; page: Page }) {
  const [date, setDate] = useState(campusDate(w.profile.timezone));
  const [query, setQuery] = useState("");
  const [budget, setBudget] = useState(100);
  const [diet, setDiet] = useState(
    w.profile.diet === "vegetarian" ? "true" : "any",
  );
  const [sort, setSort] = useState("price");
  const [tab, setTab] = useState("day");
  const [archive, setArchive] = useState(false);
  let path = "/v1/notices";
  let kind = "notices";
  const params = new URLSearchParams();
  if (page === "Timetable") {
    params.set("on", date);
    params.set("week", String(tab === "week"));
    path = "/v1/timetable";
    kind = "classes";
  } else if (page === "Canteen") {
    params.set("budget", String(budget));
    if (diet !== "any") params.set("vegetarian", diet);
    if (tab === "meals") {
      path = "/v1/meals";
      kind = "meals";
    } else {
      path = "/v1/menu";
      kind = "food";
      params.set("query", query);
      params.set("sort", sort);
    }
  } else if (page === "Faculty") {
    path = "/v1/faculty";
    kind = "faculty";
    params.set("query", query);
  } else if (page === "Notices") {
    params.set("query", query);
    params.set("include_outdated", String(archive));
  } else if (page === "Events") {
    path = "/v1/events";
    kind = "events";
    params.set("start", date);
    params.set("query", query);
  } else if (page === "Calendar") {
    path = "/v1/calendar";
    params.set("start", date);
    params.set("days", "7");
  }
  const { data, error, loading } = useRemote<
    | DataRecord[]
    | {
        classes: DataRecord[];
        events: DataRecord[];
        free_slots: DataRecord[];
        conflicts: DataRecord[];
      }
  >(path + "?" + params, w);
  const rows = Array.isArray(data) ? data : [];
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">YOUR CAMPUS, A LITTLE CLOSER</div>
          <h1>{page}</h1>
          <p>{descriptions[page]}</p>
        </div>
        <div className="date-label">
          {w.profile.department} · Sem {w.profile.semester} ·{" "}
          {w.profile.section}
        </div>
      </div>
      {page === "Timetable" && (
        <div className="tabs" aria-label="Timetable view">
          <button aria-pressed={tab === "day"} onClick={() => setTab("day")}>
            Day view
          </button>
          <button aria-pressed={tab === "week"} onClick={() => setTab("week")}>
            Week view
          </button>
        </div>
      )}
      {page === "Canteen" && (
        <div className="tabs" aria-label="Menu view">
          <button aria-pressed={tab !== "meals"} onClick={() => setTab("day")}>
            Browse menu
          </button>
          <button
            aria-pressed={tab === "meals"}
            onClick={() => setTab("meals")}
          >
            Meal combinations
          </button>
        </div>
      )}
      <div className="filters">
        {["Timetable", "Calendar", "Events"].includes(page) && (
          <div className="filter-field small">
            <label htmlFor="date">
              {page === "Calendar" ? "Week starting" : "Date"}
            </label>
            <input
              id="date"
              type="date"
              value={date}
              onChange={(e) => e.target.value && setDate(e.target.value)}
            />
          </div>
        )}
        {["Notices", "Faculty", "Events"].includes(page) ||
        (page === "Canteen" && tab !== "meals") ? (
          <div className="filter-field">
            <label htmlFor="search">Search {page.toLowerCase()}</label>
            <input
              id="search"
              placeholder={
                page === "Faculty"
                  ? "Name, subject or department"
                  : page === "Events"
                    ? "AI, workshop, technical…"
                    : "Search by name or topic…"
              }
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              maxLength={200}
            />
          </div>
        ) : null}
        {page === "Canteen" && (
          <>
            <div className="filter-field small">
              <label htmlFor="budget">Maximum budget (₹)</label>
              <input
                id="budget"
                type="number"
                min={0}
                max={10000}
                value={budget}
                onChange={(e) =>
                  setBudget(
                    Math.min(10000, Math.max(0, Number(e.target.value))),
                  )
                }
              />
            </div>
            <div className="filter-field small">
              <label htmlFor="diet">Dietary preference</label>
              <select
                id="diet"
                value={diet}
                onChange={(e) => setDiet(e.target.value)}
              >
                <option value="any">All food</option>
                <option value="true">Vegetarian</option>
                <option value="false">Non-vegetarian</option>
              </select>
            </div>
            {tab !== "meals" && (
              <div className="filter-field small">
                <label htmlFor="sort">Sort by</label>
                <select
                  id="sort"
                  value={sort}
                  onChange={(e) => setSort(e.target.value)}
                >
                  <option value="price">Lowest price</option>
                  <option value="protein">Most protein</option>
                </select>
              </div>
            )}
          </>
        )}
        {page === "Notices" && (
          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: 9,
              margin: 0,
              minHeight: 44,
            }}
          >
            <input
              type="checkbox"
              checked={archive}
              onChange={(e) => setArchive(e.target.checked)}
            />
            Include archived notices
          </label>
        )}
        <Button variant="outline" onClick={w.refresh}>
          Refresh
        </Button>
      </div>
      <ErrorBox message={error} />
      {loading ? (
        <Loading />
      ) : page === "Calendar" && data && !Array.isArray(data) ? (
        <>
          <section className="panel" style={{ marginBottom: 24 }}>
            <div className="panel-heading">
              <h2>
                <Clock3 size={16} />
                Room in your day · {prettyDate(date)}
              </h2>
            </div>
            <RecordList records={data.free_slots} kind="slots" limit={3} />
          </section>
          <WeekGrid
            records={[...data.classes, ...data.events]}
            date={date}
            onSource={w.download}
          />
          <section className="section-gap">
            <h2 style={{ marginBottom: 15 }}>Schedule overlaps</h2>
            {data.conflicts.length ? (
              <RecordList records={data.conflicts} kind="conflicts" limit={3} />
            ) : (
              <Empty
                message="Your schedule is clear"
                hint="No overlaps among the listed classes and activities."
              />
            )}
          </section>
        </>
      ) : page === "Timetable" && tab === "week" ? (
        <TimetableGrid
          records={rows}
          caption={`Timetable for the week of ${prettyDate(date)}`}
          onSource={w.download}
        />
      ) : (
        <>
          <div className="section-header">
            <p>
              {rows.length} result{rows.length === 1 ? "" : "s"}
              {page === "Canteen" ? " · Available items only" : ""}
            </p>
            {page === "Timetable" && (
              <span className="subtle">
                <CalendarDays
                  size={13}
                  style={{ display: "inline", marginRight: 6 }}
                />
                {prettyDate(date)}
              </span>
            )}
          </div>
          <RecordList
            records={rows}
            kind={kind}
            onSource={w.download}
            limit={page === "Canteen" ? 12 : 8}
          />
          {page === "Canteen" && (
            <p className="subtle" style={{ marginTop: 20 }}>
              Nutritional values are estimates unless marked verified. Ask the
              vendor about ingredients and allergens. Combinations use distinct
              available items.
            </p>
          )}
        </>
      )}
    </>
  );
}

function WeekGrid({
  records,
  date,
  onSource,
}: {
  records: DataRecord[];
  date: string;
  onSource: (id: string) => void;
}) {
  const start = new Date(date + "T12:00:00");
  const day = start.getDay();
  start.setDate(start.getDate() - (day === 0 ? 6 : day - 1));
  const dates = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(start);
    d.setDate(d.getDate() + i);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  });
  return (
    <div className="calendar-grid">
      {dates.map((d) => {
        const rows = records.filter((r) =>
          String(r.start || r.date).startsWith(d),
        );
        return (
          <section key={d} className="calendar-day" aria-label={prettyDate(d)}>
            <h3>{prettyDate(d)}</h3>
            {rows.length ? (
              rows.map((r, i) => (
                <RecordCard
                  key={String(r.id) || i}
                  record={r}
                  kind={r.subject ? "classes" : "events"}
                  onSource={onSource}
                />
              ))
            ) : (
              <p className="subtle">No listed activities</p>
            )}
          </section>
        );
      })}
    </div>
  );
}
