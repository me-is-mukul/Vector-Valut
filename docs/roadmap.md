# Roadmap — AI-Powered Intelligent Document & Image Management System

> **Companion docs:** [planning.md](planning.md) (scope & decisions) · [architecture.md](architecture.md) (diagrams & flows)
> **Purpose:** planning.md §10 names the nine phases. This doc is the executable version — what to build, in what order, and how you know a phase is done.
> **Last updated:** 2026-07-13

## Status

| Phase | | Notes |
|---|---|---|
| 0 — Foundations | ✅ **done** | Walking skeleton: watcher, dedupe, durable job queue, crash recovery, write-ahead undo log, NiceGUI shell. |
| 1 — Ingestion & extraction | ✅ **done** | PyMuPDF + pdfplumber fallback, DOCX, PPTX. Per-page spans throughout. Header/footer stripping. |
| 2 — OCR agent | ⬜ next | `NullOcr` is still the stub. Scans are correctly *flagged* `is_image_based` and routed to Review. |
| 3 — Embeddings & classification | ✅ **done** | `bge-small`, semester-aware classifier, Subject KB (§7 resolved), **floors calibrated by measurement** — `scripts/calibrate.py`. |
| 4 — Review & learning | ⬜ | Review Queue lists items; the approve/reassign/merge *actions* are not built. |
| 5 — RAG chatbot | ✅ **done** | Ollama + qwen2.5:7b, page-exact citations, and a hard refusal to answer without grounding. |
| 6 — Image pipeline | ⬜ | |
| 7 — Unified search | ⬜ | Document search works; images not yet in the mix. |
| 8 — Packaging | ⬜ | |

Phases 1, 3 and 5 were built together because a chatbot is worthless without real
embeddings (Phase 3), and real embeddings are worthless if the app can only read `.txt`
(Phase 1). Phase 2 was skipped for now, which is why scanned PDFs still go to Review.

---

