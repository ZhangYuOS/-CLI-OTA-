# AI Prompt Log — OTA A/B Partition Simulator

---

## Step 1: Project Scaffold & Firmware Server (2026-05-16)

- **User Prompt / Intent:**
  Initialize the project structure for an IoT OTA A/B partition upgrade simulator.
  Create the FastAPI backend that exposes a `/version` endpoint and serves a dummy
  firmware binary (`firmware_v2.bin`). Generate a project-level `requirements.txt`
  and document this step.

- **AI Solution:**
  Created the directory layout (`server/`, `client/`, `docs/`). Implemented
  `server/main.py` — a FastAPI application with two endpoints:

  - `GET /version` — computes SHA-256 of the firmware file on the fly and returns
    `latest_version`, `firmware` filename, `size`, and `sha256`.
  - `GET /firmware` — streams `server/release/firmware_v2.bin` as an octet-stream
    download.

  Generated a 5 MiB dummy file filled with zeros (`dd if=/dev/zero ...`) placed at
  `server/release/firmware_v2.bin`. Authored `requirements.txt` with pinned
  dependencies for FastAPI/Uvicorn (server) and Click/Requests/Rich (client).

---

## Step 2: Client Implementation & Network Adaptation (2026-05-16)

- **User Prompt / Intent:**
  Upgrade the OTA architecture from local-only simulation to a real physical
  network setup. The server must bind to `0.0.0.0` so physical Linux dev boards
  on the same LAN can reach it. Build the client CLI with four commands
  (`status`, `check`, `update`, `rollback`) and a global `--server-url` option so
  the user can pass the PC's LAN IP. Implement strict A/B partition logic: never
  switch the active slot unless the downloaded firmware passes SHA-256
  verification.

- **AI Solution:**
  Added a `__main__` block to `server/main.py` running Uvicorn on `0.0.0.0:8000`
  by default. Created `client/device_status.json` (slot A active, v1.0.0) with
  companion directories `client/slot_a/` and `client/slot_b/`.

  Built `client/cli.py` as a Click group with a global `--server-url` option
  (default `http://127.0.0.1:8000`). Each command uses Rich exclusively for
  output:

  - **`status`** — reads `device_status.json` and renders an A/B slot table with
    active-standby markers, version strings, and truncated SHA-256 hashes.
  - **`check`** — calls `GET /version` on the server, compares installed vs.
    latest version in a Rich table, and advises the user.
  - **`update`** — four-phase pipeline:
    1. Fetch server metadata (`/version`).
    2. Stream download to the *standby* slot directory with a Rich progress bar
       (bytes, speed, ETA).
    3. Compute SHA-256 of the downloaded file; if mismatch, **delete the file
       and abort** without touching the active slot.
    4. Only on match: flip `active_slot` in `device_status.json` and persist.
  - **`rollback`** — manually toggles the active slot to the other partition,
    guarded against rolling back to an empty slot.

  End-to-end test validated the full cycle: status → check → update (A→B) →
  rollback (B→A). All state transitions are captured in the JSON file and
  surfaced by the `status` command.

---

## Step 3: Advanced OTA Features — Multi-Version, Ping-Pong & Auto-Rollback (2026-05-16)

- **User Prompt / Intent:**
  Add three advanced OTA capabilities: (1) server-side multi-version firmware
  management with a publish script and version-queryable endpoints; (2) client-side
  target-version selection with explicit ping-pong slot transition logging; and
  (3) defensive abort handling (KeyboardInterrupt cleanup) plus a simulated
  boot-crash auto-rollback flag on the update command.

- **AI Solution:**

  **Server — Dynamic Publishing:**
  Created `server/release/manifest.json` as the version registry (keyed by
  version string, tracks filename/size/sha256 per release). Built
  `server/publish.py` — a Click CLI that generates a 5 MiB firmware image with a
  unique version header (ensuring distinct SHA-256 per version), computes the
  checksum, and upserts the manifest. Updated `server/main.py` to load the
  manifest at startup with a legacy fallback path. Both `GET /version` and
  `GET /firmware` now accept an optional `?v=<version>` query parameter; omitted
  returns the latest.

  **Client — Version Selection & Ping-Pong:**
  Added `--target-version` option to both `check` and `update` commands. When
  set, the client appends `?v=<version>` to server requests, enabling
  installation of a specific firmware release. The `update` command now emits a
  Rich-styled ping-pong line before the slot switch:
  `Slot A(v1.0.0) → Slot B(v2.0.0)`. This naturally chains across multiple
  updates: `A(v1) → B(v2) → A(v3)`.

  **Client — Abort & Auto-Rollback:**
  Wrapped the download phase in `try / except (requests.RequestException,
  KeyboardInterrupt)`. On `KeyboardInterrupt`, the partial firmware file is
  deleted and an explicit "Download Aborted. Active slot remains unchanged"
  message is shown. Added `--simulate-boot-crash` boolean flag to `update`.
  After a successful download + verify + slot switch, if the flag is set the
  client prints `[Simulating Reboot...] → KERNEL PANIC! Watchdog triggered.`
  and immediately executes a rollback to the previous slot, proving the
  watchdog recovery path.

  **Verification:**
  - Published v3.0.0 via `publish.py` — unique SHA-256 confirmed.
  - Server correctly serves latest (v3.0.0) and version-specific (`?v=2.0.0`).
  - Ping-pong chain A→B (v2.0.0) then B→A (v3.0.0) validated.
  - `--simulate-boot-crash` auto-rollback: slot switched to B then watchdog
    restored A — confirmed via `status` command.

