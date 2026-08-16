<!-- @format -->

# famly-fetch

![Static Badge](https://img.shields.io/badge/Python-3-blue?style=flat&logo=Python)
![PyPI](https://img.shields.io/pypi/v/famly-fetch)

Fetch your (kid's) images from famly.co

**NOTICE: I no longer have access to Famly, so I am merely the steward of this
code base. If you create PRs with improvements or bugfixes, please make sure
to test them before submitting them.**

## Local Development

Python 3.10 or newer is required. To run the project locally from source:

```bash
git clone https://github.com/ileodo/famly-fetch.git
cd famly-fetch
python -m venv .venv
```

Activate the virtual environment:

- **Windows:** `.venv\Scripts\activate`
- **Mac/Linux:** `source .venv/bin/activate`

Then install the package in editable mode:

```bash
pip install -e .
famly-fetch
```

To deactivate the virtual environment when done:

```bash
deactivate
```

## Running CI Locally

The GitHub Actions workflow (`.github/workflows/ci.yml`) runs the unit tests,
lints, and format-checks the code across a matrix of Python versions (3.10,
3.11, 3.12, 3.13). You can
reproduce that workflow on your own machine before pushing, using the
`scripts/ci-local.sh` wrapper around [`act`](https://github.com/nektos/act).

It runs the exact same steps as GitHub (checkout, set up Python, install
dependencies, `ruff check`, `ruff format --check`, and unit tests) inside Ubuntu containers, one
per matrix entry, so any failure you see locally matches what CI will report.

The workflow runs in a local Docker image built from `ci-local.Dockerfile`,
which extends the standard `act` runner with Node.js (the stock image has no
`node` on PATH, which the checkout and setup-python actions need). The wrapper
builds this image automatically on first run.

Requirements:

- [`act`](https://github.com/nektos/act) (`brew install act` on macOS)
- A running Docker daemon

Usage:

```bash
# Run all four matrix builds (3.10-3.13) concurrently
scripts/ci-local.sh

# Run only the Python 3.11 build
scripts/ci-local.sh -v 3.11

# List the jobs without running them
scripts/ci-local.sh -l

# Rebuild the local runner image (e.g. after editing ci-local.Dockerfile)
scripts/ci-local.sh -b

# Pass extra flags straight through to act
scripts/ci-local.sh -- --verbose
```

The first run builds the runner image, which can take a minute or two. On Apple
silicon the script automatically requests the `linux/amd64` image so the
containers match GitHub's runners. Run `scripts/ci-local.sh -h` for the full
list of options.

## Get Started

```bash
pip install -e .
famly-fetch
```

Installing from the checked-out directory ensures the code you reviewed is the
code that runs. Installing the separately published PyPI package is not covered
by this repository review.

Enter your email and password when prompted, or provide an access token for
authentication. Run `famly-fetch --help` to get the full help page.

### Bright Horizons and two-factor authentication

The Bright Horizons Family App is a supported Famly deployment. Pass its public
application URL; the strict network policy maps it to the matching API origin:

```bash
famly-fetch \
  --famly-base-url https://familyapp.brighthorizons.co.uk \
  --child Riley \
  --journey --notes --tagged-post-text --include-files --include-videos \
  --export-text --pictures-folder pictures-riley
```

If the account requires two-factor authentication, the command prompts for the
current authenticator-app code only after the password has been accepted. It
also prompts for a login context when the account offers more than one. For a
non-interactive run, use `--login-context` with the displayed name or ID and
provide `FAMLY_TWO_FACTOR_CODE` in the environment. A recovery code can be
provided with `--recovery-code` or `FAMLY_RECOVERY_CODE` instead. Do not put
passwords, authenticator codes, recovery codes, or access tokens directly in
shell history.

Initial two-factor setup, including scanning the QR code, must be completed in
the Famly or Bright Horizons web app. The downloader supports subsequent login
challenges but does not enroll a new authenticator.

If the login contains more than one child, use `--child` with an exact child name
or Famly child ID. Use a separate output folder for each child so their archives
and download state remain independent:

```bash
famly-fetch --child Riley --export-text --journey \
  --pictures-folder pictures-riley
famly-fetch --child Rowan --export-text --journey \
  --pictures-folder pictures-rowan
```

Enrich each archive's weekly photos with matching parent-post text using the
same child and folder:

```bash
famly-fetch --child Riley --export-text --tagged-post-text \
  --pictures-folder pictures-riley
famly-fetch --child Rowan --export-text --tagged-post-text \
  --pictures-folder pictures-rowan
```

Names are matched exactly without regard to uppercase or lowercase. If two
children have the same name, the command reports their IDs so one can be selected
unambiguously. The option applies to tagged photos, Journey entries, and notes.
It cannot be combined with `--messages`, `--liked`, or `--feed`, because those
account-wide sources cannot be reliably restricted to one child.

Archives created before child selection was stored may have inferred a combined
title from shared observations. To add the selected child to an existing archive
without downloading any images again, run:

```bash
famly-fetch --child Riley --no-tagged --export-text \
  --pictures-folder pictures-riley
```

This updates the archive metadata and regenerates its Markdown and HTML in the
same folder. Subsequent standalone generation keeps the corrected title.

Downloaded images will be stored in the `pictures` directory of the
the folder where you run this program from.

Every newly created output folder receives a small `.gitignore` that excludes
its contents, reducing the risk of accidentally committing private photos or
archive text when the repository is under Git. Existing `.gitignore` files are
never overwritten. Keeping personal output outside the repository is still the
safest arrangement.

By default, it will only download images where you have tagged your child. The
timestamp supplied by Famly is embedded in the filename and EXIF metadata. Famly
has removed the camera's original metadata, so this is not guaranteed to be the
camera's true capture time. Journey photos use the observation date when Famly
provides one and otherwise use the publication timestamp.
For journey, notes and messages, the associated text is also added as an exif
comment unless disabled with `--no-text-comments`.

The images have been stripped for any metadata including EXIF
information by Famly. You can optionally add GPS coordinates to the EXIF
data of all downloaded images by providing latitude and longitude values.

The `--stop-on-existing` option is helpful if you wish to download
images continously and just want to download what is new since last
download.

### Strict supported-Famly networking

famly-fetch enforces a strict outbound-network policy:

- Email, password, two-factor answers, access tokens, and API request bodies can
  be sent only to `https://app.famly.co` or the exact Bright Horizons API origin
  `https://famlyapi.familyapp.brighthorizons.co.uk`, over HTTPS.
- Passing `https://familyapp.brighthorizons.co.uk` as the base URL maps to that
  API origin; arbitrary self-hosted or lookalike hosts remain blocked.
- Images, videos, and attachments can be downloaded only from `famly.co`, an
  HTTPS subdomain such as `img.famly.co`, or the exact Bright Horizons media
  host `img.familyapp.brighthorizons.co.uk`.
- Every redirect and the final response URL are checked against the same policy.
- Environment-configured HTTP and HTTPS proxies are disabled so credentials and
  downloads cannot be routed through another service.
- Remote media URLs and credentials are not stored in `archive.json` or
  `archive.md`.

If Famly returns an Amazon S3, CloudFront, or any other unsupported URL, the
download is deliberately blocked before connecting. The command reports the
blocked hostname and saves whatever archive data it had safely processed so far.

The production Dockerfile also installs the checked-out local source rather than
downloading a potentially different `famly-fetch` package from PyPI.

### Downloading all feed images

The `-f` / `--feed` flag downloads all images from all nursery feed posts, regardless of likes or tags:

```bash
famly-fetch -f
```

Images are organised into subdirectories named by the post date (e.g. `pictures/2026-02-19/`), so all photos from posts on the same day are grouped together.

> **Important privacy notice:** Using `-f` will download *all* images from the nursery feed, including photos of other children who are not your own. These images are shared by the nursery within a trusted setting. As a user of this tool you are solely responsible for handling these images with care — keep them private, do not share them further, and ensure they are stored securely. Delete any images of other children if you do not need them.

### State management

famly-fetch tracks downloaded images in a state file to avoid re-downloading them.
By default, this state file is stored as `state.json` in your pictures folder.

You can customize the state file location using the `--state-file` option.

If you need to start over, simply delete (or move) the state file.

### Downloading non-image attachments and videos

Use `--include-files` to download non-image file attachments (PDFs, documents, and similar files) from messages, notes, and learning journey entries:

```bash
famly-fetch --include-files
```

Use `--include-videos` to download videos from learning journey observations and feed posts:

```bash
famly-fetch --include-videos
```

Both flags can be combined with any other flags. They reuse the same `state.json` tracking and the same date-grouped folder layout as image downloads, so re-running will skip already-downloaded files. Non-image content is stored as-is without any EXIF metadata added.

### Exporting text and a readable local archive

Use `--export-text` with messages, notes, or Journey downloads to write three
files inside the pictures folder:

- `archive.json` contains structured entries with dates, authors, child details,
  text, assessment results, and paths to associated local media.
- `archive.md` presents those entries from oldest to newest, with photos beneath
  the post they belong to.
- `archive.html` provides a centered, card-style reading view with responsive
  photo grids and explicit UTF-8 encoding.

```bash
famly-fetch --export-text --journey --notes --messages \
  --include-files --include-videos
```

The archive keeps dates, authors, text, child details, assessment results,
conversation IDs, and paths to associated local photos, videos, and files.
Entries may contain only text, only media, or both. Re-running safely merges
entries by stable ID, so separate downloads do not discard content already
archived.

Journey exports include regular and parent observations, assessments, two-year
progress checks, and up-to-speed observations. Structured assessments retain
their configuration, learning areas, selected options or age bands, per-area
notes, custom fields, observed date, and "what's next" text.

Tagged photos are archived automatically unless `--no-tagged` is used. A
tagged-photo entry is suppressed only when the same photo is already attached
to a Journey entry; a duplicate in a feed post does not suppress it.

Fetch newly tagged photos and archive their parent feed-post text in one run:

```bash
famly-fetch --export-text --tagged-post-text --pictures-folder pictures
```

The saved state skips photos already downloaded. The command then scans feed
metadata from Famly, retains only posts matching archived tagged-photo image
IDs, and refers to the local files. It does not request feed image files or
retain unmatched posts.

To enrich only photos already in the archive without downloading newly tagged
photos, add `--no-tagged`:

```bash
famly-fetch --no-tagged --export-text --tagged-post-text \
  --pictures-folder pictures
```

Standalone tagged photos are grouped into one grid per calendar week in
Markdown and HTML. Their exact dates and captions remain visible, while Journey
observations and text posts remain separate sections. When a feed post contains
one of those tagged photo IDs, its body appears once as the week's description.
This presentation does not alter the individual entries in `archive.json`.

Single photos use normal Markdown images. Posts with several photos use a
clickable, dependency-free table layout so the complete portrait or landscape
image remains visible. Structured assessment details and "what's next" text are
shown below the observation narrative.

The HTML archive is recommended for browsing because Markdown applications do
not consistently support page-width styling. It uses local system fonts and
local archive media only. Photos preserve their complete aspect ratio. Clicking
one opens a full-screen gallery; arrow buttons, keyboard arrow keys, or a swipe
move through every photo visible in the active filtered view, and Escape closes
it. The dependency-free gallery code is embedded in the HTML—there is no
library, CDN, web font, analytics, or network request. The content security
policy blocks remote connections. Controls and layouts are sized responsively
for current iPhones and iPads as well as desktop browsers.

The filter bar can show everything or narrow the archive to weekly photos,
Journey observations, assessments and progress reviews, or other entries such
as notes and messages. Parent feed posts already used to describe weekly-photo
cards are not repeated as standalone cards, although they remain unchanged in
`archive.json`. The Other filter is omitted when it would be empty. Filtering
runs entirely in the local file.

Regenerate Markdown and HTML directly from the saved JSON without contacting
Famly:

```bash
famly-fetch-markdown pictures/archive.json
famly-fetch-markdown pictures/archive.json --output family-archive.md
```

The first command writes `archive.md` and `archive.html`. A custom Markdown
output name produces the corresponding HTML name beside it.

The command prints a summary distinguishing tagged photos, weekly cards,
matching parent posts, empty post bodies, and unmatched photos. For a detailed
per-week audit with entry and media IDs, add `--render-report`:

```bash
famly-fetch-markdown pictures/archive.json --render-report
```

This also writes `pictures/archive.render-report.json`. A weekly card can
contain several parent posts and many photos.

#### Hiding entries from Markdown and HTML

To hide an entry from the readable archive, create `archive.exclude.json`
beside `archive.json`. Copy the structure from
[`archive.exclude.example.json`](archive.exclude.example.json):

```json
{
  "excluded_entry_ids": [
    "journey:replace-with-the-entry-id",
    "tagged_photo:replace-with-the-photo-id"
  ]
}
```

Use the exact `entry_id` from `archive.json` and remove the placeholder values.
The listed entries are omitted from generated Markdown and HTML only: the JSON,
download state, and local media are not changed. Removing an ID restores that
entry on the next generation without fetching it again.

No extra option is needed. Keep `archive.exclude.json` beside `archive.json`
and run:

```bash
famly-fetch-markdown pictures/archive.json
```

Invalid exclusion JSON stops readable-file generation with an error instead of
silently showing entries that were meant to be hidden.

Records use this general shape (fields are empty where they do not apply):

```json
{
  "entry_id": "message:message-id",
  "source": "message",
  "kind": "message",
  "date": "2026-02-19T10:30:00Z",
  "author": {"name": "Staff member"},
  "children": [],
  "text": "The message text",
  "media": [
    {
      "media_id": "image-id",
      "kind": "photo",
      "local_path": "2026-02-19/message-...jpg"
    }
  ],
  "metadata": {"conversation_id": "conversation-id"}
}
```

### Customizing Filenames

You can customize the filename format using the `--filename-pattern` option.
The pattern supports custom placeholders and standard strftime date/time formats.
The file extension is automatically appended. If `%ID` is omitted, famly-fetch
adds it automatically so two photos with the same timestamp cannot overwrite one
another.

**Custom placeholders:**

- `%FP` - Filename prefix (e.g., child name, "note", "message", "journey")
- `%ID` - Image ID

**Date/time formats:**
All standard strftime format codes are supported (e.g. `%Y`, `%m`, `%d` etc.)

**Default pattern:** `%FP-%Y-%m-%d_%H-%M-%S-%ID`

This produces filenames like: `child-name-2024-01-15_14-30-45-abc123.jpg`

## Command Line Help

```bash
Usage: famly-fetch [OPTIONS]

  Fetch kids' images from Famly.

Options:
  --email EMAIL                   Your Famly account email, can be set via
                                  FAMLY_EMAIL env var
  --password PASSWORD             Your Famly account password, can be set via
                                  FAMLY_PASSWORD env var
  --access-token TOKEN            Your Famly access token, can be set via
                                  FAMLY_ACCESS_TOKEN env var
  --famly-base-url URL            Famly application or API base URL; supports
                                  Famly and Bright Horizons
  --login-context NAME_OR_ID      Login context name or ID when Famly offers
                                  more than one
  --two-factor-code CODE          Authenticator-app code; prompted when
                                  required if omitted
  --recovery-code CODE            Famly two-factor recovery code
  --child NAME_OR_ID              Process one child by exact name or Famly
                                  child ID
  --no-tagged                     Don't download tagged images
  -j, --journey                   Download images from child Learning Journey
  -n, --notes                     Download images from child notes
  -m, --messages                  Download images from messages
  -l, --liked                     Download images which is liked by the
                                  parents from all posts (in the feed)
  -f, --feed                      Download all images from all posts (in the
                                  feed)
  --tagged-post-text              Archive parent feed-post text for tagged
                                  photos
  --include-files                 Also download non-image file attachments
                                  (PDFs, docs, etc.) from messages, notes, and
                                  journeys
  --include-videos                Also download videos from learning journey
                                  observations and feed posts
  --export-text                   Write a structured archive.json and
                                  chronological archive.md alongside downloads
  -p, --pictures-folder DIRECTORY
                                  Directory to save downloaded pictures, can
                                  be set via FAMLY_PICTURES_FOLDER env var
                                  [default: pictures]
  -e, --stop-on-existing          Stop downloading when an already downloaded
                                  file is encountered
  -u, --user-agent                User Agent used in Famly requests, can be
                                  set via FAMLY_USER_AGENT env var  [default:
                                  famly-fetch/<version>]
  --latitude LAT                  Latitude for EXIF GPS data, can be set via
                                  LATITUDE env var
  --longitude LONG                Longitude for EXIF GPS data, can be set via
                                  LONGITUDE env var
  --text-comments / --no-text-comments
                                  Add observation and message body text to
                                  image EXIF UserComment field
  --filename-pattern PATTERN      Filename pattern. Custom patterns: %FP
                                  (prefix), %ID (image ID). Supports strftime
                                  formats (e.g., %Y, %m, %d). File extension
                                  is automatically appended. Can be set via
                                  FAMLY_FILENAME_PATTERN env var  [default:
                                  %FP-%Y-%m-%d_%H-%M-%S-%ID]
  --state-file FILE               Path to state file for tracking downloaded
                                  images, can be set via FAMLY_STATE_FILE env
                                  var  [default: (<pictures-
                                  folder>/state.json)]
  --version                       Show the version and exit.
  --help                          Show this message and exit.
```

## Known Issues

### Connection reset error

When downloading a large number of images, you may see:

```
An exception occurred: <urlopen error [WinError 10054] An existing connection was forcibly closed by the remote host>
```

This is caused by the Famly server closing the connection after too many requests. Simply re-run the same command — already downloaded images are tracked in `state.json` and will be skipped, so the download will resume from where it left off.

## Docker

If you have Docker set up you can easily run as follows:

Build the container:

```bash
docker build -t famly-fetch -f dev.Dockerfile .
docker run --rm -it -v $PWD/pictures:/app/pictures famly-fetch
```

Or use docker compose workflow

```bash
docker compose build
docker compose run --rm app
```

The Compose configuration prompts for credentials interactively instead of
placing a Famly password in persistent container environment metadata.
