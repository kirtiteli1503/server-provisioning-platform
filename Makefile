VENV ?= .venv
BIN  := $(VENV)/bin

.PHONY: help setup up inventory ping configure status destroy lint test check

help:
	@echo "make setup      create venv and install dependencies"
	@echo "make up         create VMs and write inventory/hosts.ini"
	@echo "make ping       verify Ansible can reach every node"
	@echo "make configure  run the site playbook"
	@echo "make status     show VM state and IPs"
	@echo "make destroy    delete and purge the VMs"
	@echo "make lint       yamllint + ansible-lint"
	@echo "make test       pytest"
	@echo "make check      lint + test + playbook syntax check (same as CI)"

setup:
	python3 -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -r requirements.txt

up:
	$(BIN)/python scripts/provision.py up

inventory:
	$(BIN)/python scripts/provision.py inventory

ping:
	$(BIN)/ansible all -m ansible.builtin.ping

configure:
	$(BIN)/ansible-playbook site.yml

status:
	$(BIN)/python scripts/provision.py status

destroy:
	$(BIN)/python scripts/provision.py down

lint:
	$(BIN)/yamllint .
	$(BIN)/ansible-lint

test:
	$(BIN)/pytest -q

check: lint test
	$(BIN)/ansible-playbook --syntax-check -i localhost, site.yml
