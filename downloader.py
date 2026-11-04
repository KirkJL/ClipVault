
#!/usr/bin/env python3
"""
ClipVault - GitHub Actions to Google Drive

Features:
- Single-video and JSON-manifest batch processing
- YouTube watch URL validation
- MP4 download using yt-dlp and FFmpeg
- OAuth refresh-token authentication
- Resumable Google Drive uploads
- Duplicate detection using a stable clip ID
- Safe filenames and size limits
- Per-video reporting
- Temporary-file cleanup

Only download content you have permission to retrieve.
"""

import json
import os
import re
import sys
import time
import hashlib
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests
import yt_dlp

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload


# --------------------------------------------------
# Configuration
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
REPORT_DIR = BASE_DIR / "reports"

MODE = os.getenv("DOWNLOAD_MODE", "single").strip().lower()
VIDEO_URL = os.getenv("VIDEO_URL", "").strip()
MANIFEST_PATH = os.getenv("MANIFEST_PATH", "halo-60.json").strip()
QUALITY = int(os.getenv("VIDEO_QUALITY", "1080"))

CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN", "")
DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")

SCOPES = [
    "https://www.googleapis.com/auth/drive.file"
]

MAX_FILE_SIZE = 4 * 1024 * 1024 * 1024
MAX_BATCH_ITEMS = 100
MAX_RETRIES = 3

ALLOWED_QUALITIES = {720, 1080, 1440, 2160}

DOWNLOAD_DIR.mkdir(exist_ok=True)
REPORT_DIR.mkdir(exist_ok=True)


# --------------------------------------------------
# Helpers
# --------------------------------------------------

def timestamp():
    return datetime.now(timezone.utc).isoformat()


def safe_filename(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value))
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value[:120] or "untitled"


def video_id_from_url(url):
    """
    Only accept recognised YouTube video URL formats.
    Search-result URLs are deliberately rejected.
    """
    try:
        parsed = urlparse(url)

        if parsed.scheme != "https":
            return None

        host = (parsed.hostname or "").lower()

        if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            if parsed.path == "/watch":
                values = parse_qs(parsed.query).get("v", [])
                candidate = values[0] if values else ""
            elif parsed.path.startswith("/shorts/"):
                candidate = parsed.path.split("/")[2]
            elif parsed.path.startswith("/live/"):
                candidate = parsed.path.split("/")[2]
            else:
                return None

        elif host == "youtu.be":
            candidate = parsed.path.strip("/")

        else:
            return None

        if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
            return candidate

    except (ValueError, IndexError):
        pass

    return None


def canonical_video_url(url):
    video_id = video_id_from_url(url)

    if not video_id:
        return None

    return f"https://www.youtube.com/watch?v={video_id}"


def stable_clip_id(url):
    video_id = video_id_from_url(url)

    if not video_id:
        raise ValueError("Invalid YouTube video URL")

    return "youtube-" + video_id


def load_items():
    if MODE == "single":
        canonical = canonical_video_url(VIDEO_URL)

        if not canonical:
            raise ValueError(
                "Single mode requires a valid YouTube video URL."
            )

        return [{
            "id": stable_clip_id(canonical),
            "name": stable_clip_id(canonical),
            "url": canonical
        }]

    if MODE != "batch":
        raise ValueError("Mode must be single or batch")

    manifest = (BASE_DIR / MANIFEST_PATH).resolve()

    if not manifest.is_relative_to(BASE_DIR):
        raise ValueError("Manifest must be inside the repository")

    if manifest.suffix.lower() != ".json":
        raise ValueError("Manifest must be a JSON file")

    if not manifest.is_file():
        raise FileNotFoundError("Manifest not found")

    if manifest.stat().st_size > 5 * 1024 * 1024:
        raise ValueError("Manifest exceeds 5 MB")

    with manifest.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    if isinstance(data, dict):
        data = data.get("clips", data.get("items", []))

    if not isinstance(data, list):
        raise ValueError("Manifest must contain a list of clips")

    if len(data) > MAX_BATCH_ITEMS:
        raise ValueError("Manifest exceeds the batch limit")

    items = []

    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            continue

        # Prefer the exact video chosen in ClipVault.
        # The original "source" is usually a search URL.
        candidate = item.get("video") or item.get("url") or item.get("source") or ""
        canonical = canonical_video_url(str(candidate))

        if not canonical:
            items.append({
                "id": str(item.get("id", f"clip-{index}")),
                "name": str(item.get("name", item.get("title", f"Clip {index}"))),
                "url": None
            })
            continue

        items.append({
            "id": stable_clip_id(canonical),
            "name": str(item.get("name", item.get("title", f"Clip {index}"))),
            "url": canonical
        })

    return items


