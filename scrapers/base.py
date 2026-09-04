from dataclasses import dataclass


@dataclass(frozen=True)
class Slot:
    """1つの「施設 x 日付 x 時間帯」の空き情報。"""

    facility: str
    date: str  # "YYYYMMDD"
    time_label: str

    def key(self) -> str:
        return f"{self.facility}|{self.date}|{self.time_label}"

    def describe(self) -> str:
        y, m, d = self.date[:4], self.date[4:6], self.date[6:8]
        return f"{y}/{m}/{d} {self.time_label} - {self.facility}"
