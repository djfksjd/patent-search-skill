# -*- coding: utf-8 -*-
"""kipris_expand.py 계약 테스트 — fixture XML 주입, 네트워크 없음.

Codex 머지 게이트 계약을 fixture 기반으로 검증한다:
  - priorArt(후방 인용) 채워짐 추출 + examinerQuotationFlag
  - 빈 familyInfo = unknown (패밀리 없음으로 단정 금지)
  - 파싱 실패 seed = failed(빈 성공으로 병합 금지)
  - 후보 상한 도달 = partial(limit_reached), not saturated
  - cited_by = unsupported 고정
  - 월 원장 hard stop (reserve_one 단위)
"""
import json
import os

import pytest

from conftest import FAKE_KEY, fixture_bytes


def run_main(mod, monkeypatch, argv_tail, fetch):
    monkeypatch.setattr(mod, "fetch", fetch)
    monkeypatch.setattr(mod.sys, "argv", ["kipris_expand.py"] + argv_tail)
    mod.main()


def read_expansion(out_dir):
    with open(os.path.join(out_dir, "expansion.json"), encoding="utf-8") as f:
        return json.load(f)


# ---- 후방 인용(priorArt) 추출 + 빈 패밀리 unknown ----

def test_prior_art_extracted_with_examiner_flag(expand_mod, monkeypatch, tmp_path):
    out = str(tmp_path / "out")
    run_main(expand_mod, monkeypatch, ["10-2099-0000001", "--out", out],
             lambda url, reserve: fixture_bytes("bib_expand_priorart.xml"))
    exp = read_expansion(out)
    pa = exp["axes"]["prior_art_backward"]
    assert pa["status"] == "complete"
    cands = pa["candidates"]
    assert len(cands) == 3
    by_num = {c["documentsNumber"]: c for c in cands}
    kr = by_num["KR1020210147858 A"]
    assert kr["examiner_cited"] is True
    assert kr["examinerQuotationFlag"] == "Y"
    assert kr["normalized_appno"] == "1020210147858"  # KR 13자리 정규화
    # 빈 examinerQuotationFlag → examiner_cited False
    assert by_num["KR1020200011122 A"]["examiner_cited"] is False
    # 해외 문헌은 국내 재조회 대상 아님(normalized_appno None)
    assert by_num["US2019123456 A1"]["normalized_appno"] is None
    assert by_num["US2019123456 A1"]["country"] == "US"


def test_empty_family_is_unknown_not_absent(expand_mod, monkeypatch, tmp_path):
    """<familyInfo/> 빈 값을 '패밀리 없음'으로 단정하지 않는다(계약 2)."""
    out = str(tmp_path / "out")
    run_main(expand_mod, monkeypatch, ["1020990000001", "--out", out],
             lambda url, reserve: fixture_bytes("bib_expand_priorart.xml"))
    fam = read_expansion(out)["axes"]["family"]
    assert fam["status"] == "unknown"
    assert fam["n_candidates"] == 0
    assert "단정" in fam["reason"] or "unknown" in fam["reason"]


def test_populated_family_becomes_candidates(expand_mod, monkeypatch, tmp_path):
    out = str(tmp_path / "out")
    run_main(expand_mod, monkeypatch, ["1020990000002", "--out", out],
             lambda url, reserve: fixture_bytes("bib_expand_family.xml"))
    fam = read_expansion(out)["axes"]["family"]
    # 모든 seed가 비어있지 않은 패밀리를 반환 → complete
    assert fam["status"] == "complete"
    assert fam["n_candidates"] == 2
    kr = [c for c in fam["candidates"] if c["country"] == "KR"]
    assert kr and kr[0]["normalized_appno"] == "1020880000009"


# ---- cited_by unsupported 고정 ----

def test_cited_by_always_unsupported(expand_mod, monkeypatch, tmp_path):
    out = str(tmp_path / "out")
    run_main(expand_mod, monkeypatch, ["1020990000001", "--out", out],
             lambda url, reserve: fixture_bytes("bib_expand_priorart.xml"))
    cb = read_expansion(out)["axes"]["cited_by"]
    assert cb["status"] == "unsupported"
    assert cb["candidates"] == []
    assert cb["source"] is None


# ---- 파싱 실패 seed = failed(빈 성공으로 병합 금지) ----

