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


class ExclusionFileError(ValueError):
    """Raised when an archive exclusion file cannot be safely applied."""


def exclusion_path(json_path: Path) -> Path:
    """Return the automatic exclusion-list path for an archive JSON file."""

    return json_path.resolve().with_suffix(".exclude.json")


def load_excluded_entry_ids(json_path: Path) -> set[str]:
    """Load stable entry IDs excluded from this archive's generated outputs."""

    path = exclusion_path(json_path)
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExclusionFileError(f"Could not read exclusion file: {path}") from error

    if not isinstance(payload, dict):
        raise ExclusionFileError(f"Exclusion file must contain a JSON object: {path}")
    entry_ids = payload.get("excluded_entry_ids")
    if not isinstance(entry_ids, list) or not all(
        isinstance(entry_id, str) and entry_id.strip() for entry_id in entry_ids
    ):
        raise ExclusionFileError(
            f'Exclusion file needs an "excluded_entry_ids" list of strings: {path}'
        )
    return {entry_id.strip() for entry_id in entry_ids}


def _payload_without_excluded_entries(payload: dict, json_path: Path) -> dict:
    excluded_entry_ids = load_excluded_entry_ids(json_path)
    if not excluded_entry_ids:
        return payload
    filtered = dict(payload)
    filtered["entries"] = [
        entry
        for entry in payload.get("entries", [])
        if entry.get("entry_id") not in excluded_entry_ids
    ]
    return filtered


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
        write_html(self.json_path, self.html_path)


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


def _html_text(value) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    text = text_to_markdown(value)
    paragraphs = []
    for paragraph in re.split(r"\n{2,}", text):
        escaped = html_escape(paragraph.strip()).replace("\n", "<br>")
        escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
        escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", escaped)
        if escaped:
            paragraphs.append(f"<p>{escaped}</p>")
    return "".join(paragraphs)


def _html_assessment(assessment: dict) -> str:
    parts = ['<section class="assessment"><h3>Assessment</h3>']
    setting = assessment.get("setting") or {}
    if setting.get("title"):
        parts.append(
            '<p class="configuration">Configuration: '
            f"{html_escape(str(setting['title']))}</p>"
        )

    for area_result in assessment.get("areas") or []:
        area = area_result.get("area") or {}
        title = html_escape(str(area.get("title") or "Assessment area"))
        option = (area_result.get("assessment_option") or {}).get("label")
        age_band = (area_result.get("age_band") or {}).get("label")
        refinement = area_result.get("refinement")
        result = option or refinement or age_band
        parts.append('<div class="assessment-row">')
        parts.append(f"<h4>{title}</h4>")
        if result:
            parts.append(f'<p class="result">{html_escape(str(result))}</p>')
        if age_band and age_band != result:
            parts.append(
                f'<p class="assessment-note">Age band: {html_escape(str(age_band))}</p>'
            )
        if area_result.get("note"):
            parts.append(
                f'<div class="assessment-note">{_html_text(area_result["note"])}</div>'
            )
        parts.append("</div>")

    custom_fields = assessment.get("custom_fields") or []
    if custom_fields:
        parts.append("<h4>Additional assessment information</h4>")
        parts.append('<dl class="details">')
        for field in custom_fields:
            value = field.get("value")
            if value in (None, ""):
                continue
            label = html_escape(str(field.get("label") or "Field"))
            parts.extend([f"<dt>{label}</dt>", f"<dd>{_html_text(value)}</dd>"])
        parts.append("</dl>")
    parts.append("</section>")
    return "".join(parts)


