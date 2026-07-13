# Architecture — AI-Powered Intelligent Document & Image Management System

> **Companion doc:** [planning.md](planning.md) (scope, decisions, roadmap)
> **Diagrams:** Mermaid (renders on GitHub, VS Code with a Mermaid extension, and most Markdown viewers).
> **Last updated:** 2026-07-10

---

## Table of Contents
1. [Guiding Principles](#1-guiding-principles)
2. [High-Level System Architecture](#2-high-level-system-architecture)
3. [Component Architecture](#3-component-architecture)
4. [Ingestion & File-Monitoring Flow](#4-ingestion--file-monitoring-flow)
5. [Document Processing Pipeline](#5-document-processing-pipeline)
6. [Classification Decision Flow (semester-aware + confidence)](#6-classification-decision-flow)
7. [Image Processing Pipeline](#7-image-processing-pipeline)
8. [RAG Chatbot Flow](#8-rag-chatbot-flow)
9. [Unified Search Flow](#9-unified-search-flow)
10. [Review & Learning Loop](#10-review--learning-loop)
11. [Data Model](#11-data-model)
12. [Vector Store Layout](#12-vector-store-layout)
13. [Deployment & Packaging](#13-deployment--packaging)
14. [Tech-to-Component Map](#14-tech-to-component-map)

---

## 1. Guiding Principles

- **Local-first & offline by default** — personal, identity, and medical documents never leave the machine.
- **Content over filenames** — every routing/search decision is driven by extracted text and visual embeddings.
- **One Python process** — NiceGUI (UI) and FastAPI (API + pipelines) share a single app; no JS toolchain, no separate frontend server.
- **UI-agnostic core** — pipelines, storage, and models know nothing about the UI, so the UI can be swapped (NiceGUI → Flet) without touching them.
- **Reversible & conservative** — prefer the Review Queue over a wrong auto-move; log originals for undo.
- **Same code, two delivery modes** — native desktop (PyInstaller) and hosted web (Docker).

---

## 2. High-Level System Architecture

```mermaid
flowchart TB
    subgraph User["User's Machine"]
        WF["Watched Folders<br/>Desktop · Downloads · Documents · External Drive"]
    end

    subgraph App["Single Python App (NiceGUI + FastAPI)"]
        direction TB
        UI["UI Layer — NiceGUI<br/>Onboarding · Review Queue · Chat · Search · Settings"]
        API["Service/API Layer — FastAPI<br/>orchestrates pipelines & queries"]

        subgraph Core["Core Engine"]
            direction TB
            WATCH["Watcher Service<br/>(Watchdog)"]
            QUEUE["Task Queue<br/>(asyncio / Celery-RQ upgrade)"]
            EXTRACT["Extraction + OCR Agent"]
            EMBED["Embedding Service<br/>(text + image)"]
            CLASSIFY["Classifier<br/>(semester-aware + general)"]
            FILEOP["File Organizer<br/>(move / copy / shortcut)"]
            RAG["RAG Engine<br/>(retrieve + LLM)"]
            SEARCH["Unified Search"]
            LEARN["Review & Learning"]
        end
    end

    subgraph Stores["Local Stores"]
        SQL[("SQLite<br/>metadata · jobs · corrections")]
        VEC[("Vector DB — ChromaDB<br/>doc · image · subject collections")]
        LIB["AI Library<br/>(organized folders on disk)"]
    end

    subgraph Models["Local AI Models"]
        OCRM["OCR<br/>PaddleOCR / Tesseract"]
        EMBM["Embeddings<br/>Sentence-Transformers · CLIP/SigLIP"]
        LLM["LLM<br/>Ollama (Qwen/Llama/Mistral/Gemma)"]
    end

    WF -->|"file events"| WATCH
    WATCH --> QUEUE --> EXTRACT --> EMBED --> CLASSIFY --> FILEOP --> LIB
    EXTRACT --- OCRM
    EMBED --- EMBM
    CLASSIFY --- VEC
    EMBED --> VEC
    FILEOP --> SQL

    UI <--> API
    API <--> Core
    RAG --- LLM
    RAG --- VEC
    SEARCH --- VEC
    LEARN --> SQL
    LEARN --> VEC
    API --> SQL
```

---

## 3. Component Architecture

```mermaid
flowchart LR
    subgraph Presentation
        ONB["Onboarding Wizard"]
        REVQ["Review Queue View"]
        CHAT["Chat View"]
        SRCH["Search View"]
        SET["Settings View"]
    end

    subgraph Services["Application Services (FastAPI)"]
        ING["Ingestion Service"]
        PROC["Processing Orchestrator"]
        CLS["Classification Service"]
        RAGS["RAG Service"]
        IMGS["Image Search Service"]
        USRCH["Unified Search Service"]
        FBK["Feedback/Learning Service"]
        CFG["Config Service"]
    end

    subgraph Domain["Domain / Pipelines"]
        TYPED["File-Type Detector"]
        TX["Text Extractors<br/>PyMuPDF·pdfplumber·docx·pptx"]
        OCRA["OCR Agent"]
        CLEAN["Text Cleaner/Chunker"]
        EMB["Embedders"]
        KB["Subject Knowledge Base"]
        ORG["Organizer + Undo Log"]
    end

    subgraph Infra["Infrastructure"]
        WD["Watchdog Adapter"]
        REPO["Repositories<br/>(SQLite)"]
        VDB["Vector Repos<br/>(ChromaDB)"]
        MDL["Model Runtime<br/>(Ollama / ST / CLIP)"]
    end

    ONB --> CFG
    SET --> CFG
    REVQ --> FBK
    CHAT --> RAGS
    SRCH --> USRCH
    USRCH --> RAGS & IMGS

    WD --> ING --> PROC
    PROC --> TYPED --> TX & OCRA
    OCRA --> CLEAN
    TX --> CLEAN --> EMB
    EMB --> CLS
    CLS --> KB
    CLS --> ORG
    CLS --> VDB

    RAGS --> VDB & MDL
    IMGS --> VDB & MDL
    FBK --> REPO & VDB
    PROC --> REPO
    ORG --> REPO
    EMB --> MDL
    OCRA --> MDL
    CFG --> REPO
```

---

## 4. Ingestion & File-Monitoring Flow

Handles bulk downloads, partial/temp files, and duplicates before anything hits the expensive pipeline.

```mermaid
flowchart TD
    A["Watchdog detects<br/>create / move / copy event"] --> B{"Temp or partial file?<br/>(.crdownload, .part, ~$…)"}
    B -- yes --> Z1["Ignore"]
    B -- no --> C["Debounce +<br/>stability delay<br/>(wait until size stable)"]
    C --> D["Compute content hash"]
    D --> E{"Hash already<br/>indexed?"}
    E -- yes --> Z2["Skip (duplicate)<br/>log reference"]
    E -- no --> F["Create job record<br/>(status = queued)"]
    F --> G["Enqueue for processing"]
    G --> H["Processing Pipeline<br/>(Section 5)"]
```

---

## 5. Document Processing Pipeline

The core Feature-2 pipeline, including the OCR-agent decision.

```mermaid
flowchart TD
    START["Job dequeued"] --> T["Detect file type"]
    T --> ISDOC{"Supported doc type?<br/>PDF·DOCX·PPTX·TXT·MD·image"}
    ISDOC -- no --> OTHERS["Route to 'Others'<br/>+ log unsupported"]
    ISDOC -- yes --> IMGQ{"Image-based?<br/>(scan / photo / no selectable text)"}

    IMGQ -- "selectable text present" --> EXT["Direct text extraction<br/>PyMuPDF / pdfplumber / docx / pptx"]
    IMGQ -- "image / no text" --> OCR["OCR Agent<br/>PaddleOCR → Tesseract fallback"]

    EXT --> QTXT{"Enough text<br/>extracted?"}
    OCR --> QTXT
    QTXT -- no --> LOWTXT["Flag low-text<br/>→ Review Queue"]
    QTXT -- yes --> CLEAN["Clean & normalize<br/>(dehyphenate, strip noise)"]

    CLEAN --> CHUNK["Chunk text<br/>(for RAG + citations)"]
    CHUNK --> EMB["Generate embeddings<br/>(Sentence-Transformers)"]
    EMB --> CLS["Classifier<br/>(Section 6)"]
    CLS --> DEC{"Decision"}
    DEC -- "auto (conf ≥ threshold)" --> ORG["Organizer: move / copy<br/>into AI Library folder"]
    DEC -- "review (conf < threshold)" --> RQ["Review Queue"]
    ORG --> STORE["Persist metadata (SQLite)<br/>+ vectors (ChromaDB)"]
    RQ --> STORE
    STORE --> DONE["Job complete"]
```

---

## 6. Classification Decision Flow

Semester-first academic matching, then general fallback, then the confidence gate.

```mermaid
flowchart TD
    IN["Document embedding"] --> SEM["Search CURRENT-SEMESTER<br/>subject embeddings first"]
    SEM --> S1{"Best academic score<br/>≥ academic threshold?"}
    S1 -- yes --> ACAD["Classify: Subject<br/>(e.g. Operating Systems → Paging)"]
    S1 -- no --> ALL["Broaden: search ALL semesters'<br/>subjects"]
    ALL --> S2{"Best score<br/>≥ academic threshold?"}
    S2 -- yes --> ACAD
    S2 -- no --> GEN["Match GENERAL categories<br/>(prototype embeddings + keyword rules)"]

    GEN --> S3{"Best general score<br/>≥ general threshold?"}
    S3 -- yes --> GCAT["Classify: Category<br/>(Finance/Identity/Medical/Legal/…)"]
    S3 -- no --> REVIEW["→ Review Queue<br/>(user assigns / creates category)"]

    ACAD --> GATE["Attach confidence score"]
    GCAT --> GATE
    GATE --> AUTO{"conf ≥ threshold<br/>AND auto-approve on?"}
    AUTO -- yes --> FILE["Auto-file"]
    AUTO -- no --> REVIEW
```

**Worked example (from spec):** `Operating Systems 0.94 · Computer Networks 0.71 · AI 0.39` with threshold `0.85` → auto-file to **Operating Systems**.

---

## 7. Image Processing Pipeline

Every image gets a **visual** embedding; text-heavy images *also* get an OCR-text embedding — enabling both "sunset on the beach" and "screenshot containing segmentation fault".

```mermaid
flowchart TD
    IMG["New image file"] --> HASH["Dedupe (content hash)"]
    HASH --> VENC["Vision Encoder<br/>CLIP / SigLIP"]
    VENC --> VEMB["Image embedding"]
    IMG --> TDET{"Text-heavy image?<br/>(text-density heuristic)"}
    TDET -- yes --> IOCR["OCR the image"]
    IOCR --> TEMB["Text embedding<br/>(of OCR'd text)"]
    TDET -- no --> SKIP["No OCR text"]

    VEMB --> STORE["Store in Vector DB<br/>(image collection)"]
    TEMB --> STORE
    SKIP --> STORE
    STORE --> META["Metadata: path, ocr_text,<br/>keywords, embeddings refs"]
    META --> READY["Searchable via visual + text query"]
```

---

## 8. RAG Chatbot Flow

Answers over the document collection, **with citations to file + page/section**.

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant UI as Chat View (NiceGUI)
    participant R as RAG Service
    participant E as Embedder
    participant V as Vector DB (Chroma)
    participant L as LLM (Ollama)

    U->>UI: "Which PDF contains Binary Tree LCA?"
    UI->>R: query
    R->>E: embed query
    E-->>R: query vector
    R->>V: similarity search (top-k chunks)
    V-->>R: chunks + metadata (file, page, score)
    R->>R: assemble context + citations
    R->>L: prompt (question + retrieved context)
    L-->>R: grounded answer
    R-->>UI: answer + source files (file · page/section)
    UI-->>U: rendered answer with clickable sources
```

---

## 9. Unified Search Flow

A single query fans out across document and image collections, then merges results.

```mermaid
flowchart TD
    Q["User query<br/>e.g. 'Laser tutorial' / 'Binary Tree'"] --> QE["Embed query<br/>(text encoder + CLIP text encoder)"]
    QE --> D["Search DOC vectors<br/>(PDF·PPT·DOCX·notes text)"]
    QE --> I["Search IMAGE vectors<br/>(visual)"]
    QE --> O["Search IMAGE-OCR text<br/>(screenshots/photos with text)"]
    D --> M["Merge + re-rank<br/>(normalize scores, dedupe by file)"]
    I --> M
    O --> M
    M --> R["Unified results:<br/>PDFs · slides · handwritten-note images · screenshots"]
    R --> ACT["Open · Reveal in folder · Ask in chat"]
```

---

## 10. Review & Learning Loop

Low-confidence items and user corrections feed back into future routing.

```mermaid
flowchart TD
    RQ["Review Queue item"] --> ACT{"User action"}
    ACT -- "Approve prediction" --> A1["Confirm folder"]
    ACT -- "Choose another folder" --> A2["Reassign"]
    ACT -- "Create new category" --> A3["Add custom category"]
    ACT -- "Mark Personal/General" --> A4["Set category"]
    ACT -- "Merge categories" --> A5["Merge + remap"]

    A1 --> LOG["Persist labeled example<br/>(SQLite: corrections)"]
    A2 --> LOG
    A3 --> LOG
    A4 --> LOG
    A5 --> LOG

    LOG --> UPD["Update category prototype<br/>embeddings + custom-category memory"]
    UPD --> ORG["Apply file move/copy"]
    ORG --> IMPROVE["Future classifications improved<br/>(and enables optional fine-tuning later)"]
```

---

## 11. Data Model

```mermaid
erDiagram
    FILE ||--o{ CHUNK : "split into"
    FILE ||--o{ EMBEDDING : "has"
    FILE }o--|| CATEGORY : "classified as"
    CATEGORY ||--o{ SUBJECT : "may contain"
    SUBJECT }o--|| SEMESTER : "belongs to"
    FILE ||--o{ CORRECTION : "may receive"
    FILE ||--o{ JOB : "processed by"

    FILE {
        string id PK
        string filename
        string original_path
        string organized_path
        string file_type
        int    size_bytes
        bool   is_image_based
        bool   ocr_used
        string ocr_engine
        text   extracted_text
        string subject
        string category
        string keywords
        float  confidence_score
        string decision
        bool   user_corrected
        datetime created_at
        datetime processed_at
    }
    CHUNK {
        string id PK
        string file_id FK
        int    page
        int    ordinal
        text   content
        string embedding_ref
    }
    EMBEDDING {
        string id PK
        string file_id FK
        string kind
        string collection
        string vector_ref
    }
    CATEGORY {
        string id PK
        string name
        bool   is_custom
        string prototype_ref
    }
    SUBJECT {
        string id PK
        string name
        string code
        text   description
        string topics
        string keywords
        int    credits
        string prototype_ref
    }
    SEMESTER {
        int    number PK
        bool   is_current
    }
    CORRECTION {
        string id PK
        string file_id FK
        string from_label
        string to_label
        datetime created_at
    }
    JOB {
        string id PK
        string file_id FK
        string status
        string stage
        text   error
        datetime queued_at
        datetime finished_at
    }
```

---

## 12. Vector Store Layout

ChromaDB collections (embedded, persisted locally):

```mermaid
flowchart LR
    subgraph Chroma["ChromaDB (local persistence)"]
        C1["collection: doc_chunks<br/>vec = text-embedding<br/>meta: file_id, page, subject/category"]
        C2["collection: image_visual<br/>vec = CLIP/SigLIP image embedding<br/>meta: file_id, keywords"]
        C3["collection: image_ocr_text<br/>vec = text-embedding of OCR text<br/>meta: file_id"]
        C4["collection: subject_kb<br/>vec = subject-description embedding<br/>meta: subject, code, semester"]
        C5["collection: category_prototypes<br/>vec = category prototype (updated by learning)<br/>meta: category, is_custom"]
    end

    RAGQ["RAG query"] --> C1
    IMGQ["Image search"] --> C2 & C3
    CLSQ["Classifier"] --> C4 & C5
```

> **Scale-up path:** the same collection contracts map onto **Qdrant** for the deployable/server mode; a **FAISS** flat/IVF index is an option if pure-vector speed at scale is needed.

---

## 13. Deployment & Packaging

One codebase, two targets.

```mermaid
flowchart TB
    SRC["Single Python codebase<br/>NiceGUI + FastAPI + pipelines"]

    subgraph Desktop["Delivery A — Native Desktop (primary)"]
        NAT["ui.run(native=True)<br/>pywebview window"]
        PI["PyInstaller"]
        INST["Signed installer<br/>Windows / macOS / Linux"]
    end

    subgraph Web["Delivery B — Self-hosted Web"]
        SRV["ui.run() as web server<br/>Uvicorn"]
        DK["Docker image"]
        HOST["VPS / LAN box<br/>browser UI"]
    end

    subgraph Bundled["Bundled/Local Runtime (both modes)"]
        OLL["Ollama (LLM)"]
        MDLS["Embedding + OCR models"]
        DBS["SQLite + ChromaDB<br/>(local files)"]
    end

    SRC --> NAT --> PI --> INST
    SRC --> SRV --> DK --> HOST
    INST --- Bundled
    HOST --- Bundled
```

**Notes**
- **Desktop mode** is fully offline; no ports exposed to the network.
- **Web mode** should sit behind auth/reverse-proxy; surface a clear "hosted" indicator since documents may be sensitive.
- Models are either bundled in the installer or fetched once during onboarding (see planning.md open questions).

---

## 14. Tech-to-Component Map

| Component | Technology |
|-----------|-----------|
| UI (desktop + web) | **NiceGUI** (`native=True` / web) |
| API & orchestration | **FastAPI** + **Uvicorn** |
| Background jobs | `asyncio` in-process → **Celery/RQ + Redis** (scale) |
| File monitoring | **Watchdog** |
| Type detection | `filetype` / `mimetypes` / magic bytes |
| Text extraction | **PyMuPDF**, **pdfplumber**, **python-docx**, **python-pptx**, **Unstructured** |
| OCR | **PaddleOCR** (primary), **Tesseract** (fallback), EasyOCR (optional) |
| Text embeddings | **Sentence-Transformers** (`bge-*` / MiniLM / e5) |
| Image embeddings | **CLIP / SigLIP** (`open_clip`) |
| LLM (RAG) | **Ollama** (Qwen / Llama / Mistral / Gemma) |
| RAG orchestration | **LlamaIndex** (or LangChain) |
| Vector DB | **ChromaDB** (default) · **Qdrant** / FAISS (scale) |
| Metadata DB | **SQLite** (default) · PostgreSQL (deploy option) |
| Config / schemas | **Pydantic** / Pydantic Settings |
| Packaging | **PyInstaller** (desktop) · **Docker** (web) |

---

### Cross-references
- Scope, feature table, roadmap, risks → [planning.md](planning.md)
- Classification strategy detail → [planning.md §8](planning.md#8-classification-strategy-finalized)
- Data fields → [planning.md §7](planning.md#7-data-model-finalized-fields)
