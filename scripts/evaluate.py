"""Run a real course question set; save evidence, not invented correctness scores.

This script uses only the standard library and talks to the running application.
Input is UTF-8 JSONL with question, optional expected_document_ids and expected_terms.
Automatic scoring measures retrieval evidence; answer quality requires manual judgement.
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def request_json(base_url: str, path: str, payload: dict | None = None, timeout: float = 120) -> Any:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        base_url.rstrip("/") + path,
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def read_sse(lines: Iterator[bytes]) -> Iterator[tuple[str, Any]]:
    """Handle standard SSE framing, including multi-line data and final EOF."""
    event, data = "message", []
    for raw in lines:
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            if data:
                value = "\n".join(data)
                yield event, json.loads(value)
            event, data = "message", []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
    if data:
        yield event, json.loads("\n".join(data))


def source_list(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("retrieved_sources", "sources", "citations", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def stream_chat(base_url: str, session_id: str, question: str, timeout: float) -> dict:
    payload = json.dumps({"question": question}, ensure_ascii=False).encode("utf-8")
    request = Request(
        base_url.rstrip("/") + f"/sessions/{session_id}/chat",
        data=payload,
        headers={"Accept": "text/event-stream", "Content-Type": "application/json"},
        method="POST",
    )
    result: dict = {"answer": "", "retrieved_sources": [], "citations": [], "events": [], "error": None}
    try:
        with urlopen(request, timeout=timeout) as response:
            for event, data in read_sse(iter(response)):
                result["events"].append(event)
                if event == "sources":
                    result["retrieved_sources"] = source_list(data)
                elif event == "delta":
                    result["answer"] += data if isinstance(data, str) else str(data.get("text", data.get("delta", "")))
                elif event == "done":
                    if isinstance(data, dict):
                        result["answer"] = data.get("answer", data.get("content", result["answer"]))
                        result["citations"] = source_list(data)
                        result["message_id"] = data.get("message_id")
                        if data.get("retrieved_sources"):
                            result["retrieved_sources"] = source_list({"retrieved_sources": data["retrieved_sources"]})
                elif event == "error":
                    result["error"] = data
        if "done" not in result["events"] and result["error"] is None:
            result["error"] = "Stream ended without a done event"
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}") from exc
    except (HTTPException, URLError, TimeoutError, OSError) as exc:
        # Keep already received text/sources when a transport fails mid-answer.
        result["error"] = f"HTTP/SSE transport failed: {type(exc).__name__}: {exc}"
    return result


def evaluate_expectation(case: dict, sources: list[dict]) -> dict:
    """Report expected evidence hits; this deliberately does not grade answers."""
    top_five = sources[:5]
    expected_ids = [str(value) for value in case.get("expected_document_ids", [])]
    expected_terms = [str(value) for value in case.get("expected_terms", [])]
    actual_ids = {str(source.get("document_id", "")) for source in top_five}
    evidence_text = "\n".join(str(source.get("text", source.get("excerpt", ""))) for source in top_five).casefold()
    id_hit = bool(actual_ids.intersection(expected_ids)) if expected_ids else None
    term_hits = [term for term in expected_terms if term.casefold() in evidence_text]
    term_hit = bool(term_hits) if expected_terms else None
    applicable = [value for value in (id_hit, term_hit) if value is not None]
    return {
        "retrieval_hit": all(applicable) if applicable else None,
        "document_hit": id_hit,
        "term_hit": term_hit,
        "matched_terms": term_hits,
        "top_five_document_ids": sorted(actual_ids - {""}),
        "retrieval_scope": "top_five_search_api_results",
    }


def enrich_sources(base_url: str, sources: list[dict], timeout: float) -> list[dict]:
    enriched = []
    for source in sources:
        item = dict(source)
        chunk_id = item.get("chunk_id", item.get("source_id", item.get("id")))
        if isinstance(chunk_id, str) and len(chunk_id) == 33 and chunk_id.startswith("S"):
            chunk_id = chunk_id[1:]
        if chunk_id and not (item.get("text") or item.get("excerpt")):
            try:
                detail = request_json(base_url, f"/sources/{chunk_id}", timeout=timeout)
                if isinstance(detail, dict):
                    item.update(detail)
            except Exception as exc:
                item["source_fetch_error"] = str(exc)
        enriched.append(item)
    return enriched


def load_questions(path: Path) -> list[dict]:
    cases = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Question-set line {number}: {exc}") from exc
        if not isinstance(case, dict) or not isinstance(case.get("question"), str) or not case["question"].strip():
            raise ValueError(f"Question-set line {number} needs a non-empty question")
        for field in ("expected_document_ids", "expected_terms"):
            if field in case and not isinstance(case[field], list):
                raise ValueError(f"Question-set line {number}: {field} must be a list")
        case.setdefault("id", f"q{number:02}")
        case.setdefault("category", "knowledge")
        cases.append(case)
    if not cases:
        raise ValueError("Question set is empty")
    return cases


def write_summary(results: list[dict], path: Path, base_url: str, course_id: str) -> None:
    scored = [row for row in results if row.get("category") != "outside" and (row["case"].get("expected_document_ids") or row["case"].get("expected_terms"))]
    hits = sum(row.get("retrieval_hit") is True for row in scored)
    missing_retrievals = sum(row.get("retrieval_hit") is None for row in scored)
    failures = sum(bool(row.get("error")) for row in results)
    outside = sum(row.get("category") == "outside" for row in results)
    text = (
        "# 真实 API 验收记录\n\n"
        f"- API：`{base_url}`\n- 课程 ID：`{course_id}`\n"
        f"- 题目：{len(results)}；接口失败：{failures}；资料外问题：{outside}\n"
        f"- 有预期出处的证据命中：{hits}/{len(scored)}；未获取检索结果：{missing_retrievals}\n\n"
        "此比率检查 search 接口 Top-5 是否满足指定出处/关键词，"
        "不证明答案正确。聊天实际使用的检索证据另存在 retrieved_sources 字段。\n\n"
        "在 results.csv 中逐题填写人工判定：answer_correct、citation_support、"
        "outside_refusal_correct、notes。未填写项保持 pending。"
        "资料外问题需人工确认明确说明不足且没有编造依据。\n\n"
        "检索目标为 20 个明确出处的问题至少 16 个命中；另外检查 5 个资料外问题。"
        "测试双通过结果只能证明程序行为，不能代替此真实资料验收。\n"
    )
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, required=True, help="UTF-8 JSONL question set")
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=None, help="New output directory; existing results are not overwritten")
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    cases = load_questions(args.questions)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or Path("data/evaluations") / timestamp
    results_path = output / "results.jsonl"
    if results_path.exists():
        parser.error(f"{results_path} already exists; choose a new output directory")
    # Read-only preflight: do not produce a misleading evaluation for an absent course.
    courses = request_json(args.api_url, "/courses", timeout=args.timeout)
    if isinstance(courses, dict):
        courses = courses.get("courses", courses.get("items", []))
    if not any(str(course.get("id")) == args.course_id for course in courses):
        parser.error("Course does not exist at this API URL")
    output.mkdir(parents=True, exist_ok=True)
    results = []
    with results_path.open("w", encoding="utf-8") as file:
        for index, case in enumerate(cases, 1):
            started = time.perf_counter()
            row = {"case": case, "category": case["category"], "timestamp_utc": datetime.now(timezone.utc).isoformat()}
            try:
                session = request_json(args.api_url, f"/courses/{args.course_id}/sessions", {"title": f"验收 {case['id']}"}, args.timeout)
                row["session_id"] = session["id"]
                kind = case.get("retrieval_kind", "exam" if case["category"] in ("exam", "past_questions") else "material")
                search_query = urlencode({"query": case["question"], "kind": kind, "limit": 5})
                search = request_json(args.api_url, f"/courses/{args.course_id}/search?{search_query}", timeout=args.timeout)
                top_five = enrich_sources(args.api_url, source_list(search), args.timeout)
                row["search_results"] = top_five
                row.update(evaluate_expectation(case, top_five))
                result = stream_chat(args.api_url, str(session["id"]), case["question"], args.timeout)
                result["retrieved_sources"] = enrich_sources(args.api_url, result["retrieved_sources"], args.timeout)
                row.update(result)
            except Exception as exc:
                row.update({"error": str(exc), "answer": "", "retrieved_sources": [], "citations": []})
                row.setdefault("retrieval_hit", None)
            row["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            row["manual"] = {"answer_correct": "pending", "citation_support": "pending", "outside_refusal_correct": "pending", "notes": ""}
            results.append(row)
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
            file.flush()
            print(f"[{index}/{len(cases)}] {case['id']}: " + ("ERROR" if row.get("error") else f"evidence hit={row.get('retrieval_hit')}"))
    with (output / "results.csv").open("w", encoding="utf-8-sig", newline="") as file:
        fields = ["id", "category", "question", "retrieval_hit", "elapsed_seconds", "answer", "expected_answer", "citations", "error", "answer_correct", "citation_support", "outside_refusal_correct", "notes"]
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in results:
            writer.writerow({
                "id": row["case"]["id"], "category": row["category"], "question": row["case"]["question"],
                "retrieval_hit": row.get("retrieval_hit"), "elapsed_seconds": row["elapsed_seconds"],
                "answer": row["answer"], "expected_answer": row["case"].get("expected_answer", ""),
                "citations": json.dumps(row["citations"], ensure_ascii=False), "error": row.get("error", ""), **row["manual"],
            })
    write_summary(results, output / "summary.md", args.api_url, args.course_id)
    print(f"Results: {output.resolve()}")
    return 1 if any(row.get("error") for row in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
