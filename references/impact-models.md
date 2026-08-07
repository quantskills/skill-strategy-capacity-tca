# Impact Models（冲击成本模型）

本 skill 使用的冲击估计是**启发式代理**，用于容量敏感性分析，不是券商 TCA 实测，也不是对未来成交成本的保证。

## 参与率

\[
\text{participation} = \frac{|\text{trade shares}|}{\text{ADV shares}}
\]

- 参与率 > 20%：标为 **aggressive**（冲击估计更不确定）
- 参与率 > 100%：数据/口径可疑，报告警示

## 平方根模型（主模型）

常见简化形式（Almgren–Chriss / Barra 风格的参与率项）：

\[
\text{cost\_bps} \approx \sigma_{\text{daily}} \cdot \sqrt{\text{participation}} \cdot c \cdot 10^{4}
\]

- \(\sigma_{\text{daily}}\)：日波动率代理（默认 `0.02`，可用 `--sigma-daily` 覆盖）
- \(c\)：冲击系数（默认 `0.5`，`--impact-coef`）
- 规模翻倍时，平方根冲击约变为 \(\sqrt{2}\) 倍——`validate.py` 校验此标度

**年化成本拖累**：将每笔 `notional × cost_bps / 1e4` 加总，再除以 NAV 与样本年数。

## 线性模型（对照）

\[
\text{cost\_bps} \approx \sigma_{\text{daily}} \cdot \text{participation} \cdot c \cdot 10^{4}
\]

用于对照：低参与率时线性与平方根接近；高参与率时线性更悲观。报告同时给出两者，避免单一模型幻觉。

## 容量曲线

在固定换手假设下，将成交与参与率按 AUM 倍数 \(m \in \{0.25,0.5,1,2,4,8\}\) 同比放大：

- AUM\(_m\) = \(m \times\) NAV
- participation\(_m\) = \(m \times\) participation\(_1\)

得到年化成本 vs AUM。若提供毛 alpha，在相邻点间线性插值寻找 **成本 ≈ alpha** 的盈亏平衡 AUM。

**关键假设（必须披露）**：
1. 换手随 AUM 同比放大（策略不主动降换手）
2. ADV 不随自身交易规模变化
3. \(\sigma\) 与 \(c\) 在考察区间内不变

现实中执行算法、暗池、分批、ADV 内生变化都会打破上述假设——曲线是**情景图**，不是承诺。

## Amihud 代理（可选）

若提供 `--returns`：

\[
\text{Amihud} = \frac{|r_t|}{\text{dollar volume}_t}
\]

本 skill 用策略成交名义作成交额代理（非全日市场成交额），因此只作流动性相对比较，不作跨市场绝对标定。

## 与 implementation shortfall 的关系

经典 IS = 决策中价 − 实际成交均价（再分解成延迟、冲击、时机等）。本 skill **不**接入 tick/成交回报流，因此输出的是 **IS 风格的事前冲击估计**，不是事后 IS 分解。需要实测 IS 时，应使用券商/内部 TCA 系统，本 skill 只回答容量敏感性问题。
