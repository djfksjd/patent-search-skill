# tests

네트워크 없는 fixture 기반 pytest 스위트. 스킬 루트에서 `python3 -m pytest tests/ -q`로 실행한다(pytest 필요). `scripts/kipris_search.py`·`scripts/kipris_claims.py`를 importlib로 로드해 `fetch`를 fixture XML로 교체하므로 실제 KIPRIS API를 호출하지 않고, `KIPRIS_KEY`는 가짜 값을 환경변수로 주입해 `.env`도 읽지 않는다. `tests/fixtures/`의 XML은 전부 익명화한 가공 데이터(실존 출원번호·발명 내용 아님)이며, KIPRIS 응답의 구조만 흉내낸 것이다.
