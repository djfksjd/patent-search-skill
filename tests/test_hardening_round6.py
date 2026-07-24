# -*- coding: utf-8 -*-
"""라운드 6 하드닝 회귀 테스트 (patent-search) — Codex 교차검증(2026-07-24) 실결함 고정.

  #1  malformed 검색 item(applicationNumber 부재) → partial/error (0건 위장 금지)
  #2  fto_gate가 claims 레코드 자체(error/partial/청구항 부재)를 검증
  #5  claims 응답 출원번호 부재 → 귀속 거부(fail-closed)
  #8a 리다이렉트/외부 호스트 차단 (kipris_http.open_validated)
  #8b 키 마스킹이 소문자 percent 인코딩(%2b)도 포함
  #9  validate_expansion이 family source **값**까지 대조(위조 거부)
  #10 claim_chart: bool schema_version·문헌 불일치 citation·javascript URL·잘못된 날짜 거부
"""
import json
import os
import urllib.error

import pytest

import kipris_http

AN = "1020990000001"


def _run_claims(mod, monkeypatch, tmp_path, fetch):
    monkeypatch.setattr(mod, "bump_quota", lambda *_: 1)
    monkeypatch.setattr(mod, "fetch", fetch)
    out = str(tmp_path / "out")
    monkeypatch.setattr(mod.sys, "argv", ["kipris_claims.py", AN, "--out", out])
    try:
        mod.main()
    except SystemExit:
        pass
    return json.load(open(os.path.join(out, "claims.json"), encoding="utf-8"))


# ---- #8a 리다이렉트/호스트 차단 ----
def test_http_host_ok_boundary():
    hosts = ("plus.kipris.or.kr",)
    assert kipris_http.host_ok("https://plus.kipris.or.kr/x?k=1", hosts)
    assert not kipris_http.host_ok("http://plus.kipris.or.kr/x", hosts)  # https 강제
    assert not kipris_http.host_ok("https://evil.plus.kipris.or.kr.attacker.com/x", hosts)
    assert not kipris_http.host_ok("https://plus.kipris.or.kr.evil.com/x", hosts)
    # 정확 호스트만 — 하위 도메인 불허(키 유출 방지)
    assert not kipris_http.host_ok("https://attacker.plus.kipris.or.kr/x", hosts)


def test_open_validated_blocks_redirect_to_other_host(monkeypatch):
    def fake_open(req, timeout):
        raise urllib.error.HTTPError(
            req.full_url, 302, "redir",
            {"Location": "http://attacker.example/?accessKey=SECRET"}, None)

    monkeypatch.setattr(kipris_http._OPENER, "open", fake_open)
    with pytest.raises(kipris_http.RedirectBlocked):
        kipris_http.open_validated("https://plus.kipris.or.kr/api?ServiceKey=K",
                                   30, ("plus.kipris.or.kr",), 1 << 20)


def test_open_validated_rejects_bad_initial_host():
    with pytest.raises(kipris_http.RedirectBlocked):
        kipris_http.open_validated("https://evil.example/x", 30,
                                   ("plus.kipris.or.kr",))


# ---- #8b 키 마스킹 소문자 인코딩 ----
def test_redactor_masks_mixed_case_percent(search_mod):
    redact = search_mod.make_redactor("A+B/C==")
    # 서버가 대/소문자를 섞어 반사한 키(%2b 소·%2F 대·%3d 소·%3D 대)도 잡아야 한다
    assert "[REDACTED]" in redact("...ServiceKey=A%2bB%2FC%3d%3D...")
    assert "[REDACTED]" in redact("...ServiceKey=A%2BB%2fC%3D%3d...")
    # raw 키도
    assert "[REDACTED]" in redact("...=A+B/C==...")


def test_redact_body_masks_mixed_case(claims_mod):
    body = "x=A%2bB%2FC%3d%3D&y".encode()
    assert b"[REDACTED]" in claims_mod.redact_body(body, "A+B/C==")


# ---- #5 claims 응답 출원번호 부재 → 거부 ----
def test_claims_missing_response_appno_rejected(claims_mod, monkeypatch, tmp_path):
    xml = (b'<?xml version="1.0"?><response><header><resultCode>00</resultCode>'
           b'</header><body><item><biblioSummaryInfoArray><biblioSummaryInfo>'
           b'<inventionTitle>\xea\xb0\x80</inventionTitle></biblioSummaryInfo>'
           b'</biblioSummaryInfoArray><claimInfoArray><claimInfo>'
           b'<claim>c1</claim></claimInfo></claimInfoArray></item></body></response>')
    rec = _run_claims(claims_mod, monkeypatch, tmp_path, lambda url: xml)
    assert "error" in rec[AN]  # 귀속 거부 — 정상 청구항 레코드로 저장 안 됨
    assert "claims" not in rec[AN]


