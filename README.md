# 九州ふっこう応援割 발표 감시기

관광청 페이지의 현별 표(`予約開始日` / `割引対象期間`)를 주기적으로 읽어서,
`ー`(미발표) 칸이 실제 날짜로 채워지는 순간 텔레그램·디스코드·슬랙으로 알림을 보냅니다.

감시 대상: <https://www.mlit.go.jp/kankocho/page13_00002.html>

기본으로 **구마모토현**을 우선 감시 대상(`WATCH`)으로 두고, 구마모토가 바뀌면
알림 제목에 🚨 가 붙습니다. 다른 6개 현도 함께 추적하되 일반 알림으로 옵니다.

---

## 1. 알림 채널 준비 (텔레그램 권장)

1. 텔레그램에서 **@BotFather** 검색 → `/newbot` → 봇 이름 입력 → **토큰** 받기
2. 만든 봇과 대화창을 열고 아무 메시지나 한 번 보내기 (이걸 안 하면 봇이 메시지를 못 보냅니다)
3. 브라우저에서 아래 주소를 열어 `"chat":{"id":...}` 값 확인 → 그게 **chat_id**

   ```
   https://api.telegram.org/bot<토큰>/getUpdates
   ```

디스코드나 슬랙을 쓰려면 웹훅 URL만 준비하면 됩니다. 세 개 중 설정된 채널로 다 보냅니다.

## 2-A. GitHub Actions로 돌리기 (PC 꺼도 동작, 추천)

1. 새 GitHub 저장소를 만들고 이 폴더를 그대로 push
2. 저장소 **Settings → Secrets and variables → Actions**에서 추가
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - (선택) `DISCORD_WEBHOOK_URL`, `SLACK_WEBHOOK_URL`
3. **Actions** 탭 → `kyushu-watch` → **Run workflow**로 1회 수동 실행
   - 첫 실행은 현재 상태를 기준선으로 `state.json`에 저장만 하고 알림을 보내지 않습니다
   - 알림 연결을 눈으로 확인하려면 `report` 옵션을 켜고 실행
4. 이후 20분 간격으로 자동 실행됩니다

> 사설(private) 저장소는 Actions 무료 분이 월 2,000분이라 20분 간격이면 빠듯합니다.
> **public 저장소로 만들면 무료 무제한**입니다. 민감한 정보는 코드에 없고 토큰은 Secrets에 들어가니 public으로 두는 걸 권합니다.
> 여유를 두려면 워크플로의 cron을 `*/30`으로 늘리세요.

## 2-B. 로컬에서 상주시키기

```bash
pip install -r requirements.txt

export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_CHAT_ID="..."

python watch.py --test            # 알림 연결 확인
python watch.py --loop 900        # 15분 간격으로 계속 감시
```

백그라운드로 두려면:

```bash
nohup python watch.py --loop 900 > watch.log 2>&1 &
```

macOS 재부팅에도 살아있게 하려면 launchd, 리눅스면 systemd 또는 crontab에
`*/15 * * * * cd /path/to/kyushu-watch && /usr/bin/python3 watch.py` 형태로 등록하면 됩니다.

## 실행 옵션

| 명령 | 동작 |
|---|---|
| `python watch.py` | 1회 체크 (cron / Actions용) |
| `python watch.py --loop 900` | 900초 간격 상주 감시 |
| `python watch.py --report` | 변경이 없어도 현재 표를 알림으로 전송 |
| `python watch.py --test` | 알림 채널 연결 테스트 |
| `python watch.py --html-file page.html` | 로컬 HTML로 파싱만 검증 |

## 환경 변수

| 변수 | 설명 |
|---|---|
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | 텔레그램 알림 |
| `DISCORD_WEBHOOK_URL` | 디스코드 알림 |
| `SLACK_WEBHOOK_URL` | 슬랙 알림 |
| `WATCH` | 우선 감시할 현 (쉼표 구분, 기본 `熊本県`). 예: `熊本県,大分県` |
| `STATE_FILE` | 상태 파일 경로 (기본 `state.json`) |

## 감지 항목

- `予約開始日`, `割引対象期間` 칸의 값 변화 (미발표 → 발표 / 내용 수정 / 발표 → 미발표 회귀)
- 현 이름에 공식 페이지 **링크가 새로 걸리는 것** — 실무적으로 발표의 가장 빠른 신호
- 표 자체를 못 찾는 경우(페이지 구조 변경) 1회 경고 알림

## 주의

- 관광청 페이지 갱신과 OTA(라쿠텐트래블·자란·JTB 등)의 실제 판매 개시 사이에는 시차가 있습니다. 이 알림은 "현이 발표했다"는 신호이고, 예약 버튼이 열리는 건 그 다음입니다.
- 예약 개시 전에 이미 잡아둔 숙박은 할인 대상이 아닙니다. 알림을 받으면 기존 예약 취소 조건을 먼저 확인하세요.
- 요청 간격을 과하게 짧게(1~2분) 두지 마세요. 20~30분이면 충분합니다.
