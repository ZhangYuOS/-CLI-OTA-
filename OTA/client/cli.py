"""
OTA Client CLI — A/B partition firmware update tool.

Commands: status, check, update, rollback

Features:
  - Resumable downloads (HTTP Range, append mode)
  - Device telemetry reporting (POST /report)
"""

import hashlib
import json
import os

import click
import requests
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table

console = Console()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATUS_FILE = os.path.join(BASE_DIR, "device_status.json")
SLOT_A_DIR = os.path.join(BASE_DIR, "slot_a")
SLOT_B_DIR = os.path.join(BASE_DIR, "slot_b")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_status():
    with open(STATUS_FILE, "r") as f:
        return json.load(f)


def save_status(data):
    with open(STATUS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def standby_of(active):
    return "B" if active == "A" else "A"


def slot_dir(slot):
    return SLOT_A_DIR if slot == "A" else SLOT_B_DIR


def compute_sha256(filepath):
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def report_to_server(server_url: str, status_msg: str):
    """POST telemetry to the server's /report endpoint. Best-effort."""
    state = load_status()
    payload = {
        "device_id": state.get("device_id", "unknown"),
        "active_slot": state["active_slot"],
        "version": state["current_version"],
        "status": status_msg,
    }
    try:
        requests.post(f"{server_url}/report", json=payload, timeout=5)
    except requests.RequestException:
        pass  # telemetry is best-effort; never block the user


def _do_rollback():
    """Internal rollback: flip active slot back to the standby. Returns new state."""
    state = load_status()
    active = state["active_slot"]
    standby = standby_of(active)
    standby_info = state["slots"][standby]

    if standby_info.get("version") is None:
        console.print(f"[bold red]✗ Slot {standby} is empty — cannot rollback.[/bold red]")
        raise SystemExit(1)

    state["active_slot"] = standby
    state["current_version"] = standby_info["version"]
    save_status(state)

    console.print(
        f"[bold green]✓ Auto-rollback complete.[/bold green] "
        f"Active slot restored to [bold cyan]{standby}[/bold cyan] (v{standby_info['version']})"
    )
    return state


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group()
@click.option(
    "--server-url",
    default="http://127.0.0.1:8000",
    show_default=True,
    help="OTA server URL (use LAN IP for physical boards, e.g. http://192.168.1.50:8000)",
)
@click.pass_context
def cli(ctx, server_url):
    """OTA A/B Partition Firmware Update Client."""
    ctx.ensure_object(dict)
    ctx.obj["server_url"] = server_url.rstrip("/")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@cli.command()
def status():
    """Display the current A/B slot table."""

    state = load_status()
    active = state["active_slot"]

    table = Table(title="Device A/B Slot Status", highlight=True)
    table.add_column("Slot", style="cyan bold", justify="center")
    table.add_column("Active", justify="center")
    table.add_column("Version", style="yellow", justify="center")
    table.add_column("SHA256", style="dim", justify="left")

    for slot in ("A", "B"):
        marker = "[bold green] ✓ ACTIVE[/bold green]" if slot == active else "[dim]— standby[/dim]"
        info = state["slots"][slot]
        version = info.get("version") or "[dim]—[/dim]"
        sha = (info.get("sha256") or "")[:16] + "..." if info.get("sha256") else "[dim]—[/dim]"
        table.add_row(slot, marker, version, sha)

    console.print(table)


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--target-version", default=None, help="Check a specific version instead of latest")
@click.pass_context
def check(ctx, target_version):
    """Query the server for the latest (or a specific) firmware version."""

    server = ctx.obj["server_url"]
    state = load_status()
    current_version = state["current_version"]

    query = f"?v={target_version}" if target_version else ""
    label = f"v{target_version}" if target_version else "latest"

    console.print(f"[dim]Server:[/dim] {server}")
    console.print(f"[dim]Query:[/dim] {label}")

    try:
        resp = requests.get(f"{server}/version{query}", timeout=10)
        resp.raise_for_status()
        info = resp.json()
    except requests.ConnectionError:
        console.print("[bold red]✗ Cannot reach server[/bold red] — check network or server URL")
        raise SystemExit(1)
    except requests.RequestException as e:
        console.print(f"[bold red]✗ Server error:[/bold red] {e}")
        raise SystemExit(1)

    table = Table(title=f"Firmware Version Check ({label})")
    table.add_column("", style="dim", width=12)
    table.add_column("Version", justify="center")
    table.add_column("Size", justify="right")
    table.add_column("SHA256", style="dim", overflow="fold")

    table.add_row("[yellow]Installed[/yellow]", current_version, "—", "—")
    table.add_row(
        "[green]Available[/green]",
        info["latest_version"],
        f"{info['size']:,} bytes",
        info["sha256"],
    )

    console.print(table)

    if current_version != info["latest_version"]:
        console.print("[bold yellow]⚠ Update available.[/bold yellow]  Run [bold cyan]update[/bold cyan] to apply.")
    else:
        console.print("[bold green]✓ Device is up-to-date.[/bold green]")


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--target-version", default=None, help="Install a specific version instead of latest")
@click.option(
    "--simulate-boot-crash",
    is_flag=True,
    default=False,
    help="Simulate a kernel panic after a successful update to trigger auto-rollback",
)
@click.pass_context
def update(ctx, target_version, simulate_boot_crash):
    """Download firmware (resumable), verify checksum, and switch to the standby slot."""

    server = ctx.obj["server_url"]
    state = load_status()
    active = state["active_slot"]
    standby = standby_of(active)
    target_dir = slot_dir(standby)

    os.makedirs(target_dir, exist_ok=True)

    query = f"?v={target_version}" if target_version else ""

    console.print(f"[dim]Server:[/dim] {server}")
    console.print(f"Active slot:  [bold green]{active}[/bold green] (v{state['current_version']})")
    console.print(f"Standby slot: [bold yellow]{standby}[/bold yellow]")

    # --- Phase 1: Fetch server metadata ------------------------------------

    try:
        resp = requests.get(f"{server}/version{query}", timeout=10)
        resp.raise_for_status()
        info = resp.json()
    except requests.ConnectionError:
        console.print("[bold red]✗ Cannot reach server[/bold red] — check network or server URL")
        raise SystemExit(1)
    except requests.RequestException as e:
        console.print(f"[bold red]✗ Server error:[/bold red] {e}")
        raise SystemExit(1)

    target_ver = info["latest_version"]
    expected_sha256 = info["sha256"]
    expected_size = info["size"]

    if state["current_version"] == target_ver:
        console.print(f"[bold yellow]Already on v{target_ver}. Nothing to do.[/bold yellow]")
        return

    # --- Phase 2: Resumable download with progress -------------------------

    console.print(f"\n[bold]Downloading[/bold] firmware v{target_ver}  ({expected_size:,} bytes)")

    dest = os.path.join(target_dir, "firmware.bin")

    # Check for an existing partial file to resume
    resume_offset = 0
    headers = {}
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        resume_offset = os.path.getsize(dest)
        if resume_offset < expected_size:
            headers["Range"] = f"bytes={resume_offset}-"
            console.print(f"[dim]Resuming from byte {resume_offset:,} ({resume_offset * 100 // expected_size}%)[/dim]")
        else:
            console.print("[dim]Existing file is complete — restarting download.[/dim]")
            resume_offset = 0

    file_mode = "ab" if resume_offset > 0 else "wb"

    try:
        dl = requests.get(f"{server}/firmware{query}", stream=True, timeout=60, headers=headers)

        if resume_offset > 0 and dl.status_code == 206:
            console.print("[dim]Server accepted Range request (206 Partial Content).[/dim]")
        elif resume_offset > 0 and dl.status_code != 206:
            # Server didn't support Range — restart from scratch
            console.print("[yellow]Server did not honour Range request. Restarting from 0.[/yellow]")
            resume_offset = 0
            file_mode = "wb"

        dl.raise_for_status()

        progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
        )
        with progress:
            task = progress.add_task("firmware.bin", total=expected_size, completed=resume_offset)
            with open(dest, file_mode) as f:
                for chunk in dl.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        progress.update(task, advance=len(chunk))

    except KeyboardInterrupt:
        console.print("")
        console.print("[bold yellow]Download Aborted by user. Active slot remains unchanged.[/bold yellow]")
        console.print(f"[dim]Partial file kept at {dest}  ({os.path.getsize(dest):,} / {expected_size:,} bytes).[/dim]")
        console.print("[dim]Run [bold]update[/bold] again to resume.[/dim]")
        report_to_server(server, "Download Aborted")
        raise SystemExit(0)

    except requests.RequestException as e:
        console.print(f"[bold red]✗ Download failed:[/bold red] {e}")
        console.print("[bold red]Active slot remains unchanged.[/bold red]")
        console.print(f"[dim]Partial file kept at {dest}. Run [bold]update[/bold] again to resume.[/dim]")
        report_to_server(server, "Download Aborted")
        raise SystemExit(1)

    # --- Phase 3: Verify SHA-256 -------------------------------------------

    console.print("\n[bold]Verifying[/bold] SHA-256 checksum …", end=" ")
    actual_sha256 = compute_sha256(dest)

    if actual_sha256 != expected_sha256:
        console.print("[bold red]MISMATCH[/bold red]")
        console.print(f"  Expected: {expected_sha256}")
        console.print(f"  Actual:   {actual_sha256}")
        console.print("[bold red]✗ Firmware corrupted or tampered — aborting.[/bold red]")
        console.print("[bold red]  Active slot has NOT been changed.[/bold red]")
        os.remove(dest)
        report_to_server(server, "Hash Verification Failed")
        raise SystemExit(1)

    console.print("[bold green]MATCH[/bold green]")

    # --- Phase 4: Switch active slot ---------------------------------------

    previous_active = active
    previous_version = state["current_version"]

    console.print(
        f"\n[bold]Ping-Pong:[/bold] "
        f"Slot [bold green]{active}[/bold green](v{previous_version}) → "
        f"Slot [bold cyan]{standby}[/bold cyan](v{target_ver})"
    )
    console.print(f"[bold]Switching active slot:[/bold]  [bold green]{active}[/bold green] → [bold cyan]{standby}[/bold cyan]")

    state["active_slot"] = standby
    state["current_version"] = target_ver
    state["slots"][standby]["version"] = target_ver
    state["slots"][standby]["sha256"] = actual_sha256
    save_status(state)

    console.print(f"\n[bold green]✓ Update successful![/bold green]")
    console.print(f"  Active slot: [bold cyan]{standby}[/bold cyan]")
    console.print(f"  Version:     [bold cyan]v{target_ver}[/bold cyan]")

    report_to_server(server, "Update Successful")

    # --- Phase 5: Simulated boot crash (optional) --------------------------

    if simulate_boot_crash:
        console.print(
            f"\n[bold red][Simulating Reboot...][/bold red] → [bold red]KERNEL PANIC! Watchdog triggered.[/bold red]"
        )
        _do_rollback()
        report_to_server(server, "Rollback Triggered")


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------


@cli.command()
@click.pass_context
def rollback(ctx):
    """Manually roll back to the other slot."""

    server = ctx.obj["server_url"]
    state = load_status()
    active = state["active_slot"]
    standby = standby_of(active)
    standby_info = state["slots"][standby]

    if standby_info.get("version") is None:
        console.print(f"[bold red]✗ Slot {standby} is empty — nothing to roll back to.[/bold red]")
        raise SystemExit(1)

    console.print(f"[bold yellow]⚠ Initiating rollback[/bold yellow]")
    console.print(f"  From slot: [bold red]{active}[/bold red] (v{state['current_version']})")
    console.print(f"  To slot:   [bold green]{standby}[/bold green] (v{standby_info['version']})")

    _do_rollback()
    report_to_server(server, "Rollback Triggered")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()
