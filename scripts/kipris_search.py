# -*- coding: utf-8 -*-
"""KIPRIS Plus 자유검색(getWordSearch) 일괄 실행 → TSV + 매니페스트.

사용:
  python3 kipris_search.py "검색어1" "검색어2" ... [--rows 30] [--max-pages 3] [--out DIR]
  python3 kipris_search.py --file queries.txt --out ./work

- 키: 환경변수 KIPRIS_KEY 또는 스크립트 상위 폴더(.env)의 KIPRIS_KEY=... 라인.
  키는 어떤 출력·저장 파일에도 남기지 않는다(에러 메시지도 마스킹).
- HTTPS 고정. 전송 실패 시 http 폴백하지 않는다. 429/5xx/타임아웃은 2회까지 지수 백오프 재시도.
- fail-closed: 응답에 resultCode 또는 totalCount가 없으면(스키마 변경 신호) 그 검색식은 실패로
  처리한다 — 0건을 "선행기술 없음"으로 오인하지 않기 위함.
- 출력: <out>/kipris_results.tsv        검색식 간 출원번호 중복 제거(queries 열에 매칭 검색식 병기)
        <out>/raw_<실행시각>_K<n>_p<page>.xml  원본 응답(근거 보존 — 공개 저장소 커밋 금지)
        <out>/search_manifest.json      검색식별 전체/수집 건수·partial·retrieved_at
- 기존 산출물이 있는 --out에는 쓰지 않는다(--force로만 덮어쓰기) — 증거 보존.
- 종료 코드: 0=전 검색식 성공, 1=하나 이상 실패. 에러로 중단된 검색식도 partial=true로 기록된다.
- 쿼터: 호출 수 = 검색식 수 × 페이지 수 (무료 월 1,000회). 실행별 사용량을 스킬 폴더
  kipris_quota.json(YYYY-MM별 누적)에 기록하고 stdout에 표시한다.
"""
import argparse, csv, json, os, re, sys, time
import urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET

BASE = "https://plus.kipris.or.kr/kipo-api/kipi/patUtiModInfoSearchSevice/getWordSearch"
ERR = {"30": "등록되지 않은 키 — plus.kipris.or.kr 마이페이지에서 APIKEY 확인",
       "31": "상품 이용기간 비활성 — '특허·실용 공개·등록공보' 상품 신청/기간 확인"}
MAX_BODY = 20 * 1024 * 1024
CALLS = [0]  # 이번 실행의 API 호출 수(재시도 포함)


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
    variants = {key, urllib.parse.quote(key, safe=""), urllib.parse.quote(key),
                urllib.parse.quote_plus(key)}
    def redact(text):
        for v in variants:
            if v:
                text = text.replace(v, "[REDACTED]")
        return text
    return redact


def redact_body(body, key):
    for v in (key, urllib.parse.quote(key, safe=""), urllib.parse.quote(key),
              urllib.parse.quote_plus(key)):
        if v:
            body = body.replace(v.encode(), b"[REDACTED]")
    return body


def fetch(url, tries=3):
    """429/5xx/네트워크 오류만 지수 백오프 재시도. 그 외 HTTP 오류는 즉시 raise(쿼터 보호)."""
    delay, last = 1.0, None
    for _ in range(tries):
        CALLS[0] += 1
        try:
            body = urllib.request.urlopen(url, timeout=30).read(MAX_BODY)
            if len(body) >= MAX_BODY:
                raise RuntimeError("응답이 20MB 한도에서 절단됨 — --rows 축소 필요")
            return body
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
    """스킬 폴더 kipris_quota.json에 YYYY-MM별 호출 수 누적. 이 파일은 이 머신에서 이
    스크립트로 실행한 호출만 세는 하한 추정치다 — 정확한 잔량은 KIPRIS Plus 마이페이지 확인."""
    try:
        skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(script_path)))
        path = os.path.join(skill_dir, "kipris_quota.json")
        data = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
        month = time.strftime("%Y-%m")
        data[month] = int(data.get(month, 0)) + n
        json.dump(data, open(path, "w", encoding="utf-8"), indent=1)
        return data[month]
    except Exception:
        return None


