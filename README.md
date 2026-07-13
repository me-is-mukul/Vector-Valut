# Vector Vault

A local-first desktop app that reads your documents, files them where they belong, and lets
you find anything by describing it — including photos.

**Nothing leaves your machine.** The language model and the embedding models all run
locally. The app makes no network calls except to download its own models, once.

---

## What it does

**Drop a folder into the chat.** It reads every file, builds a knowledge base from the
contents, and proposes where each one should go — with a reason for each. Nothing moves
until you say Apply, and everything can be undone.

**Ask it anything about your documents.** Answers come with the file and the exact page
cited. If nothing in your library is relevant, it says so instead of making something up.

**Find a photo by describing it.** Type *"a man lifting a baby"* and the photos appear. No
tagging, no filenames — it looks at the pictures.

**It keeps working when you close the window.** It lives in the system tray, watches your
Downloads folder, and files new documents as they arrive. Quit properly from the tray icon.

---

## The rules it will not break

**The model never touches your files.** When it decides how to organize a folder, it emits a
*plan* — data, not shell commands. It cannot invent an `rm`, cannot mangle a filename with a
quote in it, and cannot escape the library folder. You see the whole plan before a single
byte moves, and it goes through an engine that logs every move before making it.
([`test_a_hallucinated_path_cannot_escape_the_library`](tests/test_planner.py))

**The chatbot cannot answer from its own head.** If retrieval finds nothing relevant, the
model is *never even called*. Ask it who won the 1987 Formula One championship and it will
refuse, though it certainly knows. Your library holds medical records and bank statements;
an assistant that invents their contents is worse than none.
([`test_the_llm_is_never_called_when_nothing_is_relevant`](tests/test_rag.py))

**Nothing is overwritten, and everything is reversible.** Colliding names get a `(1)`
suffix. Undo of a copy refuses to run if the original has gone missing, because deleting the
library copy would then destroy the only copy.

---

## Install

**From the installer** (Windows):

```
dist/VectorVault-0.1.0-win64.msi
```

219 MB. It installs the app and starts it at login. On first run it detects your hardware,
recommends a language model that will actually fit in your GPU, installs Ollama, and
downloads the weights with a progress bar.

That recommendation matters more than it sounds: pick a model too big for the VRAM and
Ollama silently spills half of it onto the CPU, so the app "works" but every answer takes
forty seconds and you conclude the product is slow.

**From source:**

```bash
uv venv
uv pip install -e ".[docs,ml,rag,desktop]" --group dev
uv run osdc
```

**To rebuild the installer:**

```bash
uv pip install -e ".[build]"
uv run python build_msi.py bdist_msi
```

---

## Make it know *your* course

The academic classifier is only as good as its Subject Knowledge Base, and the one that
ships is a **generic sample B.Tech CSE syllabus that I made up**. Replace it with yours:

**[`src/osdc/data/curriculum.yaml`](src/osdc/data/curriculum.yaml)**

```yaml
current_semester: 5
semesters:
  - number: 5
    subjects:
      - name: Operating Systems
        code: CS301
        description: >
          Processes and threads, CPU scheduling, semaphores, deadlock, paging,
          page tables, TLB, virtual memory, page replacement, thrashing...
        topics: [paging, deadlock, semaphore, tlb, thrashing, page fault]
```

`description` and `topics` are what get embedded, so write them in the vocabulary a real
document about that subject would use. "Paging, segmentation, TLB, page faults" beats
"students will learn about memory management."

Edit the file and the knowledge base rebuilds itself on the next start.

---

## Thresholds were measured, not guessed

```bash
uv run python scripts/calibrate.py
```

Sentence embeddings have a high baseline — two *unrelated* texts still score ~0.4-0.55. My
first guess at the similarity floors (0.45 / 0.40) would have filed an article about **Arctic
terns under Data Structures** (0.549) and an **invoice under Programming Fundamentals**
(0.463). The measured floors are **0.62** and **0.53**, with clean separation.

Re-run the script whenever you change the curriculum.

---

## Architecture

```
ui/  api/     →  may import services/
services/     →  may import pipeline/, storage/
pipeline/     →  may import domain/
domain/       →  imports nothing of ours
```

Enforced in CI by [`.importlinter`](.importlinter), so it cannot rot.

Everything swappable is a `Protocol` in [`domain/ports.py`](src/osdc/domain/ports.py), and
every concrete choice is made in exactly one file,
[`container.py`](src/osdc/container.py). The app was built as a walking skeleton with fake
AI first; turning on the real models changed three lines there and nothing else.

- [docs/planning.md](docs/planning.md) — scope and decisions
- [docs/architecture.md](docs/architecture.md) — diagrams, pipelines, data model
- [docs/roadmap.md](docs/roadmap.md) — what's built, what's next

---

## Development

```bash
uv run pytest            # 114 tests
uv run ruff check .
uv run mypy
uv run lint-imports      # the layering contract
```
