"use client";
import {EventsView} from './events-view';
import { useState, useEffect, useCallback } from "react";
import type { Session } from "@supabase/supabase-js";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import {
  GraduationCap,
  Home,
  MessagesSquare,
  CalendarDays,
  Utensils,
  FileText,
  MoreHorizontal,
  ChevronRight,
  ChevronDown,
  Menu,
  X,
  LogOut,
  ShieldCheck,
  Code2,
  Search,
  Sparkles,
  ArrowUpRight,
  Clock3,
  MapPin,
  UserRound,
  ArrowRight,
  BookOpen,
  LoaderCircle,
  TriangleAlert,
  Settings,
  Users,
  Compass,
  Sun,
  Moon,
  Laptop,
  Check,
  Copy,
} from "lucide-react";
import {
  supabase,
  api,
  API,
  ONBOARDING_TENANT,
  AUTH_CALLBACK_ERROR,
  campusDate,
  prettyDate,
  text,
  type Profile,
  type Membership,
  type Overview,
  type DataRecord,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Empty, DemoBadge } from "@/components/result-cards";
import { ChatView } from "@/components/chat-view";
import { BrowseView } from "@/components/browse-view";
import { AdminView } from "@/components/admin-view";
import { SettingsView } from "@/components/settings-view";

export type Page =
  | "Home"
  | "Ask CampusOS"
  | "Timetable"
  | "Canteen"
  | "Notices"
  | "Events"
  | "Faculty"
  | "Calendar"
  | "Settings"
  | "Admin Console"
  | "System Intelligence";
export interface Workspace {
  session: Session;
  tenant: string;
  profile: Profile;
  download: (id: string) => void;
  refresh: () => void;
  revision: number;
}

export function useRemote<T>(
  path: string | null,
  w: Pick<Workspace, "session" | "tenant" | "revision">,
) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let live = true;
    setLoading(true);
    setData(null);
    setError("");
    if (path === null) { setLoading(false); return; }
    api<T>(path, w.session, w.tenant)
      .then((value) => {
        if (live) setData(value);
      })
      .catch((e) => {
        if (live)
          setError(e instanceof Error ? e.message : "Could not load data.");
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [path, w.session, w.tenant, w.revision]);
  return { data, error, loading };
}
export function ErrorBox({ message }: { message: string }) {
  return message ? (
    <div className="error" role="alert">
      <TriangleAlert size={18} />
      <span>{message}</span>
    </div>
  ) : null;
}
export function Loading() {
  return (
    <div className="loading" role="status">
      <LoaderCircle size={18} className="spin" />
      Loading your campus…
    </div>
  );
}
export function Logo() {
  return (
    <div className="brand">
      <span className="brand-mark">
        <GraduationCap size={22} />
      </span>
      <span>CampusOS</span>
      <small>AI</small>
    </div>
  );
}
const loginSchema = z.object({
  email: z.email("Enter a valid email address."),
  password: z.string().min(8, "Use at least eight characters."),
});

