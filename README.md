# skill-strategy-capacity-tca

**简体中文** | [English](README.en.md)

策略容量与交易成本分析（TCA）。回测常给毛收益，却很少回答「放大资金后冲击会吃掉多少 alpha」。本 skill 从成交与 ADV 估计参与率、平方根/线性冲击、换手、集中度与容量曲线；冲击是估计而非保证，不提供买卖指令。

<p align="center">
  <img alt="role" src="https://img.shields.io/badge/role-容量·TCA-brightgreen">
  <img alt="output" src="https://img.shields.io/badge/output-参与率·冲击·容量曲线-blue">
  <img alt="validation" src="https://img.shields.io/badge/validation-9%2F9自检通过-orange">
  <img alt="deps" src="https://img.shields.io/badge/deps-pandas%20%2B%20numpy-9cf">
  <img alt="license" src="https://img.shields.io/badge/license-GPLv3-blue">
</p>

`skill-strategy-capacity-tca` 是 QuantSkills 社区的组合类 Skill。它与**组合归因**（收益从哪来）、**策略诊断**（信号是否衰减）互补：本 skill 专答**容量与实现成本**。

## 这个 Skill 解决什么问题

- 策略相对 ADV 的**参与率**是否 aggressive？
- 平方根/线性冲击下，年化成本拖累大概多少？
- 资金放大到 2× / 4× / 8× 后，成本曲线如何走？毛 alpha 在何处被吃光？

## 方法要点

- **参与率** = `|shares| / ADV`；缺失 ADV 计入丢弃；>20% 警示
- **平方根冲击** `cost_bps ≈ σ · √participation · c · 1e4`（线性作对照）
- **容量曲线** AUM 倍数 `[0.25, 0.5, 1, 2, 4, 8]`；可选 alpha 盈亏平衡插值
- **可证伪**：`scripts/validate.py` 校验 √2 标度、单调性、护栏与 HTML 自包含

详见 `references/impact-models.md`。

## 快速开始

```bash
pip install -r requirements.txt

# 自检
python scripts/validate.py

# 示例数据
python scripts/capacity_tca.py \
  --trades examples/data/trades.csv \
  --adv examples/data/adv.csv \
  --price examples/data/price.csv \
  --returns examples/data/returns.csv \
  --alpha 0.05 --nav 1e8 \
  --out examples/output/
```

指定 `--out` 时输出：`capacity_tca.txt`、`.json`、`.html`（暖纸色自包含报告，内联 SVG，离线可用）。加 `--no-html` 可跳过 HTML。

## 输入列

| 文件 | 必需列 |
|------|--------|
| trades | `date,symbol` + `shares`（及可选 `side`）或 `notional` |
| adv | `date,symbol` + `adv_shares` 或 `adv_notional` |
| price（可选） | `date,symbol,close` |
| returns（可选） | `date,symbol,ret` → Amihud 代理 |

## 目录结构

```
skill-strategy-capacity-tca/
├── SKILL.md
├── README.md / README.en.md
├── agents/
│   ├── openai.yaml
│   ├── portable-loader.md
│   └── cursor-rule.mdc
├── references/
│   ├── impact-models.md
│   └── source_boundary.md
├── scripts/
│   ├── capacity_tca.py
│   └── validate.py
└── examples/
    ├── data/
    └── output/
```

## 边界

冲击模型是启发式估计，不是券商实测 TCA；容量曲线假设 ADV 与换手结构不变。证据优先，不作交易建议。详见 `references/source_boundary.md`。

## License

GPL-3.0-only
