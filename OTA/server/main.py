"""
OTA Firmware Server — serves version info and firmware binaries.
Supports multi-version firmware via manifest.json (re-read from disk on
every request so publish.py changes take effect without a restart).

Supports HTTP Range requests for resumable downloads and a /report endpoint
for device telemetry.
"""

import hashlib
import json
import logging
import os
import re

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("ota-server")

app = FastAPI(title="OTA Firmware Server", version="1.1.0")

RELEASE_DIR = os.path.join(os.path.dirname(__file__), "release")
MANIFEST_PATH = os.path.join(RELEASE_DIR, "manifest.json")

# Legacy fallback for pre-manifest deployments
LEGACY_FILE = "firmware_v2.0.0.bin"
LEGACY_VERSION = "2.0.0"

# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------


def _read_manifest():
    """Read manifest.json from disk (always fresh)."""
    if os.path.exists(MANIFEST_PATH):
        with open(MANIFEST_PATH, "r") as f:
            return json.load(f)
    return None


def _resolve_firmware(version: str | None):
    """Return (filename, version_string, size, sha256) for a version query."""
    manifest = _read_manifest()

    if manifest is None:
        filepath = os.path.join(RELEASE_DIR, LEGACY_FILE)
        if not os.path.exists(filepath):
            raise HTTPException(status_code=503, detail="No firmware available on server")
        sha = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha.update(chunk)
        return LEGACY_FILE, LEGACY_VERSION, os.path.getsize(filepath), sha.hexdigest()

    if version is not None:
        entry = manifest["versions"].get(version)
        if entry is None:
            known = ", ".join(manifest["versions"].keys())
            raise HTTPException(
                status_code=404, detail=f"Version '{version}' not found. Known: {known}"
            )
        return entry["filename"], version, entry["size"], entry["sha256"]

    latest_ver = manifest["latest"]
    entry = manifest["versions"][latest_ver]
    return entry["filename"], latest_ver, entry["size"], entry["sha256"]


# ---------------------------------------------------------------------------
# Range-request helper
# ---------------------------------------------------------------------------

_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _parse_range(header: str, total_size: int):
    """Parse a Range header. Returns (start, end, content_range, content_length)
    or None if the header is unparseable."""
    m = _RANGE_RE.match(header.strip())
    if not m:
        return None

    start_s, end_s = m.group(1), m.group(2)

    if start_s:
        start = int(start_s)
    else:
        start = 0  # suffix range not supported; treat as 0

    if end_s:
        end = int(end_s)
    else:
        end = total_size - 1

    if start > end or start >= total_size:
        return None

    end = min(end, total_size - 1)
    content_length = end - start + 1
    content_range = f"bytes {start}-{end}/{total_size}"

    return start, end, content_range, content_length


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/version")
async def get_version(v: str | None = Query(default=None)):
    """Return firmware version metadata.  ?v=<version> for a specific release."""
    filename, version, size, sha256 = _resolve_firmware(v)

    filepath = os.path.join(RELEASE_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=503, detail=f"Firmware file '{filename}' missing on disk")

    return {
        "latest_version": version,
        "firmware": filename,
        "size": size,
        "sha256": sha256,
    }


@app.get("/firmware")
async def download_firmware(request: Request, v: str | None = Query(default=None)):
    """Download a firmware binary. Supports HTTP Range for resumable downloads.

    Returns 206 Partial Content when a valid Range header is present,
    otherwise 200 with the full file.
    """
    filename, version, total_size, sha256 = _resolve_firmware(v)

    filepath = os.path.join(RELEASE_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail=f"Firmware file '{filename}' missing on disk")

    range_header = request.headers.get("range")

    if range_header:
        parsed = _parse_range(range_header, total_size)
        if parsed is None:
            raise HTTPException(status_code=416, detail="Range not satisfiable")
        start, end, content_range, content_length = parsed

        def ranged_stream():
            with open(filepath, "rb") as f:
                f.seek(start)
                remaining = content_length
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    yield chunk
                    remaining -= len(chunk)

        return StreamingResponse(
            ranged_stream(),
            status_code=206,
            media_type="application/octet-stream",
            headers={
                "Accept-Ranges": "bytes",
                "Content-Range": content_range,
                "Content-Length": str(content_length),
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    # Full download
    def full_stream():
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                yield chunk

    return StreamingResponse(
        full_stream(),
        status_code=200,
        media_type="application/octet-stream",
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(total_size),
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------

class TelemetryPayload(BaseModel):
    device_id: str
    active_slot: str
    version: str
    status: str


@app.post("/report")
async def report_telemetry(payload: TelemetryPayload):
    """Accept device telemetry and log it to the server console."""
    log.info(
        "[TELEMETRY] device=%s  slot=%s  version=%s  status=%s",
        payload.device_id,
        payload.active_slot,
        payload.version,
        payload.status,
    )
    return {"received": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
