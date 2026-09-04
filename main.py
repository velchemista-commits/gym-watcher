"""体育館空き通知アプリ - メインエントリポイント。

config.yaml の設定に従い、対象日(土日祝)の空き状況を取得し、
前回実行時からの新規空きスロットがあればメール通知する。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import jpholiday
import yaml

from scrapers import ome
from scrapers.base import Slot

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.yaml"
STATE_PATH = ROOT / "state.json"

JST = ZoneInfo("Asia/Tokyo")


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_state() -> set[str]:
    if not STATE_PATH.exists():
        return set()
    with open(STATE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return set(data.get("available_slot_keys", []))


def save_state(slot_keys: set[str]) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {
                "updated_at": time_now_iso(),
                "available_slot_keys": sorted(slot_keys),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
        f.write("\n")


def time_now_iso() -> str:
    return datetime.now(JST).isoformat()


def target_dates(config: dict) -> list[str]:
    weekdays_cfg = config["target_weekdays"]
    lookahead = config["lookahead_days"]
    today = datetime.now(JST).date()

    dates = []
    for offset in range(1, lookahead + 1):
        d = today + timedelta(days=offset)
        is_holiday = jpholiday.is_holiday(d)
        is_saturday = d.weekday() == 5
        is_sunday = d.weekday() == 6

        if (
            (is_saturday and weekdays_cfg.get("saturday"))
            or (is_sunday and weekdays_cfg.get("sunday"))
            or (is_holiday and weekdays_cfg.get("holiday"))
        ):
            dates.append(d.strftime("%Y%m%d"))
    return dates


def collect_current_slots(config: dict) -> list[Slot]:
    target = config["target"]

    if target["municipality"] != "ome":
        raise NotImplementedError(f"未対応の自治体: {target['municipality']}")

    dates = target_dates(config)
    min_consecutive = target.get("min_consecutive_blocks", ome.DEFAULT_MIN_CONSECUTIVE)
    print(f"対象日数: {len(dates)}日", file=sys.stderr)

    return ome.fetch_all_slots(dates, target["mokuteki_code"], min_consecutive)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="メール送信・state.json保存を行わず、結果をコンソール表示のみ行う",
    )
    args = parser.parse_args()

    config = load_config()
    current_slots = collect_current_slots(config)
    current_keys = {s.key() for s in current_slots}
    key_to_slot = {s.key(): s for s in current_slots}

    previous_keys = load_state()
    new_keys = current_keys - previous_keys
    new_slots = [key_to_slot[k] for k in new_keys]

    print(f"現在の空き件数: {len(current_slots)}", file=sys.stderr)
    print(f"新規に空きが出た件数: {len(new_slots)}", file=sys.stderr)
    for s in sorted(new_slots, key=lambda s: (s.date, s.time_label)):
        print(f"  NEW: {s.describe()}", file=sys.stderr)

    if args.dry_run:
        print("(--dry-run のためメール送信・state保存はスキップしました)", file=sys.stderr)
        return

    if new_slots:
        from notifier import send_new_slot_email

        send_new_slot_email(new_slots, config["target"]["reservation_url"])
        print("通知メールを送信しました", file=sys.stderr)

    save_state(current_keys)


if __name__ == "__main__":
    main()
