"""青梅市施設予約管理システム(P-kashikan)のスクレイパー。

このシステムは検索結果(空き状況)が単純なHTTP POSTの応答には含まれず、
実ブラウザでの操作(目的で検索タブ→スポーツ→目的選択→検索→日送り)を
経由しないと表示されないことを実機検証で確認済み(生のrequestsによる
POST再現では常に空の検索画面が返ってくるだけだった)。そのため
Playwrightでブラウザ操作そのものを自動化して結果HTMLを取得する。

流れ:
  1. トップページを開き「目的から探す」→「スポーツ」→対象の目的(例: バレーボール)を選択
  2. 「検索」ボタンを押すと本日分の空き状況が表示される
  3. 「1日後」リンクを1日ずつクリックして日送りし、対象日に到達するたびに
     その時点のHTMLをパースする(直接特定日へジャンプする手段は未確認のため、
     lookahead日数分だけ日送りクリックを繰り返す)
"""
from __future__ import annotations

import re
import sys
import time
from typing import Iterable, List, Optional, Sequence

from bs4 import BeautifulSoup
from playwright.sync_api import Page, sync_playwright

from .base import Slot, build_time_label, find_qualifying_runs

MUNICIPALITY = "青梅市"
BASE_URL = "https://k4.p-kashikan.jp/ome-city/index.php"

MOKUTEKI_VOLLEYBALL = "037"

# 系サイトの凡例上「●」「○」「〇」はいずれも「空き」を意味する
# (● = 空き, 〇/○ = 空き(インターネット予約受付中) 等、状況により記号が変わる)
AVAILABLE_MARKS = {"○", "●", "〇"}

TIME_BLOCK_LABELS = [
    "9:00-12:00",
    "12:00-13:00",
    "13:00-15:00",
    "15:00-17:00",
    "17:00-19:00",
    "19:00-21:00",
]
TIME_BLOCK_STARTS = [label.split("-")[0] for label in TIME_BLOCK_LABELS]
TIME_BLOCK_ENDS = [label.split("-")[1] for label in TIME_BLOCK_LABELS]

DEFAULT_MIN_CONSECUTIVE = 3
DEFAULT_ALWAYS_NOTIFY_INDEXES: tuple[int, ...] = ()

_DATE_RE = re.compile(r"(\d{4}).*?年\s*(\d{1,2})月\s*(\d{1,2})日")


def fetch_all_slots(
    target_dates: Iterable[str],
    mokuteki_code: str = MOKUTEKI_VOLLEYBALL,
    min_consecutive: int = DEFAULT_MIN_CONSECUTIVE,
    always_notify_indexes: Iterable[int] = DEFAULT_ALWAYS_NOTIFY_INDEXES,
    facility_filters: Sequence[str] = (),
) -> List[Slot]:
    """target_dates(YYYYMMDDの集合)に含まれる日だけ空き(○/●)スロットを取得する。

    対象日以外の日もページ内部では通過するが、パース・保存は対象日のみ行う。
    1日は6つの時間帯枠(9-12/12-13/13-15/15-17/17-19/19-21)に分かれており、
    同一施設・同一日で min_consecutive 枠以上連続して空いている場合のみ、
    その連続区間をまとめて1件として返す(単発1〜2枠の空きは対象外)。
    """
    target_set = set(target_dates)
    if not target_set:
        return []
    last_target = max(target_set)

    slots: List[Slot] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(BASE_URL, timeout=30000)
            page.wait_for_load_state("networkidle")

            page.click("text=から探す")
            page.wait_for_timeout(400)
            page.click("text=スポーツ")
            page.wait_for_timeout(300)
            page.click(f'input[name="MokutekiCode"][value="{mokuteki_code}"]')
            page.wait_for_timeout(300)
            page.click('button[name="searchBtn"]')
            current = _wait_for_render(page, previous=None)
            if current is None:
                raise RuntimeError("検索結果の日付を取得できませんでした(サイト構造が変わった可能性があります)")

            guard = 0
            max_clicks = 400  # 無限ループ防止の安全弁
            while current is not None and current <= last_target and guard < max_clicks:
                if current in target_set:
                    slots.extend(
                        _parse_slots(
                            page.content(),
                            current,
                            min_consecutive,
                            always_notify_indexes,
                            facility_filters,
                        )
                    )
                if current == last_target:
                    break
                page.locator("text=1日後").first.click()
                next_date = _wait_for_render(page, previous=current)
                if next_date is None or next_date == current:
                    print(
                        f"日送りが{current}より進みませんでした。"
                        "予約システム側の表示可能期間の上限に達した可能性があるため打ち切ります。",
                        file=sys.stderr,
                    )
                    break
                current = next_date
                guard += 1
        finally:
            browser.close()

    return slots


def _wait_for_render(page: Page, previous: Optional[str], timeout_ms: int = 15000) -> Optional[str]:
    """日付表示が previous から変化し、かつ空き状況テーブルが描画されるまで待つ。

    クリック直後は一瞬テーブルが空になってから再描画されるため、固定sleepでは
    タイミング次第で描画前の空の状態を拾ってしまうことがある(実機検証で確認済み)。
    そのため日付とテーブル件数の両方が安定するまでポーリングする。
    """
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        current = _read_date(page)
        if current is not None and current != previous and page.locator("div.koma-area").count() > 0:
            return current
        page.wait_for_timeout(200)
    return _read_date(page)


def _read_date(page: Page) -> Optional[str]:
    text = page.inner_text("body")
    m = _DATE_RE.search(text)
    if not m:
        return None
    y, mo, d = m.groups()
    return f"{int(y):04d}{int(mo):02d}{int(d):02d}"


def _parse_slots(
    html: str,
    date_str: str,
    min_consecutive: int = DEFAULT_MIN_CONSECUTIVE,
    always_notify_indexes: Iterable[int] = DEFAULT_ALWAYS_NOTIFY_INDEXES,
    facility_filters: Sequence[str] = (),
) -> List[Slot]:
    soup = BeautifulSoup(html, "html.parser")
    slots: List[Slot] = []

    for area in soup.select("div.koma-area"):
        h3 = area.select_one("h3")
        group_name = h3.get_text(strip=True) if h3 else ""

        for table in area.select("table.koma-table"):
            if table.select_one("th"):
                continue  # ヘッダー行

            name_cell = table.select_one("td.name")
            if not name_cell:
                continue

            facility_raw = name_cell.get_text(separator=" ", strip=True)
            facility_name = facility_raw.split("(")[0].strip()
            facility = f"{group_name} {facility_name}".strip()
            if facility_filters and not any(f in facility for f in facility_filters):
                continue

            data_cells = table.select("td")[1:]
            available = [cell.get_text(strip=True) in AVAILABLE_MARKS for cell in data_cells]

            for start_idx, end_idx in find_qualifying_runs(
                available, min_consecutive, always_notify_indexes
            ):
                slots.append(
                    Slot(
                        municipality=MUNICIPALITY,
                        facility=facility,
                        date=date_str,
                        time_label=build_time_label(
                            TIME_BLOCK_STARTS, TIME_BLOCK_ENDS, start_idx, end_idx
                        ),
                    )
                )

    return slots
