from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import click

from famly_fetch.downloader import FamlyDownloader


def get_version():
    try:
        return version("famly-fetch")
    except PackageNotFoundError:
        return "unknown"


def _select_children(children, selector):
    """Select one child by exact ID or case-insensitive exact name."""

    unique_children = []
    seen_ids = set()
    for child in children:
        child_id = str(child[0])
        if child_id in seen_ids:
            continue
        seen_ids.add(child_id)
        unique_children.append(child)
    children = unique_children

    if not selector:
        return children

    selector = selector.strip()
    id_matches = [child for child in children if str(child[0]) == selector]
    if id_matches:
        return id_matches

    name_matches = [
        child for child in children if str(child[1]).casefold() == selector.casefold()
    ]
    if len(name_matches) == 1:
        return name_matches
    if len(name_matches) > 1:
        matching_ids = ", ".join(str(child_id) for child_id, _ in name_matches)
        raise click.UsageError(
            f'More than one child is named "{selector}". '
            f"Use their Famly child ID instead: {matching_ids}"
        )

    available = ", ".join(
        f"{first_name} ({child_id})" for child_id, first_name in children
    )
    if not available:
        available = "none"
    raise click.UsageError(
        f'No child matched "{selector}". Available children: {available}'
    )


def _authentication_context_label(choice: dict) -> str:
    context = choice.get("context") or {}
    target = context.get("target") or {}
    if target.get("__typename") == "InstitutionSet" and target.get("title"):
        return str(target["title"])
    if target.get("__typename") == "PersonContextTarget":
        names = []
        for child in target.get("children") or []:
            name = child.get("name") or {}
            child_name = name.get("fullName") or name.get("firstName")
            if child_name:
                names.append(str(child_name))
        if names:
            return ", ".join(names)
    return str(context.get("id") or "Unnamed context")


def _authentication_challenge_resolver(
    login_context: str | None,
    two_factor_code: str | None,
    recovery_code: str | None,
):
    unused_two_factor_code = two_factor_code
    unused_recovery_code = recovery_code

    def resolve(challenge: dict) -> dict:
        nonlocal unused_two_factor_code, unused_recovery_code
        choices = challenge.get("choices") or []
        if not choices:
            raise click.ClickException("Famly returned no available login contexts")

        selected = None
        if login_context:
            selector = login_context.strip().casefold()
            matches = [
                choice
                for choice in choices
                if selector
                in {
                    str((choice.get("context") or {}).get("id") or "").casefold(),
                    _authentication_context_label(choice).casefold(),
                }
            ]
            if len(matches) == 1:
                selected = matches[0]
            elif len(matches) > 1:
                context_ids = ", ".join(
                    str((choice.get("context") or {}).get("id")) for choice in matches
                )
                raise click.UsageError(
                    f'More than one login context matched "{login_context}". '
                    f"Use a context ID instead: {context_ids}"
                )
            else:
                available = ", ".join(
                    f"{_authentication_context_label(choice)} "
                    f"({(choice.get('context') or {}).get('id')})"
                    for choice in choices
                )
                raise click.UsageError(
                    f'No login context matched "{login_context}". '
                    f"Available contexts: {available}"
                )
        elif len(choices) == 1:
            selected = choices[0]
        else:
            click.echo("Famly requires a login context:")
            for index, choice in enumerate(choices, start=1):
                click.echo(f"  {index}. {_authentication_context_label(choice)}")
            selected = choices[
                click.prompt(
                    "Select login context",
                    type=click.IntRange(1, len(choices)),
                )
                - 1
            ]

        context = selected.get("context") or {}
        required_values = {
            "deviceId": challenge.get("deviceId"),
            "loginId": challenge.get("loginId"),
            "expiresAt": challenge.get("expiresAt"),
            "userContextId": context.get("id"),
            "hmac": selected.get("hmac"),
        }
        if any(value is None for value in required_values.values()):
            raise click.ClickException(
                "Famly returned an incomplete authentication challenge"
            )

        submitted_two_factor_code = None
        submitted_recovery_code = None
        if selected.get("requiresTwoFactor"):
            if unused_recovery_code:
                submitted_recovery_code = unused_recovery_code
                unused_recovery_code = None
            else:
                code = unused_two_factor_code
                unused_two_factor_code = None
                if not code:
                    code = click.prompt(
                        "Enter the code from your authenticator app",
                        hide_input=True,
                        type=str,
                    )
                try:
                    submitted_two_factor_code = int(code)
                except (TypeError, ValueError) as error:
                    raise click.UsageError(
                        "The two-factor code must contain digits only"
                    ) from error

        return {
            **required_values,
            "twoFactorCode": submitted_two_factor_code,
            "recoveryCode": submitted_recovery_code,
        }

    return resolve


