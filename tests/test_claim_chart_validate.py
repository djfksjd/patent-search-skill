# -*- coding: utf-8 -*-
"""claim_chart_validate.py 스키마 v1 규칙 테스트 — 네트워크 없음."""
import json

import pytest

CIT = {"document_number": "1020990000001", "locator": "[0042]",
       "quote": "가공 인용문", "url": "https://example.invalid/doc",
       "verified_at": "2099-01-02"}


def base_chart():
    return {
        "schema_version": 1,
        "chart_type": "patentability",
        "rows": [{"id": "r1", "limitation": "A가 B의 출력에 따라 C를 제어한다"}],
        "columns": [{"id": "d1", "document_number": "1020990000001"}],
        "cells": [{"row": "r1", "column": "d1", "value": "E", "citation": dict(CIT)}],
    }


def run_cli(mod, monkeypatch, tmp_path, chart):
    p = tmp_path / "chart.json"
    p.write_text(json.dumps(chart, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod.sys, "argv", ["claim_chart_validate.py", str(p)])
    mod.main()


def test_valid_chart_passes(chart_mod, monkeypatch, tmp_path, capsys):
    run_cli(chart_mod, monkeypatch, tmp_path, base_chart())
    assert "검증 통과" in capsys.readouterr().out


def test_valid_inherency_cell_passes(chart_mod):
    chart = base_chart()
    chart["cells"][0].update(value="I", inherency_rationale="가공 논거 — 필연적 포함")
    assert chart_mod.validate(chart) == []


def test_n_and_question_need_no_citation(chart_mod):
    chart = base_chart()
    chart["cells"] = [{"row": "r1", "column": "d1", "value": "N"},
                      {"row": "r1", "column": "d1", "value": "?"}]
    assert chart_mod.validate(chart) == []


@pytest.mark.parametrize("value", ["E", "I", "P"])
def test_eip_without_citation_fails(chart_mod, value):
    chart = base_chart()
    cell = {"row": "r1", "column": "d1", "value": value}
    if value == "I":
        cell["inherency_rationale"] = "가공 논거"
    chart["cells"] = [cell]
    violations = chart_mod.validate(chart)
    assert any("citation 필수" in v for v in violations)


def test_inherency_without_rationale_fails(chart_mod):
    chart = base_chart()
    chart["cells"][0]["value"] = "I"  # citation은 있음, rationale 없음
    violations = chart_mod.validate(chart)
    assert any("inherency_rationale 필수" in v for v in violations)


@pytest.mark.parametrize("field", ["document_number", "locator", "quote", "url",
                                   "verified_at"])
def test_citation_missing_field_fails(chart_mod, field):
    chart = base_chart()
    del chart["cells"][0]["citation"][field]
    violations = chart_mod.validate(chart)
    assert any(f"citation.{field}" in v for v in violations)


def test_blank_citation_field_fails(chart_mod):
    chart = base_chart()
    chart["cells"][0]["citation"]["quote"] = "   "  # 공백만 = 근거 아님
    assert any("citation.quote" in v for v in chart_mod.validate(chart))


def test_bad_value_and_dangling_refs_fail(chart_mod):
    chart = base_chart()
    chart["cells"] = [{"row": "no_such_row", "column": "no_such_col", "value": "X"}]
    violations = chart_mod.validate(chart)
    assert any("row가 rows에 없음" in v for v in violations)
    assert any("column이 columns에 없음" in v for v in violations)
    assert any("E|I|P|N|?" in v for v in violations)


def test_structural_violations_all_reported(chart_mod):
    chart = {"schema_version": 2, "chart_type": "both", "rows": [], "columns": [],
             "cells": []}
    violations = chart_mod.validate(chart)
    for kw in ["schema_version", "chart_type", "rows", "columns", "cells"]:
        assert any(kw in v for v in violations)


def test_duplicate_ids_fail(chart_mod):
    chart = base_chart()
    chart["rows"].append({"id": "r1", "limitation": "중복 id 행"})
    assert any("중복" in v for v in chart_mod.validate(chart))


def test_cli_exit_1_on_violation(chart_mod, monkeypatch, tmp_path, capsys):
    chart = base_chart()
    del chart["cells"][0]["citation"]
    with pytest.raises(SystemExit) as e:
        run_cli(chart_mod, monkeypatch, tmp_path, chart)
    assert e.value.code == 1
    assert "스키마 위반" in capsys.readouterr().out


def test_schema_file_and_validator_agree_on_version(chart_mod):
    """references/claim_chart_schema.json이 v1 계약의 원본 — 상수 동기화 확인."""
    import pathlib
    schema_path = (pathlib.Path(chart_mod.__file__).resolve().parent.parent
                   / "references" / "claim_chart_schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["properties"]["schema_version"]["const"] == 1
    assert set(schema["properties"]["cells"]["items"]["properties"]["value"]["enum"]) \
        == chart_mod.VALUES
    assert tuple(schema["properties"]["cells"]["items"]["properties"]["citation"]
                 ["required"]) == chart_mod.CITATION_FIELDS
