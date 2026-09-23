# Automated Server Provisioning & Monitoring Platform

Spin up a small fleet of Ubuntu servers, harden them, deploy an app, and monitor everything, all from a few `make` commands.

A Python CLI creates the VMs and generates the Ansible inventory. Ansible then configures each server through idempotent, role-based playbooks. Every push is linted and tested in CI.

## Architecture

```
                 make up                        make configure
  cluster.yml ───────────► scripts/provision.py ───────────► Ansible (site.yml)
  (desired fleet)              │                                  │
                               │ multipass launch                 │ SSH (key-only)
                               ▼                                  ▼
                    ┌───────────────────┐   ┌───────────────────┐   ┌───────────────────┐
                    │     spp-web       │   │     spp-app       │   │    spp-monitor    │
                    │  role: web        │   │  role: app        │   │  role: monitor    │
                    └───────────────────┘   └───────────────────┘   └───────────────────┘
                              ▲                       ▲                        │
                              └──── node_exporter ────┴──── Prometheus ◄──────┘  (Grafana dashboards)
```

## What's built so far

| Phase | Status | Description |
|-------|--------|-------------|
| 1. Provisioning CLI | Done | `scripts/provision.py` creates VMs from `cluster.yml`, injects an SSH key via cloud-init, writes `inventory/hosts.ini` |
| 2. Baseline hardening | Done | `common` role: packages, key-only SSH with rollback on bad config, UFW firewall, fail2ban, unattended security upgrades |
| 3. App deployment | Planned | `docker` + `app` roles deploying a Flask + Redis Compose stack, `nginx` reverse proxy |
| 4. Monitoring | Planned | node_exporter on every node, Prometheus + Grafana on `monitor`, alert rules |
| 5. Health-check script | Planned | Python script that checks disk/memory/services and posts alerts |

## Quick start

Requirements: [Multipass](https://multipass.run), Python 3.10+, and a Linux, macOS or WSL2 shell for Ansible (see *Windows notes*).

```bash
make setup      # create .venv and install dependencies
make up         # create the VMs and write inventory/hosts.ini
make ping       # confirm Ansible can reach every node
make configure  # apply the site playbook
make status     # show VM state and IPs
make destroy    # delete and purge the VMs
```

Run `make configure` a second time. A healthy, idempotent playbook reports `changed=0` for every host.

## Project layout

```
cluster.yml               desired fleet: names, sizes, roles
scripts/provision.py      Multipass wrapper + inventory generator
site.yml                  main playbook
group_vars/all.yml        fleet-wide variable overrides
roles/common/             baseline hardening role
tests/                    pytest unit tests for the provisioner
.github/workflows/ci.yml  yamllint, ansible-lint, pytest, syntax check
```

## Design decisions

- **Idempotent provisioning.** `up` only launches missing VMs, starts stopped ones, and skips running ones, so it is safe to re-run.
- **Generated inventory.** VM IPs change between runs, so the inventory is rendered from `multipass list` instead of being maintained by hand.
- **Lockout-safe hardening.** The SSH drop-in is validated with `sshd -t` and removed automatically if invalid. UFW always allows SSH before the default-deny policy is enabled.
- **Least surprise for `down`.** It only touches VMs named in `cluster.yml` and uses `delete --purge` on those names, not a global purge.
- **Strict linting.** `ansible-lint` runs at the `production` profile and CI fails on any violation.

## Configuration

Edit `cluster.yml` to change node sizes or add nodes. Override role defaults in `group_vars/all.yml`. Open extra firewall ports per group, for example in `group_vars/web.yml`:

```yaml
common_ufw_extra_rules:
  - port: "80"
    proto: tcp
    comment: HTTP
```

## Development

```bash
make lint    # yamllint + ansible-lint
make test    # pytest
make check   # everything CI runs
```

## Windows notes

Ansible's control node does not run natively on Windows, so run this project from WSL2 (or a Linux/macOS machine).

- Clone the repo **inside the WSL filesystem** (for example `~/projects/`), not under `/mnt/c/`. Ansible ignores `ansible.cfg` in world-writable directories and SSH rejects keys with loose permissions.
- Install Multipass on Windows and point the CLI at it: `export MULTIPASS_BIN=multipass.exe`. The provisioner converts paths with `wslpath` automatically.
- WSL2 must be able to reach the Multipass VM network. If `make ping` times out, check that your VM IPs are routable from WSL2. Alternatively, run everything on a Linux host, or point the same playbooks at AWS free-tier instances.

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `Command not found: multipass` | Multipass not installed or not on `PATH`; set `MULTIPASS_BIN` |
| `make ping` hangs or times out | VM IPs not reachable from the machine running Ansible |
| `Permission denied (publickey)` | Old VM from a previous key; run `make destroy` then `make up` |
| `ansible.cfg` ignored warning | Repo is in a world-writable directory (common under `/mnt/c`) |