def _html_media(entry: dict, json_path: Path, output_path: Path) -> str:
    grouped: dict[str, list[dict]] = {}
    for item in entry.get("media", []):
        grouped.setdefault(item.get("kind", "file"), []).append(item)

    parts = []
    photo_paths = []
    for item in grouped.get("photo", []):
        path = _relative_media_path(item, json_path, output_path)
        if path:
            photo_paths.append(path)
    if photo_paths:
        parts.append('<div class="photos">')
        gallery_id = _gallery_id(str(entry.get("entry_id") or entry.get("date")))
        gallery_caption = _display_date(entry.get("date"))
        remaining_count = len(photo_paths) - 1 if len(photo_paths) >= 3 else 0
        for index, path in enumerate(photo_paths):
            classes = ["photo"]
            if len(photo_paths) == 1:
                classes.append("wide")
            if len(photo_paths) >= 3 and index == 0:
                classes.append("hero")
            if (
                len(photo_paths) >= 3
                and remaining_count % 2 == 1
                and index == len(photo_paths) - 1
            ):
                classes.append("wide")
            escaped_path = html_escape(path, quote=True)
            class_name = " ".join(classes)
            parts.append(
                f'<a class="{class_name}" href="{escaped_path}" '
                f'data-gallery="{gallery_id}" '
                f'data-caption="{html_escape(gallery_caption, quote=True)}">'
                f'<img src="{escaped_path}" alt="Photo {index + 1}" loading="lazy">'
                "</a>"
            )
        parts.append("</div>")

    videos = grouped.get("video", [])
    if videos:
        parts.append('<section class="attachments"><h3>Videos</h3>')
        for item in videos:
            path = _relative_media_path(item, json_path, output_path)
            if path:
                escaped_path = html_escape(path, quote=True)
                parts.append(
                    f'<video controls preload="metadata" src="{escaped_path}"></video>'
                )
        parts.append("</section>")

    files = grouped.get("file", [])
    if files:
        parts.append('<section class="attachments"><h3>Files</h3><ul>')
        for item in files:
            path = _relative_media_path(item, json_path, output_path)
            if path:
                escaped_path = html_escape(path, quote=True)
                name = html_escape(str(item.get("filename") or Path(path).name))
                parts.append(f'<li><a href="{escaped_path}">{name}</a></li>')
        parts.append("</ul></section>")
    return "".join(parts)


def _html_photo_week(group: dict, json_path: Path, output_path: Path) -> str:
    photo_items = _weekly_photo_items(group)
    gallery_id = _gallery_id(str(group.get("entry_id")))
    parts = [
        '<article class="post photo-week" data-entry-category="weekly-photos">',
        '<header class="post-header">',
    ]
    parts.append(f"<h2>{html_escape(_display_week(group.get('date')))}</h2>")
    parts.append('<span class="badge">Weekly photos</span></header>')
    parts.append('<div class="meta">')
    children = ", ".join(_weekly_children(group))
    if children:
        parts.append(f"<span>Child: {html_escape(children)}</span>")
    parts.append(f"<span>{len(photo_items)} photos</span></div>")

    parent_posts = _weekly_parent_posts(group)
    if parent_posts:
        parts.append('<section class="weekly-summary"><h3>This week</h3>')
        for post in parent_posts:
            parts.append('<div class="weekly-post-description">')
            parts.append(_html_text(post["text"]))
            attribution = f"Feed post from {_display_date(post.get('date'))}"
            author = (post.get("author") or {}).get("name")
            if author:
                attribution += f", by {author}"
            parts.append(
                f'<p class="weekly-post-attribution">{html_escape(attribution)}</p>'
            )
            parts.append("</div>")
        parts.append("</section>")

    parts.append('<div class="photos weekly-photos">')
    remaining_count = len(photo_items) - 1 if len(photo_items) >= 3 else 0
    for index, (entry, item) in enumerate(photo_items):
        path = _relative_media_path(item, json_path, output_path)
        if not path:
            continue
        classes = ["weekly-photo"]
        if len(photo_items) == 1:
            classes.append("wide")
        if len(photo_items) >= 3 and index == 0:
            classes.append("hero")
        if (
            len(photo_items) >= 3
            and remaining_count % 2 == 1
            and index == len(photo_items) - 1
        ):
            classes.append("wide")
        escaped_path = html_escape(path, quote=True)
        date = _display_date(entry.get("date"))
        lightbox_caption = date
        if entry.get("text"):
            caption_text = text_to_markdown(str(entry["text"])).replace("\n", " ")
            lightbox_caption = f"{date} | {caption_text}"
        parts.append(f'<figure class="{" ".join(classes)}">')
        parts.append(
            f'<a class="photo" href="{escaped_path}" '
            f'data-gallery="{gallery_id}" '
            f'data-caption="{html_escape(lightbox_caption, quote=True)}">'
            f'<img src="{escaped_path}" alt="Photo from {html_escape(date)}" loading="lazy">'
            "</a>"
        )
        parts.append(f"<figcaption><time>{html_escape(date)}</time>")
        author = (entry.get("author") or {}).get("name")
        if author:
            parts.append(
                f'<span class="photo-author">By {html_escape(str(author))}</span>'
            )
        if entry.get("text"):
            parts.append(
                f'<div class="photo-caption">{_html_text(entry["text"])}</div>'
            )
        parts.append("</figcaption></figure>")
    parts.append("</div></article>")
    return "".join(parts)