def test_parse_failure_seed_is_recorded_error(expand_mod, monkeypatch, tmp_path):
    out = str(tmp_path / "out")
    with pytest.raises(SystemExit) as e:
        run_main(expand_mod, monkeypatch, ["1020990000001", "--out", out],
                 lambda url, reserve: fixture_bytes("bib_expand_no_resultcode.xml"))
    assert e.value.code == 1  # 하나 이상 seed 실패
    exp = read_expansion(out)
    assert exp["seed_errors"] and "schema_error" in exp["seed_errors"][0]["error"]
    # 전 seed 실패 → 축 status failed(빈 성공 아님)
    assert exp["axes"]["family"]["status"] == "failed"
    assert exp["axes"]["prior_art_backward"]["status"] == "failed"


def test_appno_mismatch_is_seed_failure(expand_mod, monkeypatch, tmp_path):
    xml = fixture_bytes("bib_expand_priorart.xml").replace(
        b"1020990000001", b"1020990009999")
    out = str(tmp_path / "out")
    with pytest.raises(SystemExit) as e:
        run_main(expand_mod, monkeypatch, ["1020990000001", "--out", out],
                 lambda url, reserve: xml)
    assert e.value.code == 1
    assert "불일치" in read_expansion(out)["seed_errors"][0]["error"]


# ---- 후보 상한 도달 = partial(limit_reached) ----

def _many_priorart_xml(an, n):
    rows = "".join(
        f"<priorArtDocumentsInfo><documentsNumber>KR10202100{i:05d} A</documentsNumber>"
        f"<examinerQuotationFlag>Y</examinerQuotationFlag></priorArtDocumentsInfo>"
        for i in range(n))
    return (
        '<?xml version="1.0" encoding="UTF-8"?><response><header><resultCode>00'
        '</resultCode></header><body><item><biblioSummaryInfoArray><biblioSummaryInfo>'
        f"<applicationNumber>{an}</applicationNumber><inventionTitle>T</inventionTitle>"
        "</biblioSummaryInfo></biblioSummaryInfoArray><priorArtDocumentsInfoArray>"
        f"{rows}</priorArtDocumentsInfoArray></item></body></response>").encode()


def test_citation_limit_reached_is_partial(expand_mod, monkeypatch, tmp_path):
    out = str(tmp_path / "out")
    # 25건 priorArt > 상한 20 → partial(limit_reached)
    run_main(expand_mod, monkeypatch,
             ["1020990000001", "--out", out, "--max-citation-candidates", "20"],
             lambda url, reserve: _many_priorart_xml("1020990000001", 25))
    exp = read_expansion(out)
    pa = exp["axes"]["prior_art_backward"]
    # 상한 도달 → saturated가 아니라 partial(limit_reached) (계약 3)
    assert pa["status"] == "partial"
    assert pa["n_candidates"] == 20  # 상한에서 절단
    assert pa["applied_limit"] == 20


def _family_xml(an):
    return (
        '<?xml version="1.0" encoding="UTF-8"?><response><header><resultCode>00'
        '</resultCode></header><body><item><biblioSummaryInfoArray><biblioSummaryInfo>'
        f"<applicationNumber>{an}</applicationNumber><inventionTitle>T</inventionTitle>"
        "</biblioSummaryInfo></biblioSummaryInfoArray><familyInfoArray><familyInfo>"
        f"<applicationNumber>US20{an[-6:]} A1</applicationNumber><countryCode>US"
        "</countryCode></familyInfo></familyInfoArray></item></body></response>").encode()


def test_dropped_seeds_over_max_marks_partial(expand_mod, monkeypatch, tmp_path):
    out = str(tmp_path / "out")
    seeds = [f"10209900000{i:02d}" for i in range(1, 8)]  # 7 seeds > max 5

    def fetch(url, reserve):
        reserve()
        an = url.split("applicationNumber=")[1].split("&")[0]
        return _family_xml(an)

    run_main(expand_mod, monkeypatch, seeds + ["--out", out, "--max-seeds", "5"], fetch)
    exp = read_expansion(out)
    assert exp["dropped_seeds"] == 2
    assert len(exp["seeds"]) == 5
    # seed 상한 초과분 존재 → 완결로 보지 않는다
    assert exp["axes"]["family"]["status"] == "partial"


# ---- 두 seed 연속 신규 0건 종료 ----

