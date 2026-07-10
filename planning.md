# Planning — AI-Powered Intelligent Document & Image Management System

> **Status:** Finalized concept & scope
> **Last updated:** 2026-07-10
> **Companion doc:** [architecture.md](architecture.md) (component diagrams & pipeline flowcharts)

---

## 1. One-Line Summary

A local-first desktop assistant that **watches your folders, reads the actual content of every document and image, files them into meaningful academic and general categories automatically, and lets you find anything by asking in plain English** — no filenames, no manual foldering.

---

## 2. Problem Statement

Students and knowledge workers accumulate thousands of files — lecture PDFs, scanned notes, screenshots, bills, certificates, photos. Today these end up in `Downloads/`, `Desktop/`, or `New Folder (3)/` with names like `Scan_20240412.pdf`. Retrieval depends on remembering **where** you put something and **what you named it** — the two things humans are worst at.

Existing tools fail because they organize by **metadata** (name, date, extension) rather than by **meaning**. Our system organizes by **content**.

---

## 3. Goals & Non-Goals

### Goals (v1)
- Continuously monitor user-selected folders and process new files automatically.
- Understand file **content** (text + images), not filenames.
- Auto-classify into **academic** (semester/subject-aware) and **general** categories.
- Extract text from scanned/handwritten/photo documents via OCR.
- Index everything into a vector database for semantic retrieval.
- Provide a **natural-language chatbot** (RAG) that answers questions and cites source files.
- Provide **semantic image search** by description.
- Provide a **unified search** across documents and images.
- Keep a **Review Queue** for low-confidence cases and learn from corrections.
- Run **fully offline / local-first** (privacy of personal documents is a hard requirement).

