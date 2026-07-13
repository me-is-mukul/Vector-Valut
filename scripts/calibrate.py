"""Measure the similarity floors instead of guessing them.

roadmap.md §6.1 is blunt: you cannot tune a threshold by feel. The floors in
``settings.py`` decide whether a document is auto-filed or sent to Review, and picking
them from intuition is how you end up either filing everything into the wrong folder or
filing nothing at all.

    uv run python scripts/calibrate.py

The decisive number is NOT "does an OS document match Operating Systems" — that is easy.
It is **"does an invoice score below the academic floor against every subject?"** Because
if a bill scores 0.55 against Software Engineering and the academic floor sits at 0.45,
the bill gets filed as coursework and the user stops trusting the app.

So we measure two separations:

  * academic floor  — academic docs vs. their best SUBJECT score,
                      against non-academic docs' best SUBJECT score.
  * general floor   — general docs vs. their best CATEGORY score,
                      against noise's best CATEGORY score.

These probes are a smoke test, not a corpus. Replace them with your real labeled files
(roadmap.md §6.1) before trusting the numbers for anything that matters.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

if hasattr(sys.stdout, "reconfigure"):  # Windows consoles default to cp1252
    sys.stdout.reconfigure(encoding="utf-8")

from osdc.pipeline.classify.curriculum import load_curriculum
from osdc.pipeline.embed.sentence_embedder import SentenceTransformerEmbedder
from osdc.storage.vectors import cosine

ACADEMIC = "academic"
GENERAL = "general"
NOISE = "noise"

# (text, kind, expected label)
PROBES: list[tuple[str, str, str | None]] = [
    (
        "Lecture 7: Paging and virtual memory. A page fault occurs when the requested page "
        "is not resident. The TLB caches recent translations. Thrashing happens when the "
        "working set exceeds available frames.",
        ACADEMIC,
        "Operating Systems",
    ),
    (
        "The three-way handshake establishes a TCP connection: SYN, SYN-ACK, ACK. "
        "Congestion control uses slow start and additive increase multiplicative decrease.",
        ACADEMIC,
        "Computer Networks",
    ),
    (
        "An AVL tree is a self-balancing binary search tree. Rotations restore the balance "
        "factor after insertion. Lookup is O(log n).",
        ACADEMIC,
        "Data Structures",
    ),
    (
        "Normalization removes redundancy. A relation in BCNF has no partial dependencies. "
        "Two-phase locking guarantees serializability of transactions.",
        ACADEMIC,
        "Database Management Systems",
    ),
    (
        "Gradient descent minimises the loss function. Overfitting is reduced by "
        "regularization and cross validation. The confusion matrix gives precision and recall.",
        ACADEMIC,
        "Machine Learning",
    ),
    (
        "Shift-reduce parsing builds the parse tree bottom up. The LALR table is smaller "
        "than the canonical LR table. Three address code is the intermediate representation.",
        ACADEMIC,
        "Compiler Design",
    ),
    # --- non-academic. The critical property: these must score LOW against every SUBJECT.
    (
        "TAX INVOICE. Invoice number 4471. Amount due: Rs 12,400. GST @ 18% included. "
        "Payment due within 30 days. Bank account and IFSC code below.",
        GENERAL,
        "Finance",
    ),
    (
        "Prescription. Patient: R. Kumar. Diagnosis: acute bronchitis. Medication: "
        "Amoxicillin 500mg, dosage three times daily after meals for seven days.",
        GENERAL,
        "Medical",
    ),
    (
        "Government of India. Permanent Account Number card. Name, father's name, date of "
        "birth. This card is issued by the Income Tax Department.",
        GENERAL,
        "Identity",
    ),
    (
        "This Non-Disclosure Agreement is entered into by the parties hereto. Whereas the "
        "receiving party shall indemnify the disclosing party. Jurisdiction: Bengaluru.",
        GENERAL,
        "Legal",
    ),
    (
        "Semester 5 grade card. SGPA 8.4, CGPA 8.1. Fee receipt for tuition, paid in full. "
        "Hall ticket for the end semester examination.",
        GENERAL,
        "Academic Admin",
    ),
    # --- genuine noise: belongs to no subject AND no category.
    (
        "The 1987 Formula One season was contested over sixteen Grands Prix. Nelson Piquet "
        "won the drivers' championship driving for Williams-Honda.",
        NOISE,
        None,
    ),
    (
        "Sourdough starter needs equal parts flour and water, fed daily. Bulk ferment for "
        "four hours, then shape and cold proof overnight in the fridge.",
        NOISE,
        None,
    ),
    (
        "The migratory route of the Arctic tern spans pole to pole, the longest of any bird. "
        "They breed in the summer and follow the sun southward.",
        NOISE,
        None,
    ),
]


def main() -> None:
    print("Loading bge-small...\n")
    embedder = SentenceTransformerEmbedder()
    curriculum = load_curriculum()

    subject_names = [s.name for s in curriculum.subjects]
    subject_vecs = embedder.embed([s.embedding_text for s in curriculum.subjects])
    category_names = [c.name for c in curriculum.categories]
    category_vecs = embedder.embed([c.embedding_text for c in curriculum.categories])

    rows: list[tuple[str, str, str, float, str, float, str | None]] = []
    for text, kind, expected in PROBES:
        (vector,) = embedder.embed([text])
        s_label, s_score = _best(vector, subject_names, subject_vecs)
        c_label, c_score = _best(vector, category_names, category_vecs)
        rows.append((text, kind, s_label, s_score, c_label, c_score, expected))

    header = (
        f"{'probe':<26} {'kind':<9} {'best SUBJECT':<26} {'cos':>6} "
        f"{'best CATEGORY':<16} {'cos':>6}"
    )
    print(header)
    print("-" * len(header))
    for text, kind, s_label, s_score, c_label, c_score, expected in rows:
        probe = text[:24].replace("\n", " ")
        flag = ""
        if expected is not None:
            hit = s_label if kind == ACADEMIC else c_label
            flag = "" if hit == expected else f"  <-- WRONG, wanted {expected}"
        print(
            f"{probe:<26} {kind:<9} {s_label:<26} {s_score:>6.3f} "
            f"{c_label:<16} {c_score:>6.3f}{flag}"
        )

    print()
    _separation(
        "ACADEMIC floor (min_academic_similarity)",
        "academic docs, best SUBJECT score",
        [r[3] for r in rows if r[1] == ACADEMIC],
        "non-academic docs, best SUBJECT score",
        [r[3] for r in rows if r[1] != ACADEMIC],
    )
    _separation(
        "GENERAL floor (min_general_similarity)",
        "general docs, best CATEGORY score",
        [r[5] for r in rows if r[1] == GENERAL],
        "noise, best CATEGORY score",
        [r[5] for r in rows if r[1] == NOISE],
    )


def _best(vector: list[float], names: list[str], vecs: list[list[float]]) -> tuple[str, float]:
    scored = sorted(
        ((n, cosine(vector, v)) for n, v in zip(names, vecs, strict=True)),
        key=lambda pair: pair[1],
        reverse=True,
    )
    return scored[0]


def _separation(
    title: str, pos_label: str, positives: list[float], neg_label: str, negatives: list[float]
) -> None:
    print(f"== {title} ==")
    lowest_true = min(positives)
    highest_false = max(negatives)
    print(f"  lowest  {pos_label}: {lowest_true:.3f}")
    print(f"  highest {neg_label}: {highest_false:.3f}")

    if lowest_true > highest_false:
        floor = (lowest_true + highest_false) / 2
        margin = lowest_true - highest_false
        print(f"  CLEAN SEPARATION (margin {margin:.3f}) -> set the floor to {floor:.2f}\n")
    else:
        print(
            "  NO SEPARATION: something that should not match scores higher than something "
            "that should.\n  No single floor works. Make the descriptions in "
            "curriculum.yaml more specific.\n"
        )


if __name__ == "__main__":
    main()