def render_html(payload: dict, json_path: Path, output_path: Path) -> str:
    entries = sorted(payload.get("entries", []), key=_entry_sort_key)
    presented_entries = _presentation_entries(entries)
    archive_title = html_escape(_archive_title(payload))
    category_counts = {
        category: sum(_entry_category(entry) == category for entry in presented_entries)
        for category in (
            "weekly-photos",
            "observation",
            "assessment-review",
            "other",
        )
    }
    parts = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta name="referrer" content="no-referrer">',
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src 'self' file: data:; media-src 'self' file:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; font-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none';\">",
        f"<title>{archive_title}</title>",
        """<style>
*{box-sizing:border-box}
body{margin:0;background:#f4f1ec;color:#28231f;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.6}
.archive{max-width:860px;margin:0 auto;padding:56px 24px 96px}
.archive-header{text-align:center;margin-bottom:40px}
.archive-header h1{font-family:Georgia,serif;font-size:clamp(2.2rem,6vw,4rem);font-weight:500;line-height:1;margin:0 0 12px}
.generated{color:#766d64;font-size:.9rem}
.filters{align-items:center;background:rgba(244,241,236,.94);border:1px solid #ded6cc;border-radius:16px;display:flex;flex-wrap:wrap;gap:7px;justify-content:center;margin:0 0 28px;padding:8px;position:sticky;top:10px;z-index:20}
.filter-button{appearance:none;background:transparent;border:0;border-radius:10px;color:#665d55;cursor:pointer;font:inherit;font-size:.82rem;font-weight:650;padding:8px 12px}
.filter-button:hover{background:#e9e2d9}.filter-button[aria-pressed="true"]{background:#554b42;color:#fff}
.filter-count{font-size:.72rem;margin-left:4px;opacity:.72}.filter-empty{background:#fff;border:1px solid #e5ded5;border-radius:14px;color:#766d64;padding:24px;text-align:center}
.post{background:#fff;border:1px solid #e5ded5;border-radius:18px;padding:clamp(22px,5vw,44px);margin:0 0 28px;box-shadow:0 10px 30px rgba(65,49,35,.06);overflow:hidden}
.post[hidden]{display:none}
.post-header{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:22px}
.post-header h2{font-family:Georgia,serif;font-size:clamp(1.7rem,4vw,2.35rem);font-weight:500;line-height:1.15;margin:0}
.badge{background:#efe9e1;border-radius:999px;color:#665d55;font-size:.72rem;font-weight:700;letter-spacing:.06em;padding:6px 10px;text-transform:uppercase;white-space:nowrap}
.meta{display:flex;flex-wrap:wrap;gap:6px 18px;color:#766d64;font-size:.86rem;margin:-8px 0 24px}
.body{font-family:Georgia,serif;font-size:1.08rem}
.body p:first-child{margin-top:0}.body p:last-child{margin-bottom:0}
.weekly-summary{background:#f8f5f0;border-left:3px solid #b9aa99;border-radius:0 10px 10px 0;margin:0 0 24px;padding:16px 18px}
.weekly-summary h3{font-size:.82rem;letter-spacing:.06em;margin:0 0 10px;text-transform:uppercase}.weekly-post-description{font-family:Georgia,serif}.weekly-post-description+ .weekly-post-description{border-top:1px solid #e5ded5;margin-top:14px;padding-top:14px}.weekly-post-attribution{color:#766d64;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:.76rem;margin:7px 0 0}
.photos{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:28px -12px 4px}
.photo{background:#ece7e0;border-radius:10px;display:block;min-height:180px;overflow:hidden}
.photo.hero,.photo.wide{grid-column:1/-1}
.photo img{display:block;height:100%;max-height:520px;object-fit:contain;width:100%}
.photo.hero img{max-height:620px}
.weekly-photo{margin:0;min-width:0}.weekly-photo.hero,.weekly-photo.wide{grid-column:1/-1}
.weekly-photo .photo{min-height:180px}.weekly-photo.hero img{max-height:620px}
.weekly-photo figcaption{color:#766d64;font-size:.78rem;line-height:1.4;padding:7px 3px 4px}
.photo-author{margin-left:10px}.photo-caption{color:#4f4841;font-family:Georgia,serif;font-size:.9rem;margin-top:4px}
.photo-caption p{margin:0}
.lightbox{align-items:center;background:rgba(19,17,15,.96);display:flex;height:100dvh;inset:0;justify-content:center;padding:58px 76px 52px;position:fixed;width:100vw;z-index:1000}
.lightbox[hidden]{display:none}.lightbox-open{overflow:hidden}
.lightbox-stage{align-items:center;display:flex;height:100%;justify-content:center;margin:0;max-width:min(1500px,100%);position:relative;width:100%}
.lightbox-image{display:block;max-height:calc(100dvh - 130px);max-width:100%;object-fit:contain}
.lightbox-caption{bottom:-38px;color:#e7e0d8;font-size:.85rem;left:0;position:absolute;text-align:center;width:100%}
.lightbox-counter{color:#d6cec5;font-size:.78rem;left:20px;position:fixed;top:18px}
.lightbox-button{appearance:none;background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.22);border-radius:999px;color:#fff;cursor:pointer;font:inherit;height:44px;position:fixed;width:44px}
.lightbox-button:hover,.lightbox-button:focus-visible{background:rgba(255,255,255,.2);outline:2px solid #fff;outline-offset:2px}
.lightbox-close{right:18px;top:14px}.lightbox-previous{left:18px;top:calc(50% - 22px)}.lightbox-next{right:18px;top:calc(50% - 22px)}
.lightbox-button:disabled{cursor:default;opacity:.25}
.assessment,.next-step,.attachments{background:#f8f5f0;border-radius:12px;margin-top:26px;padding:20px}
.assessment h3,.next-step h3,.attachments h3{font-size:1rem;margin:0 0 14px}
.configuration{color:#766d64;font-size:.88rem;margin-top:-8px}
.assessment-row{border-top:1px solid #e5ded5;padding:14px 0}
.assessment-row h4{margin:0}.result{color:#506343;font-weight:700;margin:3px 0}.assessment-note{color:#625a52;font-size:.92rem}
.details{display:grid;grid-template-columns:minmax(120px,1fr) 2fr;gap:8px 16px}.details dt{font-weight:700}.details dd{margin:0}
.attachments video{border-radius:8px;display:block;margin-top:12px;max-height:520px;width:100%}
.attachments a{color:#395d73}
.empty{text-align:center;color:#766d64}
@media(max-width:620px){.archive{padding:28px 12px 60px}.filters{justify-content:flex-start;overflow-x:auto;top:6px;flex-wrap:nowrap}.filter-button{white-space:nowrap}.post{border-radius:12px}.post-header{display:block}.badge{display:inline-block;margin-top:12px}.photo,.weekly-photo .photo{min-height:120px}.details{display:block}.details dd{margin:0 0 10px}.lightbox{padding:54px 10px 58px}.lightbox-image{max-height:calc(100dvh - 122px)}.lightbox-previous{left:8px}.lightbox-next{right:8px}.lightbox-button{background:rgba(20,18,16,.62)}}
</style>""",
        "</head>",
        "<body>",
        '<main class="archive">',
        f'<header class="archive-header"><h1>{archive_title}</h1>',
    ]
    generated_at = payload.get("generated_at")
    if generated_at:
        parts.append(
            f'<p class="generated">Generated {_display_date(generated_at)}</p>'
        )
    parts.extend(
        [
            "</header>",
            '<nav class="filters" aria-label="Filter archive entries">',
            f'<button class="filter-button" type="button" data-filter="all" aria-pressed="true">All <span class="filter-count">{len(presented_entries)}</span></button>',
            f'<button class="filter-button" type="button" data-filter="weekly-photos" aria-pressed="false">Weekly photos <span class="filter-count">{category_counts["weekly-photos"]}</span></button>',
            f'<button class="filter-button" type="button" data-filter="observation" aria-pressed="false">Observations <span class="filter-count">{category_counts["observation"]}</span></button>',
            f'<button class="filter-button" type="button" data-filter="assessment-review" aria-pressed="false">Assessments &amp; reviews <span class="filter-count">{category_counts["assessment-review"]}</span></button>',
        ]
    )
    if category_counts["other"]:
        parts.append(
            f'<button class="filter-button" type="button" data-filter="other" aria-pressed="false">Other <span class="filter-count">{category_counts["other"]}</span></button>'
        )
    parts.extend(
        [
            "</nav>",
            '<p class="filter-empty" data-filter-empty hidden>No entries match this filter.</p>',
        ]
    )

    for entry in presented_entries:
        if entry.get("presentation") == "photo_week":
            parts.append(_html_photo_week(entry, json_path, output_path))
            continue

        kind = _label(entry.get("kind") or entry.get("source") or "Entry")
        category = _entry_category(entry)
        parts.append(f'<article class="post" data-entry-category="{category}">')
        parts.append('<header class="post-header">')
        parts.append(f"<h2>{html_escape(_display_day(entry.get('date')))}</h2>")
        parts.append(f'<span class="badge">{html_escape(kind)}</span>')
        parts.append("</header>")
        parts.append('<div class="meta">')
        author = (entry.get("author") or {}).get("name")
        if author:
            parts.append(f"<span>Written by {html_escape(str(author))}</span>")
        children = ", ".join(
            str(child.get("name"))
            for child in entry.get("children", [])
            if child.get("name")
        )
        if children:
            parts.append(f"<span>Child: {html_escape(children)}</span>")
        parts.append(f"<span>Posted {_display_date(entry.get('date'))}</span>")
        if entry.get("observed_at"):
            parts.append(f"<span>Observed {_display_date(entry['observed_at'])}</span>")
        parts.append("</div>")

        if entry.get("text"):
            parts.append(f'<div class="body">{_html_text(entry["text"])}</div>')
        if _has_assessment_content(entry.get("assessment")):
            parts.append(_html_assessment(entry["assessment"]))
        if entry.get("next_step"):
            parts.append(
                '<section class="next-step"><h3>What\'s next</h3>'
                f"{_html_text(entry['next_step'])}</section>"
            )
        parts.append(_html_media(entry, json_path, output_path))
        parts.append("</article>")

    if not entries:
        parts.append('<p class="empty">No archived entries were found.</p>')
    parts.extend(
        [
            "</main>",
            '<div class="lightbox" data-lightbox hidden aria-hidden="true" role="dialog" aria-modal="true" aria-label="Photo gallery">',
            '<span class="lightbox-counter" data-lightbox-counter></span>',
            '<button class="lightbox-button lightbox-close" data-lightbox-close type="button" aria-label="Close gallery">&#10005;</button>',
            '<button class="lightbox-button lightbox-previous" data-lightbox-previous type="button" aria-label="Previous photo">&#8592;</button>',
            '<figure class="lightbox-stage" data-lightbox-stage>',
            '<img class="lightbox-image" data-lightbox-image alt="">',
            '<figcaption class="lightbox-caption" data-lightbox-caption></figcaption>',
            "</figure>",
            '<button class="lightbox-button lightbox-next" data-lightbox-next type="button" aria-label="Next photo">&#8594;</button>',
            "</div>",
            """<script>
(() => {
  "use strict";
  const overlay = document.querySelector("[data-lightbox]");
  const image = overlay.querySelector("[data-lightbox-image]");
  const caption = overlay.querySelector("[data-lightbox-caption]");
  const counter = overlay.querySelector("[data-lightbox-counter]");
  const closeButton = overlay.querySelector("[data-lightbox-close]");
  const previousButton = overlay.querySelector("[data-lightbox-previous]");
  const nextButton = overlay.querySelector("[data-lightbox-next]");
  const stage = overlay.querySelector("[data-lightbox-stage]");
  const galleryLinks = Array.from(document.querySelectorAll("a[data-gallery]"));
  const filterButtons = Array.from(document.querySelectorAll("[data-filter]"));
  const archiveEntries = Array.from(document.querySelectorAll("[data-entry-category]"));
  const filterEmpty = document.querySelector("[data-filter-empty]");
  let items = [];
  let index = 0;
  let lastFocus = null;
  let pointerStart = null;

  function render() {
    const link = items[index];
    image.src = link.href;
    image.alt = link.querySelector("img")?.alt || "Archive photo";
    caption.textContent = link.dataset.caption || "";
    caption.hidden = !caption.textContent;
    counter.textContent = `${index + 1} / ${items.length}`;
    const single = items.length < 2;
    previousButton.disabled = single;
    nextButton.disabled = single;
  }

  function openGallery(link) {
    items = galleryLinks.filter(item => item.dataset.gallery === link.dataset.gallery);
    index = Math.max(0, items.indexOf(link));
    lastFocus = document.activeElement;
    render();
    overlay.hidden = false;
    overlay.setAttribute("aria-hidden", "false");
    document.body.classList.add("lightbox-open");
    closeButton.focus();
  }

  function closeGallery() {
    overlay.hidden = true;
    overlay.setAttribute("aria-hidden", "true");
    document.body.classList.remove("lightbox-open");
    image.removeAttribute("src");
    if (lastFocus instanceof HTMLElement) lastFocus.focus();
  }

  function move(amount) {
    if (items.length < 2) return;
    index = (index + amount + items.length) % items.length;
    render();
  }

  function applyFilter(filter) {
    let visibleCount = 0;
    archiveEntries.forEach(entry => {
      const visible = filter === "all" || entry.dataset.entryCategory === filter;
      entry.hidden = !visible;
      if (visible) visibleCount += 1;
    });
    filterButtons.forEach(button => {
      button.setAttribute("aria-pressed", String(button.dataset.filter === filter));
    });
    filterEmpty.hidden = visibleCount !== 0;
  }

  galleryLinks.forEach(link => link.addEventListener("click", event => {
    event.preventDefault();
    openGallery(link);
  }));
  filterButtons.forEach(button => button.addEventListener("click", () => {
    applyFilter(button.dataset.filter);
  }));
  closeButton.addEventListener("click", closeGallery);
  previousButton.addEventListener("click", () => move(-1));
  nextButton.addEventListener("click", () => move(1));
  overlay.addEventListener("click", event => {
    if (event.target === overlay || event.target === stage) closeGallery();
  });
  document.addEventListener("keydown", event => {
    if (overlay.hidden) return;
    if (event.key === "Escape") closeGallery();
    if (event.key === "ArrowLeft") move(-1);
    if (event.key === "ArrowRight") move(1);
  });
  stage.addEventListener("pointerdown", event => { pointerStart = event.clientX; });
  stage.addEventListener("pointerup", event => {
    if (pointerStart === null) return;
    const distance = event.clientX - pointerStart;
    pointerStart = null;
    if (Math.abs(distance) > 50) move(distance > 0 ? -1 : 1);
  });
})();
</script>""",
            "</body>",
            "</html>",
        ]
    )
    return "\n".join(parts) + "\n"


