# -*- coding: utf-8 -*-
"""kipris_search.py 계약 테스트 — fixture XML 주입, 네트워크 없음."""
import glob
import json
import os
import urllib.parse

import pytest

from conftest import FAKE_KEY, fixture_bytes


def run_main(mod, monkeypatch, tmp_path, argv_tail, fetch, quota_stub=True):
    """mod.main()을 fixture 주입 상태로 실행. 반환: (manifest dict 또는 None)."""
    if quota_stub:
        monkeypatch.setattr(mod, "bump_quota", lambda *_: 42)
    monkeypatch.setattr(mod, "fetch", fetch)
    monkeypatch.setattr(mod.sys, "argv", ["kipris_search.py"] + argv_tail)
    mod.main()


def read_manifest(out_dir):
    with open(os.path.join(out_dir, "search_manifest.json"), encoding="utf-8") as f:
        return json.load(f)


def read_tsv(out_dir):
    with open(os.path.join(out_dir, "kipris_results.tsv"), encoding="utf-8") as f:
        return f.read().splitlines()


# ── 정상 응답 파싱 ──────────────────────────────────────────────────────────

def test_ok_response_tsv_and_manifest(search_mod, monkeypatch, tmp_path):
    out = str(tmp_path / "out")
    run_main(search_mod, monkeypatch, tmp_path, ["가공 검색어", "--out", out],
             lambda url: fixture_bytes("search_ok_p1.xml"))

    lines = read_tsv(out)
    assert len(lines) == 3  # 헤더 + 2건
    header = lines[0].split("\t")
    assert header == ["appNo", "title", "applicant", "appDate", "openDate",
                      "regStatus", "ipc", "queries", "abstract_excerpt"]
    for line in lines[1:]:
        assert len(line.split("\t")) == 9  # 탭·개행 포함 값이 이스케이프되어 필드 수 유지

    row1 = dict(zip(header, lines[1].split("\t")))
    assert row1["appNo"] == "1020990000001"  # 하이픈 제거
    assert "\t" not in row1["title"] and "\n" not in row1["title"]
    assert row1["title"] == "샘플 장치 탭포함 개행포함 명칭"
    assert row1["queries"] == "K1"
    assert "탭과 개행이 섞여 있다" in row1["abstract_excerpt"]

    m = read_manifest(out)
    q = m["queries"][0]
    assert q["total"] == 2 and q["collected"] == 2
    assert q["partial"] is False and q["error"] is None
    assert q["retrieved_at"]  # 존재 + 비어있지 않음
    assert m["unique_results"] == 2


def test_empty_result_totalcount_zero(search_mod, monkeypatch, tmp_path):
    out = str(tmp_path / "out")
    run_main(search_mod, monkeypatch, tmp_path, ["빈결과", "--out", out],
             lambda url: fixture_bytes("search_empty.xml"))
    q = read_manifest(out)["queries"][0]
    assert q["total"] == 0 and q["collected"] == 0
    assert q["partial"] is False and q["error"] is None
    assert len(read_tsv(out)) == 1  # 헤더만


# ── fail-closed ────────────────────────────────────────────────────────────

def test_missing_totalcount_forces_partial_and_error(search_mod, monkeypatch, tmp_path):
    out = str(tmp_path / "out")
    with pytest.raises(SystemExit) as e:
        run_main(search_mod, monkeypatch, tmp_path, ["스키마오류", "--out", out],
                 lambda url: fixture_bytes("search_no_totalcount.xml"))
    assert e.value.code == 1
    q = read_manifest(out)["queries"][0]
    assert q["partial"] is True
    assert q["error"] and "totalCount" in q["error"]


def test_missing_resultcode_is_error(search_mod, monkeypatch, tmp_path):
    out = str(tmp_path / "out")
    with pytest.raises(SystemExit) as e:
        run_main(search_mod, monkeypatch, tmp_path, ["코드누락", "--out", out],
                 lambda url: fixture_bytes("search_no_resultcode.xml"))
    assert e.value.code == 1
    q = read_manifest(out)["queries"][0]
    assert q["partial"] is True
    assert q["error"] and "resultCode" in q["error"]


@pytest.mark.parametrize("fixture,code", [("search_err30.xml", "30"),
                                          ("search_err31.xml", "31")])
def test_kipris_error_codes_exit_nonzero(search_mod, monkeypatch, tmp_path, fixture, code):
    out = str(tmp_path / "out")
    with pytest.raises(SystemExit) as e:
        run_main(search_mod, monkeypatch, tmp_path, ["오류코드", "--out", out],
                 lambda url: fixture_bytes(fixture))
    assert e.value.code == 1
    q = read_manifest(out)["queries"][0]
    assert q["partial"] is True
    assert q["error"] and code in q["error"]


