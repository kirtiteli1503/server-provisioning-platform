#!/usr/bin/env python3
"""Provision a small Multipass VM fleet and generate an Ansible inventory.

Usage:
    provision.py up          create missing VMs, wait for IPs, write inventory
    provision.py inventory   regenerate inventory/hosts.ini from running VMs
    provision.py status      show state and IP of each VM in cluster.yml
    provision.py down [-y]   delete and purge the VMs in cluster.yml

Set MULTIPASS_BIN to override the multipass executable
(for example `multipass.exe` when running from WSL).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "cluster.yml"
INVENTORY_PATH = ROOT / "inventory" / "hosts.ini"
MULTIPASS = os.environ.get("MULTIPASS_BIN", "multipass")
REQUIRED_KEYS = ("prefix", "image", "ssh_user", "key_path", "nodes")


class ProvisionError(RuntimeError):
    """Raised for any expected, user-facing failure."""


def run(cmd: list[str], capture: bool = False) -> str:
    """Run a command, returning stdout when capture=True."""
    try:
        result = subprocess.run(cmd, check=True, text=True, capture_output=capture)
    except FileNotFoundError as exc:
        raise ProvisionError(
            f"Command not found: {cmd[0]}. Is it installed and on your PATH?"
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()
        raise ProvisionError(f"Command failed: {' '.join(cmd)}\n{detail}") from exc
    return result.stdout if capture else ""


# --------------------------------------------------------------------------
# Config and naming
# --------------------------------------------------------------------------
def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    missing = [key for key in REQUIRED_KEYS if key not in cfg]
    if missing:
        raise ProvisionError(f"{path} is missing required keys: {', '.join(missing)}")
    for node, spec in cfg["nodes"].items():
        if "role" not in spec:
            raise ProvisionError(f"Node '{node}' in {path} has no 'role'")
    return cfg


def vm_name(cfg: dict, node: str) -> str:
    return f"{cfg['prefix']}-{node}"


def key_path(cfg: dict) -> Path:
    path = Path(cfg["key_path"]).expanduser()
    return path if path.is_absolute() else ROOT / path


# --------------------------------------------------------------------------
# SSH key and cloud-init
# --------------------------------------------------------------------------
def ensure_keypair(path: Path) -> str:
    """Create an ed25519 keypair if missing; return the public key text."""
    pub = Path(f"{path}.pub")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "server-provisioning-platform", "-f", str(path)])
        print(f"Generated SSH keypair at {path}")
    return pub.read_text(encoding="utf-8").strip()


def render_cloud_init(public_key: str) -> str:
    return f"#cloud-config\nssh_authorized_keys:\n  - {public_key}\n"


def to_multipass_path(path: Path) -> str:
    """Translate a WSL path to a Windows path when driving multipass.exe."""
    if MULTIPASS.lower().endswith(".exe") and shutil.which("wslpath"):
        return run(["wslpath", "-w", str(path)], capture=True).strip()
    return str(path)


# --------------------------------------------------------------------------
# Multipass wrappers
# --------------------------------------------------------------------------
def multipass_list() -> dict[str, dict]:
    out = run([MULTIPASS, "list", "--format", "json"], capture=True)
    return {vm["name"]: vm for vm in json.loads(out).get("list", [])}


def launch_missing(cfg: dict, existing: dict[str, dict], cloud_init_file: Path) -> None:
    """Idempotently ensure every node in the config exists and is running."""
    for node, spec in cfg["nodes"].items():
        name = vm_name(cfg, node)
        state = existing.get(name, {}).get("state")
        if state == "Running":
            print(f"[skip]   {name} already running")
        elif state in ("Stopped", "Suspended"):
            print(f"[start]  {name} ({state})")
            run([MULTIPASS, "start", name])
        elif state is not None:
            print(f"[warn]   {name} is in state '{state}', leaving it alone")
        else:
            print(f"[launch] {name}")
            run(
                [
                    MULTIPASS, "launch", str(cfg["image"]),
                    "--name", name,
                    "--cpus", str(spec.get("cpus", 1)),
                    "--memory", str(spec.get("memory", "1G")),
                    "--disk", str(spec.get("disk", "8G")),
                    "--cloud-init", to_multipass_path(cloud_init_file),
                ]
            )


def wait_for_ips(cfg: dict, timeout: int = 180) -> dict[str, dict]:
    """Poll until every node reports an IPv4 address."""
    wanted = [vm_name(cfg, node) for node in cfg["nodes"]]
    deadline = time.monotonic() + timeout
    while True:
        vms = multipass_list()
        pending = [n for n in wanted if not vms.get(n, {}).get("ipv4")]
        if not pending:
            return vms
        if time.monotonic() > deadline:
            raise ProvisionError(f"Timed out waiting for IPs: {', '.join(pending)}")
        time.sleep(3)


# --------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------
def render_inventory(cfg: dict, vms: dict[str, dict], private_key: Path) -> str:
    """Build INI inventory text. Nodes without an IP are skipped."""
    groups: dict[str, list[str]] = {}
    for node, spec in cfg["nodes"].items():
        name = vm_name(cfg, node)
        ips = vms.get(name, {}).get("ipv4") or []
        if ips:
            groups.setdefault(spec["role"], []).append(f"{name} ansible_host={ips[0]}")

    lines = ["# Generated by scripts/provision.py - do not edit by hand.", ""]
    for role in sorted(groups):
        lines.append(f"[{role}]")
        lines.extend(groups[role])
        lines.append("")
    lines += [
        "[all:vars]",
        f"ansible_user={cfg['ssh_user']}",
        f"ansible_ssh_private_key_file={private_key}",
        "ansible_ssh_common_args='-o StrictHostKeyChecking=accept-new'",
        "",
    ]
    return "\n".join(lines)


def write_inventory(cfg: dict, vms: dict[str, dict]) -> None:
    missing = [vm_name(cfg, n) for n in cfg["nodes"] if not vms.get(vm_name(cfg, n), {}).get("ipv4")]
    if missing:
        print(f"[warn]   no IP yet for: {', '.join(missing)} (left out of inventory)")
    INVENTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    INVENTORY_PATH.write_text(render_inventory(cfg, vms, key_path(cfg)), encoding="utf-8")
    print(f"Wrote {INVENTORY_PATH.relative_to(ROOT)}")


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def cmd_up(cfg: dict, _args: argparse.Namespace) -> None:
    public_key = ensure_keypair(key_path(cfg))
    with tempfile.TemporaryDirectory() as tmp:
        cloud_init_file = Path(tmp) / "cloud-init.yaml"
        cloud_init_file.write_text(render_cloud_init(public_key), encoding="utf-8")
        launch_missing(cfg, multipass_list(), cloud_init_file)
    write_inventory(cfg, wait_for_ips(cfg))
    print("\nFleet is ready. Next: make ping && make configure")


def cmd_inventory(cfg: dict, _args: argparse.Namespace) -> None:
    write_inventory(cfg, multipass_list())


def cmd_status(cfg: dict, _args: argparse.Namespace) -> None:
    vms = multipass_list()
    print(f"{'NAME':<16}{'ROLE':<10}{'STATE':<12}IPV4")
    for node, spec in cfg["nodes"].items():
        name = vm_name(cfg, node)
        vm = vms.get(name, {})
        ip = (vm.get("ipv4") or ["-"])[0]
        print(f"{name:<16}{spec['role']:<10}{vm.get('state', 'absent'):<12}{ip}")


def cmd_down(cfg: dict, args: argparse.Namespace) -> None:
    vms = multipass_list()
    targets = [vm_name(cfg, n) for n in cfg["nodes"] if vm_name(cfg, n) in vms]
    if not targets:
        print("Nothing to delete.")
        return
    if not args.yes:
        answer = input(f"Delete and purge {', '.join(targets)}? [y/N] ")
        if answer.strip().lower() != "y":
            print("Aborted.")
            return
    run([MULTIPASS, "delete", "--purge", *targets])
    INVENTORY_PATH.unlink(missing_ok=True)
    print(f"Deleted: {', '.join(targets)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG, help="path to cluster.yml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("up", help="create VMs and write inventory").set_defaults(func=cmd_up)
    sub.add_parser("inventory", help="regenerate inventory").set_defaults(func=cmd_inventory)
    sub.add_parser("status", help="show VM status").set_defaults(func=cmd_status)
    down = sub.add_parser("down", help="delete and purge VMs")
    down.add_argument("-y", "--yes", action="store_true", help="skip confirmation")
    down.set_defaults(func=cmd_down)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(load_config(args.config), args)
    except ProvisionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
