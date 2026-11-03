"""公式のレストラン情報からワッフル店（FacilityID 304）を取得し、シート紐づけGASへ送る。

GitHub Actionsから5分おきに実行する。標準ライブラリのみ使用。
環境変数: WAFFLE_ENDPOINT（GAS Web AppのURL）、WAFFLE_TOKEN（GAS側と同じ合言葉）
--dry-run: 取得だけ行い、送信せずに送信内容（合言葉なし）を表示する。
"""
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

SOURCE = "https://www.tokyodisneyresort.jp/_/realtime/tdl_restaurant.json"
FACILITY_ID = "304"
JST = timezone(timedelta(hours=9))
START_MIN, END_MIN = 9 * 60, 20 * 60 + 30  # 20:30の分まで

# ヘッダーなしの取得は公式側で無応答になる。ブラウザーの一覧ページからの読込みと同じ一式を送る。
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "ja,en;q=0.9",
    "Accept-Encoding": "gzip",
    "Referer": "https://www.tokyodisneyresort.jp/tdl/restaurant/list/",
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}


def in_window(now):
    minute = now.hour * 60 + now.minute
    return START_MIN <= minute <= END_MIN


def fetch_facility(now):
    req = urllib.request.Request(f"{SOURCE}?t={int(now.timestamp() * 1000)}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as res:
        body = res.read()
        if res.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        status = res.status
    data = json.loads(body.decode("utf-8"))
    if not isinstance(data, list):
        raise ValueError("JSON配列ではありません")
    matches = [x for x in data if str(x.get("FacilityID")) == FACILITY_ID]
    if len(matches) != 1:
        raise ValueError(f"FacilityID {FACILITY_ID} が{len(matches)}件")
    return status, matches[0]


def build_payload(now):
    payload = {"checkedAt": int(now.timestamp() * 1000), "source": "github-actions"}
    last_error = ""
    for attempt in range(2):
        try:
            status, facility = fetch_facility(now)
            payload.update(httpStatus=status, facility=facility)
            return payload
        except Exception as e:  # 失敗も記録に残すため、例外内容を送る
            last_error = f"{type(e).__name__}: {e}"[:300]
            if attempt == 0:
                time.sleep(5)
    payload["error"] = last_error
    return payload


def post(endpoint, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data, headers={"Content-Type": "application/json"})
    # GAS Web AppはPOST後に結果ページへリダイレクトする。urllibはGETで追従し結果を受け取る。
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.loads(res.read().decode("utf-8"))


def main():
    dry_run = "--dry-run" in sys.argv
    now = datetime.now(JST)
    if not dry_run and not in_window(now):
        print(f"記録時間外のため取得しません: {now:%H:%M} JST")
        return 0
    payload = build_payload(now)
    if dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if "error" not in payload else 1
    endpoint, token = os.environ.get("WAFFLE_ENDPOINT"), os.environ.get("WAFFLE_TOKEN")
    if not endpoint or not token:
        print("WAFFLE_ENDPOINT / WAFFLE_TOKEN が未設定です")
        return 1
    result = post(endpoint, {**payload, "token": token})
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
