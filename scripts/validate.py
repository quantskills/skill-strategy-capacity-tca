"""容量 / TCA 自检：用合成数据验证参与率、平方根冲击标度、容量曲线与护栏。
全部通过退出码 0，否则 1。
"""

from __future__ import annotations

import math
import os
import sys
import tempfile
import traceback

import numpy as np
import pandas as pd

from capacity_tca import (
    build_report,
    capacity_curve,
    impact_cost,
    load_trades,
    participation,
    render_html,
    render_json,
    _html_escape,
    _parse_side,
)


def _make_enriched(n=40, part=0.05, nav=1e8, seed=42):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n)
    symbols = [f"S{i%8:02d}" for i in range(n)]
    adv = rng.uniform(5e5, 2e6, size=n)
    shares = adv * part
    px = rng.uniform(10, 50, size=n)
    return pd.DataFrame({
        "date": dates,
        "symbol": symbols,
        "shares": shares,
        "notional": shares * px,
        "side_sign": 1.0,
        "adv_shares": adv,
        "participation": part * np.ones(n),
        "participation_gt_1": False,
        "aggressive": part > 0.2,
    }), nav


def test_sqrt_impact_scales_with_sqrt2():
    """成交规模翻倍 → 参与率翻倍 → 平方根冲击 ≈ ×√2。"""
    p = 0.04
    c1 = impact_cost(p, sigma_daily=0.02, coef=0.5, model="sqrt")
    c2 = impact_cost(2 * p, sigma_daily=0.02, coef=0.5, model="sqrt")
    ratio = c2 / c1
    assert abs(ratio - math.sqrt(2)) < 1e-9, f"期望 √2，得到 {ratio}"


def test_participation_identity():
    """参与率恒等式 |shares|/adv。"""
    trades = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "symbol": ["AAA", "BBB"],
        "shares": [1000.0, 2500.0],
        "notional": [10000.0, 50000.0],
        "side_sign": [1.0, -1.0],
    })
    adv = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "symbol": ["AAA", "BBB"],
        "adv_shares": [20000.0, 50000.0],
    })
    en, n_drop = participation(trades, adv)
    assert n_drop == 0
    assert abs(en.iloc[0]["participation"] - 1000 / 20000) < 1e-12
    assert abs(en.iloc[1]["participation"] - 2500 / 50000) < 1e-12


def test_capacity_curve_monotonic():
    """更高 AUM 倍数 → 更高年化成本（平方根）。"""
    en, nav = _make_enriched(part=0.03)
    curve = capacity_curve(en, nav, sigma_daily=0.02, coef=0.5, alpha=0.05)
    costs = [p["ann_cost_sqrt"] for p in curve["points"]]
    for a, b in zip(costs, costs[1:]):
        assert b > a, f"容量曲线非单调：{costs}"


def test_breakeven_exists_with_alpha():
    """给定 alpha 时，成本穿过 alpha 应能插值出盈亏平衡点。"""
    en, nav = _make_enriched(part=0.08, n=60)
    # 选一个落在曲线中间的 alpha
    curve0 = capacity_curve(en, nav, 0.02, 0.5, alpha=None)
    ys = [p["ann_cost_sqrt"] for p in curve0["points"]]
    alpha = (ys[1] + ys[-2]) / 2
    curve = capacity_curve(en, nav, 0.02, 0.5, alpha=alpha)
    be = curve["breakeven"]
    assert be is not None and be.get("aum") is not None, f"未找到盈亏平衡: {be}"
    assert curve0["points"][0]["aum"] <= be["aum"] <= curve0["points"][-1]["aum"]


def test_empty_trades_raises():
    """空成交 → ValueError。"""
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "t.csv")
        pd.DataFrame({
            "date": [], "symbol": [], "shares": [], "side": []
        }).to_csv(p, index=False)
        try:
            load_trades(p)
        except ValueError:
            return
    raise AssertionError("空成交未被拒绝")


def test_missing_adv_counted():
    """缺失 ADV 行计入丢弃数。"""
    trades = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
        "symbol": ["A", "B", "C"],
        "shares": [100.0, 200.0, 300.0],
        "notional": [1000.0, 2000.0, 3000.0],
        "side_sign": [1.0, 1.0, 1.0],
    })
    adv = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-04"]),
        "symbol": ["A", "C"],
        "adv_shares": [10000.0, 15000.0],
    })
    en, n_drop = participation(trades, adv)
    assert n_drop == 1, f"期望丢弃 1，得到 {n_drop}"
    assert len(en) == 2


def test_html_selfcontained():
    """HTML 自包含：无外部网络依赖，含 SVG 与免责说明。"""
    en, nav = _make_enriched()
    rep = build_report(en, 0, nav, 0.02, 0.5, alpha=0.05)
    html = render_html(rep)
    assert "<svg" in html
    assert "参与率" in html or "participation" in html.lower() or "Capacity" in html
    for bad in (
        'src="http', 'href="http', "<link", "<script src",
        "cdn.", "googleapis", "@import", "url(http",
    ):
        assert bad not in html, f"HTML 引用了外部资源: {bad}"
    for c in rep["caveats"]:
        assert _html_escape(c) in html, "免责说明在 HTML 中丢失"


def test_json_keys():
    """JSON 报告含关键键。"""
    en, nav = _make_enriched()
    rep = build_report(en, 0, nav, 0.02, 0.5, alpha=0.04)
    raw = render_json(rep)
    data = __import__("json").loads(raw)
    for key in (
        "participation", "turnover", "cost_at_base", "capacity_curve",
        "concentration", "caveats", "n_trades", "nav",
    ):
        assert key in data, f"缺 JSON 键: {key}"
    assert "points" in data["capacity_curve"]
    assert "ann_cost_sqrt" in data["cost_at_base"]
    assert "histogram" in data["participation"]


def test_side_parsing_buy_sell_and_signed():
    """side 同时支持 buy/sell 字符串与 ±1。"""
    assert _parse_side("buy") == 1.0
    assert _parse_side("SELL") == -1.0
    assert _parse_side(1) == 1.0
    assert _parse_side(-1) == -1.0
    assert _parse_side("+1") == 1.0
    assert _parse_side("-1") == -1.0
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "t.csv")
        pd.DataFrame({
            "date": ["2024-01-02", "2024-01-03", "2024-01-04"],
            "symbol": ["X", "Y", "Z"],
            "side": ["buy", "sell", 1],
            "shares": [100, 200, 150],
            "notional": [1000, 2000, 1500],
        }).to_csv(p, index=False)
        df = load_trades(p)
        assert list(df["side_sign"]) == [1.0, -1.0, 1.0]


TESTS = [
    test_sqrt_impact_scales_with_sqrt2,
    test_participation_identity,
    test_capacity_curve_monotonic,
    test_breakeven_exists_with_alpha,
    test_empty_trades_raises,
    test_missing_adv_counted,
    test_html_selfcontained,
    test_json_keys,
    test_side_parsing_buy_sell_and_signed,
]


def main():
    passed = 0
    for fn in TESTS:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
            passed += 1
        except Exception:
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(TESTS)} 通过")
    return 0 if passed == len(TESTS) else 1


if __name__ == "__main__":
    sys.exit(main())
