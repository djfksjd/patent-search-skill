# tests

네트워크 없는 fixture 기반 pytest 스위트. 스킬 루트에서 `python3 -m pytest tests/ -q`로 실행한다(pytest 필요). `scripts/kipris_search.py`·`scripts/kipris_claims.py`·`scripts/kipris_legal_status.py`·`scripts/kipris_expand.py`(및 오프라인 검증기 `fto_gate.py`·`claim_chart_validate.py`)를 importlib로 로드해 `fetch`를 fixture XML로 교체하므로 실제 KIPRIS API를 호출하지 않고(확장 스크립트는 동시 실행 lock과 원장 예약도 스텁으로 교체해 실제 `kipris_quota.json`을 건드리지 않는다 — 원장 예약 hard stop은 tmp 원장으로 별도 단위 검증), `KIPRIS_KEY`는 가짜 값을 환경변수로 주입해 `.env`도 읽지 않는다. `tests/fixtures/`의 XML은 전부 익명화한 가공 데이터(실존 출원번호·발명 내용 아님)이며, KIPRIS 응답의 구조만 흉내낸 것이다.
