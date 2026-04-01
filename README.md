# 라오어 무한매수법 자동매매 봇

한국투자증권 Open API를 사용하여 **라오어의 무한매수법**을 자동화하는 프로그램입니다.
미국 ETF(TQQQ, QQQ 등)를 대상으로 매일 자동 주문하고, 텔레그램 알림과 구글 스프레드시트 기록을 제공합니다.

---

## 무한매수법 전략 요약

### 기본 규칙
- 총 투자금을 **40분할**로 나눔 (1회차 = 총자금 / 40)
- 1회차: 현재가 지정가 매수 (1회차 전체 금액)
- 2회차~: 매일 **0.5회차씩 2건의 LOC 매수** 주문
- 목표 수익률(기본 10%) 도달 시 전량 매도 후 새 사이클 시작

### 매일 주문 (3건)

| 주문 | 가격 | 수량 | 체결 조건 |
|------|------|------|-----------|
| LOC 매수 (평단) | 평균단가 | 0.5회차 분 (floor) | 종가 <= 평균단가 |
| LOC 매수 (고가) | 목표 익절가 | 0.5회차 분 (floor) | 종가 <= 목표 익절가 |
| 지정가 매도 | 목표 익절가 | 전량 (Day Order) | 장중 목표가 도달 시 |

### 체결 시나리오

| 종가 위치 | 결과 |
|-----------|------|
| 종가 <= 평단 | LOC 양쪽 체결 (1회차 매수) |
| 평단 < 종가 <= 고가 | LOC 고가만 체결 (0.5회차 매수) |
| 종가 > 고가 | LOC 미체결, 지정가 매도 체결 가능 (익절) |

### 40회차 소진 전략
남은 분할이 1회차 미만이면 40회차 소진으로 판단. 환경변수로 4가지 전략 중 선택:

| 전략 | 동작 |
|------|------|
| `quarter` (기본) | 보유분 1/4 매도 -> 시드 재확보 -> 매수 재개 (1회만, 재소진 시 full_exit) |
| `lower_target` | 목표 수익률을 5%로 하향, 매도만 유지 |
| `hold` | 매수 중단, 지정가 매도만 유지 |
| `full_exit` | 전량 매도 후 새 사이클 |

### KIS API 주문 코드 (미국 해외주식)

| 주문 유형 | 실전 ORD_DVSN | 모의투자 ORD_DVSN |
|----------|--------------|-----------------|
| LOC 매수 | `34` (장마감지정가) | `00` (지정가 대체) |
| 지정가 매도 (익절) | `00` | `00` |
| 즉시 매수 (1회차) | `00` (현재가+2%) | `00` (현재가+2%) |
| 즉시 매도 (강제/quarter/full_exit) | `00` (현재가-2%) | `00` (현재가-2%) |

---

## 기능

- 한국투자증권 Open API 연동 (LOC 매수, 지정가 매도, 잔고/체결 조회)
- 무한매수법 전략 자동 실행 (40분할, LOC 주문, 익절 판단)
- 멀티 종목 지원 (종목별 독립 사이클)
- 텔레그램 알림 (주문, 체결, 익절, 낙폭 경고, 에러)
- 텔레그램 명령어 (`/status`, `/sell`, `/pause`, `/resume`, `/report`, `/dryrun`)
- 구글 스프레드시트 기록 (일별 기록 + 사이클 요약 + 연간 백업)
- 수익률 차트 생성 (주간/월간)
- USD/KRW 환율 자동 기록
- 드라이런 모드 (실주문 없이 시뮬레이션)
- 모의투자/실전투자 토글
- 상시 실행 봇 (텔레그램 명령어 수신 + APScheduler 자동 주문)

### 안전장치

| 장치 | 동작 | 기본값 |
|------|------|--------|
| 하루 1회 주문 | `last_order_date` 체크 | - |
| 일일 주문 횟수 제한 | 초과 시 중단 + 알림 | 2회 |
| 최대 주문 수량 | LOC 평단/고가 각각 수량 캡 | 10주 |
| 자동 일시중지 | 낙폭 초과 시 자동 `/pause` | -30% |
| 낙폭 경고 | 텔레그램 알림 (매매 계속) | -20% |
| 체결 이중 처리 방지 | `processed_order_ids` | - |
| 주문 재시도 금지 | `no_retry=True` (중복 주문 방지) | - |
| 2단계 매도 확인 | `/sell` -> `/confirm_sell` (30초) | - |

---

## 사전 준비

### 1. 한국투자증권 Open API

