# Source Boundary

本 skill 只对用户提供的成交与 ADV（及可选价格/收益）做容量与冲击估计，不预测、不喊单。

Allowed sources（允许）:

- 用户自备的回测/实盘成交导出（csv：date, symbol, side/shares 或 notional）
- 公开或用户有权使用的 ADV / 日成交量数据
- 可选：用户自备的收盘价、日收益序列（用于股数↔名义转换与 Amihud 代理）
- 经数据类 skill（如 `skill-pandadata-api`）合规获取的行情/成交量，由用户整理后传入

Not allowed unless the user has rights and explicitly provides them:

- 付费墙内 / 会员专享 Level-2、券商专有 TCA 报表（除非用户明确提供）
- 非公开的订单簿、暗池成交、内部成交均价库

## 输出边界

- 只输出：参与率、换手、冲击成本**估计**、容量曲线、集中度、可选盈亏平衡 AUM
- 不输出：买卖指令、目标仓位、保证可成交规模、点位预测
- 每个关键结论须区分：**数据事实**（参与率、换手、丢弃笔数）vs **模型估计**（冲击 bps、盈亏平衡）
- 必须声明：冲击模型是启发式估计，不是券商实测 implementation shortfall；容量曲线假设 ADV 与换手结构不变
- 与组合归因、策略诊断互补，不替代它们