### Non-Goals (v1)
- Cloud sync / multi-device sync (future).
- Multi-user / shared team libraries (future).
- Real-time collaborative editing of documents.
- Full document editing — we organize and retrieve, we don't edit content.
- Mobile app (the chosen UI stack keeps this open for later, but it's out of scope now).

---

## 4. Target User & Primary Use Case

**Primary persona:** A college student (the knowledge base is seeded with the college's subjects, subject codes, topics, and the student's *current semester*).

**Secondary persona:** Any individual who wants their personal document/photo library auto-organized and searchable (finance, identity, medical, work, personal).

**Signature use case:** A student downloads a scanned PDF of Operating Systems notes named `IMG_4821.pdf`. Within seconds it is OCR'd, recognized as *Operating Systems → Paging*, moved into `AI Library/Academics/Semester 5/Operating Systems/`, and becomes answerable via chat ("show me my notes on paging").

---

## 5. Finalized Feature Set

| # | Feature | Description | v1 |
|---|---------|-------------|----|
| 0 | **Onboarding & Setup** | Pick destination library root, monitored folders, preferences (move/copy/keep, thresholds, OCR, auto-approve). | ✅ |
| 1 | **Filesystem Watcher** | Detect create/copy/download/move events in monitored folders; enqueue for processing. | ✅ |
| 2 | **Document Segregation** | Type detection → text extraction / OCR → clean → embed → classify → file → store metadata. | ✅ |
| 3 | **OCR Agent** | Detect image-based docs; extract text so scans behave like digital PDFs. | ✅ |
| 4 | **Academic Classifier** | Semester-aware subject matching against a subject knowledge base. | ✅ |
| 5 | **General Classifier** | Finance / Identity / Work / Personal / Medical / Legal / Projects / Books / Others. | ✅ |
| 6 | **Confidence & Review Queue** | Auto-file above threshold; queue below it for user decision. | ✅ |
| 7 | **Document RAG Chatbot** | Ask questions; get answers with cited files + page/section. | ✅ |
| 8 | **Semantic Image Search** | CLIP/SigLIP visual embeddings; search by description. | ✅ |
| 9 | **Image OCR** | Extract text from text-heavy screenshots/photos; store alongside visual embedding. | ✅ |
| 10 | **Unified Search** | One search bar across documents + images. | ✅ |
| 11 | **Review & Learning Loop** | Corrections update classification memory & custom categories. | ✅ (rule/memory-based) |
| 12 | Shortcut Mode | Keep original in place, create organized shortcuts. | ⏳ future |
| 13 | Continual fine-tuning | Train a personalized classifier head from accumulated corrections. | ⏳ future |
| 14 | Spreadsheet support | XLSX/CSV parsing. | ⏳ future |

---

## 6. Finalized Tech Stack (Python-only)

The brief requires a **Python-only** stack that is both **distributable** (installable desktop app) **and deployable** (hostable as a web service). Below is the finalized selection.

### 6.1 UI — Decision: **NiceGUI** ✅

**Recommendation: NiceGUI is the primary UI stack.**

**Why it wins for this project:**
- **Pure Python.** No JavaScript/React/Electron toolchain — the whole app is one language.
- **Built on FastAPI** (the chosen backend). The UI and the AI backend become **a single Python process / single FastAPI app** — one thing to run, ship, and debug. No separate frontend server, no REST boilerplate between UI and logic.
- **Distributable as a native desktop app:** `ui.run(native=True)` opens a real OS window (via `pywebview`); package to a single binary with **PyInstaller**.
- **Deployable as a web app:** the exact same code runs as a hosted web server (Docker / any VPS) — satisfying "deployable also" with zero rewrite.
- Modern component set (built on Vue + Quasar): tables, dialogs, file pickers, upload areas, chat UI, dark mode — everything the Review Queue, chat, and search screens need.
- Async-native, so long-running OCR/embedding jobs and live progress updates are straightforward.

**One codebase → two delivery modes:** ship a desktop installer *and* host it on a server from the same source.

**Alternatives considered (documented so the choice is defensible):**

| Option | Pure Python | Native desktop | Web-deployable | Fit / Notes |
|--------|:-----------:|:--------------:|:--------------:|-------------|
| **NiceGUI** ✅ | ✅ | ✅ (pywebview) | ✅ | **Chosen** — merges with FastAPI backend; both delivery modes from one codebase. |
| **Flet** | ✅ | ✅ (Flutter) | ✅ | Great native look, future mobile path. Runs as its *own* runtime — backend stays a separate process. Strong second choice if you want a more "app-like" feel or mobile later. |
| **PySide6 / PyQt6** | ✅ | ✅ (best native) | ❌ | Most powerful native desktop, but **not web-deployable** and heavier UI code. Pick only if native polish outranks "deployable." |
| Streamlit / Gradio | ✅ | ⚠️ (web-only) | ✅ | Fastest to prototype, but page-reload model and weak local-FS/state handling make it a poor fit for a persistent file-manager UI. Good for a quick internal demo only. |
| CustomTkinter | ✅ | ✅ | ❌ | Simple, but dated UX and no web deploy. |

> **Fallback rule:** if native desktop polish or a future mobile client becomes the priority, switch the UI layer to **Flet** — the backend, pipelines, and data model in this plan are UI-agnostic and unaffected.

### 6.2 Full Stack

| Layer | Choice | Notes |
|-------|--------|-------|
| **UI** | **NiceGUI** (`native=True` desktop / web deploy) | Single Python app with the backend. |
| **Backend / API** | **FastAPI** + **Uvicorn** | Hosts pipeline endpoints; shares process with NiceGUI. |
| **Async / jobs** | `asyncio` + a lightweight in-process task queue (upgrade path: **Celery/RQ + Redis**) | Ingestion & embedding run off the UI thread. |
| **File monitoring** | **Watchdog** | Cross-platform FS events. |
| **Text extraction** | **PyMuPDF** (fitz), **pdfplumber**, **python-docx**, **python-pptx**, **Unstructured** | Per-format extractors. |
| **OCR** | **PaddleOCR** (primary, best accuracy incl. handwriting) · **Tesseract** (light fallback) · **EasyOCR** (optional) | Configurable engine. |
| **Text embeddings** | **Sentence-Transformers** (e.g. `bge-*` / `all-MiniLM` / `e5`) | Local, CPU-friendly. |
| **Image embeddings** | **CLIP / SigLIP** (`open_clip` or `sentence-transformers` CLIP) | Shared text↔image space for image search. |
| **LLM (RAG)** | **Ollama** (Qwen / Llama / Mistral / Gemma, quantized) | Fully local generation. |
| **Vector DB** | **ChromaDB** (default, embedded, zero-ops) · **Qdrant** (upgrade for scale) · FAISS (optional index) | Local persistence. |
| **Metadata DB** | **SQLite** (default, embedded) · PostgreSQL (optional, for deploy) | Files, categories, corrections, jobs. |
| **RAG orchestration** | **LlamaIndex** (primary) or LangChain | Chunking, retrieval, citation. |
| **Packaging** | **PyInstaller** (desktop binary) · **Docker** (web deploy) | Two delivery targets. |
| **Config / models** | **Pydantic Settings**, `pydantic` schemas | Typed settings & data contracts. |

**Design principle:** every default is **local, embedded, and offline** (SQLite + ChromaDB + Ollama + local OCR/embeddings). The "optional" swaps (Postgres, Qdrant, Celery/Redis) are the same interfaces at larger scale for the *deployable* server mode.

---

## 7. Data Model (finalized fields)

Per processed file, stored in SQLite (metadata) + ChromaDB (vectors):

| Field | Source |
|-------|--------|
| `id`, `filename`, `original_path`, `organized_path` | ingestion |
| `file_type`, `size`, `created_at`, `processed_at` | FS + pipeline |
| `is_image_based`, `ocr_used`, `ocr_engine` | OCR agent |
| `extracted_text` (full), `text_chunks[]` | extraction/chunking |
| `subject` / `category`, `semester` | classifier |
| `keywords[]` | classifier / keyphrase extraction |
| `confidence_score`, `decision` (`auto` / `review`) | classifier |
| `embedding_ref` (doc), `image_embedding_ref` | vector DB pointers |
| `user_corrected` (bool), `corrected_to` | review loop |

See [architecture.md](architecture.md#8-data-model) for the ER diagram and vector-collection layout.

---

## 8. Classification Strategy (finalized)

1. **Embed** the cleaned document text.
2. **Semester-first academic match:** compare against embeddings of *current-semester* subjects first (drastically cuts false positives), then broaden to all subjects only if no strong match.
3. **General fallback:** if no subject clears the academic threshold, match against general-category prototype embeddings + keyword rules (Finance, Identity, Medical, Legal, etc.).
4. **Confidence gate:**
   - `score ≥ threshold` → auto-file (or auto-approve, per preference).
   - `score < threshold` → **Review Queue**.
5. **Review Queue actions:** accept · choose another folder · create new folder/category · mark Personal/General · merge categories.
6. **Learning:** every correction is persisted as a labeled example and updates category prototype embeddings / custom-category memory — improving future routing without retraining in v1; enables optional fine-tuning later.

---

## 9. Delivery Modes

| Mode | How | Audience |
|------|-----|----------|
| **Desktop app (primary)** | NiceGUI `native=True`, packaged with PyInstaller into a signed installer per OS. | End users / students. |
| **Self-hosted web app** | Same FastAPI+NiceGUI app in Docker on a VPS/LAN box; browser UI. | Power users, labs, demo/deploy. |

---

## 10. Roadmap (phased)

Indicative sequencing; each phase is independently demoable.

- **Phase 0 — Foundations:** project scaffold, settings/onboarding, SQLite + ChromaDB wiring, NiceGUI shell with dark mode.
- **Phase 1 — Ingestion & extraction:** Watchdog monitor, file-type detection, text extraction for PDF/DOCX/PPTX/TXT/MD, job queue.
- **Phase 2 — OCR agent:** image-based detection, PaddleOCR/Tesseract, unified text output.
- **Phase 3 — Embeddings & classification:** subject knowledge base, semester-aware academic classifier, general classifier, confidence gate, file move/copy.
- **Phase 4 — Review & learning:** Review Queue UI, corrections persistence, custom categories, prototype updates.
- **Phase 5 — Document RAG chatbot:** chunking, retrieval, Ollama answers with citations (file + page/section).
- **Phase 6 — Image pipeline & search:** CLIP/SigLIP embeddings, image OCR, semantic image search.
- **Phase 7 — Unified search:** single interface across documents + images.
- **Phase 8 — Packaging:** PyInstaller desktop build + Docker web build; onboarding polish.
- **Future:** shortcut mode, continual fine-tuning, spreadsheets, cloud/multi-device sync, mobile (Flet).

---

## 11. Key Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| OCR accuracy on handwriting/low-quality scans | Wrong/empty text → misclassification | PaddleOCR primary; confidence gate routes weak extractions to Review; store raw + OCR text. |
| Misclassification erodes trust | User stops trusting auto-move | Conservative threshold + "ask before moving uncertain" default; **Move is reversible** (log original path, offer undo). |
| Large models are heavy on modest laptops | Slow / high RAM | Quantized Ollama models, small embedding models, CPU-friendly defaults, background indexing with throttling. |
| Destructive moves lose files | Data loss | Default to **Copy**; on Move, keep an audit log + undo; never delete originals without explicit confirm. |
| Watcher storms (bulk downloads, temp files) | Queue floods, partial files processed | Debounce, ignore temp/partial extensions, stability delay before processing. |
| Duplicate / re-ingested files | Clutter, wasted compute | Content hash (dedupe) before pipeline. |
| First-run model downloads | Broken offline promise | Bundle or pre-fetch models during setup; show progress; offline after first run. |
| Privacy of personal/identity docs | Sensitive data exposure | 100% local by default; no network calls in desktop mode; clear indicator when web-deployed. |

---

## 12. Success Metrics

- **Auto-classification precision** on a labeled test set (target: high precision > recall — prefer Review over wrong moves).
- **% of files auto-filed** without review (coverage).
- **Search top-3 hit rate** for natural-language queries (docs + images).
- **Time-to-file** per document (ingestion → filed).
- **Correction rate trend** (should fall as the learning loop improves prototypes).

---

## 13. Open Questions

- Ship models bundled (bigger installer) or download-on-first-run (needs network once)?
- Default embedding model (accuracy vs. size vs. speed) — benchmark `bge-small` vs `e5-small` vs `MiniLM`.
- Chunking strategy for RAG citations (page-based vs. semantic) to guarantee accurate page/section references.
- How aggressively should the learning loop mutate category prototypes vs. keep them stable?
- Undo window / retention policy for moved originals.

---

## 14. Definition of Done (v1)

A user can install the desktop app, complete onboarding, drop mixed documents and images into a watched folder, watch them get OCR'd, classified (semester-aware) and filed, review the uncertain ones, then **ask a question in chat and get a cited answer** and **find an image by describing it** — all offline.
