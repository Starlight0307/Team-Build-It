# 주식 플러그인 구현 계획 (1~3단계, 모의 계좌 전용)

**목표:** 한국투자증권 모의 계좌를 연결해 손익 브리핑, 대시보드 위젯, 사람이 확정하는 주문, 사용자 규칙 자동매매를 제공한다.

**설계:** `docs/superpowers/specs/2026-11-05-stock-plugin-design.md`

**구조:** 플러그인 파일 하나에 계좌·조회·주문·자동매매 로직을 두고, 화면은 위젯 파일 하나에 둔다. AI에게 공개하는 도구는 3개뿐이며 주문 함수는 공개하지 않는다.

## 공통 제약

- 접속 주소는 `https://openapivts.koreainvestment.com:29443`만 쓴다. 실계좌 주소와 실계좌 tr_id를 코드에 넣지 않는다.
- 공식 예제(koreainvestment/open-trading-api)로 확인한 값: 잔고 `VTTC8434R`, 현재가 `FHKST01010100`, 매도 `VTTC0011U`, 매수 `VTTC0012U`, 토큰 `/oauth2/tokenP`, 종목 마스터 `new.real.download.dws.co.kr/common/master/{kospi,kosdaq}_code.mst.zip`.
- 정규장 지정가만 지원한다. 사용자당 계좌 1개. 로그인 사용자만 쓸 수 있다.
- 키, 토큰, 계좌번호 전체는 도구 결과, 로그, 오류 문장에 넣지 않는다.

## 검토 집중 항목

1. AI 도구에 주문 함수가 노출되면 안 된다 → 도구 목록 테스트
2. 주문 응답이 불분명할 때 재시도하면 이중 주문이 된다 → 시간 초과 테스트
3. 자동매매 도중 끄면 새 주문이 나가면 안 된다 → 주문 직전 재확인 테스트
4. 앱 재시작이나 계정 전환 뒤 자동매매가 이어지면 안 된다 → 꺼짐 테스트
5. 조회 실패나 만료 토큰을 현재 값처럼 보여 주면 안 된다 → 오류·토큰 갱신 테스트

## 작업

### 작업 1: 플러그인 본체

- 파일: `plugins/stock_portfolio.py`, `tests/unit/test_stock_portfolio.py`
- 공개 도구: `get_stock_briefing()`, `open_stock_order(side, stock, quantity, price=0)`, `stop_stock_auto_trading()`
- 화면 전용 함수: `connect_account`, `disconnect_account`, `account_status`, `fetch_portfolio`, `get_price`, `resolve_stock`, `validate_order`, `place_order`, `set_order_settings`, `add_rule`, `delete_rule`, `set_auto_limits`, `enable_auto`, `stop_auto`, `auto_state`, `auto_tick`, `is_recommendation_request`

### 작업 2: 화면

- 파일: `widget/stock_panel.py`, `tests/unit/test_stock_panel.py`
- `StockPanel`, 계좌 설정 창, 주문 창, 자동매매 창

### 작업 3: 앱에 연결

- 파일: `core/plugins_registry.py`, `core/tool_metadata.py`, `core/ai_worker.py`, `app_main.py`
- 플러그인 등록, 지문 갱신, 대시보드 위젯, 사용자 전환, 추천 질문 차단
- 확인: `QT_QPA_PLATFORM=offscreen python3 -m pytest tests/unit -q` 통과, 실제 앱 화면 확인
