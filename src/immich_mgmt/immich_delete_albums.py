#!/usr/bin/env python3

"""
Immich v3.x - Bulk delete albums and all assets inside them.

Features:
    - Interactive album selection
    - Album name/pattern matching
    - Dry-run by default
    - Explicit DELETE confirmation
    - Handles albums with >1000 assets
    - Deduplicates assets across selected albums
    - Permanently deletes assets through the Immich API
    - Deletes selected albums afterward

Requirements:
    Python 3.9+
    requests

Install:
    python3 -m pip install requests


Environment variables:

    IMMICH_URL
        Default:
            http://192.168.2.6:2283/api

    IMMICH_API_KEY
        Your Immich API key


Examples:

    # Interactive selection
    python3 immich_delete_albums.py

    # Find albums matching a pattern
    python3 immich_delete_albums.py --match '2020*'

    # Find albums containing "Screenshots"
    python3 immich_delete_albums.py --match '*Screenshots*'

    # Actually delete matching albums/assets
    python3 immich_delete_albums.py --match '2020*' --permanent

    # Override server URL
    python3 immich_delete_albums.py \
        --url 'http://192.168.2.6:2283/api' \
        --match '2020*'
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import sys
import time
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_IMMICH_URL = "http://192.168.2.6:2283/api"

PAGE_SIZE = 1000

# Number of assets sent in one DELETE request.
DELETE_BATCH_SIZE = 500


# ---------------------------------------------------------------------------
# Immich API client
# ---------------------------------------------------------------------------

class ImmichClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "x-api-key": api_key,
            }
        )

    def request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> requests.Response:

        url = f"{self.base_url}/{path.lstrip('/')}"

        try:
            response = self.session.request(
                method,
                url,
                timeout=120,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Unable to connect to Immich:\n"
                f"  {url}\n"
                f"  {exc}"
            ) from exc

        if not response.ok:
            body = response.text.strip()

            raise RuntimeError(
                f"Immich API request failed:\n"
                f"  {method} {url}\n"
                f"  HTTP {response.status_code}\n"
                f"  {body}"
            )

        return response

    # -----------------------------------------------------------------------
    # Albums
    # -----------------------------------------------------------------------

    def get_albums(self) -> list[dict[str, Any]]:
        response = self.request("GET", "/albums")

        data = response.json()

        if not isinstance(data, list):
            raise RuntimeError(
                "Unexpected response from /albums."
            )

        return data

    def delete_album(self, album_id: str) -> None:
        self.request(
            "DELETE",
            f"/albums/{album_id}",
        )

    # -----------------------------------------------------------------------
    # Album assets
    # -----------------------------------------------------------------------

    def get_album_assets(
        self,
        album_id: str,
    ) -> list[dict[str, Any]]:

        """
        Immich v3:

            GET /albums/{id}

        does not reliably provide the album's asset list.

        We therefore use:

            POST /search/metadata

        with:

            {
                "albumIds": [album_id],
                "page": 1,
                "size": 1000
            }

        and continue through all pages.
        """

        assets: list[dict[str, Any]] = []

        page = 1

        while True:

            payload = {
                "albumIds": [album_id],
                "page": page,
                "size": PAGE_SIZE,
            }

            response = self.request(
                "POST",
                "/search/metadata",
                json=payload,
            )

            data = response.json()

            asset_container = data.get("assets", {})

            if not isinstance(asset_container, dict):
                raise RuntimeError(
                    "Unexpected response from /search/metadata."
                )

            items = asset_container.get("items", [])

            if not isinstance(items, list):
                raise RuntimeError(
                    "Unexpected assets.items response."
                )

            assets.extend(items)

            print(
                f"      page {page}: "
                f"{len(items):,} assets"
            )

            next_page = asset_container.get("nextPage")

            if next_page is None:
                break

            try:
                page = int(next_page)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"Invalid nextPage: {next_page!r}"
                ) from exc

        return assets

    # -----------------------------------------------------------------------
    # Asset deletion
    # -----------------------------------------------------------------------

    def delete_assets(
        self,
        asset_ids: list[str],
    ) -> None:

        """
        DELETE /assets

        {
            "ids": [...],
            "force": true
        }

        force=true permanently deletes the assets.
        """

        total = len(asset_ids)

        for start in range(
            0,
            total,
            DELETE_BATCH_SIZE,
        ):

            batch = asset_ids[
                start:start + DELETE_BATCH_SIZE
            ]

            print(
                f"    Deleting assets "
                f"{start + 1:,}-{start + len(batch):,} "
                f"of {total:,}..."
            )

            self.request(
                "DELETE",
                "/assets",
                json={
                    "ids": batch,
                    "force": True,
                },
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_album_name(
    album: dict[str, Any],
) -> str:

    return str(
        album.get("albumName")
        or album.get("name")
        or "<unnamed album>"
    )


def get_asset_filename(
    asset: dict[str, Any],
) -> str:

    filename = asset.get("originalFileName")

    if filename:
        return str(filename)

    original_path = asset.get("originalPath")

    if original_path:
        return str(original_path).split("/")[-1]

    return "<unknown>"


def get_asset_type(
    asset: dict[str, Any],
) -> str:

    return str(
        asset.get("type")
        or "UNKNOWN"
    )


def print_separator() -> None:
    print("=" * 72)


# ---------------------------------------------------------------------------
# Pattern matching
# ---------------------------------------------------------------------------

def find_matching_albums(
    albums: list[dict[str, Any]],
    pattern: str,
) -> list[dict[str, Any]]:

    """
    Match album names using shell-style wildcards.

    Examples:

        2020*
        *Screenshots*
        Trip*
        *backup*
    """

    matches = []

    for album in albums:

        name = get_album_name(album)

        if fnmatch.fnmatchcase(
            name,
            pattern,
        ):
            matches.append(album)

    return matches


# ---------------------------------------------------------------------------
# Interactive album selection
# ---------------------------------------------------------------------------

def select_albums_interactively(
    albums: list[dict[str, Any]],
) -> list[dict[str, Any]]:

    if not albums:
        print("No albums found.")
        return []

    print()
    print_separator()
    print("Immich albums")
    print_separator()

    for index, album in enumerate(
        albums,
        start=1,
    ):

        name = get_album_name(album)

        asset_count = album.get("assetCount")

        if asset_count is not None:
            count_text = (
                f"{asset_count:,} assets"
            )
        else:
            count_text = (
                "asset count unknown"
            )

        print(
            f"{index:4}. "
            f"{name} "
            f"({count_text})"
        )

    print()
    print("Select albums by number.")
    print("Examples:")
    print("  1")
    print("  1,3,5")
    print("  1-5")
    print("  1,3-7,12")
    print()

    while True:

        selection = input(
            "Albums to delete: "
        ).strip()

        if not selection:
            print("Nothing selected.")
            continue

        try:
            indices = parse_selection(
                selection,
                len(albums),
            )
        except ValueError as exc:
            print(
                f"Invalid selection: {exc}"
            )
            continue

        return [
            albums[index - 1]
            for index in indices
        ]


def parse_selection(
    text: str,
    maximum: int,
) -> list[int]:

    result: set[int] = set()

    for part in text.split(","):

        part = part.strip()

        if not part:
            continue

        if "-" in part:

            pieces = part.split("-")

            if len(pieces) != 2:
                raise ValueError(
                    f"Invalid range: {part}"
                )

            try:
                start = int(
                    pieces[0].strip()
                )
                end = int(
                    pieces[1].strip()
                )
            except ValueError:
                raise ValueError(
                    f"Invalid range: {part}"
                )

            if start > end:
                start, end = end, start

            numbers = range(
                start,
                end + 1,
            )

        else:

            try:
                numbers = [int(part)]
            except ValueError:
                raise ValueError(
                    f"Invalid number: {part}"
                )

        for number in numbers:

            if number < 1 or number > maximum:
                raise ValueError(
                    f"{number} is outside "
                    f"the valid range 1-{maximum}"
                )

            result.add(number)

    return sorted(result)


# ---------------------------------------------------------------------------
# Display matching albums
# ---------------------------------------------------------------------------

def display_selected_albums(
    albums: list[dict[str, Any]],
) -> None:

    print()
    print_separator()
    print("Selected albums")
    print_separator()

    for index, album in enumerate(
        albums,
        start=1,
    ):

        name = get_album_name(album)

        album_id = album.get("id")

        asset_count = album.get(
            "assetCount"
        )

        if asset_count is not None:
            count_text = (
                f"{asset_count:,} assets"
            )
        else:
            count_text = (
                "asset count unknown"
            )

        print(
            f"{index:4}. "
            f"{name} "
            f"({count_text})"
        )

        print(
            f"      ID: {album_id}"
        )


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------

def run(
    client: ImmichClient,
    permanent: bool,
    pattern: str | None,
) -> None:

    # -----------------------------------------------------------------------
    # Load albums
    # -----------------------------------------------------------------------

    print()
    print("Loading albums...")

    albums = client.get_albums()

    # -----------------------------------------------------------------------
    # Select albums
    # -----------------------------------------------------------------------

    if pattern:

        print()
        print(
            f"Searching albums with pattern: "
            f"{pattern!r}"
        )

        selected_albums = find_matching_albums(
            albums,
            pattern,
        )

        if not selected_albums:

            print()
            print(
                "No albums matched the pattern."
            )

            return

        display_selected_albums(
            selected_albums
        )

    else:

        selected_albums = (
            select_albums_interactively(
                albums
            )
        )

        if not selected_albums:
            return

    # -----------------------------------------------------------------------
    # Scan assets
    # -----------------------------------------------------------------------

    print()
    print_separator()
    print("Scanning selected albums")
    print_separator()

    assets_by_id: dict[
        str,
        dict[str, Any]
    ] = {}

    for album in selected_albums:

        album_id = album.get("id")

        album_name = get_album_name(
            album
        )

        if not album_id:

            print(
                f"WARNING: album "
                f"'{album_name}' has no ID."
            )

            continue

        print()
        print(
            f"Album: {album_name}"
        )

        print(
            f"ID:    {album_id}"
        )

        assets = client.get_album_assets(
            str(album_id)
        )

        print(
            f"    Total assets found: "
            f"{len(assets):,}"
        )

        for asset in assets:

            asset_id = asset.get("id")

            if not asset_id:

                print(
                    "    WARNING: asset without ID; "
                    "skipping."
                )

                continue

            assets_by_id[
                str(asset_id)
            ] = asset

    asset_ids = list(
        assets_by_id.keys()
    )

    # -----------------------------------------------------------------------
    # Deletion plan
    # -----------------------------------------------------------------------

    print()
    print_separator()
    print("DELETION PLAN")
    print_separator()

    print()
    print("Albums:")

    for album in selected_albums:

        print(
            f"  - {get_album_name(album)}"
        )

    print()
    print(
        f"Total albums:   "
        f"{len(selected_albums):,}"
    )

    print(
        f"Unique assets:  "
        f"{len(asset_ids):,}"
    )

    if pattern:

        print()
        print(
            f"Pattern: "
            f"{pattern!r}"
        )

    if permanent:

        print()
        print(
            "MODE: PERMANENT DELETION"
        )

    else:

        print()
        print(
            "MODE: DRY RUN"
        )

        print(
            "Nothing will be deleted."
        )

    # -----------------------------------------------------------------------
    # Asset preview
    # -----------------------------------------------------------------------

    preview_count = min(
        20,
        len(asset_ids),
    )

    if preview_count:

        print()
        print(
            f"First {preview_count} assets:"
        )

        for asset_id in asset_ids[
            :preview_count
        ]:

            asset = assets_by_id[
                asset_id
            ]

            filename = (
                get_asset_filename(asset)
            )

            asset_type = (
                get_asset_type(asset)
            )

            print(
                f"  {asset_id}  "
                f"[{asset_type}]  "
                f"{filename}"
            )

        remaining = (
            len(asset_ids)
            - preview_count
        )

        if remaining > 0:

            print(
                f"  ... and "
                f"{remaining:,} more"
            )

    # -----------------------------------------------------------------------
    # Dry run
    # -----------------------------------------------------------------------

    if not permanent:

        print()
        print_separator()

        print(
            "DRY RUN COMPLETE"
        )

        print_separator()

        print()
        print(
            "Nothing was changed."
        )

        print()
        print(
            "If the result looks correct, "
            "run the same command with:"
        )

        print()
        print(
            "    --permanent"
        )

        return

    # -----------------------------------------------------------------------
    # Final confirmation
    # -----------------------------------------------------------------------

    print()
    print_separator()

    print(
        "WARNING: THIS OPERATION IS DESTRUCTIVE."
    )

    print()

    print(
        f"You are about to permanently "
        f"delete {len(asset_ids):,} assets"
    )

    print(
        f"and {len(selected_albums):,} albums."
    )

    print()

    print(
        "The assets themselves will be "
        "deleted from Immich."
    )

    print()

    print(
        "This is NOT merely removing assets "
        "from the albums."
    )

    print()

    print(
        "Type DELETE to continue."
    )

    confirmation = input(
        "> "
    ).strip()

    if confirmation != "DELETE":

        print()
        print(
            "Aborted."
        )

        return

    # -----------------------------------------------------------------------
    # Delete assets
    # -----------------------------------------------------------------------

    if asset_ids:

        print()
        print_separator()

        print(
            "Deleting assets"
        )

        print_separator()

        client.delete_assets(
            asset_ids
        )

        print()

        print(
            f"Deleted "
            f"{len(asset_ids):,} assets."
        )

    # -----------------------------------------------------------------------
    # Delete albums
    # -----------------------------------------------------------------------

    print()
    print_separator()

    print(
        "Deleting albums"
    )

    print_separator()

    deleted_albums = 0

    for album in selected_albums:

        album_id = album.get("id")

        album_name = get_album_name(
            album
        )

        if not album_id:
            continue

        print(
            f"Deleting album: "
            f"{album_name}"
        )

        client.delete_album(
            str(album_id)
        )

        deleted_albums += 1

        time.sleep(0.1)

    # -----------------------------------------------------------------------
    # Complete
    # -----------------------------------------------------------------------

    print()
    print_separator()

    print(
        "COMPLETE"
    )

    print_separator()

    print(
        f"Albums deleted: "
        f"{deleted_albums:,}"
    )

    print(
        f"Assets deleted: "
        f"{len(asset_ids):,}"
    )

    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:

    parser = argparse.ArgumentParser(
        description=(
            "Bulk delete Immich albums and "
            "all assets contained in them."
        )
    )

    parser.add_argument(
        "--permanent",
        action="store_true",
        help=(
            "Actually delete assets and albums. "
            "Without this option the command "
            "performs a dry run."
        ),
    )

    parser.add_argument(
        "--match",
        metavar="PATTERN",
        help=(
            "Select albums by shell-style name "
            "pattern, e.g. '2020*' or "
            "'*Screenshots*'."
        ),
    )

    parser.add_argument(
        "--url",
        default=os.environ.get(
            "IMMICH_URL",
            DEFAULT_IMMICH_URL,
        ),
        help=(
            "Immich API URL. "
            "Default: %(default)s"
        ),
    )

    args = parser.parse_args()

    api_key = os.environ.get(
        "IMMICH_API_KEY"
    )

    if not api_key:

        print(
            "ERROR: IMMICH_API_KEY is not set.",
            file=sys.stderr,
        )

        print()

        print(
            "Example:"
        )

        print(
            '  export IMMICH_API_KEY="YOUR_API_KEY"'
        )

        return 1

    print()
    print(
        "Immich v3.x Album Deletion Tool"
    )

    print(
        f"Server: {args.url}"
    )

    if args.match:

        print(
            f"Pattern: {args.match!r}"
        )

    if args.permanent:

        print(
            "Mode:   PERMANENT DELETION"
        )

    else:

        print(
            "Mode:   DRY RUN"
        )

    client = ImmichClient(
        base_url=args.url,
        api_key=api_key,
    )

    try:

        run(
            client=client,
            permanent=args.permanent,
            pattern=args.match,
        )

    except KeyboardInterrupt:

        print()
        print()
        print(
            "Interrupted."
        )

        return 130

    except RuntimeError as exc:

        print()
        print(
            "ERROR:",
            file=sys.stderr,
        )

        print(
            exc,
            file=sys.stderr,
        )

        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )