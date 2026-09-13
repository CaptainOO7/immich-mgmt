# immich-mgmt

A small Python utility for managing [Immich](https://immich.app/) albums through the Immich REST API.

## Features

- Bulk delete Immich albums
- Delete all assets contained in selected albums
- Select albums interactively
- Select albums using shell-style name patterns
- Dry-run mode by default
- Explicit confirmation before permanent deletion
- Handles albums containing more than 1,000 assets
- Deduplicates assets that appear in multiple selected albums

## Requirements

- Python 3.9+
- Immich v3.x
- An Immich API key
- [uv](https://docs.astral.sh/uv/) (recommended)

## Installation

Clone the repository:

```bash
git clone https://github.com/CaptainOO7/immich-mgmt.git
cd immich-mgmt
```

Install dependencies:

```bash
uv sync
```

## Configuration

Create a `.env` file in the project root:

```dotenv
IMMICH_URL=http://192.168.2.6:2283/api
IMMICH_API_KEY=your_immich_api_key
```

Make sure `.env` is included in `.gitignore` so the API key is never committed to Git.

## Usage

### Interactive mode

Run without `--match` to select albums interactively:

```bash
uv run python src/immich_mgmt/immich_delete_albums.py
```

The script will list the albums and allow you to select them by number.

### Pattern matching

Use `--match` to select albums by name.

For example, to find all albums ending with `_snapshot_image`:

```bash
uv run python src/immich_mgmt/immich_delete_albums.py \
    --match '*_snapshot_image'
```

The pattern uses shell-style wildcards.

Examples:

```text
2024*            Albums starting with "2024"
*Screenshots*    Albums containing "Screenshots"
Trip*            Albums starting with "Trip"
*Backup          Albums ending with "Backup"
```

### Dry run

The script performs a dry run by default:

```bash
uv run python src/immich_mgmt/immich_delete_albums.py \
    --match '*_snapshot_image'
```

It scans the matching albums and displays the assets that would be deleted, but makes no changes.

### Permanent deletion

To actually delete the assets and albums:

```bash
uv run python src/immich_mgmt/immich_delete_albums.py \
    --match '*_snapshot_image' \
    --permanent
```

The script requires you to explicitly type:

```text
DELETE
```

before performing the operation.

## Important

This tool deletes the **assets themselves**, not just their membership in an album.

For example:

```text
Album A
├── photo-1
├── photo-2
└── photo-3

Album B
├── photo-3
└── photo-4
```

If Album A is selected, `photo-3` will also be permanently deleted from Immich, including its presence in Album B.

Use the dry-run mode and carefully review the deletion plan before using `--permanent`.

## Version

The current version is defined in `pyproject.toml`.

## License

MIT License
Copyright (c) 2026 CaptainOO7