def test_repeated_page_fingerprint_aborts_as_partial(search_mod, monkeypatch, tmp_path):
    out = str(tmp_path / "out")
    # 서버가 pageNo를 무시하고 같은 페이지를 반복하는 상황: 항상 동일 fixture 반환
    with pytest.raises(SystemExit) as e:
        run_main(search_mod, monkeypatch, tmp_path,
                 ["반복페이지", "--rows", "2", "--max-pages", "3", "--out", out],
                 lambda url: fixture_bytes("search_repeat_page.xml"))
    assert e.value.code == 1
    q = read_manifest(out)["queries"][0]
    assert q["error"] and q["error"].startswith("repeated_page")
    assert q["partial"] is True
    assert q["collected"] == 2  # 1페이지 수집분만 (부풀림 없음)
    assert len(read_tsv(out)) == 3  # 중복 페이지 항목이 두 번 들어가지 않음


# ── redaction ──────────────────────────────────────────────────────────────

def test_redactor_masks_all_url_encoding_variants(search_mod):
    redact = search_mod.make_redactor(FAKE_KEY)
    for variant in (FAKE_KEY,
                    urllib.parse.quote(FAKE_KEY, safe=""),
                    urllib.parse.quote(FAKE_KEY),
                    urllib.parse.quote_plus(FAKE_KEY)):
        masked = redact(f"GET ...?ServiceKey={variant}&word=x")
        assert variant not in masked
        assert "[REDACTED]" in masked


def test_redact_body_masks_bytes_variants(search_mod):
    for variant in (FAKE_KEY, urllib.parse.quote_plus(FAKE_KEY)):
        body = f"<xml>ServiceKey={variant}</xml>".encode()
        out = search_mod.redact_body(body, FAKE_KEY)
        assert variant.encode() not in out
        assert b"[REDACTED]" in out


def test_raw_xml_saved_with_key_redacted(search_mod, monkeypatch, tmp_path):
    out = str(tmp_path / "out")
    leaked = urllib.parse.quote_plus(FAKE_KEY)
    body = fixture_bytes("search_ok_p1.xml") + f"<!-- ServiceKey={leaked} -->".encode()
    run_main(search_mod, monkeypatch, tmp_path, ["원본저장", "--out", out],
             lambda url: body)
    raws = [f for f in os.listdir(out) if f.startswith("raw_") and f.endswith(".xml")]
    assert raws, "원본 XML이 저장되어야 한다"
    saved = (tmp_path / "out" / raws[0]).read_bytes()
    assert leaked.encode() not in saved and FAKE_KEY.encode() not in saved
    assert b"[REDACTED]" in saved


# ── 출력 폴더 보호 ─────────────────────────────────────────────────────────

def test_refuses_to_overwrite_existing_outputs(search_mod, monkeypatch, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "kipris_results.tsv").write_text("기존 증거\n", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        run_main(search_mod, monkeypatch, tmp_path, ["덮어쓰기", "--out", str(out)],
                 lambda url: fixture_bytes("search_ok_p1.xml"))
    assert e.value.code  # 비0/비어있지 않은 종료 (메시지 문자열)
    assert "기존 산출물" in str(e.value.code)
    assert (out / "kipris_results.tsv").read_text(encoding="utf-8") == "기존 증거\n"


def test_force_allows_overwrite(search_mod, monkeypatch, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "kipris_results.tsv").write_text("기존\n", encoding="utf-8")
    run_main(search_mod, monkeypatch, tmp_path,
             ["강제", "--out", str(out), "--force"],
             lambda url: fixture_bytes("search_ok_p1.xml"))
    assert len(read_tsv(str(out))) == 3


# ── bump_quota ─────────────────────────────────────────────────────────────

def test_bump_quota_atomic_write(search_mod, monkeypatch, tmp_path):
    skill = tmp_path / "fake_skill"
    (skill / "scripts").mkdir(parents=True)
    script_path = str(skill / "scripts" / "kipris_search.py")
    quota = skill / "kipris_quota.json"
    month = search_mod.time.strftime("%Y-%m")
    quota.write_text(json.dumps({month: 5}), encoding="utf-8")

    replaced = []
    real_replace = os.replace
    monkeypatch.setattr(search_mod.os, "replace",
                        lambda src, dst: (replaced.append((src, dst)), real_replace(src, dst)))

    assert search_mod.bump_quota(script_path, 3) == 8
    # 프로세스 고유 tmp 파일에 쓴 뒤 os.replace로 원자적 교체
    assert replaced and f".tmp.{os.getpid()}" in replaced[0][0] \
        and replaced[0][1] == str(quota)
    assert not glob.glob(str(quota) + ".tmp*")
    data = json.loads(quota.read_text(encoding="utf-8"))  # 유효 JSON
    assert data[month] == 8
