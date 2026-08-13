from dataclasses import dataclass
from datetime import datetime

from famly_fetch.dates import parse_famly_datetime


@dataclass
class File:
    file_id: str
    url: str
    date: datetime
    name: str | None
    text: str | None

    @staticmethod
    def from_dict(
        data: dict, date_override: str, text_override: str | None = None
    ) -> "File":
        return File(
            file_id=data["fileId"],
            url=data["url"],
            date=parse_famly_datetime(date_override),
            name=data.get("filename"),
            text=text_override,
        )
