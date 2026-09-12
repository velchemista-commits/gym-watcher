from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple


@dataclass(frozen=True)
class Slot:
    """1つの「施設 x 日付 x 連続時間帯」の空き情報。"""

    municipality: str
    facility: str
    date: str  # "YYYYMMDD"
    time_label: str

    def key(self) -> str:
        return f"{self.municipality}|{self.facility}|{self.date}|{self.time_label}"

    def describe(self) -> str:
        y, m, d = self.date[:4], self.date[4:6], self.date[6:8]
        return f"{y}/{m}/{d} {self.time_label} - {self.municipality} {self.facility}"


def find_qualifying_runs(
    available: Sequence[bool],
    min_consecutive: int,
    always_notify_indexes: Iterable[int] = (),
) -> List[Tuple[int, int]]:
    """空き枠の連続区間のうち、通知対象となるものを (開始idx, 終了idx) で返す。

    通知対象の条件はどちらか:
      - min_consecutive 枠以上連続している
      - always_notify_indexes の枠を含む(1枠だけでも通知したい枠の指定)

    区間は最大まで伸ばした状態で返す(重複する短い区間は返さない)。
    """
    always = set(always_notify_indexes)

    runs: List[Tuple[int, int]] = []
    start = None
    for idx, is_available in enumerate(available):
        if is_available:
            if start is None:
                start = idx
        else:
            if start is not None:
                runs.append((start, idx - 1))
            start = None
    if start is not None:
        runs.append((start, len(available) - 1))

    qualifying = []
    for run_start, run_end in runs:
        length = run_end - run_start + 1
        covers_always = any(i in always for i in range(run_start, run_end + 1))
        if length >= min_consecutive or covers_always:
            qualifying.append((run_start, run_end))
    return qualifying


def build_time_label(
    starts: Sequence[str],
    ends: Sequence[str],
    run_start: int,
    run_end: int,
) -> str:
    """連続区間の時間表記を作る。

    枠が時間的に連続していれば "13:00-19:00" のような範囲に、
    枠間に休憩などの切れ目があれば "9:00-12:00 + 13:00-17:00" のように連結する。
    """
    if run_start >= len(starts) or run_end >= len(ends):
        return f"枠{run_start + 1}-枠{run_end + 1}"

    parts = []
    segment_start = starts[run_start]
    for i in range(run_start, run_end + 1):
        if i < run_end and ends[i] == starts[i + 1]:
            continue
        parts.append(f"{segment_start}-{ends[i]}")
        if i < run_end:
            segment_start = starts[i + 1]
    return " + ".join(parts)
