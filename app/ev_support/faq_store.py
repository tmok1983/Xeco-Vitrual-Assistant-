from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


# Thai and CJK runs are expanded into overlapping n-grams so retrieval can
# match phrasing variants without over-weighting single-character overlap.
TOKEN_RE = re.compile(r"[a-z0-9]+|[\u0E00-\u0E7F]+|[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]+", re.IGNORECASE)


@dataclass(frozen=True)
class FAQEntry:
    doc_id: str
    language: str
    question: str
    answer: str
    tags: list[str]
    intent: str | None = None


@dataclass(frozen=True)
class FAQHit:
    entry: FAQEntry
    score: int


class LocalFAQRetriever:
    def __init__(self, faq_path: str | Path) -> None:
        self.faq_path = Path(faq_path)
        self.entries = self._load_entries()

    def _load_entries(self) -> list[FAQEntry]:
        if not self.faq_path.exists():
            return []

        payload = json.loads(self.faq_path.read_text(encoding="utf-8"))
        entries: list[FAQEntry] = []
        for item in payload.get("faqs", []):
            entries.append(
                FAQEntry(
                    doc_id=item["doc_id"],
                    language=item["language"],
                    question=item["question"],
                    answer=item["answer"],
                    tags=item.get("tags", []),
                    intent=item.get("intent"),
                )
            )
        return entries

    def _tokenize(self, text: str) -> set[str]:
        raw_tokens = TOKEN_RE.findall(text.lower())
        tokens: set[str] = set()
        for token in raw_tokens:
            if token.isascii() and token.isalpha() and len(token) > 3 and token.endswith("s"):
                tokens.add(token[:-1])
                tokens.add(token)
                continue

            if token.isascii():
                tokens.add(token)
                continue

            if len(token) == 1:
                tokens.add(token)
                continue

            tokens.add(token)
            max_n = min(4, len(token))
            for size in range(2, max_n + 1):
                for idx in range(0, len(token) - size + 1):
                    tokens.add(token[idx : idx + size])
        return tokens

    def _score_entry(self, query_lower: str, query_tokens: set[str], entry: FAQEntry) -> int:
        question_lower = entry.question.lower()
        answer_lower = entry.answer.lower()
        tags_lower = " ".join(entry.tags).lower()

        question_tokens = self._tokenize(question_lower)
        answer_tokens = self._tokenize(answer_lower)
        tag_tokens = self._tokenize(tags_lower)

        score = 0
        score += 3 * len(query_tokens & question_tokens)
        score += 1 * len(query_tokens & answer_tokens)
        score += 2 * len(query_tokens & tag_tokens)

        if query_lower and query_lower in question_lower:
            score += 8
        elif question_lower and question_lower in query_lower:
            score += 5
        return score

    def retrieve(self, query: str, language: str, top_k: int = 3) -> list[FAQHit]:
        query_lower = query.lower()
        query_tokens = self._tokenize(query_lower)
        hits: list[FAQHit] = []

        for entry in self.entries:
            if entry.language != language:
                continue
            score = self._score_entry(query_lower, query_tokens, entry)

            if score > 0:
                hits.append(FAQHit(entry=entry, score=score))

        hits.sort(key=lambda hit: (-hit.score, hit.entry.doc_id))
        return hits[:top_k]

    def retrieve_by_intents(self, query: str, language: str, intents: list[str], top_k: int = 3) -> list[FAQHit]:
        if not intents:
            return []
        intent_set = set(intents)
        query_lower = query.lower()
        query_tokens = self._tokenize(query_lower)
        hits: list[FAQHit] = []

        for entry in self.entries:
            if entry.language != language or entry.intent not in intent_set:
                continue
            score = self._score_entry(query_lower, query_tokens, entry)
            if score <= 0:
                score = 1
            hits.append(FAQHit(entry=entry, score=score))

        hits.sort(key=lambda hit: (-hit.score, hit.entry.doc_id))
        return hits[:top_k]
