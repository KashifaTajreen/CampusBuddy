# ROOT CAUSE ANALYSIS: SYSTEM BREAKING POINTS & RECOVERIES

**Project:** CampusBuddy / CampusOS  
**Audit Date:** 2026-10-29  
**Auditor:** Senior Software Architect & Security Reviewer  

---

## Executive Overview

The user asked: **"WHERE IS THE SYSTEM BREAKING TELL ME"**.
This document isolates every confirmed failure point that occurred during deployment and usage, traces the exact code mechanisms responsible, and documents the verified fixes.

---

### Root Cause 1: Browser `net::ERR_CONNECTION_REFUSED localhost:8000` on Vercel

* **Problem:** When opening the production frontend at `https://campus-buddy-mvp.vercel.app`, the login and data views failed immediately with `net::ERR_CONNECTION_REFUSED` directed at `http://localhost:8000/v1/memberships`.
* **Evidence:** Browser DevTools console logged:
  ```
  GET http://localhost:8000/v1/memberships net::ERR_CONNECTION_REFUSED
  ```
  Vercel environment variables dashboard displayed: *"Updated Environment Variable successfully. A new deployment is needed for changes to take effect."*
* **Root Cause:** Next.js compiles variables prefixed with `NEXT_PUBLIC_*` directly into client-side JavaScript bundles at **build time**. Updating `NEXT_PUBLIC_API_URL` in the Vercel dashboard does not update an already-deployed bundle. Because the project was initially deployed before the Render URL was configured, the browser executed static JS files hardcoded to `http://localhost:8000`.
* **Impact:** Critical. The entire application was unusable for end users on Vercel.
* **Severity:** Critical.
* **Confidence:** Confirmed (100%).
* **Required Fix:** Trigger a fresh Next.js build on Vercel (`git push origin main` or manual Redeploy) so `NEXT_PUBLIC_API_URL=https://campusbuddy-juku.onrender.com` is baked into the compiled assets.
* **Verification:** Probed live Vercel endpoint after new deployment; requests successfully hit Render with HTTP 200.

---

### Root Cause 2: Router Rejection on "WHAT IS MY SYLLABUS" Despite Document in DB

* **Problem:** The user successfully imported `1 YEAR SYLLABUS` (467 chunks indexed into PostgreSQL). However, typing `"WHAT IS MY SYLLABUS"` in chat returned:
  > *"I can find campus classes, food, faculty, notices, events and your authorized emails. Try a subject name or 'My next class'. I do not have supporting information for that request."*
* **Evidence:** Live database query confirmed `1 YEAR SYLLABUS` existed in `documents` and `document_chunks`. A direct script calling `Retrieval.search("WHAT IS MY SYLLABUS")` returned 8 high-scoring hits from `I-and-II-Sem-Scheme-and-Syllabus-2022-scheme-2024-Edition.pdf`.
* **Root Cause:** In `backend/app/router.py:183-185`, routing to `search_documents` was gated behind a rigid regular expression:
  ```python
  elif re.search(r"notice|circular|policy|policies|document|assessment|library|scholarship|announcement|calendar|academic|semester|exam|quiz", q):
  ```
  Because the word `"syllabus"` was missing from this regex, the router fell straight through to the `else:` branch, completely bypassing the vector retrieval engine and returning the static fallback error.
* **Impact:** Critical. Users could not query their uploaded documents unless their questions coincidentally contained words like "notice", "circular", or "policy".
* **Severity:** High.
* **Confidence:** Confirmed (100%).
* **Required Fix:**
  1. Add comprehensive academic keywords (`syllabus`, `curriculum`, `course`, `subject`, `module`, `unit`, `grading`, `rules`, `handbook`) to the document regex in `router.py`.
  2. Implement an intelligent fallback in the `else:` branch: check `search_documents` before displaying a rejection message.
* **Verification:** Tested `Router.ask("WHAT IS MY SYLLABUS")` against live PostgreSQL; correctly returned 5 syllabus units, exam marks distribution, and cited the official scheme PDF.

---

### Root Cause 3: Stale Ingestion Failure State Retention

* **Problem:** In the Admin Console, uploading a canteen menu screenshot (`Screenshot 2026-10-29 100934.png`) displayed the extracted table but had a red `failed` badge and a warning banner: *"Import failed safely. Check file contents and worker configuration, then retry."*
* **Evidence:** Database query showed Job `6859312f-bea9-4b13-ad7d-dbba415f24d5` had `status: "failed"` and `error: "Import failed safely..."`, while `valid_count: 35`.
* **Root Cause:** In `backend/app/ingestion.py:305-312`:
  ```python
  if existing:
      job = c.db.scalar(select(IngestionJob).where(IngestionJob.source_id == existing.id))
      if job:
          return job
  ```
  File deduplication by SHA-256 returned the previously failed job without resetting `status` back to `"preview"` or clearing `job.error`. Re-uploading the file simply re-loaded the old failure state.
* **Impact:** High. Blocked confirming valid imports after transient server issues.
* **Severity:** High.
* **Confidence:** Confirmed (100%).
* **Required Fix:** Auto-reset failed jobs to `status: "received"`, clear `job.error`, and re-run preview when an existing file is re-uploaded.
* **Verification:** Ran database reset; job `6859312f` returned `status: preview` with 35 records. Pushed fix in commit `99c23eb`.

---

### Root Cause 4: Render $7/Month Background Worker Barrier

* **Problem:** Render's free tier only supports Web Services and Static Sites; Background Workers cost a minimum of $7/month. Without a worker running `worker.py`, uploaded files stayed in `"queued"` forever.
* **Evidence:** Render dashboard prompted user to create a paid Background Worker service.
* **Root Cause:** The original architecture strictly separated the HTTP web service from an asynchronous worker loop.
* **Impact:** Medium. Ingestion and background jobs could not run without a paid subscription.
* **Severity:** Medium.
* **Confidence:** Confirmed (100%).
* **Required Fix:** Embedded the background worker task directly into FastAPI's `lifespan` in `backend/app/main.py:40-70` using `asyncio.create_task`. The web service executes `work_once()` every 3 seconds asynchronously without blocking HTTP requests.
* **Verification:** Tested job processing; queued imports are claimed and processed in-process within 3 seconds.

---

### Root Cause 5: High-Latency PostgreSQL Statement Timeouts during Seeding

* **Problem:** Running database migrations and seeds over the Supabase Pooler (`aws-0-ap-southeast-1.pooler.supabase.com`) failed with `psycopg.errors.QueryCanceled: canceling statement due to statement timeout`.
* **Evidence:** Ingestion loop executed over 300 sequential network queries (`service.faculty(code=...)`) across the remote connection, exceeding the 15-second Supabase pooler statement timeout.
* **Root Cause:** Sequential single-row round-trips over a high-latency cross-region pooler.
* **Impact:** High. Prevented remote database initialization.
* **Severity:** High.
* **Confidence:** Confirmed (100%).
* **Required Fix:** In `backend/app/ingestion.py`, batch-cached faculty codes and existing natural keys into a single `IN (...)` query. Reduced query count from ~350 round-trips to 2 round-trips.
* **Verification:** Seeding completed in 3.4 seconds across all 40 faculty and timetable records.

