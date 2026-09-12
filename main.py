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
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import jpholiday
import yaml

from scrapers import adachi, arakawa, ichikawa, katsushika, koto, matsudo, ome
from scrapers.base import Slot

SCRAPERS = {
    "ome": ome,
    "koto": koto,
    "arakawa": arakawa,
    "ichikawa": ichikawa,
    "katsushika": katsushika,
    "matsudo": matsudo,
    "adachi": adachi,
}

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


def collect_current_slots(
    config: dict,
    on_target_done: Optional[Callable[[dict, list[Slot]], None]] = None,
) -> list[Slot]:
    """全自治体を順に確認する。

    on_target_done を渡すと、1自治体分の取得が終わるたびに
    (target設定, その自治体のスロット一覧) で呼び出される。自治体ごとに
    確認でき次第すぐ通知したい場合はここでメール送信を行う。
    """
    dates = target_dates(config)
    print(f"対象日数: {len(dates)}日", file=sys.stderr)

    all_slots: list[Slot] = []
    for target in config["targets"]:
        key = target["municipality"]
        scraper = SCRAPERS.get(key)
        if scraper is None:
            raise NotImplementedError(f"未対応の自治体: {key}")

        name = target.get("name", key)
        try:
            slots = scraper.fetch_all_slots(
                dates,
                target["sport_code"],
                target.get("min_consecutive_blocks", scraper.DEFAULT_MIN_CONSECUTIVE),
                target.get("always_notify_block_indexes", scraper.DEFAULT_ALWAYS_NOTIFY_INDEXES),
                target.get("facility_filters", ()),
            )
        except Exception as e:
            # 1自治体が落ちても他の自治体の通知は続ける
            print(f"{name}: 取得に失敗しました: {e}", file=sys.stderr)
            continue

        excludes = target.get("facility_excludes", ())
        if excludes:
            slots = [s for s in slots if not any(x in s.facility for x in excludes)]

        print(f"{name}: {len(slots)}件", file=sys.stderr)
        if on_target_done:
            on_target_done(target, slots)
        all_slots.extend(slots)

    return all_slots


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="メール送信・state.json保存を行わず、結果をコンソール表示のみ行う",
    )
    args = parser.parse_args()

    config = load_config()
    previous_keys = load_state()
    total_new = 0

    def handle_target(target: dict, slots: list[Slot]) -> None:
        nonlocal total_new
        name = target.get("name", target["municipality"])
        key_to_slot = {s.key(): s for s in slots}
        new_keys = set(key_to_slot) - previous_keys
        if not new_keys:
            return

        new_slots = [key_to_slot[k] for k in new_keys]
        total_new += len(new_slots)
        print(f"{name}: 新規に空きが出た件数: {len(new_slots)}", file=sys.stderr)
        for s in sorted(new_slots, key=lambda s: (s.date, s.time_label)):
            print(f"  NEW: {s.describe()}", file=sys.stderr)

        if args.dry_run:
            return

        from notifier import send_new_slot_email

        send_new_slot_email(new_slots, {name: target["reservation_url"]}, subject_prefix=name)
        print(f"{name}: 通知メールを送信しました", file=sys.stderr)

    current_slots = collect_current_slots(config, on_target_done=handle_target)
    current_keys = {s.key() for s in current_slots}

    print(f"現在の空き件数(合計): {len(current_slots)}", file=sys.stderr)
    print(f"新規に空きが出た件数(合計): {total_new}", file=sys.stderr)

    if args.dry_run:
        print("(--dry-run のためメール送信・state保存はスキップしました)", file=sys.stderr)
        return

    save_state(current_keys)


if __name__ == "__main__":
    main()
