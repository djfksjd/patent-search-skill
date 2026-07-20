# -*- coding: utf-8 -*-
"""KIPRIS Plus 서지상세(getBibliographyDetailInfoSearch) — 청구항 전문 수집 → JSON.

사용:
  python3 kipris_claims.py 1020260075385 10-2024-0067638 ... [--out DIR]
  python3 kipris_claims.py --file appnos.txt --out ./work

- 키: 환경변수 KIPRIS_KEY 또는 스크립트 상위 폴더(.env). 키는 어떤 출력에도 남기지 않는다.
- HTTPS 고정. 출원번호는 하이픈 제거 후 13자리 숫자만 허용.
- 출력: <out>/claims.json           문헌별 제목·서지·청구항 전문
        <out>/bib_<출원번호>.xml     원본 응답(공개 저장소 커밋 금지 — 조사 대상 노출)
- 종료 코드: 0=전건 성공, 1=하나 이상 실패/스키마 오류.
- 한계(중요): 이 응답의 청구항은 공보 서지 기준이며 최신 보정·정정·무효심판 결과를
  반영한다는 보장이 없다 → JSON에 current_enforceable_claims="unknown"으로 명시.
  FTO 판단에 쓰기 전 KIPRIS 웹/행정처리 이력으로 현재 청구항·법적 상태를 재확인할 것.
- 정독 대상만 호출할 것 — 문헌 1건 = API 1회 (무료 쿼터 월 1,000회).
"""
import argparse, json, os, re, sys, time
import urllib.parse, urllib.request
import xml.etree.ElementTree as ET

BASE = ("https://plus.kipris.or.kr/kipo-api/kipi/patUtiModInfoSearchSevice/"
        "getBibliographyDetailInfoSearch")
MAX_BODY = 20 * 1024 * 1024


def load_key(script_path):
    key = os.environ.get("KIPRIS_KEY", "").strip()
    if key:
        return key
    env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(script_path))), ".env")
    if os.path.exists(env):
        for line in open(env, encoding="utf-8"):
            if line.startswith("KIPRIS_KEY="):
                return line.split("=", 1)[1].strip()
    sys.exit("KIPRIS_KEY 없음 — 환경변수 또는 스킬 폴더 .env에 설정 (README 참고)")


def make_redactor(key):
    variants = {key, urllib.parse.quote(key, safe=""), urllib.parse.quote(key)}
    def redact(text):
        for v in variants:
            if v:
                text = text.replace(v, "[REDACTED]")
        return text
    return redact


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("appnos", nargs="*", help="출원번호(예: 1020260075385 또는 10-2026-0075385)")
    ap.add_argument("--file", help="출원번호 목록 파일(줄당 1개, # 주석 허용)")
    ap.add_argument("--out", default=".", help="출력 폴더")
    a = ap.parse_args()

    raw_targets = list(a.appnos) + (
        [l.strip() for l in open(a.file, encoding="utf-8")
         if l.strip() and not l.startswith("#")] if a.file else [])
    targets, bad = [], []
    for t in raw_targets:
        canon = t.replace("-", "").strip()
        if re.fullmatch(r"\d{13}", canon):
            if canon not in targets:
                targets.append(canon)
        else:
            bad.append(t)
    if bad:
        sys.exit(f"잘못된 출원번호 형식(13자리 숫자 필요): {', '.join(bad)}")
    if not targets:
        ap.error("출원번호가 없습니다")

    os.makedirs(a.out, exist_ok=True)
    key = load_key(__file__)
    redact = make_redactor(key)

    out, failed = {}, 0
    for an in targets:
        try:
            params = urllib.parse.urlencode({"applicationNumber": an, "ServiceKey": key})
            body = urllib.request.urlopen(f"{BASE}?{params}", timeout=30).read(MAX_BODY)
            if key.encode() in body or urllib.parse.quote(key, safe="").encode() in body:
                body = body.replace(key.encode(), b"[REDACTED]").replace(
                    urllib.parse.quote(key, safe="").encode(), b"[REDACTED]")
            with open(os.path.join(a.out, f"bib_{an}.xml"), "wb") as f:
                f.write(body)
            root = ET.fromstring(body)
            code = root.findtext(".//resultCode")
            if code and code != "00":
                raise RuntimeError(f"resultCode {code}: {root.findtext('.//resultMsg') or ''}")
            claims = [(c.findtext("claim") or "").strip() for c in root.findall(".//claimInfo")]
            title = (root.findtext(".//inventionTitle") or "").strip()
            if not claims or not title:
                raise RuntimeError("schema_error: 청구항 또는 발명명칭이 응답에 없음 "
                                   "(미공개/취하 문헌이거나 API 스키마 변경 — 원본 XML 확인)")
        except Exception as e:
            failed += 1
            out[an] = {"error": redact(f"{type(e).__name__}: {e}")}
            print(f"{an}: 실패 — {out[an]['error']}", file=sys.stderr)
            time.sleep(0.5)
            continue
        out[an] = {
            "title": title,
            "applicant": (root.findtext(".//applicantName") or "").strip(),
            "regStatus_공보서지기준": root.findtext(".//registerStatus") or "",
            "current_enforceable_claims": "unknown",  # 보정·정정·심판 미반영 가능 — 웹에서 재확인
            "appDate": root.findtext(".//applicationDate") or "",
            "openDate": root.findtext(".//openDate") or "",
            "priority": [p.findtext("priorityApplicationDate") or ""
                         for p in root.findall(".//priorityInfo")],
            "n_claims": len(claims),
            "claims_source": "getBibliographyDetailInfoSearch(공보 서지)",
            "claims": claims,
        }
        print(f"{an} [{out[an]['regStatus_공보서지기준']}] {title[:50]} — 청구항 {len(claims)}개")
        time.sleep(0.5)

    path = os.path.join(a.out, "claims.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"저장: {path} (성공 {len(targets)-failed} / 실패 {failed})")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
