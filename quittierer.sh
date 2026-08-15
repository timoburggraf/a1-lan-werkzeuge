#!/usr/bin/env bash
# Startet den Stoerungs-Quittierer mit Druckerdaten aus dem Vault.
set -euo pipefail
BASIS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAULT="$HOME/.claude/skills/vault/scripts/vault_cli.py"
BAMBU_A1_IP="$(python3 "$VAULT" get BAMBU_A1_IP --raw)"
BAMBU_A1_SERIAL="$(python3 "$VAULT" get BAMBU_A1_SERIAL --raw)"
BAMBU_A1_ACCESS_CODE="$(python3 "$VAULT" get BAMBU_A1_ACCESS_CODE --raw)"
export BAMBU_A1_IP BAMBU_A1_SERIAL BAMBU_A1_ACCESS_CODE
exec "$BASIS/.venv/bin/python" "$BASIS/stoerungs_quittierer.py"
