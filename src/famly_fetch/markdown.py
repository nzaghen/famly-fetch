"""Command-line conversion of a Famly JSON archive to Markdown."""

from pathlib import Path

import click

from famly_fetch.archive import write_markdown


@click.command()
@click.argument(
    "archive_json",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "-o",
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Markdown output path (default: the JSON filename with .md).",
)
def main(archive_json: Path, output: Path | None):
    """Convert ARCHIVE_JSON into chronological Markdown."""

    output_path = write_markdown(archive_json, output)
    click.echo(f"Markdown archive written to {output_path}")


if __name__ == "__main__":
    main()
