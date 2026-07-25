import os
import re
import shutil
from pathlib import Path


AUDIO_EXTENSIONS = {".m4b", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wma", ".aac"}
DETECTABLE_AUDIO_EXTENSIONS = {".m4b", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac"}
ASIN_PATTERN = re.compile(r"\[(B0[A-Z0-9]{8})\]")


def cleanup_item_files(
    items_data: list[dict],
    destination_dir: str,
    source_dir: str,
) -> list[dict]:
    """Remove audio files from destination and source directories for deleted items."""
    cleaned = []
    dest_dirs_removed: set[str] = set()

    for item in items_data:
        item_path = item.get("path", "")
        rel_path = item.get("relPath", "")
        title = item.get("media", {}).get("metadata", {}).get("title", "unknown")
        entry: dict = {"title": title, "dest_removed": False, "source_removed": False}

        dest_path = resolve_dest_path(item_path, rel_path, destination_dir)
        if dest_path and dest_path not in dest_dirs_removed:
            entry.update(try_rmtree(dest_path, "dest"))
            if entry["dest_removed"]:
                dest_dirs_removed.add(dest_path)

        if source_dir:
            clean_source_by_audio_files(Path(source_dir), item, entry)

        cleaned.append(entry)
    return cleaned


def resolve_dest_path(item_path: str, rel_path: str, destination_dir: str) -> str:
    """Resolve the actual filesystem path for an ABS item's destination folder."""
    if item_path and Path(item_path).is_dir():
        return item_path
    if rel_path and destination_dir:
        candidate = Path(destination_dir) / rel_path
        if candidate.is_dir():
            return str(candidate)
    return ""


def try_rmtree(path: str, prefix: str) -> dict:
    """Attempt to remove a directory tree, returning status dict."""
    result: dict = {f"{prefix}_removed": False}
    try:
        shutil.rmtree(path)
        result[f"{prefix}_removed"] = True
        result[f"{prefix}_path"] = path
    except Exception as e:
        result[f"{prefix}_error"] = str(e)
    return result


def clean_source_by_audio_files(source: Path, item: dict, entry: dict) -> None:
    """Remove source folder matching by audio filenames or title."""
    if not source.is_dir():
        return
    audio_files = item.get("media", {}).get("audioFiles", [])
    filenames = {
        Path(audio_file.get("metadata", {}).get("filename", "")).stem.lower()
        for audio_file in audio_files
    }
    filenames.discard("")

    for child in source.iterdir():
        if not child.is_dir():
            continue
        child_files = {f.stem.lower() for f in child.rglob("*") if f.is_file()}
        if filenames and filenames & child_files:
            entry.update(try_rmtree(str(child), "source"))
            return

    title = item.get("media", {}).get("metadata", {}).get("title", "").lower()
    if not title:
        return
    for child in source.iterdir():
        if child.is_dir() and title in child.name.lower():
            entry.update(try_rmtree(str(child), "source"))
            return


def scan_directory(dir_path: str, include_folders: bool = True) -> dict:
    """Scan a directory for audio book folders, files, and extensions."""
    base = Path(dir_path)
    info: dict = {"path": dir_path, "exists": base.is_dir()}
    if not base.is_dir():
        return info

    ext_counts: dict[str, int] = {}
    total_size = 0
    folders: list[dict] = []

    if include_folders:
        for child in sorted(base.iterdir()):
            if child.is_dir():
                folder_info = scan_book_folder(child, AUDIO_EXTENSIONS, ext_counts)
                total_size += folder_info.get("size", 0)
                folders.append(folder_info)
    else:
        for file_path in base.rglob("*"):
            if file_path.is_file() and file_path.suffix.lower() in AUDIO_EXTENSIONS:
                total_size += file_path.stat().st_size
                ext = file_path.suffix.lower()
                ext_counts[ext] = ext_counts.get(ext, 0) + 1

    info["folder_count"] = len([child for child in base.iterdir() if child.is_dir()])
    info["extensions"] = ext_counts
    info["total_audio_size_mb"] = round(total_size / (1024 * 1024), 1)
    if include_folders:
        info["folders"] = folders
    return info


def scan_book_folder(
    folder: Path,
    audio_exts: set[str],
    ext_counts: dict[str, int],
) -> dict:
    """Scan a single book folder for audio files."""
    audio_files = []
    folder_size = 0
    for file_path in folder.rglob("*"):
        if file_path.is_file() and file_path.suffix.lower() in audio_exts:
            audio_files.append(file_path.name)
            folder_size += file_path.stat().st_size
            ext = file_path.suffix.lower()
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
    return {
        "name": folder.name,
        "audio_files": audio_files,
        "size": folder_size,
        "size_mb": round(folder_size / (1024 * 1024), 1),
    }


def detect_audio_extension(source_dir: Path) -> str:
    """Detect the most common audio extension in the source directory."""
    if not source_dir.is_dir():
        return ""
    counts: dict[str, int] = {}
    for child in source_dir.iterdir():
        if not child.is_dir():
            continue
        for file_path in child.rglob("*"):
            if (
                file_path.is_file()
                and file_path.suffix.lower() in DETECTABLE_AUDIO_EXTENSIONS
            ):
                ext = file_path.suffix.lower()
                counts[ext] = counts.get(ext, 0) + 1
    if not counts:
        return ""
    return max(counts, key=lambda ext: (ext == ".m4b", counts[ext]))


def extract_asins_from_dir(source_dir: str) -> list[str]:
    """Scan source directory for audiobook files and extract ASINs from filenames."""
    asins: set[str] = set()
    if not os.path.isdir(source_dir):
        return []
    for root, _, files in os.walk(source_dir):
        for filename in files:
            if filename.endswith((".m4b", ".mp3")):
                match = ASIN_PATTERN.search(filename)
                if match:
                    asins.add(match.group(1))
    return sorted(asins)
