#!/usr/bin/env python3

"""Fetch all famly.co pictures of your kid

Auth has two versions:

 - "non-v2" has a `?accessToken=XXX` as a GET-parameter
 - v2-urls demands a `x-famly-accesstoken: XXX` header

"""

import json
import os
import shutil
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import click
import piexif
import piexif.helper

from famly_fetch.api_client import ApiClient
from famly_fetch.archive import ArchiveExporter
from famly_fetch.file import File
from famly_fetch.image import BaseImage, Image, SecretImage
from famly_fetch.video import Video


class FamlyDownloader:
    def __init__(
        self,
        email: str,
        password: str,
        famly_base_url: str,
        pictures_folder: Path,
        stop_on_existing: bool,
        text_comments: bool,
        state_file: Path,
        user_agent: str | None = None,
        access_token: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        filename_pattern: str = "%FP-%Y-%m-%d_%H-%M-%S-%ID",
        include_files: bool = False,
        include_videos: bool = False,
        export_text: bool = False,
    ):
        self._pictures_folder: Path = pictures_folder
        self._pictures_folder.mkdir(parents=True, exist_ok=True)
        self._protect_output_folder()

        self.stop_on_existing = stop_on_existing
        self.latitude = latitude
        self.longitude = longitude
        self.text_comments = text_comments
        self.filename_pattern = filename_pattern
        self.state_file = state_file
        self.include_files = include_files
        self.include_videos = include_videos
        self.archive = ArchiveExporter(self._pictures_folder) if export_text else None
        self.downloaded_images = self.load_state()

        self._apiClient = ApiClient(
            base_url=famly_base_url, user_agent=user_agent, access_token=access_token
        )
        if not access_token:
            self._apiClient.login(email, password)

    def _protect_output_folder(self):
        """Keep generated personal data out of an accidental Git commit."""

        ignore_path = self._pictures_folder / ".gitignore"
        if not ignore_path.exists():
            ignore_path.write_text("*\n!.gitignore\n", encoding="utf-8")

    @staticmethod
    def _remote_id(item: dict, *keys: str) -> str | None:
        for key in keys:
            value = item.get(key)
            if value:
                return str(value)
        return None

    @staticmethod
    def _assessment_areas(observation: dict) -> list[dict]:
        results = []
        remark = observation.get("remark") or {}
        for area_result in remark.get("areas") or []:
            area = area_result.get("area") or {}
            refinement_settings = area_result.get("areaRefinementSettings") or {}
            age_band = refinement_settings.get("ageBandSetting") or {}
            assessment_option = refinement_settings.get("assessmentOptionSetting") or {}
            framework = area.get("framework") or {}
            results.append(
                {
                    "area": {
                        "id": area.get("id"),
                        "parent_id": area.get("parentId"),
                        "framework_id": area.get("frameworkId"),
                        "title": area.get("title"),
                        "description": area.get("description"),
                        "abbreviation": area.get("abbr"),
                        "framework": {
                            "id": framework.get("id"),
                            "title": framework.get("title"),
                            "owner": framework.get("owner"),
                        }
                        if framework
                        else None,
                    },
                    "refinement": area_result.get("refinement"),
                    "note": area_result.get("note"),
                    "age_band": {
                        "id": age_band.get("ageBandSettingId"),
                        "from": age_band.get("from"),
                        "to": age_band.get("to"),
                        "label": age_band.get("label"),
                    }
                    if age_band
                    else None,
                    "assessment_option": {
                        "id": assessment_option.get("assessmentOptionSettingId"),
                        "label": assessment_option.get("label"),
                        "background_color": assessment_option.get("backgroundColor"),
                        "font_color": assessment_option.get("fontColor"),
                    }
                    if assessment_option
                    else None,
                }
            )
        return results

    @classmethod
    def _assessment_data(cls, observation: dict) -> dict | None:
        if observation.get("variant") not in {"ASSESSMENT", "TWO_YEAR_PROGRESS"}:
            return None
        assessment_setting = (observation.get("settings") or {}).get(
            "assessmentSetting"
        ) or {}
        custom_fields = []
        for field_result in (observation.get("remark") or {}).get(
            "customFieldValues"
        ) or []:
            setting = field_result.get("customFieldSetting") or {}
            custom_fields.append(
                {
                    "id": setting.get("customFieldId"),
                    "assessment_settings_id": setting.get("assessmentSettingsId"),
                    "label": setting.get("label"),
                    "order": setting.get("order"),
                    "value": field_result.get("value"),
                }
            )
        return {
            "setting": {
                "id": assessment_setting.get("assessmentSettingsId"),
                "title": assessment_setting.get("title"),
            }
            if assessment_setting
            else None,
            "areas": cls._assessment_areas(observation),
            "custom_fields": custom_fields,
        }

    def _archive_media(
        self,
        media_id: str,
        kind: str,
        path: Path,
        filename: str | None = None,
    ) -> dict | None:
        if not self.archive:
            return None
        return self.archive.media(media_id, kind, path, filename)

    def save_archive(self):
        if self.archive:
            self.archive.save()

    def set_archive_children(self, children: list[tuple[str, str]]):
        if self.archive:
            self.archive.set_archive_children(children)

    def load_state(self):
        if self.state_file.exists():
            with open(self.state_file, "r") as f:
                return json.load(f)
        return {}

    def save_state(self):
        with open(self.state_file, "w") as f:
            json.dump(self.downloaded_images, f)

    def mark_as_downloaded(self, img_id: str):
        self.downloaded_images[img_id] = datetime.now(timezone.utc).isoformat()

    def get_all_children(self):
        my_info = self._apiClient.me_me_me()
        all_children = []

        # Current children
        for role in my_info["roles2"]:
            all_children.append((role["targetId"], role["title"]))

        # Previous children (that's what they call it)
        prev_children = []
        for ele in my_info["behaviors"]:
            if ele["id"] == "ShowPreviousChildren":
                prev_children = ele["payload"]["children"]

        for child in prev_children:
            all_children.append((child["childId"], child["name"]["firstName"]))

        return all_children

    def get_parents_ids(self, child_id: str) -> set[str]:
        relations = self._apiClient.get_relations(child_id)
        return {x["loginId"] for x in relations if x["loginId"]}

    def download_images_from_notes(self, child_id, first_name):
        click.secho(
            f"Downloading learning journey images for {first_name}...", fg="green"
        )
        next_ref = None

        while True:
            click.echo("Fetching next 100 notes")
            batch = self._apiClient.get_child_notes(
                child_id, cursor=next_ref, first=100
            )
            click.echo(f"{len(batch['result'])} fetched.")

            for _i, note in enumerate(batch["result"]):
                author = note["createdBy"]["name"]["fullName"]
                body = note.get("text") or ""
                text = body + " - " + author
                date = note["createdAt"]
                archived_media = []
                should_stop = False

                for img_dict in note["images"]:
                    img = SecretImage.from_dict(
                        img_dict,
                        date_override=date,
                        text_override=text if self.text_comments else None,
                    )
                    click.echo(f" - image {img.img_id} from note at {img.date}")

                    file_path = self.download_file_path(img, f"{first_name}-note")
                    archive_item = self._archive_media(img.img_id, "photo", file_path)
                    if archive_item:
                        archived_media.append(archive_item)
                    if img.img_id in self.downloaded_images:
                        click.secho(
                            f"Image {img.img_id} already downloaded, {'stopping download' if self.stop_on_existing else 'skipping'}.",
                            fg="yellow",
                        )
                        if self.stop_on_existing:
                            should_stop = True
                            break
                        else:
                            continue
                    self.fetch_image(img, file_path)
                    self.mark_as_downloaded(img.img_id)

                if self.include_files and not should_stop:
                    should_stop = self._download_files_from_item(
                        note.get("files") or [],
                        date=date,
                        text=text,
                        filename_prefix=f"{first_name}-note",
                        archive_media=archived_media,
                    )

                if self.archive:
                    note_id = self._remote_id(note, "id", "noteId")
                    self.archive.add_entry(
                        entry_id=self.archive.entry_id(
                            "note", note_id, child_id, date, body, author
                        ),
                        source="note",
                        kind=note.get("noteType") or "note",
                        date=date,
                        published_at=note.get("publishedAt"),
                        author=author,
                        children=[{"id": child_id, "name": first_name}],
                        text=body,
                        media=archived_media,
                    )

                if should_stop:
                    self.save_state()
                    return

            next_ref = batch["next"]

            if not next_ref:
                break

        self.save_state()

    def download_images_from_learning_journey(self, child_id, first_name):
        click.secho(
            f"Downloading learning journey images for {first_name}...", fg="green"
        )

        next_cursor = None

        while True:
            click.echo("Fetching next 100 learning journey entries")
            batch = self._apiClient.learning_journey_query(
                child_id, cursor=next_cursor, first=100
            )
            click.echo(f"{len(batch['results'])} fetched.")

            for _i, observation in enumerate(batch["results"]):
                author = observation["createdBy"]["name"]["fullName"]
                remark = observation.get("remark") or {}
                body = remark.get("body") or ""
                rich_text_body = remark.get("richTextBody")
                observed_at = remark.get("date")
                next_step_data = observation.get("nextStep") or {}
                next_step = (
                    next_step_data.get("body")
                    or next_step_data.get("richTextBody")
                    or None
                )
                text = body + " - " + author
                date = observation["status"]["createdAt"]
                archived_media = []
                should_stop = False

                for img_dict in observation["images"]:
                    img = SecretImage.from_dict(
                        img_dict,
                        date_override=date,
                        text_override=text if self.text_comments else None,
                    )
                    click.echo(f" - image {img.img_id} from observation at {img.date}")

                    file_path = self.download_file_path(img, f"{first_name}-journey")
                    archive_item = self._archive_media(img.img_id, "photo", file_path)
                    if archive_item:
                        archived_media.append(archive_item)
                    if img.img_id in self.downloaded_images:
                        click.secho(
                            f"Image {img.img_id} already downloaded, {'stopping download' if self.stop_on_existing else 'skipping'}.",
                            fg="yellow",
                        )
                        if self.stop_on_existing:
                            should_stop = True
                            break
                        else:
                            continue
                    self.fetch_image(img, file_path)
                    self.mark_as_downloaded(img.img_id)

                if self.include_files and not should_stop:
                    should_stop = self._download_files_from_item(
                        observation.get("files") or [],
                        date=date,
                        text=text,
                        filename_prefix=f"{first_name}-journey",
                        archive_media=archived_media,
                    )

                if self.include_videos and not should_stop:
                    should_stop = self._download_videos_from_item(
                        observation.get("videos") or [],
                        date=date,
                        text=text,
                        filename_prefix=f"{first_name}-journey",
                        archive_media=archived_media,
                    )

                if self.archive:
                    observation_id = self._remote_id(observation, "id", "observationId")
                    observed_children = [{"id": child_id, "name": first_name}]
                    api_children = []
                    for child in observation.get("children", []):
                        name = child.get("name")
                        if isinstance(name, dict):
                            name = name.get("fullName") or name.get("firstName")
                        if name:
                            api_children.append(
                                {
                                    "id": child.get("id")
                                    or (child_id if name == first_name else None),
                                    "name": name,
                                }
                            )
                    if api_children:
                        observed_children = api_children
                    assessment = self._assessment_data(observation)
                    metadata = {
                        "variant": observation.get("variant"),
                        "version": observation.get("version"),
                    }
                    if rich_text_body and rich_text_body != body:
                        metadata["rich_text_body"] = rich_text_body
                    if not assessment:
                        learning_areas = self._assessment_areas(observation)
                        if learning_areas:
                            metadata["learning_areas"] = learning_areas
                    self.archive.add_entry(
                        entry_id=self.archive.entry_id(
                            "journey",
                            observation_id,
                            child_id,
                            date,
                            body,
                            author,
                        ),
                        source="journey",
                        kind=observation.get("variant") or "observation",
                        date=date,
                        observed_at=observed_at,
                        author=author,
                        children=observed_children,
                        text=body,
                        next_step=next_step,
                        assessment=assessment,
                        media=archived_media,
                        metadata=metadata,
                    )

                if should_stop:
                    self.save_state()
                    return

            next_cursor = batch["next"]

            if not next_cursor:
                break

        self.save_state()

    def download_tagged_images(self, child_id, first_name):
        """Download images by childId"""
        click.secho(f"Downloading tagged images for {first_name}...", fg="green")

        imgs = self._apiClient.make_api_request(
            "GET", "/api/v2/images/tagged", params={"childId": child_id}
        )

        click.echo(f"Fetching {len(imgs)} tagged images for {first_name}")

        for img_no, img_dict in enumerate(imgs, start=1):
            img = Image.from_dict(img_dict)
            click.echo(f" - image {img.img_id} at {img.date} ({img_no}/{len(imgs)})")

            file_path = self.download_file_path(img, first_name)
            if self.archive:
                archive_item = self._archive_media(img.img_id, "photo", file_path)
                self.archive.add_entry(
                    entry_id=self.archive.entry_id(
                        "tagged_photo", img.img_id, child_id, img.date.isoformat()
                    ),
                    source="tagged_photo",
                    kind="photo",
                    date=img.date.isoformat(),
                    author=None,
                    children=[{"id": child_id, "name": first_name}],
                    text=img.text,
                    media=[archive_item] if archive_item else [],
                )
            if img.img_id in self.downloaded_images:
                click.secho(
                    f"Image {img.img_id} already downloaded, {'stopping download' if self.stop_on_existing else 'skipping'}.",
                    fg="yellow",
                )
                if self.stop_on_existing:
                    return
                else:
                    continue

            # sleep for 1s to avoid 400 errors
            time.sleep(1)
            self.fetch_image(img, file_path)
            self.mark_as_downloaded(img.img_id)

        self.save_state()

    def download_images_from_messages(self):
        click.secho("Downloading images from messages...", fg="green")

        conv_ids = self._apiClient.make_api_request("GET", "/api/v2/conversations")
        click.echo(f"Found {len(conv_ids)} conversations")

        for conv_id in reversed(conv_ids):
            conversation_id = str(conv_id["conversationId"])
            conversation = self._apiClient.make_api_request(
                "GET", "/api/v2/conversations/%s" % conversation_id
            )
            for msg in reversed(conversation["messages"]):
                body = msg.get("body") or ""
                author = (msg.get("author") or {}).get("title")
                text = body + (" - " + author if author else "")
                date = msg["createdAt"]
                archived_media = []
                should_stop = False
                for img_dict in msg["images"]:
                    img = Image.from_dict(
                        img_dict,
                        date_override=date,
                        text_override=text if self.text_comments else None,
                    )

                    click.echo(f" - image {img.img_id} from message at {img.date}")

                    file_path = self.download_file_path(img, "message")
                    archive_item = self._archive_media(img.img_id, "photo", file_path)
                    if archive_item:
                        archived_media.append(archive_item)

                    if img.img_id in self.downloaded_images:
                        click.secho(
                            f"Image {img.img_id} already downloaded, {'stopping download' if self.stop_on_existing else 'skipping'}.",
                            fg="yellow",
                        )
                        if self.stop_on_existing:
                            should_stop = True
                            break
                        else:
                            continue
                    self.fetch_image(img, file_path)
                    self.mark_as_downloaded(img.img_id)

                if self.include_files and not should_stop:
                    should_stop = self._download_files_from_item(
                        msg.get("files") or [],
                        date=date,
                        text=text,
                        filename_prefix="message",
                        archive_media=archived_media,
                    )

                if self.archive:
                    message_id = self._remote_id(msg, "id", "messageId")
                    self.archive.add_entry(
                        entry_id=self.archive.entry_id(
                            "message",
                            message_id,
                            conversation_id,
                            date,
                            body,
                            author,
                        ),
                        source="message",
                        kind="message",
                        date=date,
                        author=author,
                        text=body,
                        media=archived_media,
                        metadata={"conversation_id": conversation_id},
                    )

                if should_stop:
                    self.save_state()
                    return
        self.save_state()

    @staticmethod
    def _feed_author(feed_item: dict) -> str | None:
        for container_key in ("author", "createdBy", "originator"):
            container = feed_item.get(container_key) or {}
            if isinstance(container, str):
                return container
            for key in ("title", "fullName", "name"):
                value = container.get(key)
                if isinstance(value, str) and value:
                    return value
                if isinstance(value, dict):
                    nested = value.get("fullName")
                    if nested:
                        return nested
        return None

    def _record_feed_entry(
        self, feed_item: dict, date: str, archived_media: list[dict]
    ):
        if not self.archive:
            return
        feed_id = self._remote_id(
            feed_item, "feedItemId", "postId", "id", "originatorId"
        )
        author = self._feed_author(feed_item)
        body = feed_item.get("body") or ""
        self.archive.add_entry(
            entry_id=self.archive.entry_id("feed", feed_id, date, body, author),
            source="feed",
            kind="post",
            date=date,
            author=author,
            text=body,
            media=archived_media,
            metadata={"originator_id": feed_item.get("originatorId")},
        )

    def archive_parent_posts_for_tagged_photos(
        self, selected_children: list[tuple[str, str]] | None = None
    ):
        """Archive matching feed-post text without downloading any feed media."""

        if not self.archive:
            raise ValueError("Parent post text requires --export-text")
        tagged_media = self.archive.media_index(
            "tagged_photo", "photo", selected_children=selected_children
        )
        if not tagged_media:
            click.secho(
                "No archived tagged photos were found; no parent posts to match.",
                fg="yellow",
            )
            return

        click.secho(
            "Matching archived tagged photos to parent feed-post text...", fg="green"
        )
        unmatched_ids = set(tagged_media)
        matched_posts = 0
        cursor = None
        older_than = None
        while unmatched_ids:
            response = self._apiClient.feed(
                cursor=cursor, older_than=older_than, limit=20
            )
            feed_items = response.get("feedItems") or []
            if not feed_items:
                break
            last_item = feed_items[-1]
            cursor = last_item.get("feedItemId")
            older_than = last_item.get("createdDate")

            for feed_item in feed_items:
                if not str(feed_item.get("originatorId") or "").startswith("Post:"):
                    continue
                matched_media = []
                for image in feed_item.get("images") or []:
                    image_id = image.get("imageId") or image.get("id")
                    if image_id is None:
                        continue
                    image_id = str(image_id)
                    if image_id in tagged_media:
                        matched_media.append(tagged_media[image_id])
                        unmatched_ids.discard(image_id)
                if not matched_media:
                    continue
                self._record_feed_entry(
                    feed_item,
                    feed_item["createdDate"],
                    matched_media,
                )
                matched_posts += 1

            if not cursor and not older_than:
                break

        click.echo(
            f"Matched {matched_posts} parent feed posts for "
            f"{len(tagged_media) - len(unmatched_ids)} of {len(tagged_media)} tagged photos."
        )

    def download_images_from_feed(self, liked_by_ids: set[str]):
        click.secho("Downloading liked images in posts...", fg="green")

        cursor = None
        older_than = None
        while True:
            click.echo("Fetching next 10 Posts")
            response = self._apiClient.feed(
                cursor=cursor, older_than=older_than, limit=10
            )
            if not response["feedItems"]:
                break
            last_item = response["feedItems"][-1]
            cursor = last_item["feedItemId"]
            older_than = last_item["createdDate"]
            for feed_item in response["feedItems"]:
                if not feed_item["originatorId"].startswith("Post:"):
                    # not a Post item
                    continue
                create_date = feed_item["createdDate"]
                archived_media = []
                should_stop = False
                for img_dict in feed_item["images"]:
                    if not (
                        img_dict["liked"]
                        or [
                            like
                            for like in img_dict["likes"]
                            if like["loginId"] in liked_by_ids
                        ]
                    ):
                        # not liked by parents
                        continue
                    img = Image.from_dict(
                        img_dict,
                        date_override=create_date,
                        text_override=feed_item["body"] if self.text_comments else None,
                    )
                    click.echo(f" - image {img.img_id} from post at {create_date}")

                    file_path = self.download_file_path(img, "post")
                    archive_item = self._archive_media(img.img_id, "photo", file_path)
                    if archive_item:
                        archived_media.append(archive_item)

                    if img.img_id in self.downloaded_images:
                        click.secho(
                            f"Image {img.img_id} already downloaded, {'stopping download' if self.stop_on_existing else 'skipping'}.",
                            fg="yellow",
                        )
                        if self.stop_on_existing:
                            should_stop = True
                            break
                        else:
                            continue
                    self.fetch_image(img, file_path)
                    self.mark_as_downloaded(img.img_id)

                feed_text = feed_item.get("body") if self.text_comments else None
                if self.include_files and not should_stop:
                    should_stop = self._download_files_from_item(
                        feed_item.get("files") or [],
                        date=create_date,
                        text=feed_text,
                        filename_prefix="post",
                        archive_media=archived_media,
                    )
                if self.include_videos and not should_stop:
                    should_stop = self._download_videos_from_item(
                        feed_item.get("videos") or [],
                        date=create_date,
                        text=feed_text,
                        filename_prefix="post",
                        archive_media=archived_media,
                    )

                self._record_feed_entry(feed_item, create_date, archived_media)
                if should_stop:
                    self.save_state()
                    return

        self.save_state()

    def download_all_images_from_feed(self, batch_size=20, batch_pause=10):
        click.secho("Downloading all images from feed posts...", fg="green")

        cursor = None
        older_than = None
        batch_count = 0
        while True:
            click.echo("Fetching next 10 Posts")
            response = self._apiClient.feed(
                cursor=cursor, older_than=older_than, limit=10
            )
            if not response["feedItems"]:
                break
            last_item = response["feedItems"][-1]
            cursor = last_item["feedItemId"]
            older_than = last_item["createdDate"]
            for feed_item in response["feedItems"]:
                if not feed_item["originatorId"].startswith("Post:"):
                    continue
                create_date = feed_item["createdDate"]
                archived_media = []
                should_stop = False
                for img_dict in feed_item["images"]:
                    img = Image.from_dict(
                        img_dict,
                        date_override=create_date,
                        text_override=feed_item["body"] if self.text_comments else None,
                    )
                    click.echo(f" - image {img.img_id} from post at {create_date}")

                    file_path = self.download_file_path(img, "post")
                    archive_item = self._archive_media(img.img_id, "photo", file_path)
                    if archive_item:
                        archived_media.append(archive_item)

                    if img.img_id in self.downloaded_images:
                        click.secho(
                            f"Image {img.img_id} already downloaded, {'stopping download' if self.stop_on_existing else 'skipping'}.",
                            fg="yellow",
                        )
                        if self.stop_on_existing:
                            should_stop = True
                            break
                        else:
                            continue
                    self.fetch_image(img, file_path)
                    self.mark_as_downloaded(img.img_id)
                    self.save_state()
                    batch_count += 1
                    if batch_count % batch_size == 0:
                        click.secho(
                            f"Downloaded {batch_count} images, pausing {batch_pause}s...",
                            fg="cyan",
                        )
                        time.sleep(batch_pause)

                feed_text = feed_item.get("body") if self.text_comments else None
                if self.include_files and not should_stop:
                    should_stop = self._download_files_from_item(
                        feed_item.get("files") or [],
                        date=create_date,
                        text=feed_text,
                        filename_prefix="post",
                        archive_media=archived_media,
                    )
                if self.include_videos and not should_stop:
                    should_stop = self._download_videos_from_item(
                        feed_item.get("videos") or [],
                        date=create_date,
                        text=feed_text,
                        filename_prefix="post",
                        archive_media=archived_media,
                    )

                self._record_feed_entry(feed_item, create_date, archived_media)
                if should_stop:
                    self.save_state()
                    return

    def _download_files_from_item(
        self,
        file_dicts: list,
        date: str,
        text: str | None,
        filename_prefix: str,
        archive_media: list[dict] | None = None,
    ) -> bool:
        """Download every File attachment on a single note/observation/message.

        Returns True if the caller should stop (only when stop_on_existing is
        set and an already-downloaded file is encountered)."""
        for file_dict in file_dicts:
            f = File.from_dict(
                file_dict,
                date_override=date,
                text_override=text if self.text_comments else None,
            )
            click.echo(f" - file {f.file_id} ({f.name or '?'}) at {f.date}")

            file_path = self.attachment_path(
                attachment_id=f.file_id,
                attachment_url=f.url,
                date=f.date,
                filename_prefix=filename_prefix,
                original_name=f.name,
            )
            archive_item = self._archive_media(f.file_id, "file", file_path, f.name)
            if archive_media is not None and archive_item:
                archive_media.append(archive_item)

            if f.file_id in self.downloaded_images:
                click.secho(
                    f"File {f.file_id} already downloaded, "
                    f"{'stopping download' if self.stop_on_existing else 'skipping'}.",
                    fg="yellow",
                )
                if self.stop_on_existing:
                    return True
                continue

            self.fetch_binary(f.url, file_path)
            self.mark_as_downloaded(f.file_id)
        return False

    def _download_videos_from_item(
        self,
        video_dicts: list,
        date: str,
        text: str | None,
        filename_prefix: str,
        archive_media: list[dict] | None = None,
    ) -> bool:
        """Download every Video attachment on a single observation.

        Returns True if the caller should stop (stop_on_existing semantics)."""
        for video_dict in video_dicts:
            try:
                v = Video.from_dict(
                    video_dict,
                    date_override=date,
                    text_override=text if self.text_comments else None,
                )
            except (KeyError, ValueError) as e:
                click.secho(
                    f"Skipping unrecognised video dict ({sorted(video_dict.keys())}): {e}",
                    fg="yellow",
                )
                continue
            if v is None:
                click.secho(
                    f"Skipping video {video_dict.get('videoId', '?')} — no playable URL yet.",
                    fg="yellow",
                )
                continue

            click.echo(f" - video {v.video_id} at {v.date}")

            file_path = self.attachment_path(
                attachment_id=v.video_id,
                attachment_url=v.url,
                date=v.date,
                filename_prefix=filename_prefix,
                original_name=None,
            )
            archive_item = self._archive_media(v.video_id, "video", file_path)
            if archive_media is not None and archive_item:
                archive_media.append(archive_item)

            if v.video_id in self.downloaded_images:
                click.secho(
                    f"Video {v.video_id} already downloaded, "
                    f"{'stopping download' if self.stop_on_existing else 'skipping'}.",
                    fg="yellow",
                )
                if self.stop_on_existing:
                    return True
                continue

            self.fetch_binary(v.url, file_path)
            self.mark_as_downloaded(v.video_id)
        return False

    def attachment_path(
        self,
        attachment_id: str,
        attachment_url: str,
        date: datetime,
        filename_prefix: str,
        original_name: str | None,
    ) -> Path:
        """Pick a destination path for a non-image attachment (file or video).

        Uses the original filename if available (sanitised, with date prefix
        for sortability and id suffix to disambiguate); otherwise falls back
        to the same filename pattern images use, with the extension derived
        from the URL path."""
        date_dir = date.strftime("%Y-%m-%d")
        dir_path = Path(self._pictures_folder, date_dir)
        dir_path.mkdir(parents=True, exist_ok=True)

        if original_name:
            safe = (
                "".join(
                    c if c.isalnum() or c in "-_." else "_" for c in original_name
                ).strip("._")
                or attachment_id
            )
            stem, ext = os.path.splitext(safe)
            filename = (
                f"{filename_prefix}-{date.strftime('%Y-%m-%d_%H-%M-%S')}"
                f"-{attachment_id}-{stem}{ext}"
            )
        else:
            ext = os.path.splitext(urlparse(attachment_url).path)[1].lower()
            filename = self.filename_pattern
            filename = filename.replace("%FP", filename_prefix)
            filename = filename.replace("%ID", attachment_id)
            filename = date.strftime(filename) + ext
        return Path(dir_path, filename)

    def fetch_binary(self, url: str, file_path: Path):
        """Stream a URL to disk. Used for non-image attachments where EXIF
        injection doesn't apply."""
        req = urllib.request.Request(url=url)
        with urllib.request.urlopen(req) as r, open(file_path, "wb") as f:
            if r.status != 200:
                raise Exception(f"Broken! {r.read().decode('utf-8')}")
            shutil.copyfileobj(r, f)

    def download_file_path(self, img: BaseImage, filename_prefix: str) -> Path:
        """Generate the file path for the downloaded image."""

        file_ext = os.path.splitext(urlparse(img.url).path)[1].lower()

        # Replace custom patterns first (to avoid collisions with strftime patterns)
        filename = self.filename_pattern
        filename = filename.replace("%FP", filename_prefix)
        filename = filename.replace("%ID", img.img_id)

        filename = img.date.strftime(filename)
        filename = filename + file_ext

        date_dir = img.date.strftime("%Y-%m-%d")
        dir_path = Path(self._pictures_folder, date_dir)
        dir_path.mkdir(parents=True, exist_ok=True)
        return Path(dir_path, filename)

    def fetch_image(self, img: BaseImage, file_path: Path):
        req = urllib.request.Request(url=img.url)

        captured_date_for_exif = img.date.strftime("%Y:%m:%d %H:%M:%S")

        if img.date.tzinfo is not None:
            timezone_offset = img.date.strftime("%z")
            # Convert from +0200 to +02:00 format
            if len(timezone_offset) == 5:
                timezone_offset = timezone_offset[:3] + ":" + timezone_offset[3:]
        else:
            timezone_offset = None

        with urllib.request.urlopen(req) as r, open(file_path, "wb") as f:
            if r.status != 200:
                raise Exception(f"Broken! {r.read().decode('utf-8')}")
            shutil.copyfileobj(r, f)

        try:
            piexif.load(str(file_path.resolve()))
        except piexif.InvalidImageDataError:
            click.secho(
                "Not a JPEG/TIFF or corrupted image, skip exif updating.", fg="yellow"
            )
            return

        # Prepare the EXIF data
        exif_dict = {
            "Exif": {piexif.ExifIFD.DateTimeOriginal: captured_date_for_exif.encode()}
        }

        if timezone_offset:
            exif_dict["Exif"][piexif.ExifIFD.OffsetTimeOriginal] = (
                timezone_offset.encode()
            )

        if img.text:
            exif_dict["Exif"][piexif.ExifIFD.UserComment] = (
                piexif.helper.UserComment.dump(img.text, encoding="unicode")
            )

        # Add GPS data if latitude and longitude are provided
        if self.latitude is not None and self.longitude is not None:
            from fractions import Fraction

            def to_deg(value, loc):
                if value < 0:
                    loc_value = loc[0]
                elif value > 0:
                    loc_value = loc[1]
                else:
                    loc_value = ""
                abs_value = abs(value)
                deg = int(abs_value)
                t1 = (abs_value - deg) * 60
                min_val = int(t1)
                sec = round((t1 - min_val) * 60, 2)
                return deg, min_val, sec, loc_value

            def to_rational(number):
                f = Fraction(number).limit_denominator(10000)
                return (f.numerator, f.denominator)

            lat_deg = to_deg(self.latitude, ["S", "N"])
            lng_deg = to_deg(self.longitude, ["W", "E"])

            exiv_lat = (
                to_rational(lat_deg[0]),
                to_rational(lat_deg[1]),
                to_rational(lat_deg[2]),
            )
            exiv_lng = (
                to_rational(lng_deg[0]),
                to_rational(lng_deg[1]),
                to_rational(lng_deg[2]),
            )

            exif_dict["GPS"] = {  # type: ignore[assignment]
                piexif.GPSIFD.GPSVersionID: (2, 0, 0, 0),
                piexif.GPSIFD.GPSLatitudeRef: lat_deg[3].encode(),
                piexif.GPSIFD.GPSLatitude: exiv_lat,
                piexif.GPSIFD.GPSLongitudeRef: lng_deg[3].encode(),
                piexif.GPSIFD.GPSLongitude: exiv_lng,
            }

        exif_bytes = piexif.dump(exif_dict)

        # Write the EXIF data to the image
        piexif.insert(exif_bytes, str(file_path.resolve()))