# --------------------------------------------------
# Google Drive OAuth
# --------------------------------------------------

def create_drive_service():
    required = {
        "GOOGLE_CLIENT_ID": CLIENT_ID,
        "GOOGLE_CLIENT_SECRET": CLIENT_SECRET,
        "GOOGLE_REFRESH_TOKEN": REFRESH_TOKEN,
        "GOOGLE_DRIVE_FOLDER_ID": DRIVE_FOLDER_ID
    }

    missing = [name for name, value in required.items() if not value]

    if missing:
        raise ValueError(
            "Missing GitHub secrets: " + ", ".join(missing)
        )

    credentials = Credentials(
        token=None,
        refresh_token=REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=SCOPES
    )

    credentials.refresh(Request())

    return build(
        "drive",
        "v3",
        credentials=credentials,
        cache_discovery=False
    )


def drive_existing_file(service, clip_id):
    """
    Search only inside the configured destination folder.

    Duplicate detection uses appProperties rather than
    filenames, so renaming a file does not break tracking.
    """
    escaped_id = clip_id.replace("\\", "\\\\").replace("'", "\\'")
    escaped_folder = DRIVE_FOLDER_ID.replace("\\", "\\\\").replace("'", "\\'")

    query = (
        f"'{escaped_folder}' in parents "
        f"and trashed = false "
        f"and appProperties has {{ key='clipvault_id' "
        f"and value='{escaped_id}' }}"
    )

    response = service.files().list(
        q=query,
        spaces="drive",
        fields="nextPageToken, files(id,name,webViewLink)",
        page_size=10
    ).execute()

    files = response.get("files", [])
    return files[0] if files else None


# --------------------------------------------------
# Video download
# --------------------------------------------------

def download_video(item):
    clip_id = item["id"]
    url = item["url"]

    folder = Path(tempfile.mkdtemp(
        prefix="clipvault-",
        dir=DOWNLOAD_DIR
    ))

    output_template = str(folder / "%(id)s.%(ext)s")

    options = {
        "format": (
            f"bestvideo[height<={QUALITY}][ext=mp4]+"
            f"bestaudio[ext=m4a]/"
            f"best[height<={QUALITY}][ext=mp4]/"
            f"best[height<={QUALITY}]"
        ),
        "outtmpl": output_template,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "ignoreerrors": False,
        "retries": MAX_RETRIES,
        "fragment_retries": MAX_RETRIES,
        "socket_timeout": 30,
        "quiet": False,
        "no_warnings": False,
        "restrictfilenames": True,
        "max_filesize": MAX_FILE_SIZE,
        "match_filter": yt_dlp.utils.match_filter_func(
            "duration <= 7200"
        ),
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)

        if not info:
            raise RuntimeError("No video information returned")

        files = list(folder.glob("*.mp4"))

        if not files:
            raise RuntimeError("No MP4 file was produced")

        video_file = max(
            files,
            key=lambda path: path.stat().st_size
        )

        if video_file.stat().st_size > MAX_FILE_SIZE:
            raise RuntimeError("Downloaded file exceeds size limit")

        title = safe_filename(
            info.get("title") or item["name"]
        )

        return folder, video_file, title

    except Exception:
        import shutil
        shutil.rmtree(folder, ignore_errors=True)
        raise


# --------------------------------------------------
# Google Drive upload
# --------------------------------------------------

