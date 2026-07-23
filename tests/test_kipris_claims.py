# -*- coding: utf-8 -*-
"""kipris_claims.py 계약 테스트 — fixture XML 주입, 네트워크 없음."""
import json
import os

import pytest

from conftest import fixture_bytes

AN = "1020990000001"


def run_main(mod, monkeypatch, argv_tail, fetch):
    monkeypatch.setattr(mod, "bump_quota", lambda *_: 42)
    monkeypatch.setattr(mod, "fetch", fetch)
    monkeypatch.setattr(mod.sys, "argv", ["kipris_claims.py"] + argv_tail)
    mod.main()


def read_claims(out_dir):
    with open(os.path.join(out_dir, "claims.json"), encoding="utf-8") as f:
        return json.load(f)


def test_ok_record_schema_v2(claims_mod, monkeypatch, tmp_path):
    out = str(tmp_path / "out")
    run_main(claims_mod, monkeypatch, ["10-2099-0000001", "--out", out],
             lambda url: fixture_bytes("bib_ok.xml"))
    rec = read_claims(out)[AN]
    assert rec["schema_version"] == 2
    # 빈/공백 청구항은 필터링된다
    assert rec["claims"] == ["청구항 1. 가공 텍스트로 이루어진 첫 번째 청구항.",
                             "청구항 2. 가공 텍스트로 이루어진 두 번째 청구항."]
    assert rec["n_claims"] == 2
    # priority는 {number, date} 객체 목록
    assert rec["priority"] == [{"number": "1020980000009", "date": "20980909"}]
    assert rec["current_enforceable_claims"] == "unknown"
    assert rec["title"] == "익명화된 샘플 발명"
    assert rec["retrieved_at"]
    assert os.path.exists(os.path.join(out, f"bib_{AN}.xml"))


def test_response_appno_mismatch_is_error(claims_mod, monkeypatch, tmp_path):
    out = str(tmp_path / "out")
    with pytest.raises(SystemExit) as e:
        run_main(claims_mod, monkeypatch, [AN, "--out", out],
                 lambda url: fixture_bytes("bib_mismatch.xml"))
    assert e.value.code == 1
    rec = read_claims(out)[AN]
    assert "error" in rec and "불일치" in rec["error"]
    assert "claims" not in rec  # 성공 레코드로 위장하지 않음


def test_refresh_failure_preserves_existing_record(claims_mod, monkeypatch, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    existing = {AN: {"schema_version": 2, "title": "기존 가공 제목",
                     "claims": ["기존 가공 청구항 1."], "n_claims": 1,
                     "retrieved_at": "2099-01-01T00:00:00+0900"}}
    (out / "claims.json").write_text(json.dumps(existing, ensure_ascii=False),
                                     encoding="utf-8")

    def boom(url):
        raise RuntimeError("simulated network failure")

    with pytest.raises(SystemExit) as e:
        run_main(claims_mod, monkeypatch, [AN, "--out", str(out)], boom)
    assert e.value.code == 1
    rec = read_claims(str(out))[AN]
    # 기존 정상 레코드는 보존, 실패는 last_refresh_error로만 기록
    assert rec["claims"] == ["기존 가공 청구항 1."]
    assert rec["title"] == "기존 가공 제목"
    assert "simulated network failure" in rec["last_refresh_error"]
    assert "error" not in rec


def test_zero_claims_response_is_failure(claims_mod, monkeypatch, tmp_path):
    out = str(tmp_path / "out")
    with pytest.raises(SystemExit) as e:
        run_main(claims_mod, monkeypatch, [AN, "--out", out],
                 lambda url: fixture_bytes("bib_no_claims.xml"))
    assert e.value.code == 1  # 성공 위장 금지 — 종료 코드 비0
    rec = read_claims(out)[AN]
    assert "error" in rec and "schema_error" in rec["error"]
    assert "claims" not in rec and "schema_version" not in rec


def test_merge_keeps_unrelated_records(claims_mod, monkeypatch, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    other = "1020990000777"
    existing = {other: {"schema_version": 2, "title": "무관한 기존 문헌",
                        "claims": ["기존 청구항."], "n_claims": 1}}
    (out / "claims.json").write_text(json.dumps(existing, ensure_ascii=False),
                                     encoding="utf-8")
    run_main(claims_mod, monkeypatch, [AN, "--out", str(out)],
             lambda url: fixture_bytes("bib_ok.xml"))
    data = read_claims(str(out))
    assert data[other]["claims"] == ["기존 청구항."]  # 병합 — 기존 레코드 유지
    assert data[AN]["schema_version"] == 2
