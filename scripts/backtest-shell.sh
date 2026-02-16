#!/usr/bin/env bash
# Source this file to enter a backtest-ready shell for atlas-trading-engine.
# Usage: source scripts/backtest-shell.sh

set -euo pipefail

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "This script must be sourced, not executed."
  echo "Usage: source scripts/backtest-shell.sh"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_DIR}"

if [[ ! -d .venv ]]; then
  echo "Creating virtual environment at ${PROJECT_DIR}/.venv"
  python3.12 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if [[ ! -f .env ]]; then
  echo "Missing .env. Creating from .env.example"
  cp .env.example .env
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

missing_vars=()
for key in OANDA_API_KEY OANDA_ACCOUNT_ID OANDA_ENV; do
  if [[ -z "${!key:-}" ]]; then
    missing_vars+=("${key}")
  fi
done

if [[ ${#missing_vars[@]} -gt 0 ]]; then
  echo "Warning: missing required env vars: ${missing_vars[*]}"
else
  echo "OANDA environment variables loaded."
fi

echo "Backtest shell ready in ${PROJECT_DIR}"
echo "Try: python -m app.backtest.run --symbol AUD_USD --timeframe M15 --days 30 --units 1000 --spread_pips 1.2"
