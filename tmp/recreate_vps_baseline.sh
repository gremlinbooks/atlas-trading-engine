#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKTREE_DIR="${WORKTREE_DIR:-/tmp/atlas-vps-baseline}"
BASE_REF="${BASE_REF:-origin/main}"
BRANCH_NAME="${BRANCH_NAME:-vps-baseline-repro}"

MAX_HOLD_BARS="${MAX_HOLD_BARS:-32}"
DRAWDOWN_STOP_PIPS="${DRAWDOWN_STOP_PIPS:-15}"
DRAWDOWN_STOP_BARS="${DRAWDOWN_STOP_BARS:-4}"

SYMBOL="${SYMBOL:-AUD_USD}"
TIMEFRAME="${TIMEFRAME:-M15}"
DAYS="${DAYS:-30}"
UNITS="${UNITS:-8000}"
SPREAD_PIPS="${SPREAD_PIPS:-1.3}"
TP1_PIPS="${TP1_PIPS:-20}"
TP1_CLOSE_PCT="${TP1_CLOSE_PCT:-30}"
TRAIL_DRAWDOWN_PCT="${TRAIL_DRAWDOWN_PCT:-2.0}"
BE_LOCK_PIPS="${BE_LOCK_PIPS:-20}"

SL_VALUES=("$@")
if [ "${#SL_VALUES[@]}" -eq 0 ]; then
  SL_VALUES=(24 26 28 30 32)
fi

cd "${ROOT_DIR}"

echo "Repo: ${ROOT_DIR}"
echo "Base ref: ${BASE_REF}"
echo "Worktree: ${WORKTREE_DIR}"
echo "Branch: ${BRANCH_NAME}"
echo "Max hold: ${MAX_HOLD_BARS}"
echo "Drawdown stop: ${DRAWDOWN_STOP_PIPS} pips for ${DRAWDOWN_STOP_BARS} bars"
echo

if git worktree list --porcelain | grep -Fq "worktree ${WORKTREE_DIR}"; then
  echo "Worktree already registered at ${WORKTREE_DIR}"
else
  echo "Creating detached worktree at ${WORKTREE_DIR}"
  git worktree add --detach "${WORKTREE_DIR}" "${BASE_REF}"
fi

if git show-ref --verify --quiet "refs/heads/${BRANCH_NAME}"; then
  echo "Branch ${BRANCH_NAME} already exists"
else
  echo "Creating branch ${BRANCH_NAME} from ${BASE_REF}"
  git branch "${BRANCH_NAME}" "${BASE_REF}"
fi

cd "${WORKTREE_DIR}"

for sl in "${SL_VALUES[@]}"; do
  echo
  echo "Running backtest with sl_pips=${sl}"
  STRATEGY_MAX_HOLD_BARS="${MAX_HOLD_BARS}" \
  STRATEGY_DRAWDOWN_STOP_PIPS="${DRAWDOWN_STOP_PIPS}" \
  STRATEGY_DRAWDOWN_STOP_BARS="${DRAWDOWN_STOP_BARS}" \
  python -m app.backtest.run \
    --symbol "${SYMBOL}" \
    --timeframe "${TIMEFRAME}" \
    --days "${DAYS}" \
    --units "${UNITS}" \
    --spread_pips "${SPREAD_PIPS}" \
    --tp1_pips "${TP1_PIPS}" \
    --sl_pips "${sl}" \
    --tp1_close_pct "${TP1_CLOSE_PCT}" \
    --trail_drawdown_pct "${TRAIL_DRAWDOWN_PCT}" \
    --be_lock_pips "${BE_LOCK_PIPS}" \
    --bar_fill_policy conservative \
    --use_runner true \
    --use_bid_ask true \
    --exec_profile live_reality
done
