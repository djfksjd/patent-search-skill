# -*- coding: utf-8 -*-
"""KIPRIS Plus 서지상세(getBibliographyDetailInfoSearch) — 청구항 전문 수집 → JSON.

사용:
  python3 kipris_claims.py 1020260075385 10-2024-0067638 ... [--out DIR]
  python3 kipris_claims.py --file appnos.txt --out ./work

- 키: 환경변수 KIPRIS_KEY 또는 스크립트 상위 폴더(.env). 키는 어떤 출력에도 남기지 않는다.
- HTTPS 고정. 출원번호는 하이픈 제거 후 13자리 숫자만 허용. 429/5xx/타임아웃은 백오프 재시도.
- fail-closed: resultCode 부재(스키마 변경 신호), 빈 청구항([""] 포함), 응답 출원번호 불일치는
  전부 해당 문헌 실패로 처리한다.
- 출력: <out>/claims.json           문헌별 제목·서지·청구항 전문 — 기존 파일이 있으면 병합(누적)
        <out>/bib_<출원번호>.xml     원본 응답(공개 저장소 커밋 금지 — 조사 대상 노출)
- 종료 코드: 0=전건 성공, 1=하나 이상 실패/스키마 오류.
- 한계(중요): 이 응답의 청구항은 공보 서지 기준이며 최신 보정·정정·무효심판 결과를
  반영한다는 보장이 없다 → JSON에 current_enforceable_claims="unknown"으로 명시.
  FTO 판단에 쓰기 전 KIPRIS 웹/행정처리 이력으로 현재 청구항·법적 상태를 재확인할 것.
- 정독 대상만 호출할 것 — 문헌 1건 = API 1회 (무료 쿼터 월 1,000회). 사용량은 스킬 폴더
  kipris_quota.json에 누적 기록된다.
"""
import argparse, json, os, re, sys, time
import urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
import kipris_http

KIPRIS_HOSTS = ("plus.kipris.or.kr",)
BASE = ("https://plus.kipris.or.kr/kipo-api/kipi/patUtiModInfoSearchSevice/"
        "getBibliographyDetailInfoSearch")
MAX_BODY = 20 * 1024 * 1024
CALLS = [0]


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
    # 대소문자 혼합 %XX 인코딩까지 잡는 정규식 마스킹(kipris_http.make_redact)
    return kipris_http.make_redact(key)


def redact_body(body, key):
    return kipris_http.make_redact_bytes(key)(body)


def fetch(url, tries=3):
    delay, last = 1.0, None
    for _ in range(tries):
        CALLS[0] += 1
        try:
            return kipris_http.open_validated(url, 30, KIPRIS_HOSTS, MAX_BODY)
        except kipris_http.RedirectBlocked:
            raise  # 외부 호스트 유출 시도 — 재시도 없이 즉시 실패
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                last = e
            else:
                raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
        time.sleep(delay)
        delay *= 3
    raise last