@click.command()
@click.option(
    "--email",
    envvar="FAMLY_EMAIL",
    help="Your Famly account email, can be set via FAMLY_EMAIL env var",
    metavar="EMAIL",
    type=str,
)
@click.option(
    "--password",
    envvar="FAMLY_PASSWORD",
    help="Your Famly account password, can be set via FAMLY_PASSWORD env var",
    metavar="PASSWORD",
    hide_input=True,
    type=str,
)
@click.option(
    "--access-token",
    envvar="FAMLY_ACCESS_TOKEN",
    help="Your Famly access token, can be set via FAMLY_ACCESS_TOKEN env var",
    metavar="TOKEN",
    type=str,
)
@click.option(
    "--famly-base-url",
    envvar="FAMLY_BASE_URL",
    help="Famly application or API base URL; supports Famly and Bright Horizons",
    metavar="URL",
    default="https://app.famly.co",
    type=str,
)
@click.option(
    "--login-context",
    envvar="FAMLY_LOGIN_CONTEXT",
    help="Login context name or ID when Famly offers more than one",
    metavar="NAME_OR_ID",
    type=str,
)
@click.option(
    "--two-factor-code",
    envvar="FAMLY_TWO_FACTOR_CODE",
    help="Authenticator-app code; prompted when required if omitted",
    metavar="CODE",
    hide_input=True,
    type=str,
)
@click.option(
    "--recovery-code",
    envvar="FAMLY_RECOVERY_CODE",
    help="Famly two-factor recovery code",
    metavar="CODE",
    hide_input=True,
    type=str,
)
@click.option(
    "--child",
    "child_selector",
    help="Process one child by exact name or Famly child ID",
    metavar="NAME_OR_ID",
    type=str,
)
@click.option("--no-tagged", is_flag=True, help="Don't download tagged images")
@click.option(
    "-j", "--journey", is_flag=True, help="Download images from child Learning Journey"
)
@click.option("-n", "--notes", is_flag=True, help="Download images from child notes")
@click.option("-m", "--messages", is_flag=True, help="Download images from messages")
@click.option(
    "-l",
    "--liked",
    is_flag=True,
    help="Download images which is liked by the parents from all posts (in the feed)",
)
@click.option(
    "-f",
    "--feed",
    is_flag=True,
    help="Download all images from all posts (in the feed)",
)
@click.option(
    "--tagged-post-text",
    is_flag=True,
    help="Archive parent feed-post text for tagged photos",
)
@click.option(
    "--include-files",
    is_flag=True,
    help="Also download non-image file attachments (PDFs, docs, etc.) from messages, notes, and journeys",
)
@click.option(
    "--include-videos",
    is_flag=True,
    help="Also download videos from learning journey observations and feed posts",
)
@click.option(
    "--export-text",
    is_flag=True,
    help="Write a structured archive.json and chronological archive.md alongside downloads",
)
@click.option(
    "-p",
    "--pictures-folder",
    envvar="FAMLY_PICTURES_FOLDER",
    type=click.Path(
        file_okay=False,
        dir_okay=True,
        exists=False,
        writable=True,
        resolve_path=True,
        path_type=Path,
    ),
    default="pictures",
    show_default=True,
    help="Directory to save downloaded pictures, can be set via FAMLY_PICTURES_FOLDER env var",
)
@click.option(
    "-e",
    "--stop-on-existing",
    is_flag=True,
    help="Stop downloading when an already downloaded file is encountered",
)
@click.option(
    "-u",
    "--user-agent",
    envvar="FAMLY_USER_AGENT",
    default=f"famly-fetch/{get_version()}",
    help="User Agent used in Famly requests, can be set via FAMLY_USER_AGENT env var",
    metavar="",
    show_default=True,
    type=str,
)
@click.option(
    "--latitude",
    envvar="LATITUDE",
    type=float,
    help="Latitude for EXIF GPS data, can be set via LATITUDE env var",
    metavar="LAT",
)
@click.option(
    "--longitude",
    envvar="LONGITUDE",
    type=float,
    help="Longitude for EXIF GPS data, can be set via LONGITUDE env var",
    metavar="LONG",
)
@click.option(
    "--text-comments/--no-text-comments",
    is_flag=True,
    default=True,
    help="Add observation and message body text to image EXIF UserComment field",
)
@click.option(
    "--filename-pattern",
    envvar="FAMLY_FILENAME_PATTERN",
    default="%FP-%Y-%m-%d_%H-%M-%S-%ID",
    show_default=True,
    help="Filename pattern. Custom patterns: %FP (prefix), %ID (image ID). Supports strftime formats (e.g., %Y, %m, %d). File extension is automatically appended. Can be set via FAMLY_FILENAME_PATTERN env var",
    metavar="PATTERN",
    type=str,
)
@click.option(
    "--state-file",
    envvar="FAMLY_STATE_FILE",
    type=click.Path(
        file_okay=True,
        dir_okay=False,
        writable=True,
        resolve_path=True,
        path_type=Path,
    ),
    default=None,
    show_default="<pictures-folder>/state.json",
    help="Path to state file for tracking downloaded images, can be set via FAMLY_STATE_FILE env var",
    metavar="FILE",
)
@click.version_option()
def main(
    email: str,
    password: str,
    access_token: str,
    famly_base_url: str,
    login_context: str,
    two_factor_code: str,
    recovery_code: str,
    child_selector: str,
    no_tagged: bool,
    journey: bool,
    notes: bool,
    messages: bool,
    liked: bool,
    feed: bool,
    tagged_post_text: bool,
    include_files: bool,
    include_videos: bool,
    export_text: bool,
    pictures_folder: Path,
    stop_on_existing: bool,
    user_agent: str,
    latitude: float,
    longitude: float,
    text_comments: bool,
    filename_pattern: str,
    state_file: Path,
):
    """Fetch kids' images from Famly."""

    if tagged_post_text and not export_text:
        raise click.UsageError("--tagged-post-text requires --export-text")
    if two_factor_code and recovery_code:
        raise click.UsageError(
            "--two-factor-code and --recovery-code cannot be used together"
        )
    if access_token and (login_context or two_factor_code or recovery_code):
        raise click.UsageError(
            "Login-context and two-factor options cannot be used with --access-token"
        )
    if child_selector and (messages or liked or feed):
        raise click.UsageError(
            "--child cannot be combined with --messages, --liked, or --feed "
            "because those sources cannot be reliably filtered by child"
        )

    if state_file is None:
        state_file = pictures_folder / "state.json"

    # Validate authentication parameters
    if not access_token and (not email or not password):
        if not email:
            email = click.prompt("Enter your famly.co email address", type=str)
        if not password:
            password = click.prompt(
                "Enter your famly.co password", hide_input=True, type=str
            )

    if access_token and (email or password):
        click.secho(
            "Warning: Both access token and email/password provided. Using access token.",
            fg="yellow",
        )

    famly_downloader = None
    caught_error = None
    try:
        famly_downloader = FamlyDownloader(
            email=email,
            password=password,
            famly_base_url=famly_base_url,
            pictures_folder=pictures_folder,
            stop_on_existing=stop_on_existing,
            text_comments=text_comments,
            state_file=state_file,
            user_agent=user_agent,
            access_token=access_token,
            latitude=latitude,
            longitude=longitude,
            filename_pattern=filename_pattern,
            include_files=include_files,
            include_videos=include_videos,
            export_text=export_text,
            challenge_resolver=_authentication_challenge_resolver(
                login_context, two_factor_code, recovery_code
            ),
        )

        if messages:
            famly_downloader.download_images_from_messages()

        # Process each child
        parent_ids = set()
        children = _select_children(famly_downloader.get_all_children(), child_selector)
        famly_downloader.set_archive_children(children)
        for child_id, first_name in children:
            if liked:
                parent_ids |= famly_downloader.get_parents_ids(child_id)
            if not no_tagged:
                famly_downloader.download_tagged_images(child_id, first_name)
            if journey:
                famly_downloader.download_images_from_learning_journey(
                    child_id, first_name
                )
            if notes:
                famly_downloader.download_images_from_notes(child_id, first_name)

        if liked:
            famly_downloader.download_images_from_feed(parent_ids)

        if tagged_post_text:
            selected_children = children if child_selector else None
            famly_downloader.archive_parent_posts_for_tagged_photos(
                selected_children=selected_children
            )

        if feed:
            famly_downloader.download_all_images_from_feed()

    except click.ClickException as error:
        caught_error = error
    except Exception as e:
        caught_error = click.ClickException(f"An exception occurred: {e}")
    finally:
        if famly_downloader:
            try:
                famly_downloader.save_state()
            except Exception as e:
                state_error = click.ClickException(f"Could not save state: {e}")
                if caught_error is None:
                    caught_error = state_error
                else:
                    caught_error = click.ClickException(
                        f"{caught_error.format_message()}; "
                        f"additionally, {state_error.format_message()}"
                    )
            try:
                famly_downloader.save_archive()
            except Exception as e:
                save_error = click.ClickException(f"Could not save archive: {e}")
                if caught_error is None:
                    caught_error = save_error
                else:
                    caught_error = click.ClickException(
                        f"{caught_error.format_message()}; "
                        f"additionally, {save_error.format_message()}"
                    )

    if caught_error is not None:
        raise caught_error


if __name__ == "__main__":
    main()
