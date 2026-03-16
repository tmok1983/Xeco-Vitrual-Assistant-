from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


# Chinese is tokenized at character level for better lexical matching across
# phrasing variants (for example, 點算 vs 點樣處理 still share many characters).
TOKEN_RE = re.compile(r"[a-z0-9]+|[\u0E00-\u0E7F]+|[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]", re.IGNORECASE)


@dataclass(frozen=True)
class FAQEntry:
    doc_id: str
    language: str
    question: str
    answer: str
    tags: list[str]


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
                )
            )
        return entries

    def retrieve(self, query: str, language: str, top_k: int = 3) -> list[FAQHit]:
        query_lower = query.lower()
        query_tokens = set(TOKEN_RE.findall(query_lower))
        hits: list[FAQHit] = []

        for entry in self.entries:
            if entry.language != language:
                continue
            question_lower = entry.question.lower()
            answer_lower = entry.answer.lower()
            tags_lower = " ".join(entry.tags).lower()

            question_tokens = set(TOKEN_RE.findall(question_lower))
            answer_tokens = set(TOKEN_RE.findall(answer_lower))
            tag_tokens = set(TOKEN_RE.findall(tags_lower))

            score = 0
            score += 3 * len(query_tokens & question_tokens)
            score += 1 * len(query_tokens & answer_tokens)
            score += 2 * len(query_tokens & tag_tokens)

            if query_lower and query_lower in question_lower:
                score += 8
            elif question_lower and question_lower in query_lower:
                score += 5

            if score > 0:
                hits.append(FAQHit(entry=entry, score=score))

        hits.sort(key=lambda hit: (-hit.score, hit.entry.doc_id))
        return hits[:top_k]
