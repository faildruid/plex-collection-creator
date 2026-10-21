"""
Crawl configured folders for media files and put matching Plex items into collections.

Run with uv:
    uv sync
    export PLEX_TOKEN=xxxxxxxx
    uv run main.py
"""
import argparse
import json
import os
import sys

from plexapi.exceptions import NotFound
from plexapi.server import PlexServer

VIDEO_EXT = {
    ".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts", ".m2ts",
    ".mpg", ".mpeg", ".webm", ".flv",
}


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


def process_collection(plex, collection_config, path_map, dry_run):
    folder = collection_config.get("path")
    collection_name = collection_config.get("name")
    library_name = collection_config.get("library")

    if not folder or not collection_name or not library_name:
        missing = [
            key
            for key in ("path", "name", "library")
            if not collection_config.get(key)
        ]
        print(
            "Skipping collection config missing required fields "
            f"({', '.join(missing)}): {collection_config}"
        )
        return

    print(f"\n=== Processing '{collection_name}' in library '{library_name}' ===")
    section = plex.library.section(library_name)

    files = crawl(folder, path_map)
    print(f"Found {len(files)} media files in {folder}")

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
        print(f"  not in Plex: {f}")
    items = list(items.values())
    print(f"Matched {len(items)} Plex items, {len(missing)} files unmatched")

    if not items:
        print("Nothing to add for this collection.")
        return

    added_items = []
    try:
        collection = section.collection(collection_name)
        existing = {i.ratingKey for i in collection.items()}
        new = [i for i in items if i.ratingKey not in existing]
        print(f"Collection exists, found {len(new)} new items")
        if new:
            added_items = new
            if not dry_run:
                collection.addItems(new)
                collection.sortUpdate(sort="alpha")
        else:
            print("No new titles; existing collection left unchanged.")
    except NotFound:
        print(f"Creating collection '{collection_name}' with {len(items)} items")
        added_items = items
        if not dry_run:
            collection = section.createCollection(collection_name, items=items)
            collection.sortUpdate(sort="alpha")

    if added_items:
        print("Titles to add:" if dry_run else "Titles added:")
        for i in added_items:
            print(f"  - {i.title}")
    print("Dry run, nothing changed." if dry_run else "Done.")


def main():
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    config = load_config(config_path)

    p = argparse.ArgumentParser(
        description="Create/update Plex collections from config.json."
    )
    p.add_argument("--collection", help="Only process this collection name from config.json")
    p.add_argument("--dry-run", action="store_true", help="Show what would happen only")
    args = p.parse_args()

    url = config.get("plex-url") or os.environ.get("PLEX_URL") or "http://localhost:32400"
    token = config.get("plex-token") or os.environ.get("PLEX_TOKEN")

    if not token:
        sys.exit("No token: set PLEX_TOKEN or add plex-token to config.json")

    collections = config.get("collections")
    if not isinstance(collections, list) or not collections:
        sys.exit("No collections configured: add a non-empty 'collections' list in config.json")

    path_map = parse_path_map(config.get("map-path"))

    plex = PlexServer(url, token)

    selected_collections = collections
    if args.collection:
        selected_collections = [
            c for c in collections if c.get("name") == args.collection
        ]
        if not selected_collections:
            sys.exit(
                f"Collection '{args.collection}' not found in config.json collections."
            )

    for collection_config in selected_collections:
        process_collection(plex, collection_config, path_map, args.dry_run)


if __name__ == "__main__":
    main()