# ---- #1 malformed 검색 item → partial/error ----
def test_search_item_without_appno_is_partial(search_mod, monkeypatch, tmp_path):
    xml = (b'<?xml version="1.0"?><response><header><resultCode>00</resultCode>'
           b'</header><body><items><item><inventionTitle>t</inventionTitle></item>'
           b'</items><totalCount>1</totalCount></body></response>')
    monkeypatch.setattr(search_mod, "bump_quota", lambda *_: 1)
    monkeypatch.setattr(search_mod, "fetch", lambda url: xml)
    out = str(tmp_path / "out")
    monkeypatch.setattr(search_mod.sys, "argv",
                        ["kipris_search.py", "질의", "--out", out])
    with pytest.raises(SystemExit) as e:
        search_mod.main()
    assert e.value.code == 1  # 실패로 종료(0건 성공 위장 금지)
    man = json.load(open(os.path.join(out, "search_manifest.json"), encoding="utf-8"))
    q = man["queries"][0]
    assert q["partial"] is True and q["error"]
    assert man["unique_results"] == 0


# ---- #1 혼합 페이지(일부만 파싱) → partial ----
def test_search_mixed_page_partial(search_mod, monkeypatch, tmp_path):
    # totalCount=2, 1건 정상 + 1건 applicationNumber 부재 → collected=usable=1<2 → partial
    xml = (b'<?xml version="1.0"?><response><header><resultCode>00</resultCode>'
           b'</header><body><items>'
           b'<item><applicationNumber>1020990000001</applicationNumber>'
           b'<inventionTitle>t1</inventionTitle></item>'
           b'<item><inventionTitle>t2</inventionTitle></item>'
           b'</items><totalCount>2</totalCount></body></response>')
    monkeypatch.setattr(search_mod, "bump_quota", lambda *_: 1)
    monkeypatch.setattr(search_mod, "fetch", lambda url: xml)
    out = str(tmp_path / "out")
    monkeypatch.setattr(search_mod.sys, "argv",
                        ["kipris_search.py", "질의", "--max-pages", "1", "--out", out])
    # partial이지만 1건은 수집 성공 → 실패 아님(exit 0), 단 manifest partial=true
    search_mod.main()
    man = json.load(open(os.path.join(out, "search_manifest.json"), encoding="utf-8"))
    q = man["queries"][0]
    assert q["collected"] == 1 and q["total"] == 2 and q["partial"] is True


# ---- #2 fto_gate claims 레코드 검증 ----
def test_classify_claims_rejects_bad_records(fto_mod):
    src = "getBibliographyDetailInfoSearch(공보 서지)"
    good = {"schema_version": 2, "claims": ["c1"], "n_claims": 1,
            "current_enforceable_claims": "unknown", "claims_source": src}
    assert fto_mod.classify_claims(good)[0] is True
    assert fto_mod.classify_claims({"error": "network"})[0] is False
    assert fto_mod.classify_claims({**good, "last_refresh_error": "x"})[0] is False
    assert fto_mod.classify_claims({**good, "claims": []})[0] is False
    assert fto_mod.classify_claims({**good, "claims": [""], "n_claims": 1})[0] is False
    assert fto_mod.classify_claims({**good, "schema_version": True})[0] is False
    assert fto_mod.classify_claims({**good, "claims_source": "garbage"})[0] is False
    assert fto_mod.classify_claims({**good, "n_claims": 5})[0] is False


def test_classify_legal_rejects_garbage_event(fto_mod):
    base = {"schema_version": 1, "current_enforceable_claims": "unknown",
            "status_source": "legStatusST27InfoSearchService/BasicInfo"
                             "(법적 상태 이력, WIPO ST.27)",
            "retrieved_at": "2099-01-01T00:00:00+0900"}
    # garbage eventDate만 → 상태 신호 없음 → 거부
    assert fto_mod.classify(AN, {AN: {**base,
        "legal_events": [{"eventDate": "garbage"}], "n_events": 1}})[0] is False
    # 유효 코드지만 n_events 불일치 → 거부
    assert fto_mod.classify(AN, {AN: {**base,
        "legal_events": [{"keyEventCode": "A10"}], "n_events": 999}})[0] is False
    # 유효 eventDate + n_events 정합 → 통과
    assert fto_mod.classify(AN, {AN: {**base,
        "legal_events": [{"eventDate": "20990101"}], "n_events": 1}})[0] is True


