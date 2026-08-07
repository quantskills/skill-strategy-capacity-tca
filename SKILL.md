---
name: strategy-capacity-tca
description: 策略容量, 冲击成本, ADV参与率, TCA, 容量曲线, implementation shortfall style cost estimate. Estimate how much capital a strategy can take before costs eat alpha — participation vs ADV, square-root/linear impact, capacity curve and breakeven AUM. Evidence-first; impact model is an estimate not a guarantee; no trade advice. Use when the user asks 策略容量, 冲击成本, ADV参与率, TCA, 容量曲线, 交易成本吃掉多少alpha, or market impact / capacity analysis on Claude Code, Codex, Cursor, Hermes, or OpenClaw.
license: GPL-3.0-only
metadata:
  organization: QuantSkills
  organization_url: https://github.com/quantskills
  repository: skill-strategy-capacity-tca
  repository_url: https://github.com/quantskills/skill-strategy-capacity-tca
  project_type: skill
  collection: strategy-capacity-tca
quantSkills:
  project_type: skill
  category: portfolio
  tags:
  - capacity
  - tca
  - market-impact
  - turnover
  - liquidity
  platforms:
  - claude-code
  - codex
  - cursor
  - hermes
  - openclaw
  language: zh-en
  status: stable
  validation_level: runnable
  maintainer_type: community
  requires: []
  summary_zh: 用成交与 ADV 估计参与率、平方根/线性冲击成本与容量曲线，回答「策略能承载多少资金、成本何时吃掉 alpha」——估计非保证，不作交易建议。
  summary_en: Estimate strategy capacity and transaction costs from trades and ADV — participation, square-root/linear impact, capacity curve and breakeven vs alpha. Estimates only, no trade advice.
---

```json qsh-form
{
  "version": 1,
  "task": {
    "placeholder": "补充成交/ADV 路径、预期 alpha、关心的容量问题（可选）",
    "required": false
  },
  "fields": [
    {
      "key": "trades",
      "label": "成交 CSV",
      "type": "text",
      "placeholder": "examples/data/trades.csv"
    },
    {
      "key": "adv",
      "label": "ADV CSV",
      "type": "text",
      "placeholder": "examples/data/adv.csv"
    },
    {
      "key": "alpha",
      "label": "预期毛年化 alpha",
      "type": "number",
      "default": 0.05,
      "help": "小数，如 0.05=5%；用于盈亏平衡容量"
    },
    {
      "key": "nav",
      "label": "起始 AUM / NAV",
      "type": "number",
      "default": 100000000
    },
    {
      "key": "impact_coef",
      "label": "平方根冲击系数",
      "type": "select",
      "default": "0.5",
      "options": [
        { "value": "0.3", "label": "0.3（偏保守）" },
        { "value": "0.5", "label": "0.5（默认）" },
        { "value": "1.0", "label": "1.0（偏激进）" }
      ]
    }
  ],
  "prompt_template": "{{#task}}任务与材料：\n{{task}}\n\n{{/task}}{{#attachments}}用户上传的材料（已放入工作区）：\n{{attachments}}\n\n{{/attachments}}用成交 {{trades}} 与 ADV {{adv}} 做策略容量/TCA：NAV={{nav}}，alpha={{alpha}}，冲击系数={{impact_coef}}。先读 SKILL.md 与 references/impact-models.md，再运行 scripts/capacity_tca.py，报告参与率、冲击成本、容量曲线与盈亏平衡；冲击为估计非保证，不给买卖指令。"
}
```

# 策略容量与交易成本分析（Strategy Capacity & TCA）

策略回测常给毛收益，却很少回答：**放大资金后冲击成本会吃掉多少 alpha？** 本 skill 从成交明细与 ADV 出发，估计 ADV 参与率、平方根/线性市场冲击、换手、集中度与容量曲线，并在给定毛 alpha 时寻找盈亏平衡 AUM。

