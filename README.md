# Plex Folder Collection

A small Python script that crawls configured folders for video files and creates/updates Plex collections containing the matching items. If a collection already exists, only items not yet in it are added, so it is safe to run repeatedly (for example from cron).

## How it works

1. Reads the `collections` list from `config.json`.
2. For each configured collection, walks the configured folder recursively and collects every video file (`.mkv`, `.mp4`, `.avi`, `.m4v`, `.mov`, `.ts`, and others).
3. Asks Plex for every item in that collection's configured library together with its file paths.
4. Matches the crawled files to Plex items by path.
5. Creates the collection, or adds the missing items to an existing one.

In TV libraries Plex collections hold shows rather than episodes, so any episode found in the folder adds its whole show to the collection.

## Requirements

- Python 3.14 or newer (managed by `uv`)
- The [`plexapi`](https://github.com/pkkid/python-plexapi) package
- Network access to your Plex Media Server
- A Plex authentication token

## Installation

This project is intended to be run with [`uv`](https://docs.astral.sh/uv/) only.

```bash
uv sync
```

Then run the script with:

```bash
uv run main.py
```

## Getting your Plex token

On the Plex server itself, the token is stored in `Preferences.xml`:

```bash
sudo grep -o 'PlexOnlineToken="[^"]*"' \
  "/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Preferences.xml"
```

The value between the quotes is your token. The file lives elsewhere for some installs:

| Install type | Location of `Preferences.xml` |
|---|---|
| Standard package (deb/rpm) | `/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/` |
| Snap | `/var/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/` |
| Docker | Inside the folder mapped to `/config` |

If you are unsure, search for it:

```bash
sudo find / -name Preferences.xml -path "*Plex Media Server*" 2>/dev/null
```

Alternatively, open Plex Web, press F12, go to the Network tab, filter for `X-Plex-Token` and refresh the page. The token appears in the request URLs.

Keep the token private, since it grants full access to your server.

## Usage

The script reads collection definitions from `config.json` and processes each one.

For a sample config you can safely store in GitHub, copy `config.example.json` to `config.json` and replace the placeholder values with your own:

```bash
cp config.example.json config.json
export PLEX_TOKEN=your_token_here
uv run main.py [options]
```

### `config.json` collection format

```json
{
  "plex-url": "http://localhost:32400",
  "plex-token": "your_token_here",
  "map-path": "/mnt/media:/data",
  "logging": {
    "level": "WARNING",
    "console": true,
    "file": true,
    "file-path": "plex_collection.log"
  },
  "collections": [
    {
      "name": "Action",
      "library": "Movies",
      "path": "/mnt/media/movies/Action"
    }
  ]
}
```

### Arguments

| Argument | Description |
|---|---|
| `--collection` | Only process the collection with this name from `config.json` |
| `--dry-run` | Show what would happen without changing anything |

Connection values are read from `config.json` (`plex-url`, `plex-token`) or environment variables (`PLEX_URL`, `PLEX_TOKEN`). The optional `map-path` config value translates prefixes when Plex sees files under a different path.

### Logging configuration

Logging is configured from `config.json` using a `logging` object:

```json
{
  "logging": {
    "level": "WARNING",
    "console": true,
    "file": true,
    "file-path": "plex_collection.log"
  }
}
```

Defaults (if omitted) are:

- `level`: `WARNING`
- `console`: `true`
- `file`: `true`
- `file-path`: `plex_collection.log` (relative paths are resolved from the project folder)

Valid levels are `DEBUG`, `INFO`, `WARNING`, `ERROR`, and `CRITICAL`.

### Examples

Process all configured collections (preview first, then apply):

```bash
uv run main.py --dry-run
uv run main.py
```

Process only one configured collection:

```bash
uv run main.py --collection "Action"
```

### Plex in Docker (path mapping)

Files are matched by their full path, so the script must see the same paths Plex does. If Plex runs in a container where `/mnt/media` on the host is mounted as `/data`, add the prefix mapping to `config.json`:

```json
{
  "map-path": "/mnt/media:/data"
}
```

A long list of "not in Plex" files in the output is usually a sign that path mapping is needed.

## Running via crontab

To keep the collections in sync automatically, add a cron entry with `crontab -e`:

```bash
crontab -e
```

Then add a line similar to this:

```cron
0 4 * * * cd /path/to/project && PLEX_TOKEN=your_token_here /home/your-user/.local/bin/uv run main.py >> /home/your-user/plex_collection.log 2>&1
```

Notes:

- Replace `/path/to/project` with the folder where this repository is checked out.
- Replace `your_token_here` with your Plex token, or export it in the cron environment if you prefer.
- Use the full path to `uv` if it is not already on the cron `PATH`.
- The `>> /home/your-user/plex_collection.log 2>&1` part keeps a log of the script output in a user-writable location.
- `/var/log` is usually root-owned. If you use it, create the file with appropriate permissions first.

If you want to ensure cron has the same shell environment as your normal login shell, you can also wrap the command like this:

```cron
0 4 * * * /bin/bash -lc 'cd /path/to/project && export PLEX_TOKEN=your_token_here && ~/.local/bin/uv run main.py >> ~/plex_collection.log 2>&1'
```

This makes the script run via `uv`, which ensures the project environment and lockfile are used correctly.

## Troubleshooting

**`No token: set PLEX_TOKEN or add plex-token to config.json`**
Export the token or add it in `config.json`. See [Getting your Plex token](#getting-your-plex-token).

**`No collections configured: add a non-empty 'collections' list in config.json`**
Add a `collections` array to `config.json`. Each entry must include `name`, `library`, and `path`.

**Files listed as "not in Plex"**
Either Plex has not scanned them yet (refresh the library and try again), they belong to a different library than the one configured in `config.json`, or the paths differ between the script and Plex (set `map-path` in `config.json`).

**`NotFound` error for the library**
The `library` name in `config.json` must match the library name shown in Plex exactly, including capitalisation and spaces.

**`permission denied: /var/log/plex_collection.log`**
Your user cannot write to `/var/log`. Use a user-writable log file path instead (for example `~/plex_collection.log`), or create and chown the log file with sudo.

**`Fatal Python error: init_fs_encoding ... No module named 'encodings'`**
This is a Python environment problem, not a problem with the script. Common fixes:

- Clear stray variables: `unset PYTHONHOME PYTHONPATH`, then remove them from `~/.bashrc`, `~/.profile` or `/etc/environment`.
- Recreate the project environment with `uv`: `rm -rf .venv && uv sync`.
- Check which interpreter runs: `which -a python3`. If the system Python itself is broken on Debian/Ubuntu, run `sudo apt install --reinstall python3-minimal libpython3-stdlib`.

**`401 Unauthorized`**
The token is wrong or expired. Fetch it again from `Preferences.xml`.

**`Invalid logging configuration: ...`**
The `logging` section in `config.json` has invalid types or values. Check that:
- `logging.level` is one of `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`
- `logging.console` and `logging.file` are booleans
- at least one of `logging.console` or `logging.file` is `true`
- `logging.file-path` is a non-empty string

## Supported file types

`.mkv .mp4 .avi .m4v .mov .wmv .ts .m2ts .mpg .mpeg .webm .flv`

To add more, extend the `VIDEO_EXT` set at the top of the script.