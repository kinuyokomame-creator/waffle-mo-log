"""公式のレストラン情報（ランド・シー）から各店の待ち時間を取得し、シート紐づけGASへ送る。

GitHub Actionsから15分おきに実行する。標準ライブラリのみ使用。
環境変数: RESTAURANT_ENDPOINT（GAS Web AppのURL）、RESTAURANT_TOKEN（GAS側と同じ合言葉）
--dry-run: 取得だけ行い、送信せずに送信内容（合言葉なし）を表示する。
"""
import gzip
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

PARKS = {
    "tdl": "https://www.tokyodisneyresort.jp/_/realtime/tdl_restaurant.json",
    "tds": "https://www.tokyodisneyresort.jp/_/realtime/tds_restaurant.json",
}
JST = timezone(timedelta(hours=9))
START_MIN, END_MIN = 8 * 60 + 30, 21 * 60  # 21:00の分まで
# ホテルのレストランは両パークの一覧に同じものが入り、待ち時間も出ない。観測したID帯で除く。
HOTEL_IDS = range(780, 800)

# ヘッダーなしの取得は公式側で無応答になる。ブラウザーの一覧ページからの読込みと同じ一式を送る。
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "ja,en;q=0.9",
    "Accept-Encoding": "gzip",
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}

# 待ち時間がない店は、公式の運営状況を短くして残す。
SHORT_STATUS = {
    "ディズニー・モバイルオーダー注文受付中": "モバイルオーダー受付中",
    "運営状況は施設でご確認ください。": "施設で確認",
    "運営・公演中止": "中止",
}


def in_window(now):
    minute = now.hour * 60 + now.minute
    return START_MIN <= minute <= END_MIN


def to_min(hhmm):
    try:
        h, m = str(hhmm).split(":")
        return int(h) * 60 + int(m)
    except ValueError:
        return None


def current_status(facility, now):
    """今の時刻を含む営業時間帯の運営状況。該当がなければ「営業時間外」。"""
    if facility.get("FacilityStatus"):
        return facility["FacilityStatus"]
    ymd, minute = now.strftime("%Y%m%d"), now.hour * 60 + now.minute
    hours = facility.get("operatingHours") or []
    for h in hours:
        start, end = to_min(h.get("OperatingHoursFrom")), to_min(h.get("OperatingHoursTo"))
        if h.get("OperatingHoursFromDate") != ymd or start is None or end is None:
            continue
        if start <= minute < end:
            return h.get("OperatingStatus") or "営業時間内"
    return "営業時間外" if hours else "情報なし"


def normalize(facility, now):
    """1店分を、シートの1マスに入れる値へまとめる。待ち時間は数値、それ以外は短い文字。"""
    lo, hi = facility.get("StandbyTimeMin"), facility.get("StandbyTimeMax")
    status = current_status(facility, now)
    if facility.get("StandbyTimeDispFlag") and lo not in (None, "") and status == "運営中":
        value = int(lo) if hi in (None, "", lo) else f"{lo}〜{hi}"
    else:
        value = SHORT_STATUS.get(status, status)
    return {
        "id": str(facility.get("FacilityID")),
        "name": facility.get("FacilityName") or "",
        "value": value,
        "updated": facility.get("UpdateTime") or "",
    }


def fetch_park(park, now):
    referer = f"https://www.tokyodisneyresort.jp/{park}/restaurant/list/"
    req = urllib.request.Request(
        f"{PARKS[park]}?t={int(now.timestamp() * 1000)}", headers={**HEADERS, "Referer": referer}
    )
    with urllib.request.urlopen(req, timeout=20) as res:
        body = res.read()
        if res.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        status = res.status
    data = json.loads(body.decode("utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("JSON配列ではありません")
    items = [
        normalize(f, now)
        for f in data
        if str(f.get("FacilityID", "")).isdigit() and int(f["FacilityID"]) not in HOTEL_IDS
    ]
    return status, items


def build_payload(now):
    payload = {"checkedAt": int(now.timestamp() * 1000), "source": "github-actions", "parks": {}}
    for park in PARKS:
        last_error = ""
        for attempt in range(2):
            try:
                status, items = fetch_park(park, now)
                payload["parks"][park] = {"ok": True, "httpStatus": status, "items": items}
                break
            except Exception as e:  # 失敗も記録に残すため、例外内容を送る
                last_error = f"{type(e).__name__}: {e}"[:300]
                if attempt == 0:
                    time.sleep(5)
        else:
            payload["parks"][park] = {"ok": False, "error": last_error}
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
    ok = all(p["ok"] for p in payload["parks"].values())
    if dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if ok else 1
    endpoint, token = os.environ.get("RESTAURANT_ENDPOINT"), os.environ.get("RESTAURANT_TOKEN")
    if not endpoint or not token:
        print("RESTAURANT_ENDPOINT / RESTAURANT_TOKEN が未設定です")
        return 1
    result = post(endpoint, {**payload, "token": token})
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") and ok else 1


if __name__ == "__main__":
    sys.exit(main())
