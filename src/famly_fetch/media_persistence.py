"""Safe local persistence for downloaded Famly media."""

from __future__ import annotations

import os
import shutil
import tempfile
import urllib.request
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from urllib.parse import urlparse

import click
import piexif
import piexif.helper

from famly_fetch.image import BaseImage
from famly_fetch.network import open_famly_media


class MediaPersistence:
    """Generate media paths and commit validated downloads atomically."""

    def __init__(
        self,
        root: Path,
        filename_pattern: str = "%FP-%Y-%m-%d_%H-%M-%S-%ID",
        latitude: float | None = None,
        longitude: float | None = None,
    ):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.filename_pattern = filename_pattern
        self.latitude = latitude
        self.longitude = longitude

    def protect_output_folder(self):
        """Keep generated personal data out of an accidental Git commit."""

        ignore_path = self.root / ".gitignore"
        if not ignore_path.exists():
            ignore_path.write_text("*\n!.gitignore\n", encoding="utf-8")

    def attachment_path(
        self,
        attachment_id: str,
        attachment_url: str,
        date: datetime,
        filename_prefix: str,
        original_name: str | None,
    ) -> Path:
        """Return a contained, collision-safe path for a file or video."""

        directory = self.root / date.strftime("%Y-%m-%d")
        directory.mkdir(parents=True, exist_ok=True)

        if original_name:
            safe = (
                "".join(
                    character if character.isalnum() or character in "-_." else "_"
                    for character in original_name
                ).strip("._")
                or attachment_id
            )
            stem, extension = os.path.splitext(safe)
            filename = (
                f"{filename_prefix}-{date.strftime('%Y-%m-%d_%H-%M-%S')}"
                f"-{attachment_id}-{stem}{extension}"
            )
        else:
            extension = os.path.splitext(urlparse(attachment_url).path)[1].lower()
            filename = self._render_pattern(date, filename_prefix, attachment_id)
            filename += extension
        return self._contained_path(directory, filename)

    def image_path(self, image: BaseImage, filename_prefix: str) -> Path:
        """Return a contained, collision-safe path for an image."""

        extension = os.path.splitext(urlparse(image.url).path)[1].lower()
        filename = self._render_pattern(image.date, filename_prefix, image.img_id)
        directory = self.root / image.date.strftime("%Y-%m-%d")
        directory.mkdir(parents=True, exist_ok=True)
        return self._contained_path(directory, filename + extension)

    def _render_pattern(
        self, date: datetime, filename_prefix: str, media_id: str
    ) -> str:
        pattern = self.filename_pattern
        if "%ID" not in pattern:
            pattern += "-%ID"
        pattern = pattern.replace("%FP", filename_prefix)
        pattern = pattern.replace("%ID", media_id)
        return date.strftime(pattern)

    def _contained_path(self, directory: Path, filename: str) -> Path:
        path = (directory / filename).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as error:
            raise ValueError(
                f"Generated filename leaves the pictures folder: {filename}"
            ) from error
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def fetch_binary(self, url: str, file_path: Path):
        """Download a non-image attachment and atomically commit it."""

        temporary_path = self._fetch_to_temporary_file(url, file_path)
        self._commit_temporary_file(temporary_path, file_path)

    def fetch_image(self, image: BaseImage, file_path: Path):
        """Download an image, add available EXIF metadata, and commit it."""

        temporary_path = self._fetch_to_temporary_file(image.url, file_path)
        try:
            piexif.load(str(temporary_path.resolve()))
        except piexif.InvalidImageDataError:
            click.secho(
                "Not a JPEG/TIFF or corrupted image, skip exif updating.", fg="yellow"
            )
            self._commit_temporary_file(temporary_path, file_path)
            return
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise

        try:
            exif = self._image_exif(image)
            piexif.insert(piexif.dump(exif), str(temporary_path.resolve()))
            self._commit_temporary_file(temporary_path, file_path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise

    def _image_exif(self, image: BaseImage) -> dict:
        captured_date = image.date.strftime("%Y:%m:%d %H:%M:%S").encode()
        exif = {
            "0th": {piexif.ImageIFD.DateTime: captured_date},
            "Exif": {
                piexif.ExifIFD.DateTimeOriginal: captured_date,
                piexif.ExifIFD.DateTimeDigitized: captured_date,
            },
        }

        if image.date.tzinfo is not None:
            offset = image.date.strftime("%z")
            if len(offset) == 5:
                offset = offset[:3] + ":" + offset[3:]
            if offset:
                exif["Exif"][piexif.ExifIFD.OffsetTimeOriginal] = offset.encode()

        if image.text:
            exif["Exif"][piexif.ExifIFD.UserComment] = piexif.helper.UserComment.dump(
                image.text, encoding="unicode"
            )

        if self.latitude is not None and self.longitude is not None:
            exif["GPS"] = self._gps_exif(self.latitude, self.longitude)
        return exif

    @classmethod
    def _gps_exif(cls, latitude: float, longitude: float) -> dict:
        lat = cls._coordinate_parts(latitude, "S", "N")
        lng = cls._coordinate_parts(longitude, "W", "E")
        return {
            piexif.GPSIFD.GPSVersionID: (2, 0, 0, 0),
            piexif.GPSIFD.GPSLatitudeRef: lat[3].encode(),
            piexif.GPSIFD.GPSLatitude: tuple(cls._rational(value) for value in lat[:3]),
            piexif.GPSIFD.GPSLongitudeRef: lng[3].encode(),
            piexif.GPSIFD.GPSLongitude: tuple(
                cls._rational(value) for value in lng[:3]
            ),
        }

    @staticmethod
    def _coordinate_parts(value: float, negative: str, positive: str) -> tuple:
        reference = negative if value < 0 else positive
        absolute = abs(value)
        degrees = int(absolute)
        minute_fraction = (absolute - degrees) * 60
        minutes = int(minute_fraction)
        seconds = round((minute_fraction - minutes) * 60, 2)
        return degrees, minutes, seconds, reference

    @staticmethod
    def _rational(number: float) -> tuple[int, int]:
        fraction = Fraction(number).limit_denominator(10000)
        return fraction.numerator, fraction.denominator

    @staticmethod
    def _expected_content_length(response) -> int | None:
        headers = getattr(response, "headers", None)
        value = headers.get("Content-Length") if headers is not None else None
        if value in (None, ""):
            return None
        try:
            expected = int(value)
        except (TypeError, ValueError) as error:
            raise ValueError("Famly returned an invalid Content-Length") from error
        if expected < 0:
            raise ValueError("Famly returned a negative Content-Length")
        return expected

    def _fetch_to_temporary_file(self, url: str, file_path: Path) -> Path:
        request = urllib.request.Request(url=url)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{file_path.name}.",
            suffix=f".part{file_path.suffix}",
            dir=file_path.parent,
            delete=False,
        )
        temporary_path = Path(temporary.name)
        try:
            with temporary, open_famly_media(request) as response:
                if response.status != 200:
                    raise RuntimeError(
                        f"Famly media request returned HTTP {response.status}"
                    )
                expected_length = self._expected_content_length(response)
                shutil.copyfileobj(response, temporary)
                actual_length = temporary.tell()
                temporary.flush()
                os.fsync(temporary.fileno())
            if expected_length is not None and actual_length != expected_length:
                raise IOError(
                    "Incomplete Famly media download: "
                    f"expected {expected_length} bytes, received {actual_length}"
                )
            return temporary_path
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _commit_temporary_file(temporary_path: Path, file_path: Path):
        try:
            temporary_path.replace(file_path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
