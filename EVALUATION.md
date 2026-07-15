# Evaluation

## Method

Three layers, in increasing order of realism:

1. **132 automated tests** (`uv run pytest`), including end-to-end pipeline tests that drop
   real files through ingestion → extraction → embedding → classification → filing, and
   simulated-UI tests that drive the actual pages.
2. **Calibration by measurement** (`uv run python scripts/calibrate.py`): probe documents
   with known subjects are scored against the knowledge base, and the accept/reject floors
   are set from the observed separation — not guessed.
3. **The shipped installer is boot-tested in CI**: the release workflow runs the frozen exe
   against a throwaway data dir and requires a healthy response before a release can
   publish. An installer that cannot start cannot ship.

## Results

**Classification floors (baseline comparison).** The naive baseline — floors guessed at
0.45 / 0.40 — *looks* reasonable and fails in practice: unrelated sentence-embedding pairs
still score ~0.4–0.55, so the baseline filed an article about Arctic terns under Data
Structures (similarity 0.549) and an invoice under Programming Fundamentals (0.463).
Measured floors of **0.62 (academic)** and **0.53 (general)** separate every probe
cleanly: real coursework scores ≥ 0.694 against its own subject while non-academic
documents peak at 0.549 against *any* subject.

**Grounding.** Property, not benchmark: when retrieval returns nothing above the 0.55
relevance floor, the LLM is provably never invoked
([test_the_llm_is_never_called_when_nothing_is_relevant](tests/test_rag.py)). Hallucinated
answers about absent documents are structurally impossible, not just unlikely.

**Safety of file operations.** A plan containing a path outside the library root is
rejected before execution
([test_a_hallucinated_path_cannot_escape_the_library](tests/test_planner.py)); name
collisions get `(1)` suffixes; undo refuses to delete the last remaining copy.

**Image search sanity check** (measured on synthetic images): querying "a solid red square"
against a mixed folder ranks the red image first at score 0.289, with unrelated images at
0.23–0.25 — consistent with CLIP's compressed similarity band, which is why the image floor
(0.20) is far below the text floor.

## Known failure cases

- **Scanned/photographed PDFs**: no OCR engine yet. They are *detected* (text-per-page
  heuristic) and routed to the review queue rather than misfiled, but they cannot be read.
- **CLIP's blind spots**: counting ("three dogs"), text inside images (screenshots are
  findable as *screenshots*, not by their words), and fine-grained classes ("golden
  retriever" vs "labrador").
- **Curriculum quality bounds classification quality**: vague subject descriptions
  ("students will learn about memory") measurably lose to concrete vocabulary ("paging,
  TLB, page faults"). The shipped curriculum is a made-up sample.
- **The calibration set is small** — a handful of probe documents, not a labeled corpus.
  The floors held up in use, but a proper labeled evaluation is the roadmap's next
  measurement task.
- **Intent routing is keyword-based** by design (fast, predictable); unusual phrasings can
  route a filing request to Q&A. The failure is visible and recoverable, never destructive.