def upload_to_drive(service, item, file_path, title):
    clip_id = item["id"]

    existing = drive_existing_file(service, clip_id)

    if existing:
        return {
            "status": "already_exists",
            "drive_file_id": existing["id"],
            "drive_url": existing.get(
                "webViewLink",
                f"https://drive.google.com/file/d/{existing['id']}/view"
            )
        }

    filename = safe_filename(title) + ".mp4"

    metadata = {
        "name": filename,
        "parents": [DRIVE_FOLDER_ID],
        "mimeType": "video/mp4",
        "appProperties": {
            "clipvault_id": clip_id,
            "clipvault_source": "youtube"
        }
    }

    media = MediaFileUpload(
        str(file_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=8 * 1024 * 1024
    )

    request = service.files().create(
        body=metadata,
        media_body=media,
        fields="id,name,webViewLink",
    )

    response = None
    retries = 0

    while response is None:
        try:
            progress, response = request.next_chunk(
                num_retries=MAX_RETRIES
            )

            if progress:
                percentage = int(progress.progress() * 100)
                print(f"Upload progress: {percentage}%")

        except HttpError as error:
            if error.resp.status not in {429, 500, 502, 503, 504}:
                raise

            retries += 1

            if retries > MAX_RETRIES:
                raise

            time.sleep(min(2 ** retries, 30))

    if not response or not response.get("id"):
        raise RuntimeError("Drive upload returned no file ID")

    return {
        "status": "uploaded",
        "drive_file_id": response["id"],
        "drive_url": response.get(
            "webViewLink",
            f"https://drive.google.com/file/d/{response['id']}/view"
        )
    }


# --------------------------------------------------
# Reporting
# --------------------------------------------------

def save_report(results):
    report = {
        "generated_at": timestamp(),
        "mode": MODE,
        "quality": QUALITY,
        "total": len(results),
        "uploaded": sum(
            item["status"] == "uploaded"
            for item in results
        ),
        "already_exists": sum(
            item["status"] == "already_exists"
            for item in results
        ),
        "skipped": sum(
            item["status"] == "skipped"
            for item in results
        ),
        "failed": sum(
            item["status"] == "failed"
            for item in results
        ),
        "items": results
    }

    path = REPORT_DIR / "clipvault-report.json"

    with path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)

    print("\n" + "=" * 50)
    print("CLIPVAULT REPORT")
    print("=" * 50)
    print(f"Total:          {report['total']}")
    print(f"Uploaded:       {report['uploaded']}")
    print(f"Already exists: {report['already_exists']}")
    print(f"Skipped:        {report['skipped']}")
    print(f"Failed:         {report['failed']}")
    print("=" * 50)

    return report


# --------------------------------------------------
# Main
# --------------------------------------------------

def main():
    if QUALITY not in ALLOWED_QUALITIES:
        raise ValueError("Unsupported video quality")

    items = load_items()

    if not items:
        raise ValueError("No clips found")

    service = create_drive_service()

    results = []

    import shutil

    try:
        for index, item in enumerate(items, start=1):
            print(
                f"\n[{index}/{len(items)}] "
                f"{safe_filename(item['name'])}"
            )

            result = {
                "id": item["id"],
                "name": item["name"],
                "status": None,
                "drive_url": None,
                "error": None
            }

            if not item["url"]:
                result["status"] = "skipped"
                result["error"] = (
                    "No exact YouTube video URL selected"
                )
                results.append(result)
                continue

            folder = None

            try:
                existing = drive_existing_file(
                    service,
                    item["id"]
                )

                if existing:
                    result["status"] = "already_exists"
                    result["drive_url"] = existing.get(
                        "webViewLink",
                        f"https://drive.google.com/file/d/{existing['id']}/view"
                    )

                else:
                    folder, file_path, title = download_video(item)

                    uploaded = upload_to_drive(
                        service,
                        item,
                        file_path,
                        title
                    )

                    result["status"] = uploaded["status"]
                    result["drive_url"] = uploaded["drive_url"]

            except Exception as error:
                result["status"] = "failed"
                result["error"] = str(error)[:500]
                print(
                    f"ERROR: {type(error).__name__}: "
                    f"{result['error']}"
                )

            finally:
                if folder:
                    shutil.rmtree(folder, ignore_errors=True)

            results.append(result)

    finally:
        report = save_report(results)

    if report["failed"] > 0:
        return 1

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(
            f"FATAL: {type(error).__name__}: {error}",
            file=sys.stderr
        )
        sys.exit(1)
