# -*- coding: utf-8 -*-
"""Claim chart 검증기 — references/claim_chart_schema.json v1 규칙 (stdlib 전용, 네트워크 없음).

사용:
  python3 claim_chart_validate.py <chart.json>

검사 규칙(스키마 v1과 동일 — 스키마 파일이 원본 계약, 이 스크립트는 실행 가능한 사본):
- 최상위 필수: schema_version==1, chart_type in {patentability, fto}, rows/columns/cells 비어있지 않음
- rows: id·limitation 비어있지 않음, id 중복 금지
- columns: id·document_number 비어있지 않음, id 중복 금지
- cells: row/column이 실제 rows/columns id를 참조, value ∈ {E,I,P,N,?}
- E/I/P: citation 필수 — document_number/locator/quote/url/verified_at 전부 비어있지 않아야 함
- I: inherency_rationale 추가 필수(비어있지 않음)

출력: 위반 목록 전체(하나만 보고하고 멈추지 않는다). 종료 코드: 0=통과, 1=위반/입력 불능.
"""
import json
import sys

VALUES = {"E", "I", "P", "N", "?"}
CITATION_FIELDS = ("document_number", "locator", "quote", "url", "verified_at")


def nonempty_str(v):
    return isinstance(v, str) and v.strip() != ""


def validate(chart):
    """위반 문자열 목록을 반환한다(빈 목록=통과)."""
    v = []
    if not isinstance(chart, dict):
        return ["최상위가 JSON 객체가 아님"]
    if chart.get("schema_version") != 1:
        v.append(f"schema_version은 1이어야 함 (현재: {chart.get('schema_version')!r})")
    if chart.get("chart_type") not in ("patentability", "fto"):
        v.append(f"chart_type은 patentability|fto 중 하나 (현재: {chart.get('chart_type')!r})")

    row_ids, col_ids = set(), set()
    rows = chart.get("rows")
    if not isinstance(rows, list) or not rows:
        v.append("rows가 비어있음/누락")
    else:
        for i, r in enumerate(rows):
            if not isinstance(r, dict):
                v.append(f"rows[{i}]가 객체가 아님")
                continue
            if not nonempty_str(r.get("id")):
                v.append(f"rows[{i}].id 누락/빈 값")
            elif r["id"] in row_ids:
                v.append(f"rows[{i}].id 중복: {r['id']}")
            else:
                row_ids.add(r["id"])
            if not nonempty_str(r.get("limitation")):
                v.append(f"rows[{i}].limitation 누락/빈 값")

    cols = chart.get("columns")
    if not isinstance(cols, list) or not cols:
        v.append("columns가 비어있음/누락")
    else:
        for i, c in enumerate(cols):
            if not isinstance(c, dict):
                v.append(f"columns[{i}]가 객체가 아님")
                continue
            if not nonempty_str(c.get("id")):
                v.append(f"columns[{i}].id 누락/빈 값")
            elif c["id"] in col_ids:
                v.append(f"columns[{i}].id 중복: {c['id']}")
            else:
                col_ids.add(c["id"])
            if not nonempty_str(c.get("document_number")):
                v.append(f"columns[{i}].document_number 누락/빈 값")

    cells = chart.get("cells")
    if not isinstance(cells, list) or not cells:
        v.append("cells가 비어있음/누락")
        return v
    for i, cell in enumerate(cells):
        if not isinstance(cell, dict):
            v.append(f"cells[{i}]가 객체가 아님")
            continue
        where = f"cells[{i}](row={cell.get('row')!r}, column={cell.get('column')!r})"
        if row_ids and cell.get("row") not in row_ids:
            v.append(f"{where}: row가 rows에 없음")
        if col_ids and cell.get("column") not in col_ids:
            v.append(f"{where}: column이 columns에 없음")
        val = cell.get("value")
        if val not in VALUES:
            v.append(f"{where}: value {val!r}는 E|I|P|N|? 중 하나여야 함")
            continue
        if val in ("E", "I", "P"):
            cit = cell.get("citation")
            if not isinstance(cit, dict):
                v.append(f"{where}: value={val} — citation 필수(문헌번호+문단/청구항 번호"
                         "+직접 인용+URL+확인일)")
            else:
                for f in CITATION_FIELDS:
                    if not nonempty_str(cit.get(f)):
                        v.append(f"{where}: citation.{f} 누락/빈 값")
        if val == "I" and not nonempty_str(cell.get("inherency_rationale")):
            v.append(f"{where}: value=I — inherency_rationale 필수(내재성 논거)")
    return v


def main():
    if len(sys.argv) != 2:
        print("사용: python3 claim_chart_validate.py <chart.json>", file=sys.stderr)
        sys.exit(1)
    try:
        with open(sys.argv[1], encoding="utf-8") as f:
            chart = json.load(f)
    except Exception as e:
        print(f"입력 불능(fail-closed): {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
    violations = validate(chart)
    if violations:
        print(f"claim chart 스키마 위반 {len(violations)}건:")
        for msg in violations:
            print(f"  - {msg}")
        sys.exit(1)
    print("claim chart 검증 통과 (스키마 v1)")


if __name__ == "__main__":
    main()
