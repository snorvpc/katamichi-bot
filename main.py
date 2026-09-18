import hashlib
import json
import os
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup


URL = os.getenv(
    "KATAMICHI_URL",
    "https://cp.toyota.jp/rentacar/?padid=ag270_fr_top_onewayma_m",
)

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

STATE_FILE = Path(
    os.getenv("STATE_FILE", "state.json")
)

FILTER_DEPARTURE = os.getenv(
    "FILTER_DEPARTURE", ""
).strip()

FILTER_ARRIVAL = os.getenv(
    "FILTER_ARRIVAL", ""
).strip()

FILTER_KEYWORD = os.getenv(
    "FILTER_KEYWORD", ""
).strip()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; KatamichiGoDiscordBot/1.0)"
    )
}


def normalize(value):
    return re.sub(
        r"\s+",
        " ",
        value or "",
    ).strip()


def fingerprint(record):
    raw = "\x1f".join(
        record.get(key, "")
        for key in (
            "departure",
            "arrival",
            "period",
            "car",
            "condition",
            "phone",
        )
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def parse_records(html):
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    lines = [
        normalize(value)
        for value in soup.stripped_strings
    ]

    records = []

    # 「出発」「店舗」「店舗名」という並びを
    # 案件の開始位置として探す
    starts = []

    for i in range(len(lines) - 2):

        if (
            lines[i] == "出発"
            and lines[i + 1] == "店舗"
        ):
            starts.append(i)

    print(
        f"案件開始位置: {len(starts)}件"
    )

    # 案件ごとに解析
    for n, start in enumerate(starts):

        if n + 1 < len(starts):
            end = starts[n + 1]
        else:
            end = len(lines)

        block = lines[start:end]

        if len(block) < 3:
            continue

        # -------------------------
        # 出発店舗
        # -------------------------

        departure = block[2]

        # -------------------------
        # 返却店舗
        # -------------------------

        arrival = ""

        for i in range(len(block) - 2):

            if (
                block[i] == "返却"
                and block[i + 1] == "店舗"
            ):
                arrival = block[i + 2]
                break

        # -------------------------
        # ラベルの値を取得
        # -------------------------

        def get_value(label):

            try:
                pos = block.index(label)

            except ValueError:
                return ""

            for value in block[pos + 1:]:

                if not value:
                    continue

                if value in {
                    "出発",
                    "返却",
                    "店舗",
                    "さらに詳細をみる",
                    "詳細を閉じる",
                }:
                    continue

                if value in {
                    "出発期間",
                    "車種",
                    "車両条件",
                    "予約電話番号",
                }:
                    return ""

                return value

            return ""

        period = get_value(
            "出発期間"
        )

        car = get_value(
            "車種"
        )

        condition = get_value(
            "車両条件"
        )

        # -------------------------
        # 電話番号
        # -------------------------

        phone = ""

        try:
            pos = block.index(
                "予約電話番号"
            )

            for value in block[pos + 1:]:

                if re.fullmatch(
                    r"\d{2,4}-\d{2,4}-\d{3,4}",
                    value,
                ):
                    phone = value
                    break

        except ValueError:
            pass

        # -------------------------
        # 案件として登録
        # -------------------------

        if (
            departure
            and arrival
            and period
            and car
            and condition
        ):

            record = {
                "departure": departure,
                "arrival": arrival,
                "period": period,
                "car": car,
                "condition": condition,
                "phone": phone,
            }

            records.append(record)

            print(
                "解析:",
                record,
            )

    # -------------------------
    # 重複除去
    # -------------------------

    unique = {}

    for record in records:

        unique[
            fingerprint(record)
        ] = record

    return list(
        unique.values()
    )


def matches(record):

    if FILTER_DEPARTURE:

        if (
            FILTER_DEPARTURE
            not in record.get(
                "departure",
                "",
            )
        ):
            return False

    if FILTER_ARRIVAL:

        if (
            FILTER_ARRIVAL
            not in record.get(
                "arrival",
                "",
            )
        ):
            return False

    if FILTER_KEYWORD:

        text = " ".join(
            record.values()
        ).lower()

        if (
            FILTER_KEYWORD.lower()
            not in text
        ):
            return False

    return True


def load_state():

    if not STATE_FILE.exists():
        return set()

    try:

        data = json.loads(
            STATE_FILE.read_text(
                encoding="utf-8"
            )
        )

        return set(data)

    except Exception:

        return set()


def save_state(state):

    STATE_FILE.write_text(
        json.dumps(
            list(state)[-5000:],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def discord_send(record):

    # コンパクトな通常メッセージとして送信
    message = (
        "🚗 **片道GO 新着**\n"
        f"出発：{record.get('departure', '不明')}\n"
        f"返却：{record.get('arrival', '不明')}\n"
        f"期間：{record.get('period', '不明')}　"
        f"車種：{record.get('car', '不明')}\n"
        f"条件：{record.get('condition', '不明')}　"
        f"予約：{record.get('phone', '不明')}\n"
        f"🔗 [片道GOを見る]({URL})"
    )

    payload = {
        "username": "片道GO通知",
        "content": message,
    }

    response = requests.post(
        WEBHOOK_URL,
        json=payload,
        timeout=20,
    )

    # Discordのレート制限
    if response.status_code == 429:

        try:
            retry_after = float(
                response.json().get(
                    "retry_after",
                    5,
                )
            )

        except Exception:
            retry_after = 5

        print(
            f"Discordレート制限。"
            f"{retry_after}秒待機します"
        )

        time.sleep(
            retry_after
        )

        response = requests.post(
            WEBHOOK_URL,
            json=payload,
            timeout=20,
        )

    response.raise_for_status()


def main():

    # -------------------------
    # 片道GOページ取得
    # -------------------------

    response = requests.get(
        URL,
        headers=HEADERS,
        timeout=30,
    )

    response.raise_for_status()

    # -------------------------
    # 案件解析
    # -------------------------

    records = [
        record
        for record in parse_records(
            response.text
        )
        if matches(record)
    ]

    print(
        f"取得: {len(records)}件"
    )

    # -------------------------
    # 現在掲載中の案件
    # -------------------------

    current = {
        fingerprint(record): record
        for record in records
    }

    # -------------------------
    # 過去の状態
    # -------------------------

    old = load_state()

    # -------------------------
    # 初回実行
    # -------------------------

    if not old:

        if current:

            first = next(
                iter(
                    current.values()
                )
            )

            print(
                "初回通知:",
                first,
            )

            discord_send(first)

        save_state(
            set(current)
        )

        print(
            "初回実行: "
            "現在掲載中の案件を記録しました"
        )

        return

    # -------------------------
    # 新着案件を探す
    # -------------------------

    new_ids = [
        record_id
        for record_id in current
        if record_id not in old
    ]

    # -------------------------
    # 新着通知
    # -------------------------

    for record_id in new_ids:

        record = current[
            record_id
        ]

        print(
            "新着:",
            record,
        )

        discord_send(record)

    # -------------------------
    # 現在の状態を保存
    # -------------------------

    save_state(
        set(current)
    )

    print(
        f"新着通知: {len(new_ids)}件"
    )


if __name__ == "__main__":
    main()
