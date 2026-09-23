import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import provision  # noqa: E402

CFG = {
    "prefix": "spp",
    "image": "24.04",
    "ssh_user": "ubuntu",
    "key_path": ".keys/id_ed25519",
    "nodes": {
        "web": {"role": "web", "cpus": 1, "memory": "1G", "disk": "8G"},
        "monitor": {"role": "monitor", "cpus": 2, "memory": "2G", "disk": "10G"},
    },
}


def test_render_inventory_groups_by_role():
    vms = {
        "spp-web": {"ipv4": ["10.0.0.5", "172.17.0.1"]},
        "spp-monitor": {"ipv4": ["10.0.0.6"]},
    }
    text = provision.render_inventory(CFG, vms, Path("/keys/id"))
    assert "[web]\nspp-web ansible_host=10.0.0.5" in text
    assert "[monitor]\nspp-monitor ansible_host=10.0.0.6" in text
    assert "ansible_user=ubuntu" in text
    assert "ansible_ssh_private_key_file=/keys/id" in text


def test_render_inventory_skips_nodes_without_ip():
    vms = {"spp-web": {"ipv4": ["10.0.0.5"]}, "spp-monitor": {"ipv4": []}}
    text = provision.render_inventory(CFG, vms, Path("/keys/id"))
    assert "spp-web" in text
    assert "spp-monitor" not in text
    assert "[monitor]" not in text


def test_render_cloud_init_injects_key():
    text = provision.render_cloud_init("ssh-ed25519 AAAA test")
    assert text.startswith("#cloud-config")
    assert "  - ssh-ed25519 AAAA test" in text


def test_multipass_list_parses_json(monkeypatch):
    payload = {"list": [{"name": "spp-web", "state": "Running", "ipv4": ["10.0.0.5"]}]}
    monkeypatch.setattr(provision, "run", lambda cmd, capture=False: json.dumps(payload))
    assert provision.multipass_list()["spp-web"]["state"] == "Running"


def test_launch_missing_is_idempotent(monkeypatch):
    calls = []
    monkeypatch.setattr(provision, "run", lambda cmd, capture=False: calls.append(cmd) or "")
    existing = {
        "spp-web": {"state": "Running"},
        "spp-monitor": {"state": "Stopped"},
    }
    provision.launch_missing(CFG, existing, Path("/tmp/ci.yaml"))
    assert calls == [[provision.MULTIPASS, "start", "spp-monitor"]]


def test_launch_missing_creates_absent_nodes(monkeypatch):
    calls = []
    monkeypatch.setattr(provision, "run", lambda cmd, capture=False: calls.append(cmd) or "")
    provision.launch_missing(CFG, {}, Path("/tmp/ci.yaml"))
    assert len(calls) == 2
    launch = calls[0]
    assert launch[1] == "launch"
    assert launch[launch.index("--name") + 1] == "spp-web"
    assert "--cloud-init" in launch


def test_load_config_rejects_missing_keys(tmp_path):
    bad = tmp_path / "cluster.yml"
    bad.write_text("prefix: spp\n", encoding="utf-8")
    with pytest.raises(provision.ProvisionError):
        provision.load_config(bad)


def test_default_cluster_config_is_valid():
    cfg = provision.load_config(provision.DEFAULT_CONFIG)
    assert set(cfg["nodes"]) >= {"web", "app", "monitor"}
