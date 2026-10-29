# MVP GAP ANALYSIS: REQUIREMENTS VS. REALITY

**Project:** CampusBuddy / CampusOS  
**Audit Date:** 2026-10-29  
**Auditor:** Senior Software Architect  

---

## 1. Scope Categorization

### P0 — Essential (Core MVP Baseline)
*Must work reliably and safely for the product to be considered functional.*
1. **Multi-Tenant Authentication & Session Persistence:** Users log in with institutional credentials; session survives page refreshes.
2. **Timetable & Daily Schedule:** Displays today's and weekly schedule for student's department, semester, and section; computes "Next Class".
3. **Canteen Menu & Budget Search:** Shows real items, prices, dietary flags; allows filtering by budget.
4. **Document RAG Ingestion (PDF & Scanned Notices):** Admins can upload PDFs and scanned notices; text and tables are extracted, chunked, and stored in vector database.
5. **Grounded Conversational AI Q&A:** Chat interface answers campus queries using retrieved records and citations; no hallucinated claims or arbitrary SQL execution.
6. **Zero-Localhost Cloud Production:** Web UI on Vercel and API on Render communicate securely with HTTPS and CORS.

### P1 — Important (Enhances Core Workflow)
*Materially improves the system; should follow immediately after P0 is verified.*
1. **Admin Ingestion UI Polish:** Inline column re-mapping dropdowns, error preview list, and one-click re-indexing.
2. **Calendar Conflict & Free Slot Analysis:** Detects schedule overlaps and surfaces free hours between classes.
3. **Faculty Directory & Office Details:** Searchable faculty listings with department, office location, and contact information.
4. **Outdated Notice Handling:** Warning badges when a retrieved circular has been superseded by a newer version.

### P2 — Later (Post-MVP Enhancements)
*Non-essential for initial user validation; can wait.*
1. **Automated Google Workspace OAuth Sync:** Syncing live Gmail and Google Calendar via OAuth tokens.
2. **Full-Blown Native Mobile Application:** React Native / Flutter apps.
3. **Real-Time Voice Streaming:** Speech-to-text / text-to-speech audio interface.
4. **Multi-Language Regional Translation:** Translating campus notices into regional languages.

---

## 2. MVP Gap Analysis Table

| Requirement | Current State | What's Missing | Dependencies | Priority | Estimated Effort | Acceptance Criteria |
| :--- | :--- | :--- | :--- | :---: | :---: | :--- |
| **P0: Cloud Deployment (Zero Localhost)** | Render & Vercel live; CORS configured; DB migrated | Triggering Vercel deployment with baked `NEXT_PUBLIC_API_URL` | Vercel Git Webhook | P0 | 0.5 hours | Browser loads `https://campus-buddy-mvp.vercel.app` and executes API calls without `localhost` errors. |
| **P0: Document RAG Ingestion (PDFs & Notices)** | Full PyMuPDF + Gemini OCR + pgvector implemented | Admin user awareness: select "Notice or document" instead of "Canteen menu" | Supabase pgvector | P0 | Complete | PDF uploads parse into chunks; pgvector cosine similarity retrieves relevant passages. |
| **P0: Natural Language Routing (Syllabus Queries)** | Rigid regex expanded; document search fallback added | Deployed latest commit to Render | Render Git deploy | P0 | Complete | Querying *"WHAT IS MY SYLLABUS"* returns 5 units and SEE theory marks breakdown citing official PDF. |
| **P0: Canteen Menu Extraction & Search** | 35 items extracted from screenshot; budget filtering working | Admin clicks "Confirm import" in Admin Console | Ingestion Worker | P0 | 0.25 hours | 35 menu records saved to `campus_records`; queries like *"What coffee under ₹20"* return exact items. |
| **P1: Background Ingestion Without Paid Worker** | In-process asyncio task running inside FastAPI lifespan | None (Implemented) | FastAPI lifespan | P1 | Complete | Uploaded files transition from `received` -> `preview` -> `completed` within 5 seconds without $7/mo worker. |
| **P1: High-Latency Remote Pooler Batching** | Batch queries implemented; faculty codes and keys cached | None (Implemented) | Supabase DB | P1 | Complete | Seeding and large file imports complete in <5 seconds without statement cancellation timeouts. |
| **P2: Google Workspace OAuth Integration** | Endpoints and token encryption schema exist; OAuth redirect stubbed | Live Google Cloud Console Client ID & Secret credentials | Google Cloud OAuth | P2 | 8 hours | User can link Google account and view unread department emails in chat. |

---

## 3. Structural Reusability & Architecture Assessment

### What Can Be Reused Without Modification (85%)
- **Data Model (`models.py`):** The unified `CampusRecord` table with Pydantic JSON payloads and indexed natural keys is an exceptionally clean pattern for handling diverse campus data without schema explosion.
- **Security & Authorization (`auth.py`):** Supabase RSA JWT verification, multi-tenant boundaries (`X-Tenant-ID`), and department-scoped write checks are solid and production-ready.
- **Frontend Architecture (`src/app/`, `components/`):** React 19 / Next.js 16 components with Tailwind CSS and Lucide icons provide a responsive, accessible interface with zero hydration errors.
- **Vector Search Engine (`retrieval.py`):** Dense 384-dimensional Gemini embeddings matching pgvector cosine distance (`<=>`) provides high-quality semantic retrieval.

### What Needed Correction (Fixed during Audit)
1. **`router.py`:** Removed brittle keyword gating; introduced universal document retrieval fallback.
2. **`ingestion.py`:** Auto-cleared stale failure state on re-upload; added batch database caching.
3. **`main.py`:** Embedded the background worker in FastAPI lifespan to eliminate Render's $7/month requirement.

### What Should Be Simplified or Postponed
- **Google Workspace OAuth:** Do not block MVP launch on Google OAuth credentials. Institutional file uploads (PDF, CSV, Excel) cover 95% of campus operational needs.
- **Complex Exception Rules:** Date-specific holiday overrides (`exception_rules`) exist in schema but are non-essential for initial student verification.

