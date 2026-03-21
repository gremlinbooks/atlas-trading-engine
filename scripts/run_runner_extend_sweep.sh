#!/usr/bin/env bash
  set -euo pipefail

  TS="$(date -u +%Y%m%d_%H%M%S)"
  OUTDIR="reports/sweeps/runner_extend_${TS}"
  mkdir -p "$OUTDIR"

  run_case () {
    local NAME="$1"
    local TP1="$2"
    local TRAIL="$3"
    local BE="$4"
    local F1T="$5"
    local F1L="$6"
    local F2T="$7"
    local F2L="$8"

    echo "===== ${NAME} ====="
    STRATEGY_DRAWDOWN_STOP_BARS=0 \
    STRATEGY_MAX_HOLD_BARS=0 \
    STRATEGY_BLOCK_TRADES=true \
    STRATEGY_BLOCK_SESSION=2100-2300 \
    STRATEGY_PB_ENABLED_LONG=false \
    STRATEGY_REJOIN_ENABLED_LONG=false \
    STRATEGY_TP1_CLOSE_PCT="$TP1" \
    STRATEGY_TRAIL_DRAWDOWN_PCT="$TRAIL" \
    STRATEGY_BE_LOCK_PIPS="$BE" \
    STRATEGY_PROFIT_FLOOR1_TRIGGER_PIPS="$F1T" \
    STRATEGY_PROFIT_FLOOR1_LOCK_PIPS="$F1L" \
    STRATEGY_PROFIT_FLOOR2_TRIGGER_PIPS="$F2T" \
    STRATEGY_PROFIT_FLOOR2_LOCK_PIPS="$F2L" \
    python -m app.backtest.run \
      --symbol AUD_USD --timeframe M15 --from 2026-01-01 --to 2026-02-18 \
      --units 21000 --spread_pips 1.4 --tp1_pips 20 --sl_pips 28 \
      --bar_fill_policy conservative --use_runner true --use_stoch_exit false \
      --exec_profile live_reality --magnifier m1 --use_bid_ask true \
      --exit_inspect_tf M1 | tee "${OUTDIR}/${NAME}.log"
  }

  run_case A_control           30 1.4 22 10 10 15 15
  run_case B_wider_trail       30 1.8 22 10 10 15 15
  run_case C_trail18_be18      30 1.8 18 10 10 15 15
  run_case D_tp1_25            25 1.8 18 10 10 15 15
  run_case E_floor_delayed     25 1.8 18 15  6 25 10
  run_case F_max_extension     20 2.2 16 20  6 30 10

  echo "Logs written to: ${OUTDIR}"