1. [한국투자증권](https://www.koreainvestment.com) 계좌 개설
2. [KIS Developers](https://apiportal.koreainvestment.com) 에서 Open API 신청
3. **모의투자** API Key 발급 (APP Key, APP Secret) - 먼저 모의투자로 테스트
4. 해외주식 거래 가능 계좌 확인

### 2. 텔레그램 봇

1. Telegram에서 [@BotFather](https://t.me/BotFather)에게 `/newbot` 명령
2. 봇 이름 지정 -> **Bot Token** 받기
3. 생성된 봇에게 `/start` 메시지 전송
4. 브라우저에서 `https://api.telegram.org/bot<토큰>/getUpdates` 접속
5. 응답에서 `"chat": {"id": 123456789}` 값이 **Chat ID**

### 3. 구글 서비스 계정

1. [Google Cloud Console](https://console.cloud.google.com) 접속
2. 프로젝트 생성 또는 선택
3. `API 및 서비스` > `라이브러리`에서 **Google Sheets API** + **Google Drive API** 활성화
4. `IAM 및 관리자` > `서비스 계정` > `서비스 계정 만들기`
5. `키` 탭 > `키 추가` > `새 키 만들기` > **JSON** 선택 > 다운로드
6. 구글 스프레드시트 생성 (빈 시트, 이름 자유 - 프로그램이 탭을 자동 생성)
7. 스프레드시트 공유: JSON 파일의 `client_email`을 **편집자**로 추가
8. 스프레드시트 URL에서 ID 확인: `https://docs.google.com/spreadsheets/d/여기가ID/edit`

---

## 설치

### 방법 1: 직접 실행 (라즈베리파이, 리눅스 서버)

```bash
git clone https://github.com/gw-space/auto_infinitetrade.git
cd auto_infinitetrade

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 방법 2: Docker

```bash
git clone https://github.com/gw-space/auto_infinitetrade.git
cd auto_infinitetrade
docker compose build
```

---

## 설정

### 1. 환경변수 (.env)

```bash
cp .env.example .env
```

`.env` 파일을 열어 실제 값 입력:

```env
# 한국투자증권 API
KIS_APP_KEY=실제_앱키
KIS_APP_SECRET=실제_앱시크릿
KIS_ACCOUNT_NUMBER=12345678-01
KIS_ENV=paper          # paper(모의투자) 또는 live(실전)

# 텔레그램
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIjKlMnOpQrStUvWxYz
TELEGRAM_CHAT_ID=123456789

# 구글 스프레드시트
GOOGLE_SHEETS_SPREADSHEET_ID=스프레드시트ID
GOOGLE_CREDENTIALS_PATH=credentials/service_account.json

# 40회차 소진 전략
OVER40_STRATEGY=quarter
```

### 2. 매매 설정 (config/settings.yaml)

```bash
cp config/settings.example.yaml config/settings.yaml
```

```yaml
tickers:
  - symbol: "TQQQ"
    exchange: "NASD"        # NASD, NYSE, AMEX
    total_capital: 10000.0  # 투입 상한 (USD)
    num_splits: 40          # 분할 수
    profit_target_pct: 0.10 # 목표 수익률 (10%)

schedule:
  order_time: "09:35"       # 주문 시간 (US Eastern)
  check_time: "16:15"       # 체결 확인 시간
  report_time: "16:30"      # 일일 리포트 시간

alerts:
  max_drawdown_pct: 0.20    # 낙폭 경고 기준 (-20%)
  max_order_qty: 10         # 1회 최대 주문 수량 (LOC 평단/고가 각각)
  max_daily_orders: 2       # 일일 최대 주문 횟수 (정상=1, 2회째 차단)
  auto_pause_drawdown_pct: 0.30  # 자동 일시중지 낙폭 (-30%)

backup:
  monthly_day: 1            # 백업 체크일 (매년 1월에 연간 백업 생성)
```

### 3. 구글 서비스 계정 키

```bash
cp ~/Downloads/프로젝트명-xxxxx.json credentials/service_account.json
```

---

## 실행

### 직접 실행

```bash
source .venv/bin/activate
python -m src.main
```

### Docker 실행

```bash
docker compose up -d        # 백그라운드 실행
docker compose logs -f      # 로그 확인
docker compose down         # 중지
```

### systemd 서비스 (라즈베리파이)

```bash
sudo nano /etc/systemd/system/infinitetrade.service
```

```ini
[Unit]
Description=InfiniteTrade Bot
After=network.target

[Service]
User=pi
WorkingDirectory=/home/pi/auto_infinitetrade
ExecStart=/home/pi/auto_infinitetrade/.venv/bin/python -m src.main
Restart=always
RestartSec=10
Environment=TZ=US/Eastern

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable infinitetrade
sudo systemctl start infinitetrade
sudo systemctl status infinitetrade   # 상태 확인
sudo journalctl -u infinitetrade -f   # 로그 확인
```

---

## 모의투자 테스트

실전 투입 전 반드시 모의투자로 테스트하세요.

### 모의투자 vs 실전 차이

| | 모의투자 (KIS_ENV=paper) | 실전 (KIS_ENV=live) |
|--|--------------------------|---------------------|
| LOC 매수 | 지정가(00)로 대체 | LOC(34) 사용 |
| 즉시 매수/매도 | 지정가(00) 현재가+/-2% | 지정가(00) 현재가+/-2% |
| LOC 시뮬레이션 | 09:35 의도 생성 -> 15:30 체결 판정 | 09:35 LOC 주문 -> 종가 체결 |
| 스케줄 | 09:35 의도 / 15:30 판정 / 16:15 체결확인 | 09:35 주문 / 16:15 체결확인 |

### 모의투자 LOC 시뮬레이션 흐름

KIS 모의투자에서는 LOC(34) 주문을 지원하지 않으므로, 장 종료 30분 전을 가상 장 마감으로 간주하여 LOC를 시뮬레이션합니다:

```
09:35 ET  LOC 주문 의도 생성 (실제 주문 X)
          ├── 1회차 → 즉시 지정가 매수 (현재가+2%)
          ├── 지정가 매도 → 즉시 주문 (목표 익절가)
          └── LOC 매수 → 평단가/고가/수량 저장만

15:30 ET  가상 장 마감 (체결 판정)
          ├── 현재가 조회 (= 가상 종가)
          ├── 가상종가 <= 평단가 → LOC 평단 체결 → 지정가 매수
          ├── 가상종가 <= 고가 → LOC 고가 체결 → 지정가 매수
          └── 텔레그램: 체결/미체결 결과

16:15 ET  체결 확인 + 구글 시트 기록
16:30 ET  일일 리포트
```

### 모의투자 테스트 절차

1. `.env`에서 `KIS_ENV=paper` 설정
2. 봇 실행: `python -m src.main`
3. 확인 사항:
   - 텔레그램 "봇 시작됨" 수신
   - `/status` 응답 정상
   - 09:35 ET에 LOC 의도 생성 텔레그램 알림
   - 15:30 ET에 체결 판정 텔레그램 알림
   - 구글 시트 기록 확인
4. 최소 3영업일 운영 후 실전 전환

### 실전 전환

```bash
# .env 수정
KIS_ENV=live

# KIS 실전투자 API 키로 변경
KIS_APP_KEY=실전_앱키
KIS_APP_SECRET=실전_앱시크릿
```

---

## 텔레그램 명령어

봇이 실행 중일 때 텔레그램에서 사용할 수 있는 명령어:

| 명령어 | 설명 |
|--------|------|
| `/status` | 보유 현황, 수익률 조회 |
| `/sell TQQQ` | 전량 매도 1단계 (확인 요청) |
| `/confirm_sell TQQQ` | 전량 매도 2단계 (30초 내 입력) |
| `/pause` | 매매 일시 중지 |
| `/resume` | 매매 재개 |
| `/dryrun on` | 드라이런 모드 (실주문 없음) |
| `/dryrun off` | 실주문 모드 |
| `/report` | 누적 수익 리포트 |
| `/help` | 명령어 도움말 |

---

## 동작 흐름

### 실전 일일 자동 실행

```
09:35 ET  주문 실행
          ├── 현재가 조회
          ├── 잔고 조회 + 상태 동기화
          ├── 낙폭 체크 (-30% 시 자동 일시중지)
          ├── 전략 판단 (매수/매도/홀딩)
          ├── LOC 매수(평단) 0.5회차 주문
          ├── LOC 매수(고가) 0.5회차 주문
          ├── 지정가 매도 전량 주문
          └── 텔레그램 알림

16:15 ET  체결 확인
          ├── 체결 내역 조회 (이중 처리 방지)
          ├── 상태 업데이트 (평단, 수량, 분할)
          ├── 구글 시트 기록
          ├── 익절 체결 시 → 사이클 종료 → 새 사이클
          └── 텔레그램 알림

16:30 ET  일일 리포트 발송
```

### 사이클 흐름

```
사이클 시작 (1회차: 현재가 지정가 매수)
    |
매일 LOC 주문 반복 (2~40회차)
    |
[분기]
├── 익절가 도달 → 전량 매도 → 수익 포함 새 사이클
└── 40회차 소진 (남은 분할 < 1.0) → OVER40_STRATEGY 실행
    ├── quarter: 1/4 매도 → 매수 재개 (1회만)
    │   └── 재소진 → full_exit 전환
    ├── lower_target: 목표 5%로 하향, 매도만 유지
    ├── hold: 매수 중단, 매도만 유지
    └── full_exit: 전량 매도 → 새 사이클
```

### 신규 사이클 자본금

```
새 사이클 자본금 = min(계좌 가용 잔고, settings.yaml 상한선)
```

---

## 구글 스프레드시트 구조

프로그램이 자동으로 시트 탭을 생성합니다 (빈 스프레드시트에서 시작).

### 일별 기록 탭

| 컬럼 | 설명 |
|------|------|
| 사이클 | 사이클 번호 |
| 날짜 | 거래일 |
| 종목 | 티커 |
| 현재가 | 종가 |
| 평균단가 | 매입 평균가 |
| 보유수량 | 주식 수 |
| LOC 평단가 | LOC 매수(평단) 주문가 |
| LOC 고가 | LOC 매수(고가) 주문가 |
| 액션 | 매수/매도/미체결 |
| 체결수량 | 실제 체결 수량 |
| 체결금액 | 실제 체결 금액 |
| 분할 | 사용/전체 (예: 15.3/40) |
| 수익률(%) | 현재 수익률 |
| USD/KRW | 환율 |
| 평가금액 | 현재가 x 보유수량 |
| 실현손익 | 누적 실현 손익 |
| 비고 | 메모 |

### 사이클 요약 탭

| 컬럼 | 설명 |
|------|------|
| 사이클 | 사이클 번호 |
| 시작일 ~ 종료일 | 사이클 기간 |
| 종목 | 티커 |
| 투입총액 / 매도총액 | USD |
| 총수익 (USD / KRW) | 손익 |
| 수익률(%) | 수익률 |
| 사용분할 | n/40 |
| 종료사유 | 익절 / 40회차 소진 등 |

### 연간 백업

매년 1월에 자동으로 백업 탭 생성 (예: `일별 기록_2026`). 데이터가 없으면 스킵.

---

## 테스트

### 단위 테스트

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

### 라오어 규칙 1:1 대응 검증 (23개 규칙, 71건)

```bash
python scripts/test_laoor_rules.py
```

### 종합 시뮬레이션 (18개 시나리오, 54건)

```bash
python scripts/test_comprehensive.py
```

### 시뮬레이터 (KIS API 없이)

```bash
python scripts/simulator.py           # 14개 시나리오
python scripts/test_3cycles.py        # 3사이클 연속
python scripts/test_over40_all.py     # 40회차 전략 4종 비교
```

### 텔레그램 + 구글 시트 연동 테스트

```bash
python scripts/test_integrations.py telegram   # 텔레그램
python scripts/test_integrations.py sheets     # 구글 시트
python scripts/test_integrations.py all        # 전부
```

### 풀 사이클 시뮬레이션 (텔레그램 + 구글 시트 기록)

```bash
python scripts/test_full_cycle.py      # 정상 익절 사이클
python scripts/test_quarter_full.py    # Quarter 전략 사이클
```

---

## 프로젝트 구조

```
auto_infinitetrade/
├── config/
│   └── settings.example.yaml     # 매매 설정 템플릿
├── src/
│   ├── main.py                    # 메인 (봇 + 스케줄러)
│   ├── kis/
│   │   ├── auth.py                # OAuth 토큰 관리
│   │   ├── client.py              # HTTP 클라이언트 (주문 재시도 금지)
│   │   ├── market.py              # 현재가 조회
│   │   ├── order.py               # LOC 매수 + 지정가 매수/매도
│   │   └── account.py             # 잔고/체결 조회
│   ├── strategy/
│   │   ├── infinite_buy.py        # 무한매수법 핵심 로직
│   │   └── state.py               # 사이클 상태 관리
│   ├── notifications/
│   │   └── telegram.py            # 텔레그램 봇 + 알림
│   ├── logging_sheet/
│   │   └── sheets.py              # 구글 시트 기록 + 연간 백업
│   ├── charts/
│   │   └── renderer.py            # 수익률 차트 생성
│   └── utils/
│       ├── config_loader.py       # 설정 로더 + 검증
│       ├── market_calendar.py     # 미국 시장 캘린더
│       └── exchange_rate.py       # USD/KRW 환율
├── scripts/                       # 시뮬레이터 + 테스트 스크립트
├── tests/                         # 단위 테스트 (32건)
├── data/                          # 상태 파일 (gitignore)
├── logs/                          # 로그 파일 (gitignore)
├── credentials/                   # 구글 서비스 계정 (gitignore)
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── .gitignore
├── requirements.txt
└── pyproject.toml
```

---

## 운영 시 주의사항

### 모의투자 먼저

- `.env`에서 `KIS_ENV=paper`로 설정하여 **반드시 모의투자에서 먼저 테스트**
- 모의투자에서는 LOC 미지원 → 15:30 가상 장 마감으로 LOC 시뮬레이션
- 최소 3영업일 이상 정상 동작 확인 후 `KIS_ENV=live`로 전환

### 보안

- `.env` 파일은 절대 커밋하지 마세요 (.gitignore에 포함됨)
- 텔레그램 봇은 `TELEGRAM_CHAT_ID`로 인증된 사용자만 명령어 실행 가능
- `/sell` 명령은 2단계 확인 (`/sell` -> `/confirm_sell`, 30초 제한)
- 토큰/상태 파일은 `0o600` 권한으로 저장
- 주문 API는 재시도 금지 (`no_retry=True`) — 중복 주문 방지

### 서머타임

- 스케줄러가 `US/Eastern` 타임존으로 동작하므로 서머타임 자동 처리
- 수동 설정 변경 불필요

### 프로그램 중단 시

- 놓친 거래일은 무시하고 다음 거래일부터 이어서 진행
- 시작 시 KIS 실제 잔고와 state.json을 자동 동기화 (reconciliation)
- 중복 주문 방지 (`last_order_date` 체크)
- 네트워크 오류로 주문 실패해도 오늘은 패스, 내일 정상 진행

### 멀티 종목

- `settings.yaml`에 종목 추가하면 종목별 독립 사이클 운영
- `total_capital` 합계가 계좌 잔고 이내가 되도록 설정
- 같은 구글 시트에 기록 (종목 컬럼으로 구분)

### 모의+실전 동시 운영

하나의 코드베이스에서 환경별 설정 파일을 분리하여 동시 운영할 수 있습니다.

#### 파일 구조

```
auto_infinitetrade/
├── .env.paper              # 모의: KIS 모의 API 키, 텔레그램 봇A, 구글시트A
├── .env.live               # 실전: KIS 실전 API 키, 텔레그램 봇B, 구글시트B
├── config/
│   ├── settings_paper.yaml # 모의: 종목, 자본금
│   ├── settings_live.yaml  # 실전: 종목, 자본금
│   └── settings.example.yaml
├── data/
│   ├── state_paper.json    # 자동 생성
│   └── state_live.json     # 자동 생성
└── src/
```

#### 실행 방법

```bash
# 모의투자
python -m src.main .env.paper   # → settings_paper.yaml + state_paper.json

# 실전투자
python -m src.main .env.live    # → settings_live.yaml + state_live.json

# 기본 (.env 하나로 운영)
python -m src.main              # → settings.yaml + state.json
```

`.env.paper`를 인자로 주면 자동으로 `config/settings_paper.yaml`과 `data/state_paper.json`을 사용합니다. 환경별 settings 파일이 없으면 기본 `settings.yaml`을 사용합니다.

#### systemd 서비스 (동시 운영)

```ini
# /etc/systemd/system/infinitetrade-paper.service
[Service]
ExecStart=/home/pi/auto_infinitetrade/.venv/bin/python -m src.main .env.paper

# /etc/systemd/system/infinitetrade-live.service
[Service]
ExecStart=/home/pi/auto_infinitetrade/.venv/bin/python -m src.main .env.live
```

```bash
sudo systemctl start infinitetrade-paper   # 모의투자
sudo systemctl start infinitetrade-live    # 실전투자
```

#### 코드 업데이트

코드가 하나이므로 `git pull` 한 번이면 양쪽 다 반영됩니다. 설정 파일(.env, settings yaml)은 gitignore 대상이므로 유지됩니다.

---

## 라이선스

이 프로젝트는 개인 사용 목적으로 작성되었습니다.
투자에 대한 모든 책임은 사용자 본인에게 있습니다.