def write_markdown(json_path: Path, output_path: Path | None = None) -> Path:
    json_path = json_path.resolve()
    output_path = (output_path or json_path.with_suffix(".md")).resolve()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    payload = _payload_without_excluded_entries(payload, json_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_markdown(payload, json_path, output_path), encoding="utf-8"
    )
    return output_path


def write_html(json_path: Path, output_path: Path | None = None) -> Path:
    json_path = json_path.resolve()
    output_path = (output_path or json_path.with_suffix(".html")).resolve()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    payload = _payload_without_excluded_entries(payload, json_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_html(payload, json_path, output_path), encoding="utf-8"
    )
    return output_path


def build_render_report(json_path: Path) -> dict:
    """Describe exactly how source entries become weekly rendered content."""

    json_path = json_path.resolve()
    raw_payload = json.loads(json_path.read_text(encoding="utf-8"))
    excluded_ids = load_excluded_entry_ids(json_path)
    raw_entries = raw_payload.get("entries", [])
    payload = _payload_without_excluded_entries(raw_payload, json_path)
    entries = sorted(payload.get("entries", []), key=_entry_sort_key)

    tagged_photo_ids = {
        str(item["media_id"])
        for entry in entries
        if _is_weekly_photo_entry(entry)
        for item in entry.get("media", [])
        if item.get("kind") == "photo" and item.get("media_id")
    }
    matching_parent_posts = []
    parent_posts_without_text = []
    matched_photo_ids = set()
    described_photo_ids = set()
    for entry in entries:
        if entry.get("source") != "feed":
            continue
        matching_ids = sorted(
            {
                str(item["media_id"])
                for item in entry.get("media", [])
                if item.get("kind") == "photo"
                and item.get("media_id")
                and str(item["media_id"]) in tagged_photo_ids
            }
        )
        if not matching_ids:
            continue
        matched_photo_ids.update(matching_ids)
        record = {
            "entry_id": entry.get("entry_id"),
            "date": entry.get("date"),
            "matched_photo_ids": matching_ids,
        }
        if str(entry.get("text") or "").strip():
            described_photo_ids.update(matching_ids)
            matching_parent_posts.append(record)
        else:
            parent_posts_without_text.append(record)

    weekly_groups = [
        entry
        for entry in _presentation_entries(entries)
        if entry.get("presentation") == "photo_week"
    ]
    week_details = []
    for group in weekly_groups:
        photo_ids = sorted(
            {
                str(item["media_id"])
                for _, item in _weekly_photo_items(group)
                if item.get("media_id")
            }
        )
        parent_posts = _weekly_parent_posts(group)
        week_details.append(
            {
                "week": _display_week(group.get("date")),
                "photo_count": len(photo_ids),
                "photo_ids": photo_ids,
                "parent_post_count": len(parent_posts),
                "parent_post_ids": [post.get("entry_id") for post in parent_posts],
            }
        )

    present_entry_ids = {
        entry.get("entry_id") for entry in raw_entries if entry.get("entry_id")
    }
    return {
        "archive_json": str(json_path),
        "source_entry_count": len(raw_entries),
        "rendered_source_entry_count": len(entries),
        "excluded_entry_ids_present": sorted(excluded_ids & present_entry_ids),
        "weekly_photos": {
            "tagged_photo_count": len(tagged_photo_ids),
            "weekly_card_count": len(weekly_groups),
            "weekly_cards_with_descriptions": sum(
                bool(detail["parent_post_count"]) for detail in week_details
            ),
            "weekly_cards_without_descriptions": sum(
                not detail["parent_post_count"] for detail in week_details
            ),
        },
        "parent_feed_posts": {
            "matching_post_count": len(matching_parent_posts)
            + len(parent_posts_without_text),
            "matching_posts_with_text": len(matching_parent_posts),
            "matching_posts_without_text": len(parent_posts_without_text),
            "posts_without_text": parent_posts_without_text,
        },
        "photo_matching": {
            "matched_photo_count": len(matched_photo_ids),
            "photos_with_parent_text": len(described_photo_ids),
            "unmatched_photo_count": len(tagged_photo_ids - matched_photo_ids),
            "unmatched_photo_ids": sorted(tagged_photo_ids - matched_photo_ids),
            "photos_whose_parent_has_no_text": sorted(
                matched_photo_ids - described_photo_ids
            ),
        },
        "weeks": week_details,
    }


def write_render_report(json_path: Path, output_path: Path | None = None) -> Path:
    json_path = json_path.resolve()
    output_path = (
        output_path or json_path.with_suffix(".render-report.json")
    ).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(build_render_report(json_path), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path
