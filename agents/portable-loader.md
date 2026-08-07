# Portable Loader

Use this loader with Hermes or OpenClaw when the runtime does not natively
discover `SKILL.md` folders. If native skill discovery is available, install
the full folder unchanged and load `SKILL.md` directly.

```text
You have access to a local skill named strategy-capacity-tca at:
<STRATEGY_CAPACITY_TCA_SKILL_ROOT>

When the user asks about strategy capacity, ADV participation, market impact,
TCA-style cost estimates, capacity curves, or whether costs eat alpha at larger
AUM:
1. Read <STRATEGY_CAPACITY_TCA_SKILL_ROOT>/SKILL.md.
2. Read references/impact-models.md for model assumptions and
   references/source_boundary.md for evidence limits.
3. Confirm trades + ADV columns (and optional price/returns/alpha/nav).
4. Use scripts/capacity_tca.py for deterministic calculations.
5. Separate data facts (participation, turnover, dropped rows) from model
   estimates (impact bps, breakeven AUM).
6. Do not provide buy/sell signals, guaranteed capacity, or investment advice.
```

Runtime placement:

- Codex: install under a Codex skill path and invoke `$strategy-capacity-tca`.
- Claude Code: install under a Claude skill path and invoke
  `$strategy-capacity-tca`.
- Cursor: copy to `.cursor/skills/strategy-capacity-tca` and enable
  `agents/cursor-rule.mdc`.
- Hermes/OpenClaw: mount the folder as a local skill root or paste the loader
  above with the real path.