def test_two_consecutive_zero_new_terminates(expand_mod, monkeypatch, tmp_path):
    out = str(tmp_path / "out")
    seeds = ["1020990000001", "1020990000002", "1020990000003"]
    calls = []

    def fetch(url, reserve):
        reserve()
        calls.append(url)
        # 세 seed 모두 동일 priorArt 집합 반환 → 2·3번째 seed는 신규 0건
        an = url.split("applicationNumber=")[1].split("&")[0]
        return _many_priorart_xml(an, 2)

    run_main(expand_mod, monkeypatch, seeds + ["--out", out], fetch)
    exp = read_expansion(out)
    assert exp["termination_reason"] == "two_consecutive_zero_new"
    assert len(calls) == 3  # 3번째에서 2회 연속 0 도달


# ---- 예산/쿼터 ----

def test_run_budget_exhausted_exits_3(expand_mod, monkeypatch, tmp_path):
    out = str(tmp_path / "out")
    seeds = ["1020990000001", "1020990000002", "1020990000003"]

    def fetch(url, reserve):
        reserve()  # reserve가 상한에서 BudgetExhausted를 던진다
        an = url.split("applicationNumber=")[1].split("&")[0]
        return _many_priorart_xml(an, 1)

    with pytest.raises(SystemExit) as e:
        run_main(expand_mod, monkeypatch,
                 seeds + ["--out", out, "--max-calls", "2"], fetch)
    assert e.value.code == 3  # 예산 도달 조기 종료
    exp = read_expansion(out)
    assert exp["termination_reason"] == "run_budget"
    assert exp["axes"]["prior_art_backward"]["status"] == "partial"


def test_reserve_one_monthly_hard_stop(tmp_path):
    """월 원장 hard stop — reserve_one 단위(스텁 아닌 실제 함수)."""
    import sys
    import time as _t

    from conftest import load_script
    fresh = load_script("kipris_expand_fresh", "kipris_expand.py")
    month = _t.strftime("%Y-%m")
    (tmp_path / "kipris_quota.json").write_text(json.dumps({month: 800}), encoding="utf-8")
    with pytest.raises(fresh.QuotaHardStop):
        fresh.reserve_one(str(tmp_path), 800, override=False)
    # override면 통과하고 801로 증가
    assert fresh.reserve_one(str(tmp_path), 800, override=True) == 801
    sys.modules.pop("kipris_expand_fresh", None)


def test_reserve_one_atomic_increment(tmp_path):
    from conftest import load_script
    import sys
    fresh = load_script("kipris_expand_fresh2", "kipris_expand.py")
    import time as _t
    month = _t.strftime("%Y-%m")
    assert fresh.reserve_one(str(tmp_path), 800, override=False) == 1
    assert fresh.reserve_one(str(tmp_path), 800, override=False) == 2
    data = json.loads((tmp_path / "kipris_quota.json").read_text())
    assert data[month] == 2
    sys.modules.pop("kipris_expand_fresh2", None)


# ---- 키 마스킹 ----

def test_raw_xml_saved_with_key_redacted(expand_mod, monkeypatch, tmp_path):
    out = str(tmp_path / "out")
    key_bytes = FAKE_KEY.encode()

    def fetch(url, reserve):
        reserve()
        return fixture_bytes("bib_expand_priorart.xml").replace(
            b"</response>", key_bytes + b"</response>")

    run_main(expand_mod, monkeypatch, ["1020990000001", "--out", out], fetch)
    saved = open(os.path.join(out, "bib_1020990000001.xml"), "rb").read()
    assert key_bytes not in saved
    assert b"[REDACTED]" in saved


def test_refuses_overwrite_without_force(expand_mod, monkeypatch, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "expansion.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        run_main(expand_mod, monkeypatch, ["1020990000001", "--out", str(out)],
                 lambda url, reserve: fixture_bytes("bib_expand_priorart.xml"))
    assert "기존 산출물" in str(e.value.code)


def test_discovery_not_evidence_documented(expand_mod, monkeypatch, tmp_path):
    out = str(tmp_path / "out")
    run_main(expand_mod, monkeypatch, ["1020990000001", "--out", out],
             lambda url, reserve: fixture_bytes("bib_expand_priorart.xml"))
    exp = read_expansion(out)
    assert "후보일 뿐" in exp["discovery_not_evidence"]
    assert "패밀리 커버리지 미확인" in exp["family_coverage_note"]
