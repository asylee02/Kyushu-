#!/usr/bin/env python3
"""
九州ふっこう応援割 - 관광청 현별 발표 테이블 감시기

관광청 페이지의 '各県の情報' 테이블을 주기적으로 읽어서
값이 'ー'(미발표)에서 실제 날짜로 바뀌는 순간 알림을 보낸다.

사용:
    python watch.py                      # 1회 체크 (cron / GitHub Actions용)
    python watch.py --loop 900           # 900초 간격으로 계속 체크 (로컬 상주용)
    python watch.py --test               # 알림 채널 연결 테스트
    python watch.py --html-file page.html  # 로컬 HTML로 파싱만 검증
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup


# 스크립트와 같은 폴더의 .env 파일을 환경변수로 읽어들인다 (있을 때만).
# 외부 패키지 없이 직접 파싱한다.
def _load_dotenv() -> None:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            # bash 습관으로 export를 붙여도 처리
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if not key:
                continue
            # 이미 설정된 실제 환경변수가 우선
            os.environ.setdefault(key, value)


_load_dotenv()

TARGET_URL = "https://www.mlit.go.jp/kankocho/page13_00002.html"
STATE_FILE = os.environ.get("STATE_FILE", "state.json")

# 특별히 주시할 현 (쉼표 구분). 이 현이 바뀌면 알림 앞에 🚨 가 붙는다.
WATCH = [p.strip() for p in os.environ.get("WATCH", "熊本県").split(",") if p.strip()]

KO = {
    "福岡県": "후쿠오카현",
    "佐賀県": "사가현",
    "長崎県": "나가사키현",
    "熊本県": "구마모토현",
    "大分県": "오이타현",
    "宮崎県": "미야자키현",
    "鹿児島県": "가고시마현",
}
PREFS = list(KO.keys())

# 미발표를 뜻하는 표기들 (전각 장음/하이픈/대시 등 다양하게 들어옴)
EMPTY_TOKENS = {"", "ー", "―", "‐", "-", "—", "–", "−", "未定", "未公表"}

KST = timezone(timedelta(hours=9))


def now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")


def norm(s: str) -> str:
    """공백/개행 정규화."""
    return re.sub(r"\s+", " ", (s or "").replace("\u3000", " ")).strip()


def is_empty(v: str) -> bool:
    return norm(v) in EMPTY_TOKENS


# ---------------------------------------------------------------- fetch / parse

def fetch_html() -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
        "Accept-Language": "ja,ko;q=0.8,en;q=0.6",
    }
    r = requests.get(TARGET_URL, headers=headers, timeout=30)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def parse(html: str) -> dict:
    """페이지에서 현별 표 + 최종 갱신일을 뽑아낸다."""
    soup = BeautifulSoup(html, "lxml")

    # 1) 최종 갱신일
    last_updated = ""
    m = re.search(r"最終更新日[：:\s]*([0-9令和元年\.\-/月日\s]+)", soup.get_text(" "))
    if m:
        last_updated = norm(m.group(1))

    # 2) '予約開始日' 헤더를 포함한 테이블 찾기
    target_table = None
    for table in soup.find_all("table"):
        text = table.get_text(" ")
        if "予約開始日" in text or "割引対象期間" in text:
            target_table = table
            break

    rows = {}
    if target_table is not None:
        for tr in target_table.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            label = norm(cells[0].get_text(" "))
            pref = next((p for p in PREFS if p in label), None)
            if not pref:
                continue
            values = [norm(c.get_text(" ")) for c in cells[1:]]
            rows[pref] = {
                "reservation_start": values[0] if len(values) > 0 else "",
                "discount_period": values[1] if len(values) > 1 else "",
                # 그 현 공식 페이지 링크가 걸렸는지 (발표의 강력한 신호)
                "link": (cells[0].find("a") or {}).get("href", "") if cells[0].find("a") else "",
            }

    return {"last_updated": last_updated, "rows": rows}


# ---------------------------------------------------------------------- notify

def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=20,
        )
        r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[telegram] 실패: {e}", file=sys.stderr)
        return False


def send_discord(text: str) -> bool:
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        return False
    try:
        plain = re.sub(r"</?b>", "**", text)
        plain = re.sub(r"<[^>]+>", "", plain)
        r = requests.post(url, json={"content": plain[:1900]}, timeout=20)
        r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[discord] 실패: {e}", file=sys.stderr)
        return False


def send_slack(text: str) -> bool:
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        return False
    try:
        plain = re.sub(r"<[^>]+>", "", text)
        r = requests.post(url, json={"text": plain}, timeout=20)
        r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[slack] 실패: {e}", file=sys.stderr)
        return False


def notify(text: str) -> None:
    sent = any([send_telegram(text), send_discord(text), send_slack(text)])
    if not sent:
        print("[알림] 설정된 채널이 없어 콘솔에만 출력합니다.", file=sys.stderr)
    print(re.sub(r"<[^>]+>", "", text))


# ----------------------------------------------------------------------- state

def load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ------------------------------------------------------------------------ diff

def diff(old: dict, new: dict) -> list:
    """변경 목록을 만든다. 각 항목: (긴급여부, 메시지라인)"""
    changes = []
    old_rows = (old or {}).get("rows", {})
    new_rows = new.get("rows", {})

    for pref, cur in new_rows.items():
        prev = old_rows.get(pref)
        if prev is None:
            continue  # 첫 실행 → 기준선만 저장
        for field, label in (
            ("reservation_start", "예약 시작일"),
            ("discount_period", "할인 대상 기간"),
        ):
            before, after = prev.get(field, ""), cur.get(field, "")
            if before == after:
                continue
            name = KO.get(pref, pref)
            if is_empty(before) and not is_empty(after):
                line = f"✅ <b>{name}</b> {label} 발표 → <b>{after}</b>"
            elif not is_empty(before) and is_empty(after):
                line = f"↩️ <b>{name}</b> {label}가 미발표로 되돌아감 ({before} → -)"
            else:
                line = f"🔄 <b>{name}</b> {label} 변경: {before} → <b>{after}</b>"
            changes.append((pref in WATCH, line))

        if not prev.get("link") and cur.get("link"):
            name = KO.get(pref, pref)
            changes.append((pref in WATCH, f"🔗 <b>{name}</b> 공식 페이지 링크 추가: {cur['link']}"))

    # 표에 새 행이 생긴 경우
    for pref in set(new_rows) - set(old_rows):
        if old_rows:
            changes.append((pref in WATCH, f"➕ 표에 <b>{KO.get(pref, pref)}</b> 행이 추가됨"))

    return changes


def table_text(data: dict) -> str:
    lines = []
    for pref in PREFS:
        row = data["rows"].get(pref)
        if not row:
            continue
        start = row["reservation_start"] or "-"
        period = row["discount_period"] or "-"
        lines.append(f"· {KO.get(pref, pref)}: 예약 {start} / 기간 {period}")
    return "\n".join(lines)


# ------------------------------------------------------------------------ main

def check_once(force_report: bool = False) -> int:
    try:
        html = fetch_html()
    except Exception as e:  # noqa: BLE001
        print(f"[{now_kst()}] 페이지 요청 실패: {e}", file=sys.stderr)
        return 0

    data = parse(html)

    if not data["rows"]:
        print(f"[{now_kst()}] 경고: 표를 찾지 못했습니다. 페이지 구조가 바뀐 것 같습니다.", file=sys.stderr)
        old = load_state()
        if old and not old.get("structure_warned"):
            notify(
                "⚠️ <b>규슈 응원할 감시기</b>\n\n"
                "관광청 페이지에서 현별 표를 찾지 못했습니다. "
                "페이지 구조가 변경됐을 수 있으니 직접 확인해 주세요.\n"
                f"{TARGET_URL}"
            )
            old["structure_warned"] = True
            save_state(old)
        return 0

    old = load_state()
    changes = diff(old, data)

    if not old:
        print(f"[{now_kst()}] 첫 실행 — 현재 상태를 기준선으로 저장합니다.")
        print(table_text(data))
        if force_report:
            notify(
                "🟢 <b>규슈 응원할 감시 시작</b>\n\n"
                f"{table_text(data)}\n\n"
                f"최종 갱신일: {data['last_updated'] or '확인 불가'}\n{TARGET_URL}"
            )
    elif changes:
        urgent = any(u for u, _ in changes)
        head = "🚨 <b>구마모토현 발표!</b>" if urgent else "📢 <b>규슈 응원할 업데이트</b>"
        body = "\n".join(line for _, line in changes)
        notify(
            f"{head}\n\n{body}\n\n"
            f"— 현재 전체 현황 —\n{table_text(data)}\n\n"
            f"관광청 최종 갱신일: {data['last_updated'] or '확인 불가'}\n{TARGET_URL}"
        )
    else:
        print(f"[{now_kst()}] 변경 없음. (최종 갱신일: {data['last_updated'] or '-'})")
        if force_report:
            notify(f"🟢 변경 없음\n\n{table_text(data)}\n\n{TARGET_URL}")

    data["checked_at"] = now_kst()
    save_state(data)
    return len(changes)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, metavar="SEC", help="지정한 초 간격으로 계속 감시")
    ap.add_argument("--test", action="store_true", help="알림 채널 테스트 후 종료")
    ap.add_argument("--report", action="store_true", help="변경이 없어도 현재 표를 알림으로 보냄")
    ap.add_argument("--html-file", help="로컬 HTML 파일로 파싱만 검증")
    args = ap.parse_args()

    if args.test:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        print(f".env 경로 : {env_path}")
        print(f".env 존재 : {'예' if os.path.exists(env_path) else '아니오'}")
        for name in (
            "DISCORD_WEBHOOK_URL",
            "SLACK_WEBHOOK_URL",
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_CHAT_ID",
        ):
            v = os.environ.get(name)
            if v:
                shown = v[:38] + "…" if len(v) > 38 else v
                print(f"  {name} = {shown}")
            else:
                print(f"  {name} = (없음)")
        print(f"주시 대상 : {', '.join(WATCH)}")
        print(f"상태 파일 : {os.path.abspath(STATE_FILE)}")
        print("-" * 40)
        notify(f"🔔 규슈 응원할 감시기 알림 테스트 ({now_kst()})")
        return

    if args.html_file:
        with open(args.html_file, encoding="utf-8") as f:
            data = parse(f.read())
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    if args.loop:
        print(f"감시 시작: {args.loop}초 간격 / 주시 대상: {', '.join(WATCH)}")
        while True:
            try:
                check_once(force_report=args.report)
            except KeyboardInterrupt:
                print("\n종료합니다.")
                return
            except Exception as e:  # noqa: BLE001
                print(f"[{now_kst()}] 예외: {e}", file=sys.stderr)
            args.report = False  # 리포트는 첫 회만
            time.sleep(args.loop)
    else:
        check_once(force_report=args.report)


if __name__ == "__main__":
    main()
