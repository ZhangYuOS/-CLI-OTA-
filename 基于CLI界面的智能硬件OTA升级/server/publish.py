"""
OTA Firmware Publisher — manage firmware versions in the release manifest.

Commands:
    publish   <version>       Generate and register a new firmware version.
    unpublish <version>       Remove a firmware version from the manifest.
    unpublish --latest        Remove the latest version.
"""

import hashlib
import json
import os

import click
from rich.console import Console
from rich.table import Table

console = Console()

RELEASE_DIR = os.path.join(os.path.dirname(__file__), "release")
MANIFEST_PATH = os.path.join(RELEASE_DIR, "manifest.json")
FIRMWARE_SIZE_MB = 5


def sha256_of(filepath):
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def load_manifest():
    if os.path.exists(MANIFEST_PATH):
        with open(MANIFEST_PATH, "r") as f:
            return json.load(f)
    return {"latest": None, "versions": {}}


def save_manifest(manifest):
    os.makedirs(RELEASE_DIR, exist_ok=True)
    with open(MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2)


def _find_highest(manifest):
    """Return the highest version key, or None if empty."""
    versions = manifest.get("versions", {})
    if not versions:
        return None
    # Sort by tuple of ints so 2.0.0 < 10.0.0
    def key(v):
        return tuple(int(x) for x in v.split("."))
    return max(versions.keys(), key=key)


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group()
def cli():
    """OTA Firmware Release Manager."""


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("version")
def publish(version):
    """Generate and register a new firmware VERSION."""

    filename = f"firmware_v{version}.bin"
    filepath = os.path.join(RELEASE_DIR, filename)

    if os.path.exists(filepath):
        console.print(f"[yellow]⚠ {filename} already exists — overwriting.[/yellow]")

    console.print("[bold]Generating[/] 5 MiB firmware image …")
    header = f"OTA|VERSION:{version}|HEADER|".encode("utf-8")
    header = header.ljust(4096, b"\x00")

    with open(filepath, "wb") as f:
        f.write(header)
        f.write(b"\x00" * ((FIRMWARE_SIZE_MB * 1024 * 1024) - len(header)))

    console.print("[bold]Computing[/] SHA-256 …")
    digest = sha256_of(filepath)
    size = os.path.getsize(filepath)

    manifest = load_manifest()
    manifest["latest"] = version
    manifest["versions"][version] = {
        "filename": filename,
        "size": size,
        "sha256": digest,
    }
    save_manifest(manifest)

    table = Table(title=f"Firmware v{version} Published")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("File", filename)
    table.add_row("Size", f"{size:,} bytes")
    table.add_row("SHA-256", digest)
    table.add_row("Manifest", MANIFEST_PATH)

    console.print(table)
    console.print(f"[bold green]✓ v{version} published.[/bold green]")


# ---------------------------------------------------------------------------
# unpublish
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--latest", "target_latest", is_flag=True, default=False,
              help="Remove the latest version (instead of specifying one).")
@click.argument("version", required=False)
def unpublish(target_latest, version):
    """Remove a firmware version from the manifest.

    \b
    Examples:
      unpublish 3.0.0       Remove v3.0.0
      unpublish --latest     Remove whatever is currently latest
    """

    manifest = load_manifest()
    versions = manifest.get("versions", {})

    if not versions:
        console.print("[bold yellow]Manifest is empty — nothing to remove.[/bold yellow]")
        return

    # Resolve target
    if target_latest:
        target = manifest.get("latest")
        if target is None:
            console.print("[bold yellow]No latest version set — nothing to remove.[/bold yellow]")
            return
    elif version:
        target = version
    else:
        console.print("[bold red]✗ Must specify a VERSION or use --latest.[/bold red]")
        raise SystemExit(1)

    if target not in versions:
        known = ", ".join(versions.keys())
        console.print(f"[bold red]✗ Version '{target}' not in manifest.[/bold red]  Known: {known}")
        raise SystemExit(1)

    entry = versions.pop(target)
    filename = entry["filename"]
    filepath = os.path.join(RELEASE_DIR, filename)

    # Update "latest" pointer
    if manifest["latest"] == target:
        manifest["latest"] = _find_highest(manifest)

    save_manifest(manifest)

    # Remove the binary
    if os.path.exists(filepath):
        os.remove(filepath)
        console.print(f"[dim]Deleted {filename}[/dim]")
    else:
        console.print(f"[dim]File {filename} already absent from disk.[/dim]")

    table = Table(title=f"Firmware v{target} Removed")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Version", target)
    table.add_row("File", filename)
    table.add_row("New latest", manifest["latest"] or "[dim]— none —[/dim]")
    table.add_row("Remaining", ", ".join(sorted(versions.keys(), key=lambda v: tuple(int(x) for x in v.split(".")))) if versions else "[dim]none[/dim]")

    console.print(table)
    console.print(f"[bold green]✓ v{target} removed from manifest.[/bold green]")


if __name__ == "__main__":
    cli()
