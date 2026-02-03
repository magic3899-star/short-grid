# 물타기 봇 - 변경 이력

---

## 개발 워크플로우 (메모)

**서버 코드 수정 시 순서:**
1. 서버에서 파일 다운로드 (`scp root@fundauto.cafe24.com:/root/trading_bot/server.py trading_bot/`)
2. 로컬에서 수정
3. 서버에 업로드 (`scp trading_bot/server.py root@fundauto.cafe24.com:/root/trading_bot/`)
4. 로컬 파일 삭제 (`rm -rf trading_bot/`)
5. Git 백업: 서버 파일 다운 → commit → push → 로컬 삭제

**서버 정보:**
- SSH: `root@fundauto.cafe24.com`
- 경로: `/root/trading_bot/`
- 웹: `http://fundauto.cafe24.com`

---

## 2026-02-03 (v2.5)

### 물타기 로직 전면 재작성

#### 핵심 버그 수정
1. **물타기 시작 시 기존 포지션에 익절 걸림** → 항상 현재 포지션 기준으로 시작, 숏만 배치
2. **따라가기 threshold 계산 오류** → `short_price * (1-N%)` → `start_entry * (1-N%)`
3. **서버 재시작 시 상태 유실** → 봇 객체 생성하여 상태 복원
4. **API에서 새 봇 생성 시 기존 상태 덮어씀** → 기존 봇 재사용
5. **place_tp_order KeyError** → `.get()` 사용 + 유효성 검사

#### 물타기 흐름 정리
```
1. 물타기 시작
   └─ 현재 포지션 = 기준수량, 현재가 = 기준가
   └─ 숏 주문 배치 (기준가 + N%)

2. 가격 따라가기 (기준가에서 N% 하락 시)
   └─ 기존 숏 취소
   └─ 기준가 갱신 (현재가)
   └─ 새 숏 배치 (새 기준가 + N%)

3. 숏 체결 (포지션 증가)
   └─ entries에 진입 기록
   └─ 익절 주문 (진입가 - N%, 물타기 수량만)

4. 익절 체결 (포지션 감소)
   └─ entries 초기화
   └─ 기준가/수량 갱신
   └─ 새 숏 배치
   └─ 반복
```

#### 로깅 강화
- 물타기 시작 시: 포지션, 주문, 설정값 상세 출력
- 익절 계산 시: 진입가, 익절%, 익절가 출력
- 따라가기 시: 기준가/숏가 변경 전후 출력

#### 서버 재시작 동작
- 상태 파일(averaging_state.json) 로드
- 봇 객체 생성 + 상태 복원
- `is_active = false` (자동 시작 안함)
- 사용자가 "물타기 시작" 클릭 시 → 현재 포지션 기준 신규 시작

---

## 2026-02-01 (v2.4)

### 핵심 수정: 중복 숏 주문 방지
- **start() 함수 완전 재작성**: 익절 주문(BUY) 존재 시 숏 주문 절대 금지
- 익절 주문이 있으면 → 물타기 진입 상태로 복원 (기준수량 자동 계산)
- 숏 주문이 있으면 → 스킵
- 둘 다 없으면 → 새 숏 주문 생성

### 로컬 파일 정리
- 불필요한 로컬 파일 삭제 (start.bat, start.ps1, index.html, web/, static/)
- 서버 파일 기준으로 Git 백업만 유지
- 로컬에서 서버 실행 금지 (항상 cafe24 서버에서만 실행)

---

## 2026-02-01 (v2.3)

### 버그 수정 및 개선
- **물타기 금액 기본값 수정**: `$10` → `$500` (index.html과 동일하게)
- **웹앱 자동 갱신 강화**: 10초마다 포지션 정보(손익 포함) 자동 갱신
- **중복 주문 방지**: 서버 재시작 시 기존 오픈 주문 확인 후 중복 방지
- **Windows 인코딩 문제 해결**: cp949 → UTF-8 강제 설정

### 물타기 로직 명확화
- **숏 주문**: 기준가(현재가) +2% 지점
- **가격 따라가기**: 숏 주문가 대비 -2% 이하로 떨어지면 숏 주문 재배치
- **익절**: 물타기 진입가 -2% 지점 (포지션 평단 아님!)
- **수동 주문 보호**: 서버가 생성한 주문만 관리, 수동 익절 주문은 건드리지 않음

### 설정값 (config.py)
```python
AVG_INTERVAL = 2      # 추가숏 간격 % (기준가 +2%)
AVG_TP_INTERVAL = 2   # 익절 간격 % (물타기 진입가 -2%)
AVG_AMOUNT = 500      # 물타기 금액 ($)
CHECK_INTERVAL = 30   # 체크 간격 (초)
```

---

## 2026-02-01 (v2.2)

### 매매 기록 저장 기능 (v2.1)
- **trades.json**에 모든 매매 기록 자동 저장
- 기록 유형:
  - `grid_entry`: 그리드 1차 진입
  - `grid_add`: 그리드 추가 주문
  - `averaging_short`: 물타기 숏 주문
  - `averaging_short_filled`: 물타기 숏 체결
  - `averaging_tp`: 물타기 익절 주문
  - `averaging_tp_filled`: 물타기 익절 체결 (수익 기록)
