"""Structured archive storage and Markdown rendering for Famly content."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from html import escape as html_escape
from html.parser import HTMLParser
from pathlib import Path


SCHEMA_VERSION = 1


def _parse_date(value: str | None) -> datetime:
    if not value:
        return datetime.max.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return datetime.max.replace(tzinfo=timezone.utc)


def _entry_sort_key(entry: dict) -> tuple:
    return (_parse_date(entry.get("date")), entry.get("entry_id", ""))


def _archive_title(payload: dict) -> str:
    """Name the archive after the child appearing most often in its entries."""

    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for entry in payload.get("entries", []):
        names_in_entry = set()
        for child in entry.get("children") or []:
            name = child.get("name") if isinstance(child, dict) else None
            if isinstance(name, dict):
                name = name.get("fullName") or name.get("firstName")
            if isinstance(name, str) and name.strip():
                names_in_entry.add(name.strip())
        for name in names_in_entry:
            first_seen.setdefault(name, len(first_seen))
            counts[name] = counts.get(name, 0) + 1

    if not counts:
        return "Famly Archive"
    child_name = max(counts, key=lambda name: (counts[name], -first_seen[name]))
    return f"{child_name}'s Famly Archive"


def _is_weekly_photo_entry(entry: dict) -> bool:
    """Return whether an entry is a standalone photo suitable for week grouping."""

    media = entry.get("media") or []
    return (
        entry.get("source") != "journey"
        and entry.get("kind") == "photo"
        and bool(media)
        and all(item.get("kind") == "photo" for item in media)
    )


def _week_start(value: str | None) -> datetime:
    parsed = _parse_date(value)
    return (parsed - timedelta(days=parsed.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _presentation_entries(entries: list[dict]) -> list[dict]:
    """Group standalone photos by calendar week without changing archive JSON."""

    tagged_photo_ids = {
        str(item["media_id"])
        for entry in entries
        if _is_weekly_photo_entry(entry)
        for item in entry.get("media", [])
        if item.get("kind") == "photo" and item.get("media_id")
    }
    feed_posts_by_photo: dict[str, list[dict]] = {}
    for entry in entries:
        if entry.get("source") != "feed" or not entry.get("text"):
            continue
        for item in entry.get("media", []):
            if item.get("kind") == "photo" and item.get("media_id"):
                feed_posts_by_photo.setdefault(str(item["media_id"]), []).append(entry)

    photo_weeks: dict[str, list[dict]] = {}
    presented = []
    for entry in entries:
        if not _is_weekly_photo_entry(entry):
            media = entry.get("media") or []
            media_ids = {
                str(item["media_id"])
                for item in media
                if item.get("kind") == "photo" and item.get("media_id")
            }
            is_weekly_parent_context = (
                entry.get("source") == "feed"
                and bool(media_ids)
                and media_ids <= tagged_photo_ids
                and all(item.get("kind") == "photo" for item in media)
            )
            if is_weekly_parent_context:
                continue
            presented.append(entry)
            continue
        parent_posts = []
        seen_parent_ids = set()
        for item in entry.get("media", []):
            for parent_post in feed_posts_by_photo.get(str(item.get("media_id")), []):
                parent_id = parent_post.get("entry_id")
                if parent_id in seen_parent_ids:
                    continue
                seen_parent_ids.add(parent_id)
                parent_posts.append(parent_post)
        if parent_posts:
            entry = dict(entry)
            entry["presentation_parent_posts"] = parent_posts
        start = _week_start(entry.get("date"))
        photo_weeks.setdefault(start.isoformat(), []).append(entry)

    for start, week_entries in photo_weeks.items():
        presented.append(
            {
                "entry_id": f"photo_week:{start}",
                "date": start,
                "presentation": "photo_week",
                "entries": sorted(week_entries, key=_entry_sort_key),
            }
        )
    return sorted(presented, key=_entry_sort_key)


class ArchiveExporter:
    """Build and persist a restart-safe structured Famly archive."""

    def __init__(self, root: Path, json_path: Path | None = None):
        self.root = root.resolve()
        self.json_path = (json_path or self.root / "archive.json").resolve()
        self.markdown_path = self.json_path.with_suffix(".md")
        self.html_path = self.json_path.with_suffix(".html")
        self._entries: dict[str, dict] = {}
        self._load_existing()

    def _load_existing(self):
        if not self.json_path.exists():
            return
        try:
            payload = json.loads(self.json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if payload.get("schema_version") != SCHEMA_VERSION:
            return
        for entry in payload.get("entries", []):
            if entry.get("entry_id"):
                self._entries[entry["entry_id"]] = entry

    @staticmethod
    def entry_id(source: str, remote_id: str | None, *fallback_parts) -> str:
        """Return a stable namespaced ID, even when an API object has no ID."""

        if remote_id:
            return f"{source}:{remote_id}"
        material = json.dumps(fallback_parts, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
        return f"{source}:generated-{digest}"

    def media(
        self,
        media_id: str,
        kind: str,
        path: Path,
        filename: str | None = None,
    ) -> dict:
        try:
            local_path = path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            local_path = Path(os.path.relpath(path.resolve(), self.root)).as_posix()
        item = {
            "media_id": str(media_id),
            "kind": kind,
            "local_path": local_path,
        }
        if filename:
            item["filename"] = filename
        return item

    def media_index(self, source: str, kind: str) -> dict[str, dict]:
        """Return archived media from one source, keyed by stable media ID."""

        return {
            str(item["media_id"]): item
            for entry in self._entries.values()
            if entry.get("source") == source
            for item in entry.get("media", [])
            if item.get("kind") == kind and item.get("media_id")
        }

    def add_entry(
        self,
        *,
        entry_id: str,
        source: str,
        kind: str,
        date: str,
        text: str | None,
        author: str | None,
        children: list[dict] | None = None,
        media: list[dict] | None = None,
        published_at: str | None = None,
        observed_at: str | None = None,
        next_step: str | None = None,
        assessment: dict | None = None,
        metadata: dict | None = None,
    ):
        new_entry = {
            "entry_id": entry_id,
            "source": source,
            "kind": kind,
            "date": date,
            "published_at": published_at,
            "observed_at": observed_at,
            "author": {"name": author} if author else None,
            "children": children or [],
            "text": text or None,
            "next_step": next_step or None,
            "assessment": assessment or None,
            "media": media or [],
            "metadata": metadata or {},
        }

        existing = self._entries.get(entry_id)
        if existing:
            by_media_id = {
                (item.get("kind"), item.get("media_id")): item
                for item in existing.get("media", [])
            }
            for item in new_entry["media"]:
                by_media_id[(item.get("kind"), item.get("media_id"))] = item
            new_entry["media"] = list(by_media_id.values())
            for field in (
                "text",
                "author",
                "published_at",
                "observed_at",
                "next_step",
                "assessment",
                "children",
                "metadata",
            ):
                if not new_entry.get(field) and existing.get(field):
                    new_entry[field] = existing[field]

        self._entries[entry_id] = new_entry

    def _output_entries(self) -> list[dict]:
        """Suppress a tagged photo only when a Journey entry owns it."""

        journey_media_ids = {
            item.get("media_id")
            for entry in self._entries.values()
            if entry.get("source") == "journey"
            for item in entry.get("media", [])
            if item.get("kind") == "photo"
        }
        entries = []
        for entry in self._entries.values():
            if entry.get("source") == "tagged_photo" and any(
                item.get("media_id") in journey_media_ids
                for item in entry.get("media", [])
            ):
                continue
            entries.append(entry)
        return sorted(entries, key=_entry_sort_key)

    def save(self):
        self.json_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "entries": self._output_entries(),
        }
        temporary_path = self.json_path.with_suffix(self.json_path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(self.json_path)
        write_markdown(self.json_path, self.markdown_path)


class _TextToMarkdownParser(HTMLParser):
    """Small, dependency-free converter for the HTML Famly sometimes returns."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.href_stack: list[str | None] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in {"script", "style"}:
            self.ignored_depth += 1
            return
        if self.ignored_depth:
            return
        if tag in {"p", "div", "br", "blockquote"}:
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in {"strong", "b"}:
            self.parts.append("**")
        elif tag in {"em", "i"}:
            self.parts.append("*")
        elif tag == "a":
            self.parts.append("[")
            self.href_stack.append(dict(attrs).get("href"))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"script", "style"}:
            if self.ignored_depth:
                self.ignored_depth -= 1
            return
        if self.ignored_depth:
            return
        if tag in {"p", "div", "blockquote", "li", "ul", "ol"}:
            self.parts.append("\n")
        elif tag in {"strong", "b"}:
            self.parts.append("**")
        elif tag in {"em", "i"}:
            self.parts.append("*")
        elif tag == "a":
            href = self.href_stack.pop() if self.href_stack else None
            self.parts.append(f"]({href})" if href else "]")

    def handle_data(self, data):
        if not self.ignored_depth:
            self.parts.append(data)

    def markdown(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def text_to_markdown(text: str) -> str:
    if not re.search(r"</?[a-zA-Z][^>]*>", text):
        return text.strip()
    parser = _TextToMarkdownParser()
    parser.feed(text)
    return parser.markdown()


def _display_date(value: str | None) -> str:
    parsed = _parse_date(value)
    if parsed.year == datetime.max.year:
        return value or "Unknown date"
    return parsed.strftime("%d %B %Y, %H:%M")


def _display_day(value: str | None) -> str:
    parsed = _parse_date(value)
    if parsed.year == datetime.max.year:
        return value or "Unknown date"
    return parsed.strftime("%d %B %Y")


def _display_week(value: str | None) -> str:
    return f"Week of {_display_day(value)}"


def _weekly_photo_items(group: dict) -> list[tuple[dict, dict]]:
    return [
        (entry, item)
        for entry in group.get("entries", [])
        for item in entry.get("media", [])
        if item.get("kind") == "photo"
    ]


def _weekly_parent_posts(group: dict) -> list[dict]:
    posts = []
    seen = set()
    for entry in group.get("entries", []):
        for post in entry.get("presentation_parent_posts", []):
            identity = post.get("entry_id") or post.get("text")
            if identity in seen:
                continue
            seen.add(identity)
            posts.append(post)
    return sorted(posts, key=_entry_sort_key)


def _weekly_children(group: dict) -> list[str]:
    names = {
        str(child.get("name"))
        for entry in group.get("entries", [])
        for child in entry.get("children", [])
        if child.get("name")
    }
    return sorted(names)


def _gallery_id(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"gallery-{digest}"


def _entry_category(entry: dict) -> str:
    if entry.get("presentation") == "photo_week":
        return "weekly-photos"
    kind = str(entry.get("kind") or "").lower().replace("-", "_")
    review_markers = (
        "assessment",
        "progress",
        "review",
        "summative",
        "two_year",
        "up_to_speed",
    )
    if entry.get("assessment") or any(marker in kind for marker in review_markers):
        return "assessment-review"
    if entry.get("source") == "journey":
        return "observation"
    return "other"


def _label(value: str) -> str:
    return value.replace("_", " ").strip().title()


def _has_assessment_content(assessment: dict | None) -> bool:
    if not assessment:
        return False
    if (assessment.get("setting") or {}).get("title"):
        return True
    for area_result in assessment.get("areas") or []:
        area = area_result.get("area") or {}
        if any(
            value not in (None, "")
            for value in (
                area.get("title"),
                (area_result.get("assessment_option") or {}).get("label"),
                (area_result.get("age_band") or {}).get("label"),
                area_result.get("refinement"),
                area_result.get("note"),
            )
        ):
            return True
    return any(
        field.get("value") not in (None, "")
        for field in assessment.get("custom_fields") or []
    )


def _assessment_lines(assessment: dict) -> list[str]:
    lines = ["### Assessment", ""]
    setting = assessment.get("setting") or {}
    if setting.get("title"):
        lines.extend([f"Configuration: {setting['title']}", ""])

    areas = assessment.get("areas") or []
    for area_result in areas:
        area = area_result.get("area") or {}
        title = area.get("title") or "Assessment area"
        option = (area_result.get("assessment_option") or {}).get("label")
        age_band = (area_result.get("age_band") or {}).get("label")
        refinement = area_result.get("refinement")
        result = option or refinement or age_band
        lines.append(f"- **{title}:** {result}" if result else f"- **{title}**")
        if age_band and age_band != result:
            lines.append(f"  - Age band: {age_band}")
        if area_result.get("note"):
            note = text_to_markdown(str(area_result["note"])).replace("\n", " ")
            lines.append(f"  - Note: {note}")
    if areas:
        lines.append("")

    custom_fields = assessment.get("custom_fields") or []
    if custom_fields:
        lines.extend(["#### Additional assessment information", ""])
        for field in custom_fields:
            label = field.get("label") or "Field"
            value = field.get("value")
            if value not in (None, ""):
                lines.append(f"- **{label}:** {value}")
        lines.append("")
    return lines


def _relative_media_path(item: dict, json_path: Path, output_path: Path) -> str | None:
    local_path = item.get("local_path")
    if not local_path:
        return None
    absolute_media_path = (json_path.parent / local_path).resolve()
    relative_media_path = Path(
        os.path.relpath(absolute_media_path, output_path.parent.resolve())
    ).as_posix()
    return relative_media_path.replace(">", "%3E")


def _photo_grid_lines(
    items: list[dict], json_path: Path, output_path: Path
) -> list[str]:
    photos = []
    for index, item in enumerate(items, start=1):
        path = _relative_media_path(item, json_path, output_path)
        if path:
            photos.append((index, path))

    if not photos:
        return []
    if len(photos) == 1:
        index, path = photos[0]
        return [f"![Photo {index}](<{path}>)", ""]

    def image_html(photo):
        index, path = photo
        escaped_path = html_escape(path, quote=True)
        return (
            f'<a href="{escaped_path}"><img src="{escaped_path}" '
            f'alt="Photo {index}" style="width:100%;height:auto;display:block;"></a>'
        )

    lines = ['<table role="presentation" style="border-collapse:collapse;width:100%;">']
    remaining = photos
    if len(photos) >= 3:
        lines.extend(
            [
                "  <tr>",
                f'    <td colspan="2" style="border:0;padding:4px;">{image_html(photos[0])}</td>',
                "  </tr>",
            ]
        )
        remaining = photos[1:]

    for offset in range(0, len(remaining), 2):
        row = remaining[offset : offset + 2]
        lines.append("  <tr>")
        if len(row) == 1:
            lines.append(
                f'    <td colspan="2" style="border:0;padding:4px;">{image_html(row[0])}</td>'
            )
        else:
            for photo in row:
                lines.append(
                    f'    <td width="50%" valign="top" style="border:0;padding:4px;">{image_html(photo)}</td>'
                )
        lines.append("  </tr>")
    lines.extend(["</table>", ""])
    return lines


def render_markdown(payload: dict, json_path: Path, output_path: Path) -> str:
    lines = [f"# {_archive_title(payload)}", ""]
    generated_at = payload.get("generated_at")
    if generated_at:
        lines.extend([f"Generated: {_display_date(generated_at)}", ""])

    entries = sorted(payload.get("entries", []), key=_entry_sort_key)
    for entry in _presentation_entries(entries):
        if entry.get("presentation") == "photo_week":
            photo_items = _weekly_photo_items(entry)
            lines.extend([f"## {_display_week(entry.get('date'))}", ""])
            children = ", ".join(_weekly_children(entry))
            if children:
                lines.append(f"- Child: {children}")
            lines.extend(
                [
                    "- Type: Weekly photos",
                    f"- Photos: {len(photo_items)}",
                    "",
                ]
            )
            parent_posts = _weekly_parent_posts(entry)
            if parent_posts:
                lines.extend(["### This week", ""])
                for post in parent_posts:
                    lines.extend([text_to_markdown(str(post["text"])), ""])
                    author = (post.get("author") or {}).get("name")
                    attribution = f"Feed post from {_display_date(post.get('date'))}"
                    if author:
                        attribution += f", by {author}"
                    lines.extend([f"*{attribution}*", ""])
            lines.extend(["### Photos", ""])
            lines.extend(
                _photo_grid_lines(
                    [item for _, item in photo_items], json_path, output_path
                )
            )
            lines.extend(["### Photo details", ""])
            for index, (photo_entry, _) in enumerate(photo_items, start=1):
                detail = f"- Photo {index}: {_display_date(photo_entry.get('date'))}"
                author = (photo_entry.get("author") or {}).get("name")
                if author:
                    detail += f", by {author}"
                lines.append(detail)
                if photo_entry.get("text"):
                    caption = text_to_markdown(str(photo_entry["text"])).replace(
                        "\n", " "
                    )
                    lines.append(f"  - {caption}")
            lines.extend(["", "---", ""])
            continue

        lines.extend([f"## {_display_day(entry.get('date'))}", ""])

        details = []
        children = ", ".join(
            child.get("name", "")
            for child in entry.get("children", [])
            if child.get("name")
        )
        if children:
            details.append(f"- Child: {children}")
        author = (entry.get("author") or {}).get("name")
        if author:
            details.append(f"- Written by: {author}")
        details.append(f"- Posted: {_display_date(entry.get('date'))}")
        if entry.get("observed_at"):
            details.append(f"- Observed: {_display_date(entry['observed_at'])}")
        if entry.get("published_at"):
            details.append(f"- Published: {_display_date(entry['published_at'])}")
        kind = entry.get("kind")
        if kind:
            details.append(f"- Type: {_label(kind)}")
        lines.extend(details)
        lines.append("")

        if entry.get("text"):
            lines.extend([text_to_markdown(entry["text"]), ""])

        if _has_assessment_content(entry.get("assessment")):
            lines.extend(_assessment_lines(entry["assessment"]))

        if entry.get("next_step"):
            lines.extend(
                ["### What's next", "", text_to_markdown(entry["next_step"]), ""]
            )

        grouped_media: dict[str, list[dict]] = {}
        for item in entry.get("media", []):
            grouped_media.setdefault(item.get("kind", "file"), []).append(item)

        for media_kind in ("photo", "video", "file"):
            items = grouped_media.get(media_kind, [])
            if not items:
                continue
            heading = {"photo": "Photos", "video": "Videos", "file": "Files"}[
                media_kind
            ]
            lines.extend([f"### {heading}", ""])
            if media_kind == "photo":
                lines.extend(_photo_grid_lines(items, json_path, output_path))
                continue
            for index, item in enumerate(items, start=1):
                relative_media_path = _relative_media_path(item, json_path, output_path)
                if not relative_media_path:
                    continue
                name = item.get("filename") or Path(relative_media_path).name
                lines.append(f"- [{name}](<{relative_media_path}>)")
            lines.append("")

        lines.extend(["---", ""])

    if not entries:
        lines.extend(["No archived entries were found.", ""])
    return "\n".join(lines).rstrip() + "\n"


def write_markdown(json_path: Path, output_path: Path | None = None) -> Path:
    json_path = json_path.resolve()
    output_path = (output_path or json_path.with_suffix(".md")).resolve()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_markdown(payload, json_path, output_path), encoding="utf-8"
    )
    return output_path