def test_fto_gate_rejects_error_claims_record(fto_mod, monkeypatch, tmp_path):
    claims = {AN: {"error": "network timeout", "partial": True}}
    legal = {AN: {"schema_version": 1, "current_enforceable_claims": "unknown",
                  "status_source": "legStatusST27InfoSearchService/BasicInfo"
                                   "(법적 상태 이력, WIPO ST.27)",
                  "legal_events": [{"keyEventCode": "A10"}], "n_events": 1,
                  "retrieved_at": "2099-01-01T00:00:00+0900"}}
    cp = tmp_path / "claims.json"
    cp.write_text(json.dumps(claims, ensure_ascii=False))
    lp = tmp_path / "legal.json"
    lp.write_text(json.dumps(legal, ensure_ascii=False))
    monkeypatch.setattr(fto_mod.sys, "argv",
                        ["fto_gate.py", "--claims", str(cp), "--legal", str(lp)])
    with pytest.raises(SystemExit) as e:
        fto_mod.main()
    assert e.value.code == 2  # 청구항 미확보 → 판정 불가


# ---- #9 validate_expansion source 값 대조 ----
def test_validate_expansion_rejects_forged_source(fto_mod):
    data = {"tool": "kipris_expand", "schema_version": 1, "seeds": [AN],
            "axes": {"family": {"status": "complete", "source": "garbage",
                                "n_candidates": 3}}}
    ok, note = fto_mod.validate_expansion(data, [AN])
    assert ok is False and "source" in note


# ---- #10 claim_chart 엄격 검증 ----
def _chart(cells, schema=1, col_doc="KR-A"):
    return {"schema_version": schema, "chart_type": "patentability",
            "title": "t", "rows": [{"id": "R", "limitation": "L"}],
            "columns": [{"id": "C", "document_number": col_doc}], "cells": cells}


def _cell(**cit):
    base = {"document_number": "KR-A", "locator": "청구항 1", "quote": "q",
            "url": "https://patents.google.com/patent/KRA",
            "verified_at": "2026-07-24"}
    base.update(cit)
    return {"row": "R", "column": "C", "value": "E", "citation": base}


def test_chart_valid_passes(chart_mod):
    assert chart_mod.validate(_chart([_cell()])) == []


def test_chart_rejects_bool_schema_version(chart_mod):
    assert chart_mod.validate(_chart([_cell()], schema=True))


def test_chart_rejects_citation_doc_mismatch(chart_mod):
    # citation 문헌번호(KR-B)가 열 문헌(KR-A)과 불일치
    v = chart_mod.validate(_chart([_cell(document_number="KR-B")]))
    assert any("불일치" in m for m in v)


def test_chart_rejects_javascript_url(chart_mod):
    v = chart_mod.validate(_chart([_cell(url="javascript:alert(1)")]))
    assert any("http(s)" in m for m in v)


def test_chart_rejects_bad_verified_at(chart_mod):
    v = chart_mod.validate(_chart([_cell(verified_at="not-a-date")]))
    assert any("verified_at" in m for m in v)


def test_chart_rejects_trailing_garbage_date(chart_mod):
    v = chart_mod.validate(_chart([_cell(verified_at="2026-07-24TRAILING")]))
    assert any("verified_at" in m for m in v)


def test_chart_rejects_url_without_host(chart_mod):
    v = chart_mod.validate(_chart([_cell(url="https://")]))
    assert any("http(s)" in m for m in v)


def test_fto_chart_allows_citation_doc_differing_from_column(chart_mod):
    # FTO 차트: 열은 '내 실시 형태'(OUR-PRODUCT), citation 문헌은 상대 특허 —
    # 불일치가 정상이므로 문헌 대조를 적용하면 안 된다(내가 introduce했던 회귀).
    chart = {"schema_version": 1, "chart_type": "fto", "title": "t",
             "rows": [{"id": "R", "limitation": "상대 독립항 한정"}],
             "columns": [{"id": "C", "document_number": "OUR-PRODUCT-V1"}],
             "cells": [_cell(document_number="KR1020990000001")]}
    assert chart_mod.validate(chart) == []