def bump_quota(script_path, n):
    try:
        skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(script_path)))
        path = os.path.join(skill_dir, "kipris_quota.json")
        data = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
        month = time.strftime("%Y-%m")
        data[month] = int(data.get(month, 0)) + n
        # 원자적 교체 — kipris_search.py bump_quota와 동일 계약
        tmp = f"{path}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, path)
        return data[month]
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("appnos", nargs="*", help="출원번호(예: 1020260075385 또는 10-2026-0075385)")
    ap.add_argument("--file", help="출원번호 목록 파일(줄당 1개, # 주석 허용)")
    ap.add_argument("--out", default=".", help="출력 폴더")
    a = ap.parse_args()

    file_targets = []
    if a.file:
        try:
            file_targets = [l.strip() for l in open(a.file, encoding="utf-8")
                            if l.strip() and not l.strip().startswith("#")]
        except OSError:
            sys.exit(f"출원번호 파일 없음/읽기 실패: {a.file}")
    raw_targets = list(a.appnos) + file_targets
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

    # 기존 claims.json이 있으면 병합(이전 실행 증거를 덮어쓰지 않는다)
    path = os.path.join(a.out, "claims.json")
    out = {}
    if os.path.exists(path):
        try:
            out = json.load(open(path, encoding="utf-8"))
            print(f"기존 claims.json에 병합(기존 {len(out)}건)", file=sys.stderr)
        except Exception:
            backup = path + ".corrupt." + time.strftime("%Y%m%d-%H%M%S")
            os.rename(path, backup)
            print(f"기존 claims.json 파싱 실패 — {backup}으로 보존 후 새로 생성", file=sys.stderr)

    failed = 0
    for an in targets:
        try:
            params = urllib.parse.urlencode({"applicationNumber": an, "ServiceKey": key})
            body = fetch(f"{BASE}?{params}")
            body = redact_body(body, key)
            with open(os.path.join(a.out, f"bib_{an}.xml"), "wb") as f:
                f.write(body)
            root = ET.fromstring(body)
            code = root.findtext(".//resultCode")
            if code is None:
                raise RuntimeError("schema_error: 응답에 resultCode 없음 — "
                                   "API 스키마 변경/차단 의심, 원본 XML 확인")
            if code != "00":
                raise RuntimeError(f"resultCode {code}: {root.findtext('.//resultMsg') or ''}")
            resp_an = (root.findtext(".//applicationNumber") or "").replace("-", "").strip()
            # 응답 출원번호가 **없거나** 요청과 다르면 귀속 불가 — fail-closed. 부재를
            # 허용하면 다른(또는 미상) 문헌의 청구항이 요청 번호로 저장된다(Codex #5).
            if resp_an != an:
                raise RuntimeError(
                    f"응답 출원번호 불일치/부재: 요청 {an} / 응답 {resp_an or '없음'} — "
                    "잘못된 문헌 귀속 방지(fail-closed), 원본 XML 확인")
            claims = [c for c in
                      ((ci.findtext("claim") or "").strip()
                       for ci in root.findall(".//claimInfo")) if c]
            title = (root.findtext(".//inventionTitle") or "").strip()
            if not claims or not title:
                raise RuntimeError("schema_error: 청구항(비어있지 않은) 또는 발명명칭이 응답에 없음 "
                                   "(미공개/취하 문헌이거나 API 스키마 변경 — 원본 XML 확인)")
        except Exception as e:
            failed += 1
            err = redact(f"{type(e).__name__}: {e}")
            # 기존 정상 레코드를 오류로 덮어쓰지 않는다 (증거 보존) — 오류는 별도 키에 기록
            if isinstance(out.get(an), dict) and out[an].get("claims"):
                out[an]["last_refresh_error"] = err
                out[an]["partial"] = True  # 최신성 미확인 — 실패 신호(Codex #6)
                print(f"{an}: 재조회 실패 — 기존 정상 레코드 유지 ({err})", file=sys.stderr)
            else:
                out[an] = {"error": err, "partial": True}
                print(f"{an}: 실패 — {err}", file=sys.stderr)
            time.sleep(0.5)
            continue
        out[an] = {
            "schema_version": 2,  # v2: priority가 {number, date} 객체 목록 (v1: 날짜 문자열 목록)
            "title": title,
            "applicant": (root.findtext(".//applicantName") or "").strip(),
            "regStatus_공보서지기준": root.findtext(".//registerStatus") or "",
            "current_enforceable_claims": "unknown",  # 보정·정정·심판 미반영 가능 — 웹에서 재확인
            "appDate": root.findtext(".//applicationDate") or "",
            "openDate": root.findtext(".//openDate") or "",
            "priority": [{"number": (p.findtext("priorityApplicationNumber") or "").strip(),
                          "date": (p.findtext("priorityApplicationDate") or "").strip()}
                         for p in root.findall(".//priorityInfo")],
            "n_claims": len(claims),
            "claims_source": "getBibliographyDetailInfoSearch(공보 서지)",
            "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "claims": claims,
        }
        print(f"{an} [{out[an]['regStatus_공보서지기준']}] {title[:50]} — 청구항 {len(claims)}개")
        time.sleep(0.5)

    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    month_total = bump_quota(__file__, CALLS[0])
    quota_msg = f" / 이번 달 누적 {month_total}회(이 머신 기준)" if month_total else ""
    print(f"저장: {path} (성공 {len(targets)-failed} / 실패 {failed}) — "
          f"API 호출 {CALLS[0]}회{quota_msg}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
