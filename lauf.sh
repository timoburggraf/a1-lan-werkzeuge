#!/usr/bin/env bash
# Startet die Spaghetti-Wache mit den Zugangsdaten aus dem Vault.
# Die Werte werden nur in die Umgebung des Kindprozesses gelegt und nirgends
# ausgegeben oder in eine Datei geschrieben.
set -euo pipefail

BASIS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAULT="$HOME/.claude/skills/vault/scripts/vault_cli.py"

if [[ ! -f "$VAULT" ]]; then
  echo "Vault-CLI nicht gefunden: $VAULT" >&2
  exit 2
fi

hole() {
  local wert
  if ! wert="$(python3 "$VAULT" get "$1" --raw 2>/dev/null)"; then
    echo "Secret '$1' fehlt im Vault." >&2
    return 1
  fi
  printf '%s' "$wert"
}

ANTHROPIC_API_KEY="$(hole ANTHROPIC_API_KEY)"
BAMBU_A1_IP="$(hole BAMBU_A1_IP)"
BAMBU_A1_SERIAL="$(hole BAMBU_A1_SERIAL)"
BAMBU_A1_ACCESS_CODE="$(hole BAMBU_A1_ACCESS_CODE)"
export ANTHROPIC_API_KEY BAMBU_A1_IP BAMBU_A1_SERIAL BAMBU_A1_ACCESS_CODE

exec "$BASIS/.venv/bin/python" "$BASIS/spaghetti_wache.py" "$@"
