# -*- coding: utf-8 -*-
"""KIPRIS Plus 자유검색(getWordSearch) 일괄 실행 → TSV + 매니페스트.

사용:
  python3 kipris_search.py "검색어1" "검색어2" ... [--rows 30] [--max-pages 3] [--out DIR]
  python3 kipris_search.py --file queries.txt --out ./work

- 키: 환경변수 KIPRIS_KEY 또는 스크립트 상위 폴더(.env)의 KIPRIS_KEY=... 라인.
  키는 어떤 출력·저장 파일에도 남기지 않는다(에러 메시지도 마스킹).
- HTTPS 고정. 전송 실패 시 http 폴백하지 않는다.
- 출력: <out>/kipris_results.tsv        검색식 간 출원번호 중복 제거(queries 열에 매칭 검색식 병기)
        <out>/raw_K<n>_p<page>.xml      원본 응답(근거 보존 — 조사 대상이 드러나므로 공개 저장소에 커밋 금지)
        <out>/search_manifest.json      검색식별 전체 건수/수집 건수/partial 여부 (부분 수집을 완전 수집으로 오인 방지)
- 종료 코드: 0=전 검색식 성공, 1=하나 이상 실패. partial(전체>수집)은 매니페스트와 stdout에 표시.
- 쿼터: 호출 수 = 검색식 수 × 페이지 수 (무료 월 1,000회) — max-pages를 아껴 쓸 것.
"""
import argparse, csv, json, os, re, sys, time
import urllib.parse, urllib.request
import xml.etree.ElementTree as ET

BASE = "https://plus.kipris.or.kr/kipo-api/kipi/patUtiModInfoSearchSevice/getWordSearch"
ERR = {"30": "등록되지 않은 키 — plus.kipris.or.kr 마이페이지에서 APIKEY 확인",
       "31": "상품 이용기간 비활성 — '특허·실용 공개·등록공보' 상품 신청/기간 확인"}
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
    ap.add_argument("queries", nargs="*", help="검색어(따옴표로 묶기)")
    ap.add_argument("--file", help="검색어 목록 파일(줄당 1개, # 주석 허용)")
    ap.add_argument("--rows", type=int, default=30, help="페이지당 건수(기본 30)")
    ap.add_argument("--max-pages", type=int, default=3, help="검색식당 최대 페이지(기본 3)")
    ap.add_argument("--out", default=".", help="출력 폴더")
    a = ap.parse_args()
    if not (1 <= a.rows <= 500) or a.max_pages < 1:
        ap.error("--rows는 1~500, --max-pages는 1 이상")

    queries, seen_q = [], set()
    for q in list(a.queries) + (
            [l.strip() for l in open(a.file, encoding="utf-8")
             if l.strip() and not l.startswith("#")] if a.file else []):
        if q not in seen_q:
            seen_q.add(q)
            queries.append(q)
    if not queries:
        ap.error("검색어가 없습니다")

    os.makedirs(a.out, exist_ok=True)
    key = load_key(__file__)
    redact = make_redactor(key)

    rows, manifest, failed = {}, [], 0
    for i, q in enumerate(queries, 1):
        qid = f"K{i}"
        collected, total, partial, error = 0, None, False, None
        for page in range(1, a.max_pages + 1):
            params = urllib.parse.urlencode(
                {"word": q, "numOfRows": a.rows, "pageNo": page, "ServiceKey": key})
            try:
                body = urllib.request.urlopen(f"{BASE}?{params}", timeout=30).read(MAX_BODY)
                root = ET.fromstring(body)
                code = root.findtext(".//resultCode")
                if code and code != "00":
                    raise RuntimeError(
                        f"KIPRIS 오류 {code}: {ERR.get(code, root.findtext('.//resultMsg') or '')}")
            except Exception as e:
                error = redact(f"{type(e).__name__}: {e}")
                break
            if key.encode() in body or urllib.parse.quote(key, safe="").encode() in body:
                body = body.replace(key.encode(), b"[REDACTED]").replace(
                    urllib.parse.quote(key, safe="").encode(), b"[REDACTED]")
            with open(os.path.join(a.out, f"raw_{qid}_p{page}.xml"), "wb") as f:
                f.write(body)
            total = int(root.findtext(".//totalCount") or 0)
            items = root.findall(".//item")
            for it in items:
                g = lambda t: (it.findtext(t) or "").strip()
                an = g("applicationNumber")
                if not an:
                    continue
                if an in rows:
                    if qid not in rows[an]["queries"].split(","):
                        rows[an]["queries"] += "," + qid
                else:
                    rows[an] = dict(
                        appNo=an, title=g("inventionTitle"), applicant=g("applicantName"),
                        appDate=g("applicationDate"), openDate=g("openDate"),
                        regStatus=g("registerStatus"), ipc=g("ipcNumber"), queries=qid,
                        abstract_excerpt=re.sub(r"[\t\n\r]", " ", g("astrtCont"))[:300])
            collected += len(items)
            if collected >= total or not items:
                break
            time.sleep(0.5)
        if error:
            failed += 1
            print(f"{qid} [{q}] → 실패: {error}", file=sys.stderr)
        else:
            partial = total is not None and collected < total
            print(f"{qid} [{q}] → {collected}건 수집 / 전체 {total}건"
                  + (" ⚠PARTIAL — max-pages 확대 또는 검색식 세분화 필요" if partial else ""))
        manifest.append(dict(qid=qid, query=q, total=total, collected=collected,
                             partial=partial, error=error))
        time.sleep(0.5)

    out_tsv = os.path.join(a.out, "kipris_results.tsv")
    with open(out_tsv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["appNo", "title", "applicant", "appDate", "openDate",
                                          "regStatus", "ipc", "queries", "abstract_excerpt"],
                           delimiter="\t")
        w.writeheader()
        for r in rows.values():
            w.writerow(r)
    json.dump(dict(tool="kipris_search", endpoint="getWordSearch(https)", queries=manifest,
                   unique_results=len(rows), note="partial=true인 검색식은 전체를 수집하지 못함"),
              open(os.path.join(a.out, "search_manifest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    print(f"중복 제거 후 총 {len(rows)}건 → {out_tsv}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
