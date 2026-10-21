"""
Crawl configured folders for media files and put matching Plex items into collections.

Run with uv:
    uv sync
    export PLEX_TOKEN=xxxxxxxx
    uv run main.py
"""
import argparse
import json
import logging
import os
import sys

from plexapi.exceptions import NotFound
from plexapi.server import PlexServer

VIDEO_EXT = {
    ".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts", ".m2ts",
    ".mpg", ".mpeg", ".webm", ".flv",
}
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def parse_path_map(value):
    """Return a (local, plex) path-map tuple parsed from config text, if set."""
    if not value or not isinstance(value, str):
        return None
    if ":" not in value:
        return None
    local, plex = value.split(":", 1)
    return os.path.normpath(local), os.path.normpath(plex)


def crawl(folder, path_map):
    """Return the set of media file paths under folder, translated to Plex's view."""
    files = set()
    for root, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            if "sample" in name.lower():
                continue
            if os.path.splitext(name)[1].lower() in VIDEO_EXT:
                path = os.path.normpath(os.path.join(os.path.abspath(root), name))
                if path_map:
                    local, plex = path_map
                    if path.startswith(local):
                        path = plex + path[len(local):]
                files.add(path)
    return files


def build_index(section):
    """Map every file path Plex knows in this library to its item (movie or episode)."""
    if section.type == "show":
        items = section.all(libtype="episode")
    else:
        items = section.all()
    index = {}
    for item in items:
        for media in item.media:
            for part in media.parts:
                index[os.path.normpath(part.file)] = item
    return index


def load_config(path):
    """Load persisted Plex config values from a JSON file, if present."""
    if not os.path.exists(path):
        return {}

    try:
        with open(path) as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def build_logging_config(config, config_path):
    logging_config = config.get("logging") or {}
    if not isinstance(logging_config, dict):
        raise TypeError("'logging' must be an object in config.json")

    level = logging_config.get("level", "WARNING")
    if not isinstance(level, str):
        raise TypeError("'logging.level' must be a string")
    level = level.upper()
    if level not in VALID_LOG_LEVELS:
        raise TypeError(
            "'logging.level' must be one of "
            + ", ".join(sorted(VALID_LOG_LEVELS))
        )

    console = logging_config.get("console", True)
    if not isinstance(console, bool):
        raise TypeError("'logging.console' must be true or false")

    file_enabled = logging_config.get("file", True)
    if not isinstance(file_enabled, bool):
        raise TypeError("'logging.file' must be true or false")

    if not console and not file_enabled:
        raise TypeError("at least one of 'logging.console' or 'logging.file' must be true")

    file_path = logging_config.get("file-path", "plex_collection.log")
    if not isinstance(file_path, str) or not file_path.strip():
        raise TypeError("'logging.file-path' must be a non-empty string")
    if not os.path.isabs(file_path):
        config_dir = os.path.dirname(config_path)
        file_path = os.path.normpath(os.path.join(config_dir, file_path))

    return {
        "level": level,
        "console": console,
        "file": file_enabled,
        "file_path": file_path,
    }


def configure_logging(config, config_path):
    logging_settings = build_logging_config(config, config_path)

    logger = logging.getLogger("plex_collection_creator")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging_settings["level"])
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    if logging_settings["console"]:
        stream_handler = logging.StreamHandler(stream=sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    if logging_settings["file"]:
        log_dir = os.path.dirname(logging_settings["file_path"])
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        file_handler = logging.FileHandler(logging_settings["file_path"], encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    logger.debug(
        "Logging configured with level=%s console=%s file=%s file_path=%s",
        logging_settings["level"],
        logging_settings["console"],
        logging_settings["file"],
        logging_settings["file_path"],
    )
    return logger


def process_collection(plex, collection_config, path_map, dry_run, logger):
    folder = collection_config.get("path")
    collection_name = collection_config.get("name")
    library_name = collection_config.get("library")

    if not folder or not collection_name or not library_name:
        missing = [
            key
            for key in ("path", "name", "library")
            if not collection_config.get(key)
        ]
        logger.warning(
            "Skipping collection config missing required fields "
            f"({', '.join(missing)}): {collection_config}"
        )
        return

    logger.info("==================================================================")
    logger.info(f"Processing '{collection_name}' in library '{library_name}'")
    section = plex.library.section(library_name)

    files = crawl(folder, path_map)
    logger.info("Found %s media files in %s", len(files), folder)

    index = build_index(section)
    items, missing = {}, []
    for f in sorted(files):
        item = index.get(f)
        if item is None:
            missing.append(f)
            continue
        # Collections in TV libraries hold shows, so use the episode's show.
        if item.type == "episode":
            key = item.grandparentRatingKey
            if key not in items:
                items[key] = plex.fetchItem(key)
        else:
            items[item.ratingKey] = item

    for f in missing:
        logger.warning("not in Plex: %s", f)
    items = list(items.values())
    logger.info("Matched %s Plex items, %s files unmatched", len(items), len(missing))

    if not items:
        logger.info("Nothing to add for this collection.")
        return

    added_items = []
    try:
        collection = section.collection(collection_name)
        existing = {i.ratingKey for i in collection.items()}
        new = [i for i in items if i.ratingKey not in existing]
        logger.info("Collection exists, found %s new items", len(new))
        if new:
            added_items = new
            if not dry_run:
                collection.addItems(new)
                collection.sortUpdate(sort="alpha")
        else:
            logger.info("No new titles; existing collection left unchanged.")
    except NotFound:
        logger.info("Creating collection '%s' with %s items", collection_name, len(items))
        added_items = items
        if not dry_run:
            collection = section.createCollection(collection_name, items=items)
            collection.sortUpdate(sort="alpha")

    if added_items:
        logger.info("Titles to add:" if dry_run else "Titles added:")
        for i in added_items:
            logger.info(f"Would add {i.title}" if dry_run else f"Added {i.title}")
    logger.info("Dry run, nothing changed." if dry_run else f"Done processing '{collection_name}' in library '{library_name}'")


def main():
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    config = load_config(config_path)
    try:
        logger = configure_logging(config, config_path)
    except (OSError, ValueError) as exc:
        sys.exit(f"Invalid logging configuration: {exc}")

    p = argparse.ArgumentParser(
        description="Create/update Plex collections from config.json."
    )
    p.add_argument("--collection", help="Only process this collection name from config.json")
    p.add_argument("--dry-run", action="store_true", help="Show what would happen only")
    args = p.parse_args()

    url = config.get("plex-url") or os.environ.get("PLEX_URL") or "http://localhost:32400"
    token = config.get("plex-token") or os.environ.get("PLEX_TOKEN")

    if not token:
        logger.error("No token: set PLEX_TOKEN or add plex-token to config.json")
        sys.exit(1)

    collections = config.get("collections")
    if not isinstance(collections, list) or not collections:
        logger.error("No collections configured: add a non-empty 'collections' list in config.json")
        sys.exit(1)

    path_map = parse_path_map(config.get("map-path"))

    plex = PlexServer(url, token)

    selected_collections = collections
    if args.collection:
        selected_collections = [
            c for c in collections if c.get("name") == args.collection
        ]
        if not selected_collections:
            logger.error(
                "Collection '%s' not found in config.json collections.",
                args.collection,
            )
            sys.exit(1)

    for collection_config in selected_collections:
        process_collection(plex, collection_config, path_map, args.dry_run, logger)


if __name__ == "__main__":
    main()
