from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "faq"


def normalize_text(value: str | None) -> str:
    return (value or "").replace("\r\n", "\n").strip()


def build_tags(intent: str, category: str, question: str, answer: str) -> list[str]:
    base = f"{intent} {category} {question} {answer}".lower()
    tokens = re.findall(r"[a-z0-9]+|[\u0E00-\u0E7F]+|[\u4e00-\u9fff]+", base)
    tags: list[str] = []
    for token in tokens:
        if len(token) < 3 and token.isascii():
            continue
        if token not in tags:
            tags.append(token)
        if len(tags) >= 12:
            break
    return tags


def make_entry(doc_id: str, language: str, question: str, answer: str, tags: list[str], category: str, intent: str) -> dict:
    return {
        "doc_id": doc_id,
        "language": language,
        "question": question,
        "answer": answer,
        "tags": tags,
        "category": category,
        "intent": intent,
    }


def iter_csv_rows(source: Path) -> list[dict[str, str]]:
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def iter_xlsx_rows(source: Path) -> list[dict[str, str]]:
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(source) as zf:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall("a:si", ns):
                texts = [t.text or "" for t in si.iterfind(".//a:t", ns)]
                shared_strings.append("".join(texts))

        sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
        rows: list[list[str]] = []
        for row in sheet.findall(".//a:row", ns):
            cells: dict[int, str] = {}
            for cell in row.findall("a:c", ns):
                ref = cell.attrib.get("r", "")
                col_letters = "".join(ch for ch in ref if ch.isalpha())
                col_idx = 0
                for ch in col_letters:
                    col_idx = col_idx * 26 + (ord(ch.upper()) - ord("A") + 1)
                col_idx -= 1

                cell_type = cell.attrib.get("t")
                value_node = cell.find("a:v", ns)
                if value_node is None:
                    value = ""
                elif cell_type == "s":
                    value = shared_strings[int(value_node.text)]
                else:
                    value = value_node.text or ""
                cells[col_idx] = value

            if not cells:
                continue
            width = max(cells) + 1
            rows.append([cells.get(i, "") for i in range(width)])

    if not rows:
        return []
    headers = rows[0]
    result: list[dict[str, str]] = []
    for row in rows[1:]:
        item = {headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))}
        result.append(item)
    return result


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: build_ev_faq_corpus.py <source_csv> <output_json>")
        return 2

    source = Path(sys.argv[1])
    output = Path(sys.argv[2])
    if not source.exists():
        print(f"source not found: {source}")
        return 1

    if source.suffix.lower() == ".csv":
        rows = iter_csv_rows(source)
    elif source.suffix.lower() == ".xlsx":
        rows = iter_xlsx_rows(source)
    else:
        print(f"unsupported source format: {source.suffix}")
        return 2

    entries: list[dict] = []
    for row in rows:
        intent = normalize_text(row.get("Intent"))
        faq_no = normalize_text(row.get("FAQ#"))
        category = normalize_text(row.get("Category"))
        question_en = normalize_text(row.get("Question (EN)"))
        question_zh = normalize_text(row.get("Question (繁中)"))
        question_th = normalize_text(row.get("Question (TH)"))
        answer_en = normalize_text(row.get("Answer (EN)"))
        answer_zh = normalize_text(row.get("Answer (繁中)"))
        answer_th = normalize_text(row.get("Answer (TH)"))
        status = normalize_text(row.get("Status"))

        if not intent or not faq_no or status.lower() == "deleted":
            continue

        base_slug = slugify(f"{faq_no}-{intent}")
        if question_en and answer_en:
            entries.append(
                make_entry(
                    doc_id=f"{base_slug}-en",
                    language="en-US",
                    question=question_en,
                    answer=answer_en,
                    tags=build_tags(intent, category, question_en, answer_en),
                    category=category,
                    intent=intent,
                )
            )

        if question_zh and answer_zh:
            entries.append(
                make_entry(
                    doc_id=f"{base_slug}-zh",
                    language="zh-HK",
                    question=question_zh,
                    answer=answer_zh,
                    tags=build_tags(intent, category, question_zh, answer_zh),
                    category=category,
                    intent=intent,
                )
            )

        if question_th and answer_th:
            entries.append(
                make_entry(
                    doc_id=f"{base_slug}-th",
                    language="th-TH",
                    question=question_th,
                    answer=answer_th,
                    tags=build_tags(intent, category, question_th, answer_th),
                    category=category,
                    intent=intent,
                )
            )

    payload = {"faqs": entries}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(entries)} faq entries to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