function Login({ authError = "" }: { authError?: string }) {
  const [localTestLogin,setLocalTestLogin]=useState(false);
  useEffect(()=>{setLocalTestLogin(process.env.NEXT_PUBLIC_ENABLE_TEST_STUDENT_LOGIN==='true'
    &&['localhost','127.0.0.1','[::1]'].includes(window.location.hostname));},[]);
  const [loginRole, setLoginRole] = useState("student");
  const roles = [
    ["institution_admin", "College Admin", ShieldCheck],
    ["department_admin", "Department Admin", Users],
    ["section_admin", "Section Admin", BookOpen],
    ["canteen_manager", "Canteen Manager", Utensils],
    ["student", "Student", GraduationCap],
  ] as const;
  const [error, setError] = useState(authError);
  useEffect(() => setError(authError), [authError]);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const {
    register,
    handleSubmit,
    getValues,
    formState: { errors },
  } = useForm<z.infer<typeof loginSchema>>({
    resolver: zodResolver(loginSchema),
  });
  async function signInWithGoogle(testStudent=false) {
    if (!supabase) return;
    if(testStudent&&(!localTestLogin||loginRole!=='student'))return;
    setBusy(true);
    setError("");
    sessionStorage.setItem("campusos-login-role", loginRole);
    try {
      const { error } = await supabase.auth.signInWithOAuth({
        provider: "google",
        options: {
          redirectTo: window.location.origin,
          queryParams: {
            ...(loginRole === "institution_admin"||testStudent ? {} : { hd: "rvce.edu.in" }),
            prompt: "select_account",
          },
        },
      });
      if (error) setError(error.message);
    } catch {
      setError("Google sign-in could not start. Please try again.");
    } finally {
      setBusy(false);
    }
  }
  async function submit(values: z.infer<typeof loginSchema>) {
    if (!supabase) return;
    setBusy(true);
    setError("");
    sessionStorage.setItem("campusos-login-role", loginRole);
    const { error } = await supabase.auth.signInWithPassword(values);
    setError(error?.message || "");
    setBusy(false);
  }
  async function reset() {
    const email = getValues("email");
    if (!z.email().safeParse(email).success) {
      setError("Enter your email address first.");
      return;
    }
    if (!supabase) return;
    setBusy(true);
    const { error } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: window.location.origin + "/reset-password",
    });
    setError(error?.message || "");
    setSent(!error);
    setBusy(false);
  }
  return (
    <main className="login-shell">
      <fieldset className="login-roles">
        <legend>Choose your workspace</legend>
        <p>Your verified institution membership determines your access.</p>
        <div className="role-options">{roles.map(([id, label, Icon]) => (
          <label key={id} className={`role-option ${loginRole === id ? "selected" : ""}`}>
            <input type="radio" name="workspace-role" value={id} checked={loginRole === id}
              aria-label={label} onChange={() => setLoginRole(id)} />
            <Icon size={19} aria-hidden="true" /><span>{label}</span>
          </label>
        ))}</div>
      </fieldset>
      <section className="login-story">
        <Logo />
        <div>
          <div className="eyebrow" style={{ color: "#a5b4fc" }}>
            ONE CAMPUS. ONE CONNECTED EXPERIENCE.
          </div>
          <h1>
            Your campus,
            <br />
            <span>at your fingertips.</span>
          </h1>
          <p>
            A clearer day starts here. Classes, answers and opportunities,
            together in one place.
          </p>
          <div className="login-features">
            <span>
              <CalendarDays size={15} />
              Stay on schedule
            </span>
            <span>
              <Sparkles size={15} />
              Find reliable answers
            </span>
            <span>
              <ShieldCheck size={15} />
              Your data, protected
            </span>
          </div>
        </div>
        <div className="login-bottom">
          CampusOS AI · Institutional intelligence, thoughtfully connected.
        </div>
      </section>
      <section className="login-form-wrap">
        <div className="login-form">
          <div className="eyebrow">YOUR CAMPUS WORKSPACE</div>
          <h2>Welcome to CampusOS</h2>
          <p>Sign in with your institution account to continue.</p>
          {!supabase ? (
            <div className="setup-note">
              <strong>Connect your institution to get started</strong>
              <p>
                This local installation needs Supabase Auth configuration. Add
                your project URL and publishable key to{" "}
                <code>frontend/.env.local</code>, configure the backend, and
                restart.
              </p>
              <p>
                Follow <code>docs/deployment.md</code> to create your first
                institution and membership. CampusOS never grants access based
                on an email address alone.
              </p>
              <a
                className="text-link"
                href="https://supabase.com/dashboard"
                target="_blank"
                rel="noopener noreferrer"
              >
                Open Supabase dashboard <ArrowUpRight size={14} />
              </a>
            </div>
          ) : (
            <>
              <ErrorBox message={error} />
              {sent && (
                <div className="success" role="status">
                  Check your inbox for the password reset link.
                </div>
              )}
              <button
                type="button"
                className="google-btn"
                onClick={()=>signInWithGoogle()}
                disabled={busy}
              >
                <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
                  <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
                  <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
                  <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z" />
                  <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z" />
                </svg>
                <span>{loginRole === "institution_admin" ? "Continue with Google" : "Continue with Google (@rvce.edu.in)"}</span>
              </button>
              {localTestLogin&&loginRole==='student'&&<>
                <Button type="button" variant="outline" disabled={busy} onClick={()=>signInWithGoogle(true)}>Test student — configured Google account</Button>
                <p className="subtle">Local testing only. The backend requires the exact verified account and user ID. A section join code is still required.</p>
              </>}
              <div className="auth-divider">
                <span>or sign in with password</span>
              </div>
              <form onSubmit={handleSubmit(submit)} noValidate>
                <div>
                  <label htmlFor="email">Institution email</label>
                  <input
                    id="email"
                    type="email"
                    autoComplete="email"
                    placeholder="you@institution.edu"
                    {...register("email")}
                    aria-invalid={!!errors.email}
                    aria-describedby={errors.email ? "email-error" : undefined}
                  />
                  {errors.email && (
                    <p id="email-error" className="status-warn">
                      {errors.email.message}
                    </p>
                  )}
                </div>
                <div>
                  <label htmlFor="password">Password</label>
                  <input
                    id="password"
                    type="password"
                    autoComplete="current-password"
                    {...register("password")}
                    aria-invalid={!!errors.password}
                  />
                  {errors.password && (
                    <p className="status-warn">{errors.password.message}</p>
                  )}
                </div>
                <Button type="submit" disabled={busy}>
                  {busy ? <LoaderCircle className="spin" size={17} /> : null}
                  Sign in <ArrowRight size={16} />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={reset}
                  disabled={busy}
                >
                  Forgot your password?
                </Button>
              </form>
              <p className="subtle" style={{ marginTop: 24 }}>
                New here? Your institution administrator can invite you and
                assign your campus access.
              </p>
              <p className="subtle">Install CampusOS from your browser menu, where supported. Current campus answers require a connection.</p>
            </>
          )}
        </div>
      </section>
    </main>
  );
}


