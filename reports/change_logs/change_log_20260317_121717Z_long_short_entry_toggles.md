# Change Log: Long/Short Entry Toggle Work

- Timestamp (UTC): 2026-03-17T12:17:17Z
- Branch: vps-baseline
- Starting commit: da5a4f5a5a13a9c80cae5a313e4424c8c41800ed

## User Goal
Add controls to selectively disable weak long entry logic (without touching shorts), so we can reduce large long SL losses.

## Pre-Change Safety Snapshot
Repository was already dirty before this change set. Existing modified/untracked files are preserved as-is.

### `git status --short` at start
```
 M .env.example
 M README.md
 M app/backtest/run.py
 M app/config.py
 M app/engine/evaluator.py
 M app/engine/strategies/oakbridge_fxtrader_v2.py
 M app/engine/strategy_base.py
 M tests/test_backtest_magnifier.py
?? app/backtest/compound_report.py
?? reports/... (many backtest report artifacts)
?? scripts/analyze_long_sl_signatures.py
?? tests/test_exit_guards.py
?? tmp/
```

## Planned File Touches (this change set)
- `app/engine/strategy_base.py`
- `app/config.py`
- `app/engine/evaluator.py`
- `app/backtest/run.py`
- `app/engine/strategies/oakbridge_fxtrader_v2.py`
- `.env.example`
- `README.md`
- `tests/test_exit_guards.py` (if test updates required)

## Rollback Instructions
If these specific changes need to be undone later, restore only the touched files listed above.

Example targeted rollback (run manually when needed):
```bash
git restore app/engine/strategy_base.py app/config.py app/engine/evaluator.py app/backtest/run.py app/engine/strategies/oakbridge_fxtrader_v2.py .env.example README.md tests/test_exit_guards.py
```

If you want to preserve current state before rollback, stash first:
```bash
git stash push -m "pre-rollback long-short-entry-toggle work"
```

## Implementation Notes
- No destructive git commands are used.
- Existing unrelated local changes are not reverted.

## Post-Change Implementation Summary

### Added New Side-Specific Entry Toggles
- `STRATEGY_PB_ENABLED_LONG`
- `STRATEGY_PB_ENABLED_SHORT`
- `STRATEGY_REJOIN_ENABLED_LONG`
- `STRATEGY_REJOIN_ENABLED_SHORT`

### Files Updated
- `app/engine/strategy_base.py`
  - Added config fields: `pb_enabled_long`, `pb_enabled_short`, `rejoin_enabled_long`, `rejoin_enabled_short`.
- `app/config.py`
  - Added env-backed settings for the four new toggles.
- `app/engine/evaluator.py`
  - Wired new settings into runtime `StrategyConfig` construction.
- `app/backtest/run.py`
  - Wired new settings into backtest `StrategyConfig` construction.
- `app/engine/strategies/oakbridge_fxtrader_v2.py`
  - Long/short pullback and rejoin logic now checks side-specific toggles.
- `.env.example`
  - Added sample env vars for the four toggles.
- `README.md`
  - Documented the four toggles.
- `tests/test_exit_guards.py`
  - Added `test_rejoin_long_respects_side_toggle`.
  - Updated rejoin fixture to deterministic rejoin-only trigger sequence.

## Validation
Executed:
```bash
.venv/bin/python -m unittest tests/test_exit_guards.py tests/test_backtest_magnifier.py
.venv/bin/python -m py_compile app/engine/strategy_base.py app/config.py app/engine/evaluator.py app/backtest/run.py app/engine/strategies/oakbridge_fxtrader_v2.py scripts/analyze_long_sl_signatures.py
```
Both commands completed successfully.

## How To Use New Toggles
Example: disable only long pullback/rejoin while preserving short behavior.
```bash
STRATEGY_PB_ENABLED_LONG=false STRATEGY_REJOIN_ENABLED_LONG=false python -m app.backtest.run ...
```

## Revert Only This Feature
```bash
git restore app/engine/strategy_base.py app/config.py app/engine/evaluator.py app/backtest/run.py app/engine/strategies/oakbridge_fxtrader_v2.py .env.example README.md tests/test_exit_guards.py
```
