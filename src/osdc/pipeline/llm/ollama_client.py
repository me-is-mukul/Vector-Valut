"""Ollama client — the local LLM runtime.

Deliberately thin. It knows how to talk to Ollama and nothing else; all prompt
construction and grounding policy lives in the RAG service, so swapping to llama.cpp or
vLLM means writing one class of this size and changing one line in ``container.py``.

Everything is local: Ollama listens on 127.0.0.1:11434 and no document text leaves the
machine. That is the hard privacy requirement in planning.md §3, and it is the whole
reason we are not calling a hosted API here.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen2.5:7b"
DEFAULT_HOST = "http://127.0.0.1:11434"


class OllamaClient:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        host: str = DEFAULT_HOST,
        temperature: float = 0.1,
        num_ctx: int = 8192,
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self._host = host
        # Low temperature on purpose: this job is "restate what the sources say", not
        # "be creative". Creativity here is a synonym for hallucination.
        self._temperature = temperature
        self._num_ctx = num_ctx
        self._timeout = timeout

    def _client(self):  # type: ignore[no-untyped-def]
        from ollama import Client

        return Client(host=self._host, timeout=self._timeout)

    def available(self) -> bool:
        """Is Ollama running, and is our model actually pulled?"""
        try:
            response = self._client().list()
        except Exception as exc:
            logger.warning("Ollama unreachable at %s: %s", self._host, exc)
            return False

        names = {
            getattr(m, "model", None) or (m.get("model") if isinstance(m, dict) else None)
            for m in getattr(response, "models", []) or []
        }
        if self.model not in names:
            logger.warning(
                "Ollama is running but %s is not pulled (have: %s). Run: ollama pull %s",
                self.model,
                ", ".join(sorted(n for n in names if n)) or "nothing",
                self.model,
            )
            return False
        return True

    def generate(self, prompt: str, system: str | None = None) -> str:
        response = self._client().chat(
            model=self.model,
            messages=self._messages(prompt, system),
            options={"temperature": self._temperature, "num_ctx": self._num_ctx},
        )
        content = response.message.content if hasattr(response, "message") else None
        return (content or "").strip()

    def generate_json(
        self, prompt: str, schema: dict[str, Any], system: str | None = None
    ) -> dict[str, Any]:
        """Constrained decoding: Ollama restricts the sampler to tokens the schema allows.

        Temperature is forced to 0 here. This output drives file moves, and there is no
        upside to creativity in a machine-readable plan.
        """
        response = self._client().chat(
            model=self.model,
            messages=self._messages(prompt, system),
            format=schema,
            options={"temperature": 0.0, "num_ctx": self._num_ctx},
        )
        content = response.message.content if hasattr(response, "message") else None
        if not content:
            return {}
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("Model returned unparseable JSON despite the schema constraint")
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _messages(prompt: str, system: str | None) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return messages