---

## Bug Fix: Server manifest stale after publish (2026-05-16)

- **User Prompt / Intent:**
  After running `python server/publish.py 4.0.0`, the running server still
  reported the previous latest version. The client `check` command did not see
  the newly published firmware.

- **AI Solution:**
  Root cause: `server/main.py` loaded `manifest.json` once at module import
  time into a global `MANIFEST` variable. `publish.py` wrote the updated
  manifest to disk, but the running server never re-read it.

  Fix: removed the module-level `MANIFEST` global. Renamed `_load_manifest()`
  → `_read_manifest()` and call it inside `_resolve_firmware()` on every
  request. The manifest is a few hundred bytes — zero performance concern.

  Verified: started server → `GET /version` returned v4.0.0 → ran
  `publish.py 6.0.0` without restart → `GET /version` immediately returned
  v6.0.0. Version-specific queries (`?v=4.0.0`) remain functional after
  publishing newer releases.

---

## Feature: Server-side firmware unpublish / rollback (2026-05-17)

- **User Prompt / Intent:**
  Add a server-side command to delete the latest (or a specific) firmware
  version by modifying `manifest.json` and cleaning up the binary file.

- **AI Solution:**
  Refactored `server/publish.py` from a single Click command into a Click group
  with two subcommands:

  - **`publish <version>`** — unchanged: generates a 5 MiB firmware image with
    a unique version header, computes SHA-256, and registers it in
    `manifest.json`.
  - **`unpublish <version>`** — removes the given version from the manifest's
    `versions` dict, deletes the corresponding `.bin` file from
    `server/release/`, and recalculates the `latest` pointer (to the highest
    remaining version, or `null` if empty).
  - **`unpublish --latest`** — convenience flag that resolves to whatever
    `manifest["latest"]` currently points to.

  Edge cases handled: empty manifest (exit gracefully), attempting to remove an
  unknown version (lists known versions), and removing the non-latest version
  (latest pointer unchanged). All output uses Rich tables, consistent with the
  project's [cli-visual-master] skill.

  Verified: started with manifest containing v2.0.0–v5.0.0. `unpublish
  --latest` removed v5.0.0 and set latest→v4.0.0. `unpublish 3.0.0` removed a
  middle version without disturbing the latest pointer. Both `.bin` files were
  deleted from disk.



---

## Step 4: Industrial Robustness — Resumable Downloads & Device Telemetry (2026-05-17)

- **User Prompt / Intent:**
  Bring the OTA system to industrial standard with two capabilities:
  (1) resumable firmware downloads via HTTP Range requests (断点续传);
  (2) device telemetry / status reporting with POST /report.

- **AI Solution:**
  Server: replaced FileResponse with StreamingResponse in /firmware; added
  _parse_range() for Range header parsing; returns 206 Partial Content with
  Content-Range. Added POST /report with Pydantic model, logs [TELEMETRY].
  Client: update command checks for partial file in standby slot; if found,
  sends Range header, opens in append mode, seeds progress bar from offset.
  Ctrl+C now retains partial file for resume. report_to_server() helper
  POSTs telemetry on update success, rollback, abort, and hash failure.
  Added "device_id": "DEV-RK3588-001" to device_status.json.

  Verified: Range bytes=0-1023 → 206; Range bytes=1024- → 206 correct length;
  created 1MB partial → detected at 20% → resumed via Range; clean update
  A→B→A ping-pong with telemetry; Ctrl+C retains partial with resume prompt.

---

## Documentation: README.md (2026-05-17)

- **User Prompt / Intent:**
  Write a comprehensive, professional README.md in Chinese covering core
  architecture, killer features, setup, usage, and advanced testing scenarios.

- **AI Solution:**
  Scanned complete project tree for accuracy. Wrote README.md with: ASCII
  architecture diagram, 6 highlighted features (ping-pong, SHA-256, resume,
  telemetry, watchdog rollback, multi-version), environment setup, full
  usage guide with all server/client commands and parameter table, two
  hands-on testing walkthroughs (resumable download and boot-crash rollback
  with complete terminal output examples), API reference, and security design
  principles. All commands use actual values from the codebase.