def read_lines(path):
    out = []
    for l in open(path, encoding="utf-8"):
        s = l.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("queries", nargs="*", help="검색어(따옴표로 묶기)")
    ap.add_argument("--file", help="검색어 목록 파일(줄당 1개, # 주석 허용)")
    ap.add_argument("--rows", type=int, default=30, help="페이지당 건수(기본 30)")
    ap.add_argument("--max-pages", type=int, default=3, help="검색식당 최대 페이지(기본 3)")
    ap.add_argument("--out", default=".", help="출력 폴더")
    ap.add_argument("--force", action="store_true",
                    help="출력 폴더에 기존 산출물이 있어도 덮어쓴다(기본: 거부)")
    a = ap.parse_args()
    if not (1 <= a.rows <= 500) or a.max_pages < 1:
        ap.error("--rows는 1~500, --max-pages는 1 이상")

    file_queries = []
    if a.file:
        try:
            file_queries = read_lines(a.file)
        except OSError:
            sys.exit(f"검색어 파일 없음/읽기 실패: {a.file}")
    queries, seen_q = [], set()
    for q in list(a.queries) + file_queries:
        if q not in seen_q:
            seen_q.add(q)
            queries.append(q)
    if not queries:
        ap.error("검색어가 없습니다")

    os.makedirs(a.out, exist_ok=True)
    for existing in ("kipris_results.tsv", "search_manifest.json"):
        if os.path.exists(os.path.join(a.out, existing)) and not a.force:
            sys.exit(f"기존 산출물 존재: {os.path.join(a.out, existing)} — "
                     "다른 --out 폴더를 쓰거나 --force로 덮어쓰기(증거 보존을 위해 비권장)")
    key = load_key(__file__)
    redact = make_redactor(key)
    run_ts = time.strftime("%Y%m%d-%H%M%S")

    clean = lambda s: re.sub(r"[\t\n\r]", " ", s)
    rows, manifest, failed = {}, [], 0
    for i, q in enumerate(queries, 1):
        qid = f"K{i}"
        collected, total, error, prev_fp = 0, None, None, None
        for page in range(1, a.max_pages + 1):
            params = urllib.parse.urlencode(
                {"word": q, "numOfRows": a.rows, "pageNo": page, "ServiceKey": key})
            try:
                body = fetch(f"{BASE}?{params}")
            except Exception as e:
                error = redact(f"{type(e).__name__}: {e}")
                break
            # 원본 응답은 검증 실패(스키마 변경) 시에도 근거로 남긴다 — 키 마스킹 후 즉시 저장
            body = redact_body(body, key)
            with open(os.path.join(a.out, f"raw_{run_ts}_{qid}_p{page}.xml"), "wb") as f:
                f.write(body)
            try:
                root = ET.fromstring(body)
                code = root.findtext(".//resultCode")
                if code is None:
                    raise RuntimeError("schema_error: 응답에 resultCode 없음 — "
                                       "API 스키마 변경/차단 의심, 원본 XML 확인")
                if code != "00":
                    raise RuntimeError(
                        f"KIPRIS 오류 {code}: {ERR.get(code, root.findtext('.//resultMsg') or '')}")
                tc = root.findtext(".//totalCount")
                if tc is None:
                    raise RuntimeError("schema_error: 응답에 totalCount 없음 — "
                                       "API 스키마 변경 의심, 원본 XML 확인")
                total = int(tc)
            except Exception as e:
                error = redact(f"{type(e).__name__}: {e}")
                break
            items = root.findall(".//item")
            # 서버가 pageNo를 무시하고 같은 페이지를 반복하면 collected가 부풀어
            # partial=false로 오판된다 — 페이지 지문 반복은 오류로 처리 (fail-closed)
            page_fp = tuple((it.findtext("applicationNumber") or "") for it in items)
            if items and page_fp == prev_fp:
                error = f"repeated_page: p{page}가 직전 페이지와 동일 — 서버 페이지네이션 이상, 수집 중단(partial)"
                break
            prev_fp = page_fp
            for it in items:
                g = lambda t: (it.findtext(t) or "").strip()
                an = g("applicationNumber").replace("-", "").strip()
                if not an:
                    continue
                if an in rows:
                    if qid not in rows[an]["queries"].split(","):
                        rows[an]["queries"] += "," + qid
                else:
                    rows[an] = dict(
                        appNo=an, title=clean(g("inventionTitle")),
                        applicant=clean(g("applicantName")),
                        appDate=g("applicationDate"), openDate=g("openDate"),
                        regStatus=clean(g("registerStatus")), ipc=clean(g("ipcNumber")),
                        queries=qid, abstract_excerpt=clean(g("astrtCont"))[:300])
            collected += len(items)
            if collected >= total or not items:
                break
            time.sleep(0.5)
        # 에러로 중단됐으면 전체를 수집하지 못한 것 — 반드시 partial로 기록 (fail-closed)
        partial = (total is None) or (collected < total)
        if error:
            failed += 1
            print(f"{qid} [{q}] → 실패: {error}"
                  + (f" (중단 전 {collected}건은 TSV에 포함 — partial)" if collected else ""),
                  file=sys.stderr)
        else:
            print(f"{qid} [{q}] → {collected}건 수집 / 전체 {total}건"
                  + (" ⚠PARTIAL — max-pages 확대 또는 검색식 세분화 필요" if partial else ""))
        manifest.append(dict(qid=qid, query=q, total=total, collected=collected,
                             partial=partial, error=error,
                             retrieved_at=time.strftime("%Y-%m-%dT%H:%M:%S%z")))
        time.sleep(0.5)

    out_tsv = os.path.join(a.out, "kipris_results.tsv")
    with open(out_tsv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["appNo", "title", "applicant", "appDate", "openDate",
                                          "regStatus", "ipc", "queries", "abstract_excerpt"],
                           delimiter="\t")
        w.writeheader()
        for r in rows.values():
            w.writerow(r)
    month_total = bump_quota(__file__, CALLS[0])
    json.dump(dict(tool="kipris_search", endpoint="getWordSearch(https)", run_ts=run_ts,
                   queries=manifest, unique_results=len(rows), api_calls=CALLS[0],
                   month_calls_estimate=month_total,
                   note="partial=true인 검색식은 전체를 수집하지 못함(에러 중단 포함)"),
              open(os.path.join(a.out, "search_manifest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    print(f"중복 제거 후 총 {len(rows)}건 → {out_tsv}")
    quota_msg = f" / 이번 달 누적 {month_total}회(이 머신 기준, 무료 1,000회)" if month_total else ""
    print(f"API 호출 {CALLS[0]}회{quota_msg}")
    if month_total and month_total > 800:
        print("⚠ 월 무료 쿼터(1,000회)의 80% 초과 — 호출을 아끼거나 KIPRIS Plus에서 잔량 확인",
              file=sys.stderr)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