**与相邻 skill 的边界**：
- **组合归因**（portfolio attribution）回答收益来自哪里（配置/选股/交互）
- **策略诊断**（diagnostics）回答信号是否衰减、换手是否异常、敞口是否漂移
- **本 skill** 回答**容量与实现成本**：能管多大、参与率多高、估计冲击多少

**输出范围**：参与率、换手、冲击成本估计、容量曲线、集中度、可选盈亏平衡点。  
**不做的事**：不给出买卖指令、不承诺真实成交成本、不替代券商 TCA / 成交分析系统。

## 何时使用

- "这个策略能管多大资金？成本会不会吃掉 alpha？"
- "成交相对 ADV 的参与率是多少？算不算 aggressive？"
- "帮我画容量曲线 / 估计平方根冲击成本 / 做一版简易 TCA"

## 输入数据

| 文件 | 列 | 说明 |
|------|----|------|
| `--trades`（必需） | `date,symbol,side,shares` 或 `date,symbol,notional` | side = buy/sell 或 +1/−1 |
| `--adv`（必需） | `date,symbol,adv_shares` 或 `adv_notional` | 平均日成交量/额 |
| `--price`（可选） | `date,symbol,close` | 股数 ↔ 名义转换 |
| `--returns`（可选） | `date,symbol,ret` | 启用 Amihud 风格非流动性代理 |
| `--alpha`（可选） | 小数，如 `0.05` | 预期毛年化 alpha，用于盈亏平衡 |
| `--nav`（可选） | 起始资金 | AUM 代理；缺省时从成交名义粗估 |

## 工作流

### 第 1 步：对齐成交与 ADV
- 确认交易日、标的代码与 ADV 口径一致（股数对股数，名义对名义）
- 缺失 ADV 的成交会被丢弃并计入警告；若可用成交为 0 则拒绝出报告

### 第 2 步：运行容量 / TCA
```bash
python scripts/capacity_tca.py --trades t.csv --adv a.csv \
  [--alpha 0.05] [--nav 1e8] [--impact-coef 0.5] [--out report/] [--no-html]
```

得到：
- **参与率** `|shares|/ADV`（>20% 标 aggressive；>100% 警示）
- **换手** 单向/双向及年化
- **冲击成本** 平方根模型为主、线性为对照（见 `references/impact-models.md`）
- **容量曲线** AUM 倍数 `[0.25, 0.5, 1, 2, 4, 8]`；若给 alpha，插值盈亏平衡点
- **集中度** Top-10 成交名义占比
- **Amihud**（仅当提供 `--returns`）

### 第 3 步：解读边界
- 冲击系数、σ 是标定参数，不是真理——换市场须重标定
- 容量曲线假设「换手随 AUM 同比放大、ADV 不变」——现实中 ADV 与执行方式会变
- 每个数字标注：是**数据事实**（参与率、换手）还是**模型估计**（冲击、盈亏平衡）

### 第 4 步：输出报告
指定 `--out` 时写出：`capacity_tca.txt`、`.json`、`.html`（自包含暖纸色 HTML，内联 SVG：参与率直方图 + 容量曲线，零外部依赖）。加 `--no-html` 可跳过 HTML。

## 严谨性红线

- **估计 ≠ 保证**：平方根冲击是启发式模型，不是券商实测 implementation shortfall
- **披露丢弃**：缺失 ADV 笔数必须写入报告
- **aggressive 警示**：参与率 > 20% 时明确提示冲击更不确定
- **拒绝空样本**：无可用成交则 ValueError，不出假报告
- **不输出指令**：只给容量与成本估计，不给买卖建议

## 自检

```bash
python scripts/validate.py   # 9 项：√冲击标度、参与率恒等式、容量单调、盈亏平衡、空样本拒绝、缺失 ADV 计数、HTML 自包含、JSON 键、side 解析
```

## 边界与来源

数据与输出边界见 `references/source_boundary.md`；冲击模型说明见 `references/impact-models.md`。本 skill 为 QuantSkills 社区原创，方法为量化通用启发式，不替代合规 TCA 系统。
