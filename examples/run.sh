#!/bin/zsh
# Wrapper for schedulers. Secrets come from the macOS Keychain at run time, never from a file:
#   security add-generic-password -s ghl-kpi-token -a "$USER" -w     (paste the Private Integration token)
#   security add-generic-password -s anthropic-api-key -a "$USER" -w (optional, for the Claude summary)
# Everything else (GHL_LOCATION_ID, DATABASE_URL, SMTP_*) lives in .env.
set -euo pipefail
cd "$(dirname "$0")/.."
export GHL_TOKEN="$(security find-generic-password -s "${GHL_TOKEN_KEYCHAIN_ITEM:-ghl-kpi-token}" -w)"
export ANTHROPIC_API_KEY="$(security find-generic-password -s anthropic-api-key -w 2>/dev/null || true)"
exec .venv/bin/python -m ghl_kpi "$@"
