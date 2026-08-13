from dataclasses import dataclass
from datetime import datetime

from famly_fetch.dates import parse_famly_datetime


@dataclass
class Video:
    video_id: str
    url: str
    date: datetime
    text: str | None

    @staticmethod
    def from_dict(
        data: dict, date_override: str, text_override: str | None = None
    ) -> "Video | None":
        url = data.get("videoUrl")
        if not url:
            return None
        return Video(
            video_id=data["videoId"],
            url=url,
            date=parse_famly_datetime(date_override),
            text=text_override,
        )