## Table of Contents
1. [The Core Strategy: Walking Skeleton First](#1-the-core-strategy-walking-skeleton-first)
2. [Decisions to Lock Before Writing Code](#2-decisions-to-lock-before-writing-code)
3. [Repository Layout](#3-repository-layout)
4. [Phase 0 — Foundation (step by step)](#4-phase-0--foundation-step-by-step)
5. [Phases 1–8](#5-phases-18)
6. [Cross-Cutting Workstreams](#6-cross-cutting-workstreams)
7. [Blocking Dependencies](#7-blocking-dependencies)
8. [Sequencing & Parallelism](#8-sequencing--parallelism)

---

## 1. The Core Strategy: Walking Skeleton First

The biggest risk in a project of this shape is **perfecting components before proving the pipeline**. It is entirely possible to spend three weeks tuning PaddleOCR on handwritten notes and only then discover that the watcher fires twice per download, the dedupe hash races the file write, and the job queue loses work on restart.

So Phase 0 does not build any AI. Phase 0 builds **the whole pipeline end to end with fake parts**:

```
drop a .txt file in a watched folder
  → watcher fires
  → dedupe by content hash
  → job row written to SQLite
  → "extractor" reads the plain text
  → "embedder" returns a deterministic fake vector
  → "classifier" matches a hardcoded keyword rule
  → organizer COPIES the file into AI Library/<category>/
  → SQLite row appears in a NiceGUI table
```

No OCR. No Ollama. No Sentence-Transformers. No ChromaDB queries that matter. It runs in under a second and it works.

Every phase after this **swaps one stub for a real implementation**, and the app never stops working. Phase 2 replaces the null OCR engine with PaddleOCR. Phase 3 replaces the fake embedder with `bge-small` and the keyword classifier with the semester-aware one. The seams are already there and already tested.

This is why §2 (the interfaces) matters more than any other decision in this document. Get the seams right and the rest is filling in boxes.

---

## 2. Decisions to Lock Before Writing Code

These are the choices that are expensive to reverse later. Recommendations are given — take them or override them, but decide now.

### 2.1 Python version — **3.13 (your current 3.13.5 is fine)**

Verified against the actual index on this machine: `paddlepaddle 3.3.1`, `paddleocr`, `chromadb 1.5.9`, `sentence-transformers 5.6.0`, `open_clip_torch 3.3.0`, `nicegui 3.14.0`, `pymupdf 1.28.0`, `llama-index 0.14.23` and `unstructured 0.24.1` all publish cp313 Windows wheels. There is no reason to pin an older interpreter.

### 2.2 Package manager — **uv**

Not installed yet. It gives you a real lockfile (`uv.lock`), reproducible installs, and fast resolution of a dependency tree that is about to include Torch. `pip install uv` or `winget install astral-sh.uv`.

### 2.3 Dependency groups — **split them from day one**

The full tree (Torch + Paddle + Chroma) is several gigabytes. If every contributor and every CI run pays that cost, iteration slows to a crawl. Define optional groups in `pyproject.toml`:

- `core` — NiceGUI, FastAPI, SQLAlchemy, Pydantic, Watchdog. This is what Phase 0 needs, and it installs in seconds.
- `ocr` — PaddleOCR, Tesseract bindings.
- `ml` — Sentence-Transformers, open_clip, Torch.
- `dev` — pytest, ruff, mypy.

The walking skeleton must run on `core` alone. That constraint is what keeps the stub seams honest.

### 2.4 Persistence — **SQLAlchemy 2.0 + Alembic, from the first commit**

The schema in architecture.md §11 will churn — categories gain prototypes, corrections gain provenance, jobs gain retry counts. Retrofitting migrations onto a database that already holds a user's real file index is miserable. Add Alembic before there is anything to migrate.

### 2.5 The job table is the source of truth — **not the in-memory queue**

An `asyncio.Queue` is fine as the *dispatcher*, but the durable state lives in SQLite. Write the job row (`status=queued`) **before** enqueueing. On startup, re-enqueue anything still `queued` or `running`.

Skip this and a crash mid-pipeline silently drops a file. For an app whose job is to *move the user's files*, a silently dropped file is a data-loss bug, not a papercut.

### 2.6 The undo log is written before the move, not after

planning.md §11 lists "destructive moves lose files" as a top risk. The mitigation has to be structural:

1. Write the move record (`from`, `to`, `status=pending`) to SQLite.
2. Perform the copy/move.
3. Mark the record `complete`.

A crash between 1 and 2 is recoverable. A crash between 2 and 3 is recoverable. A move performed with no record at all is not. Also: **default to Copy**, and make Move a setting the user opts into after they trust the classifier.

Build this in Phase 0. Retrofitting an audit log into an organizer that has already run against someone's `Downloads/` folder is how you lose files.

### 2.7 The layering rule

```
ui/  api/          →  may import services/
services/          →  may import pipeline/, storage/
pipeline/          →  may import domain/
domain/            →  imports nothing of ours
```

Never upward. This is the mechanical thing that makes "UI-agnostic core" (architecture.md §1) true rather than aspirational — and it is what makes the NiceGUI → Flet fallback in planning.md §6.1 a real option instead of a rewrite. Enforce it with an import-linter rule in CI so it cannot rot.

---

## 3. Repository Layout

```
osdc/
├── pyproject.toml              # deps split into core / ocr / ml / dev
├── uv.lock
├── alembic/                    # migrations, from commit one
├── src/osdc/
│   ├── main.py                 # entrypoint: FastAPI app + NiceGUI mounted on it
│   ├── config/
│   │   ├── settings.py         # Pydantic Settings
│   │   └── paths.py            # per-OS app data dir (platformdirs)
│   ├── domain/                 # PURE. no I/O, no framework imports.
│   │   ├── models.py           # FileRecord, Chunk, Classification, Job, MoveRecord
│   │   ├── enums.py            # Decision, JobStatus, FileType, Category
│   │   └── ports.py            # ← the Protocols. the most important file here.
│   ├── storage/
│   │   ├── db.py               # engine + session
│   │   ├── schema.py           # ORM tables (architecture.md §11)
│   │   ├── repositories.py     # FileRepo, JobRepo, CorrectionRepo, CategoryRepo
│   │   └── vectors.py          # Chroma client; collection contracts (architecture.md §12)
│   ├── pipeline/
│   │   ├── ingest/             # watcher, debounce, stability check, dedupe
│   │   ├── extract/            # per-format extractors + OCR agent
│   │   ├── embed/              # text + image embedders
│   │   ├── classify/           # semester-aware academic + general fallback
│   │   └── organize/           # copy/move + undo log
│   ├── services/               # orchestration. the ONLY thing ui/ and api/ may call.
│   │   ├── ingestion.py  processing.py  search.py  rag.py  feedback.py  config.py
│   ├── api/                    # FastAPI routers (thin — no logic)
│   └── ui/                     # NiceGUI pages (thin — no logic)
├── tests/
│   ├── corpus/                 # labeled real files — see §6.1
│   └── fixtures/
└── roadmap.md  planning.md  architecture.md
```

### `domain/ports.py` — write this file first

Everything swappable gets a `Protocol`. Sketch:

```python
class TextExtractor(Protocol):
    def supports(self, path: Path) -> bool: ...
    def extract(self, path: Path) -> ExtractedText: ...      # text + per-page spans

class OCREngine(Protocol):
    name: str
    def ocr(self, path: Path) -> OCRResult: ...              # text, per-page, confidence

class TextEmbedder(Protocol):
    dim: int
    def embed(self, texts: list[str]) -> list[Vector]: ...

class ImageEmbedder(Protocol):
    dim: int
    def embed_images(self, paths: list[Path]) -> list[Vector]: ...
    def embed_text(self, texts: list[str]) -> list[Vector]: ...   # shared CLIP space

class VectorStore(Protocol):
    def upsert(self, collection: str, ids, vectors, metadata) -> None: ...
    def query(self, collection: str, vector, k: int, where: dict | None) -> list[Hit]: ...

class Classifier(Protocol):
    def classify(self, doc: EmbeddedDocument) -> Classification: ...   # label, score, decision

class TaskQueue(Protocol):
    async def enqueue(self, job_id: str) -> None: ...
```

Phase 0 ships trivial implementations of every one of these: `PlainTextExtractor`, `NullOCR`, `HashEmbedder` (deterministic fake vectors — no model, no download), `KeywordClassifier`, `AsyncioQueue`. The real ones arrive later and nothing above them changes.

---

## 4. Phase 0 — Foundation (step by step)

**Goal:** a `.txt` file dropped into a watched folder ends up copied into the AI Library and visible in the UI — with zero AI involved.

**Exit criterion (demo this):** drop `os_notes.txt` containing the word "paging" into a watched folder. Within two seconds it appears in `AI Library/Academics/Operating Systems/`, and a row shows up in the NiceGUI table with its hash, category, confidence, and decision. Kill the app mid-processing and restart it — the job resumes.

### Step 1 — Scaffold
`uv init`, the `src/osdc/` tree from §3, `pyproject.toml` with the four dependency groups, ruff + mypy + pytest configured. Install `core` only.

### Step 2 — Config and app paths
`Settings` via Pydantic Settings: library root, watched folders, `copy | move`, confidence thresholds, OCR on/off, auto-approve on/off. Resolve the app data directory with `platformdirs` (`%LOCALAPPDATA%\osdc` on Windows) — never write databases next to the source tree. Ship a `settings.toml` default plus env-var overrides.

### Step 3 — Domain models and ports
`domain/models.py` and `domain/enums.py` from architecture.md §11. Then `domain/ports.py` (§3 above). Pure Python, no imports from anywhere else in the project. This is the contract everything else is written against.

### Step 4 — Storage
SQLAlchemy tables for `FILE`, `CHUNK`, `EMBEDDING`, `CATEGORY`, `SUBJECT`, `SEMESTER`, `CORRECTION`, `JOB`, plus a `MOVE_LOG` table that architecture.md's ER diagram doesn't have yet but §2.6 requires. Repositories on top. First Alembic migration. Wire the Chroma client and create the five collections from architecture.md §12 — empty, but created, so the contracts exist.

### Step 5 — The ingestion front door
This is subtler than it looks, and it is where the sharp edges live:

- Watchdog observer on the configured folders.
- **Ignore** `.crdownload`, `.part`, `.tmp`, `~$*`, and anything starting with `.`.
- **Debounce** — a single browser download can emit five events.
- **Stability check** — poll size + mtime until unchanged across two consecutive reads before touching the file. A file that is still being written will hash to garbage.
- **Content hash** (BLAKE2b over the bytes) → skip if already indexed, and log the duplicate reference.
- Write the `JOB` row, *then* enqueue.

Test this against a real bulk download of 50 files. It is the component most likely to embarrass you later.

### Step 6 — The stub pipeline
`PlainTextExtractor` (`.txt`/`.md` only), `NullOCR`, `HashEmbedder`, `KeywordClassifier` with a hardcoded map (`"paging" | "deadlock" → Operating Systems`, `"invoice" | "receipt" → Finance`). Confidence gate reads the real threshold from settings. Below threshold → `decision=review`.

### Step 7 — The organizer, with the undo log
Copy (default) into `AI Library/<Academics|General>/<label>/`. Filename collision → suffix, never overwrite. Move-record written before the operation, per §2.6. Add a `services/feedback.undo(move_id)` that reverses it, and test it.

### Step 8 — The NiceGUI shell
One `main.py` running FastAPI + NiceGUI in a single process (`ui.run(native=True)`). Nav: **Library** (table of processed files), **Review Queue** (empty for now), **Chat** (stub), **Search** (stub), **Settings** (bound to `Settings`). Dark mode on. Live progress via NiceGUI's async refresh as jobs complete.

### Step 9 — CI
GitHub Actions: ruff, mypy, pytest, and the import-linter layering check from §2.7. Runs on the `core` group only, so it stays fast.

---

## 5. Phases 1–8

Each phase is independently demoable and swaps stubs for real implementations. Phase numbering matches planning.md §10.

---

### Phase 1 — Real ingestion & extraction
**Swaps:** `PlainTextExtractor` → the real extractor set.

Registry of extractors resolved by detected type (magic bytes via `filetype`, not the file extension — a `.pdf` that is actually a JPEG is common in scanned-document workflows). PyMuPDF for PDF text + per-page spans, pdfplumber as the fallback for awkward layouts, python-docx, python-pptx. Text cleaner: dehyphenation across line breaks, header/footer stripping, whitespace normalization.

**Critical detail:** extraction must return **per-page spans**, not one flat string. Phase 5's citations ("file · page 12") are impossible to add later if page provenance was thrown away at extraction time. Get this right once.

**Exit:** drop a mixed folder of PDF/DOCX/PPTX/TXT — every file lands in the Library with correct extracted text, and the `is_image_based` flag is set correctly on scans (even though nothing OCRs them yet).

---

### Phase 2 — OCR agent
**Swaps:** `NullOCR` → PaddleOCR, with Tesseract as fallback.

The image-based detection heuristic (architecture.md §5) does the real work: a PDF with a selectable-text layer skips OCR entirely; a PDF whose pages yield near-zero characters gets OCR'd. Get the threshold wrong in the cheap direction and you OCR thousands of digital PDFs for nothing; wrong in the expensive direction and scans arrive at the classifier as empty strings.

Emit a per-page OCR confidence and persist it. Low-confidence extractions route straight to the Review Queue rather than being classified on garbage text.

**Exit:** a phone photo of handwritten notes, and a scanned PDF, both produce usable text and reach the right folder.

---

### Phase 3 — Embeddings & classification
**Swaps:** `HashEmbedder` → Sentence-Transformers; `KeywordClassifier` → the semester-aware classifier from architecture.md §6.

Three pieces:
1. **The Subject Knowledge Base.** See §7 — this is a blocking dependency you must resolve before the phase starts.
2. **Embedding.** Benchmark `bge-small` / `e5-small` / `MiniLM` against the labeled corpus (§6.1) and pick on measured accuracy, not vibes. This closes an open question in planning.md §13.
3. **The classifier.** Current-semester subjects first, broaden to all semesters, fall back to general category prototypes, then the confidence gate.

**Exit:** the signature use case from planning.md §4 — `IMG_4821.pdf` (a scan of OS notes) lands in `AI Library/Academics/Semester 5/Operating Systems/` automatically. And the measured precision on your labeled corpus is reported, not guessed.

---

### Phase 4 — Review & learning
Review Queue UI: approve · reassign · create category · mark personal · merge. Every action persists a `CORRECTION` row and updates the category prototype embedding in the `category_prototypes` collection.

**Design caution** (planning.md §13 asks this): update prototypes as a **running centroid with a damping factor**, not a hard overwrite. One angry correction should nudge a category, not redefine it. Keep the correction rows immutable so prototypes can always be rebuilt from scratch if the update rule turns out to be wrong.

**Exit:** correct a misfiled document; a similar document filed afterwards routes correctly without any retraining.

---

### Phase 5 — Document RAG chatbot
Chunking (the strategy is an open question in planning.md §13 — page-aware chunking is the safe default precisely because it makes citations exact), retrieval from `doc_chunks`, Ollama generation with the retrieved context, answers rendered with clickable file + page citations.

**Non-negotiable:** if retrieval returns nothing above the relevance floor, the model says "I don't have that" — it does not answer from its own weights. An organizer that confidently invents the contents of your medical records is worse than no chatbot.

**Exit:** "Which PDF contains Binary Tree LCA?" → correct answer, correct file, correct page, clickable.

---

### Phase 6 — Image pipeline & search
**Swaps:** the `ImageEmbedder` stub → CLIP/SigLIP.

Visual embeddings for every image; the text-density heuristic decides which images *also* get OCR'd into `image_ocr_text`. This is what makes both "sunset on the beach" and "screenshot containing segmentation fault" work (architecture.md §7).

**Exit:** both of those queries return the right images.

---

### Phase 7 — Unified search
Fan out one query across `doc_chunks`, `image_visual`, and `image_ocr_text`; merge and re-rank.

**The hard part is score normalization.** Cosine similarities from a text encoder and from CLIP are not on the same scale, and naively sorting the merged list will let one modality dominate. Normalize per-collection (z-score or min-max within each result set) before merging, then dedupe by `file_id`.

**Exit:** one search bar, results spanning PDFs, slides, note photos, and screenshots, ranked sensibly.

---

### Phase 8 — Packaging
PyInstaller desktop build (`--onedir`, not `--onefile` — Torch and Paddle in a onefile bundle produce a startup unpack that takes tens of seconds) and the Docker web build. Onboarding wizard polish. First-run model fetch with a progress UI, resolving the last open question in planning.md §13.

**Exit:** planning.md §14's definition of done, from a clean install on a machine that has never seen the project.

---

## 6. Cross-Cutting Workstreams

These do not live in a phase. They run alongside.

### 6.1 The labeled corpus — start collecting in Phase 1

planning.md §12 sets targets ("auto-classification precision", "search top-3 hit rate") and architecture.md §6 hardcodes a `0.85` threshold. **Neither is meaningful without a labeled test set**, and you cannot tune a threshold by feel.

From Phase 1 onward, collect `tests/corpus/` — 50 to 100 real files (digital PDFs, scans, phone photos of notes, screenshots, an invoice, an ID card, a certificate) each with its correct label in a manifest. Add `scripts/eval.py` that reports precision, recall, and coverage per threshold. Phase 3 is largely guesswork without it, and this corpus is what turns "we picked 0.85" into "0.85 is where precision crosses 95% on our data."

Keep it out of git if any file is personal — commit the manifest, gitignore the payload.

### 6.2 Safety invariants — test these like they are load-bearing, because they are

- Never overwrite an existing file at the destination.
- Never delete an original without explicit confirmation.
- Every move is reversible for the configured retention window.
- A crash at any pipeline stage loses no files and no jobs.

Write these as tests in Phase 0, not as intentions.

### 6.3 Performance budget
Set a target early ("a 20-page scanned PDF, ingestion to filed, in under N seconds on CPU") and track it per phase. OCR and embedding will dominate. Throttle background indexing so the app never makes the user's machine unpleasant to use — that is the fastest way to get uninstalled.

---

## 7. Blocking Dependencies

**The Subject Knowledge Base has no defined data source, and it blocks Phase 3.**

Feature 4 needs subjects, codes, topics, credits, and semester mapping (architecture.md §11 `SUBJECT`/`SEMESTER`). Nothing in either doc says where that data comes from. Three options, decide during Phase 0's onboarding design:

1. **Hand-authored YAML** shipped with the app, one file per curriculum. Reliable, zero magic, but it does not generalize past one college.
2. **Onboarding form** where the student types subjects for the current semester. Generalizes, but it is friction at exactly the moment the user has least patience.
3. **Syllabus import** — the user drops in a syllabus PDF and the pipeline extracts subjects and topics from it. The best experience by far, and pleasingly recursive (the app's own extraction pipeline bootstraps its own knowledge base). Also the most work, and it fails on badly formatted syllabi.

Recommendation: build **(1)** as the seed for the Phase 3 demo, ship **(2)** as the reliable path, and treat **(3)** as a Phase 3.5 enhancement once extraction is proven.

> **RESOLVED (Phase 3).** Option (1) shipped: [`src/osdc/data/curriculum.yaml`](../src/osdc/data/curriculum.yaml)
> holds 24 subjects across 8 semesters plus 8 general categories, loaded on startup and
> embedded into the `subject_kb` and `category_prototypes` collections. It is keyed on a
> fingerprint of (curriculum + embedding model), so editing the file or switching models
> rebuilds the prototypes automatically — prototypes embedded with one model are
> meaningless to another, and silently mixing them would degrade classification with no
> error anywhere.
>
> **It is a generic sample syllabus.** Options (2) the onboarding form and (3) syllabus-PDF
> import both write into this same shape, so neither is a rewrite.

**Also worth noting:** planning.md §7 links to `architecture.md#8-data-model`, but the data model is §11 in architecture.md (§8 is the RAG flow). Minor, but fix it while the docs are still small.

---

## 8. Sequencing & Parallelism

Hard dependencies:

```
Phase 0 ──┬─→ Phase 1 ──→ Phase 2 ──→ Phase 3 ──┬─→ Phase 4
          │                                     ├─→ Phase 5 ──┐
          └─────────────────→ Phase 6 ──────────┴─────────────┴─→ Phase 7 ──→ Phase 8
```

- **Phase 6 (images) only depends on Phase 0**, not on 1–3. Its pipeline branches at file-type detection and never touches the document extractors. If more than one person is working on this, image search is the clean parallel track from the very start.
- **Phase 4 and Phase 5 both depend only on Phase 3** and are independent of each other.
- **Phase 7 is the only true join point**, and it needs both document and image vectors to exist.
- **Phase 8 is genuinely last.** Do not attempt PyInstaller with Torch and Paddle in the tree until the feature set has stopped moving — that build is a multi-day fight and you only want to have it once.

No dates here, deliberately: the phase sizes depend on how many people are on this and how much time per week. But the *order* is load-bearing, and the walking skeleton in Phase 0 is what makes every later phase a swap instead of an integration.
