# skill-strategy-capacity-tca

[简体中文](README.md) | **English**

Strategy capacity and transaction-cost analysis (TCA). Backtests often report gross alpha without asking how much capital the book can take before impact eats it. This skill estimates ADV participation, square-root/linear market impact, turnover, concentration, and a capacity curve from trades and ADV. Impact figures are estimates, not guarantees — no trade advice.

<p align="center">
  <img alt="role" src="https://img.shields.io/badge/role-capacity%20%C2%B7%20TCA-brightgreen">
  <img alt="output" src="https://img.shields.io/badge/output-participation%20%C2%B7%20impact%20%C2%B7%20capacity%20curve-blue">
  <img alt="validation" src="https://img.shields.io/badge/validation-9%2F9%20self--tests-orange">
  <img alt="deps" src="https://img.shields.io/badge/deps-pandas%20%2B%20numpy-9cf">
  <img alt="license" src="https://img.shields.io/badge/license-GPLv3-blue">
</p>

`skill-strategy-capacity-tca` is a QuantSkills community portfolio skill. It complements **portfolio attribution** (where returns come from) and **strategy diagnostics** (whether signals decay): this skill answers **capacity and implementation cost**.

## What it solves

- How large is participation vs ADV — is it aggressive?
- Rough annualized cost drag under square-root / linear impact?
- How does the cost curve move at 2× / 4× / 8× AUM, and where does gross alpha break even?

## Method

- **Participation** = `|shares| / ADV`; missing ADV rows are dropped and counted; >20% flagged aggressive
- **Square-root impact** `cost_bps ≈ σ · √participation · c · 1e4` (linear as a foil)
- **Capacity curve** over AUM multipliers `[0.25, 0.5, 1, 2, 4, 8]`; optional alpha breakeven interpolation
- **Falsifiable**: `scripts/validate.py` checks √2 scaling, monotonicity, guards, and self-contained HTML

See `references/impact-models.md`.

## Quick start

```bash
pip install -r requirements.txt
python scripts/validate.py
python scripts/capacity_tca.py \
  --trades examples/data/trades.csv \
  --adv examples/data/adv.csv \
  --price examples/data/price.csv \
  --returns examples/data/returns.csv \
  --alpha 0.05 --nav 1e8 \
  --out examples/output/
```

With `--out`: `capacity_tca.txt`, `.json`, `.html` (warm-paper self-contained report, inline SVG, offline). Use `--no-html` to skip HTML.

## Runtime entrypoints

Claude Code, Codex, and native Skill runtimes load `SKILL.md` directly; Cursor uses `agents/cursor-rule.mdc`; Hermes/OpenClaw can use `agents/portable-loader.md` when native discovery is unavailable.

## Boundaries

Impact models are heuristics, not broker TCA measurements; the capacity curve assumes fixed ADV and proportional turnover. Evidence-first; no investment advice. See `references/source_boundary.md`.

## License

GPL-3.0-only
