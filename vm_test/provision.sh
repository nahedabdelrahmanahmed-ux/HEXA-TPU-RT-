#!/usr/bin/env bash
# Run once inside the VM (Ubuntu 22.04/24.04). Works without Vagrant too.
set -euo pipefail
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip curl iproute2 stress-ng
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
echo "OK. Run: ./vm_test/run_vm_tests.sh all"