- API 엔드포인트:
  - `GET /api/trades` - 매매 기록 조회 (symbol, type 필터)
  - `GET /api/trades/summary` - 매매 요약 (총 수익, 거래 수)
  - `POST /api/trades/clear` - 기록 초기화

### 포지션 청산 시 자동 종료
- 수동으로 포지션 전체 청산 시 해당 종목 물타기 자동 정지
- 30초마다 포지션 체크하여 청산 감지

### Python 서버 완전 전환 (v2.0)
- 기존 JavaScript 웹앱에서 **Python Flask 서버**로 완전 전환
- 웹 UI도 Python API를 통해서만 동작 (클라이언트에서 직접 바이낸스 호출 없음)
- 24시간 서버 운영 가능 (PC 종료해도 동작)
- 서버 재시작 시 물타기 상태 자동 복원

### 서버 구조
```
/root/trading_bot/
├── server.py           # Flask 웹서버 + 물타기 봇 + 모든 API
├── config.py           # API 키, 설정값
├── requirements.txt    # Python 패키지
├── averaging_state.json    # 물타기 상태 저장
├── watchlist.json      # 워치리스트 저장
└── static/
    └── index.html      # 웹 UI (Python API 연동)
```

### API 엔드포인트
- `GET /api/status` - 서버 상태
- `GET /api/positions` - 전체 포지션
- `GET /api/position/<symbol>` - 개별 포지션
- `GET /api/orders` - 오픈오더
- `POST /api/order/create` - 주문 생성
- `POST /api/order/cancel` - 주문 취소
- `POST /api/order/cancel_all` - 전체 취소
- `GET /api/prices` - 전체 가격
- `GET /api/price/<symbol>` - 개별 가격
- `GET /api/klines/<symbol>` - 차트 데이터
- `GET /api/bb/<symbol>` - 볼린저밴드
- `GET /api/bb/scan` - BB 상단 스캔
- `GET /api/watchlist` - 워치리스트
- `POST /api/watchlist/add` - 워치리스트 추가
- `POST /api/watchlist/remove` - 워치리스트 삭제
- `GET /api/grid/settings` - 그리드 설정
- `POST /api/grid/settings` - 그리드 설정 변경
- `POST /api/grid/place` - 그리드 주문 실행
- `GET /api/averaging/list` - 물타기 목록
- `POST /api/averaging/start` - 물타기 시작
- `POST /api/averaging/stop` - 물타기 정지
- `POST /api/averaging/force_tp` - 강제 익절
- `POST /api/averaging/fix_tp` - 주문 재배치
- `POST /api/averaging/set_base` - 기준가 설정
- `GET /api/averaging/state/<symbol>` - 물타기 상태
- `GET /api/symbol/<symbol>` - 심볼 정보
- `GET /api/logs` - 로그 조회
- `GET /api/config` - 설정값

### 물타기 로직
- **추가숏**: 기준가 +2% 지점에 숏 주문
- **익절**: 물타기 진입가 -2% 지점에 추가 수량만큼 매수
- **30초마다** 포지션 체크 및 주문 갱신
- **가격 하락 시** 숏 주문 따라가기
- **익절 체결 후** 새 사이클 자동 시작

### 그리드 주문
- 1차 진입 (시장가 or 지정가)
- 추가 주문 (간격 % 설정 가능)
- 레버리지 설정 (기본 2배 격리)

### 설정값 (config.py)
```python
AVG_INTERVAL = 2      # 추가숏 간격 % (기준가 +2%)
AVG_TP_INTERVAL = 2   # 익절 간격 % (물타기 진입가 -2%)
AVG_AMOUNT = 10       # 물타기 금액 ($)
CHECK_INTERVAL = 30   # 체크 간격 (초)
```

### 접속 주소
- **http://fundauto.cafe24.com** (포트 80)

---

## 2026-01-31

### TP 계산 버그 수정
- TP 가격: 전체 평단이 아닌 **물타기 진입가** 기준으로 수정
- TP 수량: `avgAmount * 2 / price`가 아닌 **addedQty(물타기 수량)** 사용

---

## 2026-01-29

### 물타기 모드 추가
- 손실 포지션 평단 낮추기 전략
- 추가 숏: 평단 +2% 지점에 숏 주문
- 익절: 물타기 진입가 -2% 지점에 추가된 수량만큼 매수
- 원금 보호: 물타기 시작 시점 수량은 익절 대상에서 제외
- 사이클 반복: 익절 체결 → 새 추가숏/익절 주문 자동 생성

---

## 사용법

1. 브라우저에서 http://fundauto.cafe24.com 접속
2. 코인 추가 (검색창에서 입력)
3. 코인 선택 → 차트 및 포지션 정보 표시
4. **그리드 주문**: 주문 설정 후 [숏 주문 실행] 버튼
5. **물타기**: 포지션 있을 때 [물타기 시작] 버튼
6. 자동으로 숏/익절 주문 관리됨
7. [정지] 버튼으로 물타기 비활성화 및 주문 취소
