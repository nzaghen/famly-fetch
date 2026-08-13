"""Command-line conversion of a Famly JSON archive to Markdown."""

from pathlib import Path

import click

from famly_fetch.archive import (
    build_render_report,
    write_html,
    write_markdown,
    write_render_report,
)


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
@click.option(
    "--render-report",
    is_flag=True,
    help="Write a detailed JSON audit of weekly grouping and parent-post matching.",
)
def main(archive_json: Path, output: Path | None, render_report: bool):
    """Convert ARCHIVE_JSON into chronological Markdown and styled HTML."""

    output_path = write_markdown(archive_json, output)
    html_path = write_html(archive_json, output_path.with_suffix(".html"))
    click.echo(f"Markdown archive written to {output_path}")
    click.echo(f"Styled HTML archive written to {html_path}")
    report = build_render_report(archive_json)
    weekly = report["weekly_photos"]
    parents = report["parent_feed_posts"]
    matching = report["photo_matching"]
    click.echo(
        "Render summary: "
        f"{weekly['tagged_photo_count']} tagged photos in "
        f"{weekly['weekly_card_count']} weekly cards; "
        f"{parents['matching_post_count']} matching parent posts "
        f"({parents['matching_posts_with_text']} with text, "
        f"{parents['matching_posts_without_text']} empty); "
        f"{matching['unmatched_photo_count']} unmatched photos."
    )
    if render_report:
        report_path = write_render_report(archive_json)
        click.echo(f"Detailed render report written to {report_path}")


if __name__ == "__main__":
    main()
