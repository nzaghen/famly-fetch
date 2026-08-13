"""Structured archive storage and Markdown rendering for Famly content."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
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
