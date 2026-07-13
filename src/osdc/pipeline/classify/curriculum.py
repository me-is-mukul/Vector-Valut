"""Loads the Subject Knowledge Base from YAML.

Resolves the roadmap.md §7 blocker with option (1): a hand-authored curriculum file
shipped with the app. It is reliable, has zero magic, and gets the classifier working
today. The onboarding form (option 2) and syllabus-PDF import (option 3) both write into
this same shape, so neither is a rewrite when it arrives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CURRICULUM = Path(__file__).parent.parent.parent / "data" / "curriculum.yaml"


@dataclass(frozen=True)
class Subject:
    name: str
    semester: int
    code: str | None = None
    credits: int | None = None
    description: str = ""
    topics: list[str] = field(default_factory=list)

    @property
    def embedding_text(self) -> str:
        """What actually gets embedded into the `subject_kb` collection.

        Name plus description plus topics. The topics are repeated deliberately: they are
        the concrete vocabulary a real document about this subject will use, and they do
        most of the retrieval work.
        """
        parts = [self.name, self.description.strip(), ", ".join(self.topics)]
        return ". ".join(p for p in parts if p)


@dataclass(frozen=True)
class GeneralCategory:
    name: str
    description: str = ""
    topics: list[str] = field(default_factory=list)

    @property
    def embedding_text(self) -> str:
        parts = [self.name, self.description.strip(), ", ".join(self.topics)]
        return ". ".join(p for p in parts if p)


@dataclass(frozen=True)
class Curriculum:
    subjects: list[Subject]
    categories: list[GeneralCategory]
    current_semester: int | None = None

    def subjects_in(self, semester: int) -> list[Subject]:
        return [s for s in self.subjects if s.semester == semester]


def load_curriculum(path: Path | None = None) -> Curriculum:
    raw: dict[str, Any] = yaml.safe_load((path or DEFAULT_CURRICULUM).read_text(encoding="utf-8"))

    subjects: list[Subject] = []
    for semester in raw.get("semesters") or []:
        number = int(semester["number"])
        for entry in semester.get("subjects") or []:
            subjects.append(
                Subject(
                    name=str(entry["name"]),
                    semester=number,
                    code=entry.get("code"),
                    credits=entry.get("credits"),
                    description=str(entry.get("description") or "").strip(),
                    topics=[str(t) for t in (entry.get("topics") or [])],
                )
            )

    categories = [
        GeneralCategory(
            name=str(entry["name"]),
            description=str(entry.get("description") or "").strip(),
            topics=[str(t) for t in (entry.get("topics") or [])],
        )
        for entry in raw.get("general_categories") or []
    ]

    current = raw.get("current_semester")
    return Curriculum(
        subjects=subjects,
        categories=categories,
        current_semester=int(current) if current is not None else None,
    )