function SectionOnboardingView({
  session,
  tenant,
  onJoined,
  onSignOut,
  accountError = "",
}: {
  session: Session;
  tenant: string;
  onJoined: () => void;
  onSignOut: () => void;
  accountError?: string;
}) {
  const [department, setDepartment] = useState("AIML");
  const [semester, setSemester] = useState("3");
  const [section, setSection] = useState("A");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [success, setSuccess] = useState(false);

  const departments = [
    { code: "AIML", name: "Artificial Intelligence & Machine Learning" },
    { code: "CSE", name: "Computer Science & Engineering" },
    { code: "ECE", name: "Electronics & Communication Engineering" },
    { code: "ISE", name: "Information Science & Engineering" },
    { code: "MECH", name: "Mechanical Engineering" },
    { code: "CIVIL", name: "Civil Engineering" },
    { code: "EEE", name: "Electrical & Electronics Engineering" },
    { code: "BT", name: "Biotechnology" },
  ];

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!code.trim()) {
      setError("Please enter the section join code provided by your department or faculty.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api(
        "/v1/onboarding/join-section",
        session,
        tenant,
        {
          method: "POST",
          body: JSON.stringify({
            department,
            semester: parseInt(semester, 10),
            section: section.toUpperCase(),
            code: code.trim(),
          }),
        }
      );
      setSuccess(true);
      setTimeout(() => {
        onJoined();
      }, 400);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to join section.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="onboarding-shell">
      <div className="onboarding-card">
        <Logo />
        <div className="eyebrow" style={{ marginTop: 20 }}>SECTION ONBOARDING</div>
        <h2 style={{ fontSize: 22, marginTop: 4 }}>Connect your campus timetable</h2>
        <p className="subtle" style={{ margin: "8px 0 20px" }}>
          Select your class details and enter your section join code to link your verified timetable, faculty, and notices. You only have to enter this once.
        </p>
        <ErrorBox message={error || accountError} />
        {success ? (
          <div className="success" role="status" style={{ marginTop: 12 }}>
            Section confirmed! Unlocking your campus workspace…
          </div>
        ) : (
          <form onSubmit={submit} className="onboarding-form">
            <div className="form-group">
              <label htmlFor="dept-select">Department</label>
              <select
                id="dept-select"
                value={department}
                onChange={(e) => setDepartment(e.target.value)}
              >
                {departments.map((d) => (
                  <option key={d.code} value={d.code}>
                    {d.code} · {d.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-row-2">
              <div className="form-group">
                <label htmlFor="sem-select">Semester</label>
                <select
                  id="sem-select"
                  value={semester}
                  onChange={(e) => setSemester(e.target.value)}
                >
                  {[1, 2, 3, 4, 5, 6, 7, 8].map((s) => (
                    <option key={s} value={s}>
                      Semester {s}
                    </option>
                  ))}
                </select>
              </div>
              <div className="form-group">
                <label htmlFor="sec-select">Section</label>
                <select
                  id="sec-select"
                  value={section}
                  onChange={(e) => setSection(e.target.value)}
                >
                  {["A", "B", "C", "D"].map((s) => (
                    <option key={s} value={s}>
                      Section {s}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div className="form-group">
              <label htmlFor="join-code">Section Join Code</label>
              <input
                id="join-code"
                type="text"
                placeholder="e.g. AIML3A-9F2B4A"
                value={code}
                onChange={(e) => setCode(e.target.value.toUpperCase())}
                autoComplete="off"
                spellCheck={false}
              />
              <p className="field-hint">
                Provided by your department administrator, class teacher, or CR.
              </p>
            </div>
            <Button type="submit" disabled={busy} style={{ width: "100%", marginTop: 10 }}>
              {busy ? <LoaderCircle className="spin" size={17} /> : null}
              Confirm & Unlock Campus <ArrowRight size={16} />
            </Button>
            <Button type="button" variant="ghost" onClick={onSignOut} style={{ width: "100%", marginTop: 4 }}>
              Sign out
            </Button>
          </form>
        )}
      </div>
    </main>
  );
}

export function CampusApp() {
  const [session, setSession] = useState<Session | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [authError, setAuthError] = useState("");
  const [membersLoaded, setMembersLoaded] = useState(false);
  const [members, setMembers] = useState<Membership[]>([]);
  const [tenant, setTenant] = useState("");
  const [profile, setProfile] = useState<Profile | null>(null);
  const [error, setError] = useState("");
  const [page, setPage] = useState<Page>("Home");
  const [more, setMore] = useState(false);
  const [mobile, setMobile] = useState(false);
  const [revision, setRevision] = useState(0);
  const [queued, setQueued] = useState("");
  const [theme, setTheme] = useState<"light" | "dark" | "system">("system");

  useEffect(() => {
    const saved = (localStorage.getItem("campusos-theme") as "light" | "dark" | "system") || "system";
    setTheme(saved);
    applyTheme(saved);
  }, []);

  function applyTheme(t: "light" | "dark" | "system") {
    const root = document.documentElement;
    if (t === "dark") {
      root.setAttribute("data-theme", "dark");
      root.classList.add("dark");
    } else if (t === "light") {
      root.setAttribute("data-theme", "light");
      root.classList.remove("dark");
    } else {
      root.removeAttribute("data-theme");
      const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      if (prefersDark) {
        root.classList.add("dark");
      } else {
        root.classList.remove("dark");
      }
    }
  }

  function cycleTheme() {
    const next = theme === "system" ? "light" : theme === "light" ? "dark" : "system";
    setTheme(next);
    localStorage.setItem("campusos-theme", next);
    applyTheme(next);
  }

  useEffect(() => {
    if (!supabase) {
      setAuthLoading(false);
      return;
    }
    let live = true;
    if (AUTH_CALLBACK_ERROR) setAuthError(AUTH_CALLBACK_ERROR);
    supabase.auth.getSession().then(({ data, error }) => {
      if (!live) return;
      setSession(data.session);
      if (error) setAuthError(error.message);
      setAuthLoading(false);
    }).catch(() => {
      if (live) {
        setAuthError("Your session could not be restored. Please sign in again.");
        setAuthLoading(false);
      }
    });
    const { data } = supabase.auth.onAuthStateChange((_event, next) => {
      setSession(next);
    });
    return () => {
      live = false;
      data.subscription.unsubscribe();
    };
  }, []);
  useEffect(() => {
    if (!session) {
      setProfile(null);
      setMembers([]);
      setTenant("");
      setMembersLoaded(false);
      return;
    }
    let live = true;
    setMembersLoaded(false);
    setError("");
    api<Membership[]>("/v1/memberships", session, "")
      .then((rows) => {
        if (!live) return;
        setMembers(rows);
        setMembersLoaded(true);
        const saved = window.localStorage.getItem("campusos-tenant");
        const preferred = rows.find((m) => m.role === sessionStorage.getItem("campusos-login-role"));
        setTenant((current) =>
          rows.some((m) => m.tenant_id === current)
            ? current
            : preferred?.tenant_id || (rows.some((m) => m.tenant_id === saved)
              ? saved!
              : rows[0]?.tenant_id || ""),
        );
        if (!rows.length && !ONBOARDING_TENANT)
          setError(
            "Your account has no active institution membership. Ask your administrator to configure student enrollment or grant access.",
          );
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [session, revision]);
  useEffect(() => {
    if (!session || !tenant) return;
    let live = true;
    setError("");
    api<Profile>("/v1/me", session, tenant)
      .then((p) => {
        if (live) setProfile(p);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [session, tenant, revision]);
  useEffect(() => {
    if (mobile)
      document.querySelector<HTMLButtonElement>(".sidebar nav button")?.focus();
  }, [mobile]);
  const refresh = useCallback(() => setRevision((r) => r + 1), []);
  const download = useCallback(
    async (id: string) => {
      if (!session || !tenant) return;
      try {
        const response = await fetch(`${API}/v1/sources/${id}`, {
          headers: {
            Authorization: `Bearer ${session.access_token}`,
            "X-Tenant-ID": tenant,
          },
        });
        if (!response.ok)
          throw new Error("This source is unavailable to your account.");
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        const match = response.headers
          .get("Content-Disposition")
          ?.match(/filename\*=UTF-8''(.+)/);
        link.download = match ? decodeURIComponent(match[1]) : "campus-source";
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not open source.");
      }
    },
    [session, tenant],
  );
  async function signOut() {
    try {
      const result = await supabase?.auth.signOut();
      if (result?.error) {
        const local = await supabase?.auth.getSession();
        const message = local?.data.session
          ? "Sign out could not complete. Please try again."
          : "Signed out on this browser. Server sign-out could not be confirmed.";
        setAuthError(message);
        setError(message);
        return;
      }
      sessionStorage.removeItem("campusos-login-role");
      localStorage.removeItem("campusos-tenant");
      setSession(null);
      setProfile(null);
      setAuthError("");
      setPage("Home");
    } catch {
      setError("Sign out could not complete. Please try again.");
    }
  }
  function navigate(next: Page) {
    setPage(next);
    setMobile(false);
    setError("");
  }
  function ask(question: string) {
    setQueued(question);
    navigate("Ask CampusOS");
  }
  if (authLoading) return <Loading />;
  if (!session) return <Login authError={authError} />;
  if (membersLoaded && members.length === 0 && ONBOARDING_TENANT) {
    return <SectionOnboardingView session={session} tenant={ONBOARDING_TENANT} onJoined={refresh} onSignOut={signOut} accountError={error} />;
  }
  if (
    !profile ||
    profile.tenant_id !== tenant ||
    profile.user_id !== session.user.id
  )
    return (
      <main className="content">
        <Logo />
        {error ? (
          <>
            <ErrorBox message={error} />
            <Button variant="outline" onClick={refresh}>
              Retry connection
            </Button>
            <Button variant="ghost" onClick={signOut}>
              Sign out
            </Button>
          </>
        ) : (
          <Loading />
        )}
      </main>
    );
  const w: Workspace = {
    session,
    tenant,
    profile,
    download,
    refresh,
    revision,
  };
  if (profile.role === "student" && (profile.needs_onboarding || profile.section === "unassigned" || !profile.section || profile.section === "All")) {
    return <SectionOnboardingView session={session} tenant={tenant} onJoined={refresh} onSignOut={signOut} accountError={error} />;
  }
  const admin = [
    "department_admin",
    "institution_admin",
    "section_admin",
    "canteen_manager",
  ].includes(profile.role);
  const primary: [Page, typeof Home][] = [
    ["Home", Home],
    ["Ask CampusOS", MessagesSquare],
    ["Timetable", CalendarDays],
    ["Canteen", Utensils],
    ["Notices", FileText],
  ];
  const secondary: [Page, typeof Home][] = [
    ["Events", Compass],
    ["Faculty", Users],
    ["Calendar", CalendarDays],
    ["Settings", Settings],
  ];
  return (
    <div className="app-shell">
      <a href="#main" className="skip-link">
        Skip to main content
      </a>
      <div
        className={`overlay ${mobile ? "visible" : ""}`}
        onClick={() => setMobile(false)}
        aria-hidden="true"
      />
      <aside
        className={`sidebar ${mobile ? "open" : ""}`}
        aria-label="Campus navigation"
        onKeyDown={(e) => {
          if (!mobile) return;
          if (e.key === "Escape") {
            setMobile(false);
            document.querySelector<HTMLButtonElement>(".mobile-menu")?.focus();
          }
          if (e.key === "Tab") {
            const nodes = Array.from(
              e.currentTarget.querySelectorAll<HTMLElement>(
                "button,select,a[href]",
              ),
            );
            const first = nodes[0],
              last = nodes[nodes.length - 1];
            if (e.shiftKey && document.activeElement === first) {
              e.preventDefault();
              last?.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
              e.preventDefault();
              first?.focus();
            }
          }
        }}
      >
        <Logo />
        <div className="workspace-label">YOUR WORKSPACE</div>
        <nav aria-label="Main navigation">
          {primary.map(([label, Icon]) => (
            <button
              key={label}
              onClick={() => navigate(label)}
              className={`nav-item ${page === label ? "active" : ""}`}
              aria-current={page === label ? "page" : undefined}
            >
              <Icon size={18} />
              {label}
            </button>
          ))}
          <button
            className="nav-item"
            onClick={() => setMore(!more)}
            aria-expanded={more}
          >
            <MoreHorizontal size={18} />
            More
            <ChevronDown size={14} style={{ marginLeft: "auto" }} />
          </button>
          {more &&
            secondary.map(([label, Icon]) => (
              <button
                key={label}
                onClick={() => navigate(label)}
                className={`nav-item nav-more ${page === label ? "active" : ""}`}
                aria-current={page === label ? "page" : undefined}
              >
                <Icon size={16} />
                {label}
              </button>
            ))}
          {admin && (
            <>
              <div className="workspace-label" style={{ marginTop: 25 }}>
                MANAGE CAMPUS
              </div>
              <button
                className={`nav-item ${page === "Admin Console" ? "active" : ""}`}
                onClick={() => navigate("Admin Console")}
              >
                <ShieldCheck size={17} />
                Admin Console
              </button>
              {profile.role === "institution_admin" && <button
                className={`nav-item technical-nav ${page === "System Intelligence" ? "active" : ""}`}
                onClick={() => navigate("System Intelligence")}
              >
                <Code2 size={17} />
                System Intelligence
              </button>}
            </>
          )}
        </nav>
        <div className="sidebar-bottom">
          <div className="local-status">
            <strong>
              <span className="status-dot" />
              Your campus, connected
            </strong>
            <p>Answers grounded in your sources.</p>
          </div>
          <div className="sidebar-person">
            <div className="avatar">{profile.name[0]}</div>
            <div>
              <strong>{profile.name}</strong>
              <p>{({institution_admin:"College Admin",department_admin:"Department Admin",section_admin:"Section Admin",canteen_manager:"Canteen Manager",student:"Student"} as Record<string,string>)[profile.role]}</p>
              <p>
                {profile.department} · Sem {profile.semester} ·{" "}
                {profile.section}
              </p>
            </div>
            <button
              aria-label="Sign out"
              onClick={signOut}
              style={{
                marginLeft: "auto",
                background: "none",
                border: 0,
                color: "#64748b",
                minWidth: 36,
                minHeight: 44,
              }}
            >
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>
      <div className="main-area">
        <header className="topbar">
          <button
            className="mobile-menu"
            aria-label={mobile ? "Close navigation" : "Open navigation"}
            aria-expanded={mobile}
            onClick={() => setMobile(!mobile)}
          >
            {mobile ? <X size={22} /> : <Menu size={22} />}
          </button>
          <div className="breadcrumb">
            <GraduationCap size={15} />
            Workspace
            <ChevronRight size={12} />
            <strong style={{ color: "#334155" }}>{page}</strong>
          </div>
          <div className="top-right">
            <Button type="button" variant="outline" onClick={signOut} aria-label="Log out" title="Log out of CampusOS">
              <LogOut size={16} /> Log out
            </Button>
            <label className="sr-only" htmlFor="institution">
              Institution
            </label>
            <select
              id="institution"
              className="institution-select"
              value={tenant}
              onChange={(e) => {
                setTenant(e.target.value);
                localStorage.setItem("campusos-tenant", e.target.value);
                setPage("Home");
              }}
            >
              {members.map((m) => (
                <option key={m.tenant_id} value={m.tenant_id}>
                  {m.institution}
                  {m.demo ? " · Demo Data" : ""}
                </option>
              ))}
            </select>
            <button
              className="theme-toggle-btn"
              onClick={cycleTheme}
              aria-label={`Current theme: ${theme}. Click to change.`}
              title={`Theme: ${theme}`}
            >
              {theme === "dark" ? <Moon size={17} /> : theme === "light" ? <Sun size={17} /> : <Laptop size={17} />}
            </button>
            <div className="avatar" aria-hidden="true">
              {profile.name[0]}
            </div>
          </div>
        </header>
        <main id="main" className="content" tabIndex={-1}>
          <ErrorBox message={error} />
          {page === "Home" ? (
            <HomeView w={w} ask={ask} navigate={navigate} />
          ) : page === "Ask CampusOS" ? (
            <ChatView
              key={tenant}
              w={w}
              queued={queued}
              consumed={() => setQueued("")}
            />
          ) : page === "Admin Console" || page === "System Intelligence" ? (
            <AdminView w={w} developer={page === "System Intelligence"} />
          ) : page === 'Events' ? (
            <EventsView w={w}/>
          ) : page === "Settings" ? (
            <SettingsView w={w} />
          ) : (
            <BrowseView key={`${tenant}:${page}`} w={w} page={page} />
          )}
          <footer className="page-footer">
            <span>CampusOS AI · Your campus, thoughtfully connected.</span>
            <span>
              {profile.demo
                ? "Synthetic institution · Demo Data"
                : "Institution workspace"}{" "}
              · {profile.timezone}
            </span>
          </footer>
        </main>
      </div>
      <nav className="mobile-bottom-nav" aria-label="Mobile navigation">
        <button
          className={`bottom-nav-btn ${page === "Home" ? "active" : ""}`}
          onClick={() => navigate("Home")}
          aria-label="Home"
        >
          <Home size={19} />
          <span>Home</span>
        </button>
        <button
          className={`bottom-nav-btn ${page === "Ask CampusOS" ? "active" : ""}`}
          onClick={() => navigate("Ask CampusOS")}
          aria-label="Ask CampusOS"
        >
          <MessagesSquare size={19} />
          <span>Chat</span>
        </button>
        <button
          className={`bottom-nav-btn ${page === "Timetable" ? "active" : ""}`}
          onClick={() => navigate("Timetable")}
          aria-label="Timetable"
        >
          <CalendarDays size={19} />
          <span>Classes</span>
        </button>
        <button
          className={`bottom-nav-btn ${page === "Canteen" ? "active" : ""}`}
          onClick={() => navigate("Canteen")}
          aria-label="Canteen"
        >
          <Utensils size={19} />
          <span>Food</span>
        </button>
        <button
          className={`bottom-nav-btn ${page === "Notices" ? "active" : ""}`}
          onClick={() => navigate("Notices")}
          aria-label="Notices"
        >
          <FileText size={19} />
          <span>Notices</span>
        </button>
      </nav>
    </div>
  );
}

const quick = [
  { label: "My next class", icon: CalendarDays },
  { label: "Food under ₹100", icon: Utensils },
  { label: "Latest notices", icon: FileText },
  { label: "Events this week", icon: Compass },
];
export function QuickActions({ ask }: { ask: (q: string) => void }) {
  return (
    <div className="quick-actions">
      {quick.map(({ label, icon: Icon }) => (
        <button className="quick-action" onClick={() => ask(label)} key={label}>
          <Icon size={13} />
          {label}
          <ArrowUpRight size={11} />
        </button>
      ))}
    </div>
  );
}

function HomeView({
  w,
  ask,
  navigate,
}: {
  w: Workspace;
  ask: (q: string) => void;
  navigate: (p: Page) => void;
}) {
  const { data, error, loading } = useRemote<Overview>("/v1/overview", w);
  const [question, setQuestion] = useState("");
  const today = campusDate(w.profile.timezone);
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">A LITTLE CLARITY FOR YOUR EVERYDAY</div>
          <h1>
            Good to see you, {w.profile.name.split(" ")[0]}{" "}
            <span aria-hidden="true">☀</span>
          </h1>
          <p>Let’s make the most of your campus day.</p>
        </div>
        <div className="date-label">{prettyDate(today)}</div>
      </div>
      {w.profile.demo && (
        <div className="demo-banner">
          <ShieldCheck size={15} />
          <span>
            You’re exploring a synthetic campus. All sample records are clearly
            marked <strong>Demo Data</strong>.
          </span>
        </div>
      )}
      <section className="hero" aria-label="Ask CampusOS">
        <div className="hero-heading">
          <span className="spark-icon">
            <Sparkles size={22} />
          </span>
          <div>
            <h2>A question away from a clearer day.</h2>
            <p>Your classes, campus updates, and everything in between.</p>
          </div>
        </div>
        <form
          className="ask-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (question.trim()) ask(question.trim());
          }}
        >
          <label className="sr-only" htmlFor="home-question">
            Ask anything about your campus
          </label>
          <input
            id="home-question"
            placeholder="Ask anything about your campus..."
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            maxLength={2000}
          />
          <Button type="submit" aria-label="Ask CampusOS">
            <span className="ask-label">Ask CampusOS</span>
            <ArrowUpRight size={18} />
          </Button>
        </form>
        <QuickActions ask={ask} />
        <div className="hero-footnote">
          <ShieldCheck size={12} />
          Uses your institution’s published sources, with citations when evidence is available.
        </div>
      </section>
      <div className="section-header">
        <div>
          <h2>Your day, at a glance</h2>
          <p style={{ marginTop: 5 }}>
            {w.profile.department} · Semester {w.profile.semester} · Section{" "}
            {w.profile.section}
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => navigate("Calendar")}>
          View calendar <ArrowRight size={14} />
        </Button>
      </div>
      <ErrorBox message={error} />
      {loading ? (
        <div className="dashboard-grid">
          <div className="skeleton" />
          <div className="skeleton" />
        </div>
      ) : (
        data && (
          <div className="dashboard-grid">
            <div>
              <section className="panel next-panel">
                <div className="next-label">
                  <Clock3 size={14} />
                  UP NEXT
                </div>
                {data.next_class ? (
                  <>
                    <h3>{text(data.next_class.subject)}</h3>
                    <div className="next-info">
                      <span>
                        <Clock3 size={14} />
                        {text(data.next_class.start_time).slice(0, 5)} –{" "}
                        {text(data.next_class.end_time).slice(0, 5)}
                      </span>
                      <span>
                        <MapPin size={14} />
                        {text(data.next_class.room)}
                      </span>
                      <span>{prettyDate(data.next_class.date)}</span>
                    </div>
                    <div className="next-professor">
                      With{" "}
                      {text(
                        data.next_class.faculty || data.next_class.faculty_code,
                      )}
                    </div>
                    <div className="next-bottom">
                      {data.next_class.demo ? <DemoBadge /> : <span />}
                      <button onClick={() => navigate("Timetable")}>
                        View timetable{" "}
                        <ArrowUpRight size={12} style={{ display: "inline" }} />
                      </button>
                    </div>
                  </>
                ) : (
                  <>
                    <h3>You’re all caught up.</h3>
                    <p>
                      No upcoming class is listed. Check your timetable for
                      another date.
                    </p>
                    <Button
                      variant="ghost"
                      onClick={() => navigate("Timetable")}
                    >
                      Open timetable
                    </Button>
                  </>
                )}
              </section>
              <section className="panel">
                <div className="panel-heading">
                  <h2>
                    <CalendarDays size={16} color="#6366f1" />
                    Today’s schedule
                  </h2>
                  <span className="subtle">{data.classes.length} classes</span>
                </div>
                {data.classes.length ? (
                  data.classes.map((r, i) => (
                    <div className="timeline" key={text(r.id) || i}>
                      <div className="timeline-time">
                        {text(r.start_time).slice(0, 5)}
                      </div>
                      <div className="timeline-marker" />
                      <div>
                        <h3>{text(r.subject)}</h3>
                        <p>
                          {text(r.room)} · {text(r.faculty)}
                        </p>
                        {r.demo && <DemoBadge />}
                      </div>
                    </div>
                  ))
                ) : (
                  <Empty
                    message="No classes today"
                    hint="Enjoy the breathing room, or explore upcoming campus events."
                  />
                )}
              </section>
            </div>
            <div>
              <section className="panel" style={{ marginBottom: 20 }}>
                <div className="panel-heading">
                  <h2>
                    <FileText size={16} color="#6366f1" />
                    On your radar
                  </h2>
                  <button
                    className="text-link"
                    onClick={() => navigate("Notices")}
                  >
                    All notices <ArrowRight size={12} />
                  </button>
                </div>
                {data.notices.length ? (
                  data.notices.map((r, i) => (
                    <div className="compact-notice" key={text(r.id) || i}>
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 7,
                        }}
                      >
                        <span className="subtle">{text(r.category)}</span>
                        {r.demo && <DemoBadge />}
                      </div>
                      <button
                        onClick={() => r.source_id && w.download(r.source_id)}
                      >
                        {text(r.title)}
                      </button>
                      <div className="notice-meta">
                        {prettyDate(r.published)} · View original source
                      </div>
                    </div>
                  ))
                ) : (
                  <Empty message="No current notices" />
                )}
              </section>
              <section className="panel">
                <div className="panel-heading">
                  <h2>
                    <Compass size={16} color="#06b6d4" />
                    Something to look forward to
                  </h2>
                  <button
                    className="text-link"
                    onClick={() => navigate("Events")}
                  >
                    Explore <ArrowRight size={12} />
                  </button>
                </div>
                {data.events.length ? (
                  data.events.map((r, i) => (
                    <div className="compact-event" key={text(r.id) || i}>
                      <div className="event-date">
                        {new Date(text(r.start)).toLocaleDateString("en-IN", {
                          month: "short",
                        })}
                        <strong>{new Date(text(r.start)).getDate()}</strong>
                      </div>
                      <div>
                        <h3>{text(r.title)}</h3>
                        <p>
                          {text(r.location)} ·{" "}
                          {text(r.start).split("T")[1]?.slice(0, 5)}
                        </p>
                        {r.demo && <DemoBadge />}
                      </div>
                    </div>
                  ))
                ) : (
                  <Empty
                    message="No upcoming events"
                    hint="Check back after the next campus update."
                  />
                )}
                <button
                  className="text-link"
                  onClick={() => ask("Upcoming exams")}
                >
                  <BookOpen size={13} />
                  Check upcoming examinations
                </button>
              </section>
            </div>
          </div>
        )
      )}
    </>
  );
}
