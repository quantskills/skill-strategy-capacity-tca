"""策略容量与交易成本分析（TCA）核心计算。

框架中立：输入成交明细 + ADV，估计参与率、平方根冲击成本、容量曲线与集中度。
冲击模型是估计而非保证——不输出买卖指令、不构成投资建议。

口径：
- 参与率 participation = |trade_shares| / adv_shares
- 平方根冲击 cost_bps ≈ σ_daily * sqrt(participation) * impact_coef * 1e4
- 线性冲击（对照）cost_bps ≈ σ_daily * participation * impact_coef * 1e4
- 容量曲线：按 AUM 倍数 [0.25, 0.5, 1, 2, 4, 8] 同比放大成交，估计年化成本拖累
- 若提供 --alpha，在容量曲线上插值寻找成本 ≈ alpha 的盈亏平衡容量

用法：
    python capacity_tca.py --trades t.csv --adv a.csv [--alpha 0.05] [--nav 1e8]
                           [--impact-coef 0.5] [--out report/] [--no-html]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Any, Optional

import numpy as np
import pandas as pd

DEFAULT_SIGMA_DAILY = 0.02
CAPACITY_MULTS = [0.25, 0.5, 1.0, 2.0, 4.0, 8.0]
AGGRESSIVE_PARTICIPATION = 0.20
TRADING_DAYS_PER_YEAR = 252


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _parse_side(val) -> float:
    """buy/sell 或 +1/-1 → 符号 (+1 buy, -1 sell)。无法解析返回 NaN。"""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return float("nan")
    if isinstance(val, (int, float, np.integer, np.floating)):
        v = float(val)
        if v > 0:
            return 1.0
        if v < 0:
            return -1.0
        return float("nan")
    s = str(val).strip().lower()
    if s in ("buy", "b", "long", "+", "+1", "1"):
        return 1.0
    if s in ("sell", "s", "short", "-", "-1"):
        return -1.0
    try:
        return _parse_side(float(s))
    except ValueError:
        return float("nan")


def load_trades(path: str, price: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """加载成交：需要 shares 或 notional；side 可选。"""
    df = pd.read_csv(path)
    cols = {c.lower().strip(): c for c in df.columns}
    rename = {}
    for want in ("date", "symbol", "side", "shares", "notional"):
        if want in cols:
            rename[cols[want]] = want
    df = df.rename(columns=rename)
    if "date" not in df.columns or "symbol" not in df.columns:
        raise ValueError("trades CSV 须含 date, symbol 列")
    df["date"] = pd.to_datetime(df["date"])
    df["symbol"] = df["symbol"].astype(str)

    if "side" in df.columns:
        df["side_sign"] = df["side"].map(_parse_side)
    else:
        df["side_sign"] = 1.0

    has_shares = "shares" in df.columns
    has_notional = "notional" in df.columns
    if not has_shares and not has_notional:
        raise ValueError("trades CSV 须含 shares 或 notional 列")

    if has_shares:
        df["shares"] = pd.to_numeric(df["shares"], errors="coerce")
        # 若 shares 已带符号且无有效 side，用符号当方向
        signed = df["shares"].abs() != df["shares"]
        if signed.any() and df["side_sign"].isna().all():
            df.loc[signed, "side_sign"] = np.sign(df.loc[signed, "shares"])
        df["shares"] = df["shares"].abs()
    else:
        df["shares"] = np.nan

    if has_notional:
        df["notional"] = pd.to_numeric(df["notional"], errors="coerce").abs()
    else:
        df["notional"] = np.nan

    if price is not None:
        px = price[["date", "symbol", "close"]].copy()
        df = df.merge(px, on=["date", "symbol"], how="left")
        need_sh = df["shares"].isna() & df["notional"].notna() & df["close"].notna()
        df.loc[need_sh, "shares"] = df.loc[need_sh, "notional"] / df.loc[need_sh, "close"]
        need_nt = df["notional"].isna() & df["shares"].notna() & df["close"].notna()
        df.loc[need_nt, "notional"] = df.loc[need_nt, "shares"] * df.loc[need_nt, "close"]
        df = df.drop(columns=["close"], errors="ignore")

    # 仍缺 notional 时用 shares 作代理（单位一致假设）
    if df["notional"].isna().all() and df["shares"].notna().any():
        df["notional"] = df["shares"]
    if df["shares"].isna().all() and df["notional"].notna().any():
        df["shares"] = df["notional"]

    df = df.dropna(subset=["shares"]).copy()
    if df.empty:
        raise ValueError("无可用成交（shares/notional 全空）")
    df["side_sign"] = df["side_sign"].fillna(1.0)
    return df.reset_index(drop=True)


def load_adv(path: str, price: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """加载 ADV：adv_shares 或 adv_notional。"""
    df = pd.read_csv(path)
    cols = {c.lower().strip(): c for c in df.columns}
    rename = {}
    for want in ("date", "symbol", "adv_shares", "adv_notional"):
        if want in cols:
            rename[cols[want]] = want
    df = df.rename(columns=rename)
    if "date" not in df.columns or "symbol" not in df.columns:
        raise ValueError("adv CSV 须含 date, symbol 列")
    df["date"] = pd.to_datetime(df["date"])
    df["symbol"] = df["symbol"].astype(str)

    has_sh = "adv_shares" in df.columns
    has_nt = "adv_notional" in df.columns
    if not has_sh and not has_nt:
        raise ValueError("adv CSV 须含 adv_shares 或 adv_notional")

    if has_sh:
        df["adv_shares"] = pd.to_numeric(df["adv_shares"], errors="coerce")
    else:
        df["adv_shares"] = np.nan
    if has_nt:
        df["adv_notional"] = pd.to_numeric(df["adv_notional"], errors="coerce")
    else:
        df["adv_notional"] = np.nan

    if price is not None:
        px = price[["date", "symbol", "close"]].copy()
        df = df.merge(px, on=["date", "symbol"], how="left")
        need = df["adv_shares"].isna() & df["adv_notional"].notna() & df["close"].notna()
        df.loc[need, "adv_shares"] = df.loc[need, "adv_notional"] / df.loc[need, "close"]
        need2 = df["adv_notional"].isna() & df["adv_shares"].notna() & df["close"].notna()
        df.loc[need2, "adv_notional"] = df.loc[need2, "adv_shares"] * df.loc[need2, "close"]
        df = df.drop(columns=["close"], errors="ignore")

    if df["adv_shares"].isna().all() and df["adv_notional"].notna().any():
        df["adv_shares"] = df["adv_notional"]
    return df


def load_price(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    cols = {c.lower().strip(): c for c in df.columns}
    rename = {}
    for want in ("date", "symbol", "close"):
        if want in cols:
            rename[cols[want]] = want
    df = df.rename(columns=rename)
    if not {"date", "symbol", "close"}.issubset(df.columns):
        raise ValueError("price CSV 须含 date, symbol, close")
    df["date"] = pd.to_datetime(df["date"])
    df["symbol"] = df["symbol"].astype(str)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df.dropna(subset=["close"])


def load_returns(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    cols = {c.lower().strip(): c for c in df.columns}
    rename = {}
    for want in ("date", "symbol", "ret", "return", "returns"):
        if want in cols:
            key = "ret" if want in ("ret", "return", "returns") else want
            rename[cols[want]] = key
    df = df.rename(columns=rename)
    if "ret" not in df.columns and "return" in df.columns:
        df = df.rename(columns={"return": "ret"})
    if not {"date", "symbol", "ret"}.issubset(df.columns):
        raise ValueError("returns CSV 须含 date, symbol, ret")
    df["date"] = pd.to_datetime(df["date"])
    df["symbol"] = df["symbol"].astype(str)
    df["ret"] = pd.to_numeric(df["ret"], errors="coerce")
    return df.dropna(subset=["ret"])


# ---------------------------------------------------------------------------
# Core metrics
# ---------------------------------------------------------------------------

def participation(trades: pd.DataFrame, adv: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """合并 ADV，计算参与率；返回 (enriched_df, n_dropped_missing_adv)。"""
    merged = trades.merge(
        adv[["date", "symbol", "adv_shares"]].drop_duplicates(["date", "symbol"]),
        on=["date", "symbol"],
        how="left",
    )
    missing = merged["adv_shares"].isna() | (merged["adv_shares"] <= 0)
    n_drop = int(missing.sum())
    out = merged.loc[~missing].copy()
    if out.empty:
        raise ValueError(
            f"无可用成交：全部 {len(merged)} 笔因缺失/无效 ADV 被丢弃"
        )
    out["participation"] = out["shares"].abs() / out["adv_shares"]
    out["participation_gt_1"] = out["participation"] > 1.0
    out["aggressive"] = out["participation"] > AGGRESSIVE_PARTICIPATION
    return out, n_drop


def impact_cost(
    part: float | np.ndarray | pd.Series,
    sigma_daily: float = DEFAULT_SIGMA_DAILY,
    coef: float = 0.5,
    model: str = "sqrt",
) -> float | np.ndarray | pd.Series:
    """冲击成本（bps）。model: 'sqrt' | 'linear'。"""
    p = np.asarray(part, dtype=float)
    p = np.clip(p, 0.0, None)
    if model == "sqrt":
        raw = sigma_daily * np.sqrt(p) * coef
    elif model == "linear":
        raw = sigma_daily * p * coef
    else:
        raise ValueError(f"未知 impact model: {model}")
    bps = raw * 1e4
    if np.isscalar(part) or (isinstance(part, (float, int)) and not isinstance(part, bool)):
        return float(bps) if np.ndim(bps) == 0 else float(np.asarray(bps).ravel()[0])
    if isinstance(part, pd.Series):
        return pd.Series(bps, index=part.index)
    return bps


def _span_years(dates: pd.Series) -> float:
    dmin, dmax = dates.min(), dates.max()
    days = max((dmax - dmin).days, 1)
    return max(days / 365.25, 1.0 / TRADING_DAYS_PER_YEAR)


def turnover_stats(trades: pd.DataFrame, nav: float) -> dict[str, Any]:
    """单向 / 双向换手（样本期合计与年化）。"""
    years = _span_years(trades["date"])
    total_notional = float(trades["notional"].abs().sum())
    # 双向：买卖合计；单向：合计的一半近似
    two_way = total_notional / nav if nav > 0 else float("nan")
    one_way = two_way / 2.0
    return {
        "span_years": years,
        "total_traded_notional": total_notional,
        "one_way_turnover": one_way,
        "two_way_turnover": two_way,
        "one_way_turnover_ann": one_way / years,
        "two_way_turnover_ann": two_way / years,
    }


def amihud_illiquidity(
    returns: Optional[pd.DataFrame],
    trades: pd.DataFrame,
) -> dict[str, Any]:
    """Amihud 风格 |ret|/dollar_volume；无 returns 则跳过。"""
    if returns is None:
        return {
            "available": False,
            "note": "未提供 --returns，跳过 Amihud 非流动性代理",
        }
    # dollar volume 用成交 notional 作当日代理；更理想是全日成交额
    dv = trades.groupby(["date", "symbol"], as_index=False)["notional"].sum()
    dv = dv.rename(columns={"notional": "dollar_volume"})
    m = returns.merge(dv, on=["date", "symbol"], how="inner")
    m = m[m["dollar_volume"] > 0].copy()
    if m.empty:
        return {"available": False, "note": "returns 与 trades 无交集，跳过 Amihud"}
    m["amihud"] = m["ret"].abs() / m["dollar_volume"]
    return {
        "available": True,
        "mean": float(m["amihud"].mean()),
        "median": float(m["amihud"].median()),
        "n_obs": int(len(m)),
        "note": "Amihud 代理 = |ret| / traded_notional（用策略成交额作成交额代理）",
    }


def annualized_cost_drag(
    part_series: pd.Series,
    notional: pd.Series,
    nav: float,
    years: float,
    sigma_daily: float,
    coef: float,
    model: str = "sqrt",
) -> float:
    """将每笔冲击成本加总为年化成本拖累（占 NAV 比例）。"""
    bps = impact_cost(part_series, sigma_daily=sigma_daily, coef=coef, model=model)
    cost_money = notional.abs() * (np.asarray(bps, dtype=float) / 1e4)
    total = float(np.sum(cost_money))
    if nav <= 0 or years <= 0:
        return float("nan")
    return (total / nav) / years


def capacity_curve(
    enriched: pd.DataFrame,
    nav: float,
    sigma_daily: float,
    coef: float,
    alpha: Optional[float] = None,
    mults: list[float] | None = None,
) -> dict[str, Any]:
    """按 AUM 倍数放大成交，估计年化平方根/线性成本；可选盈亏平衡点。"""
    mults = list(mults or CAPACITY_MULTS)
    years = _span_years(enriched["date"])
    base_part = enriched["participation"].astype(float)
    base_notional = enriched["notional"].astype(float)
    points = []
    for m in mults:
        # 同换手下 AUM 放大 m 倍 → 成交与参与率同步放大 m 倍
        part_m = base_part * m
        notional_m = base_notional * m
        nav_m = nav * m
        cost_sqrt = annualized_cost_drag(
            part_m, notional_m, nav_m, years, sigma_daily, coef, "sqrt"
        )
        cost_lin = annualized_cost_drag(
            part_m, notional_m, nav_m, years, sigma_daily, coef, "linear"
        )
        mean_part = float(part_m.mean())
        points.append({
            "mult": m,
            "aum": nav_m,
            "mean_participation": mean_part,
            "ann_cost_sqrt": cost_sqrt,
            "ann_cost_linear": cost_lin,
            "net_alpha_sqrt": (None if alpha is None else alpha - cost_sqrt),
        })

    breakeven = None
    if alpha is not None:
        # 在相邻点间线性插值找 cost == alpha
        xs = [p["aum"] for p in points]
        ys = [p["ann_cost_sqrt"] for p in points]
        for i in range(len(points) - 1):
            y0, y1 = ys[i], ys[i + 1]
            x0, x1 = xs[i], xs[i + 1]
            if (y0 - alpha) * (y1 - alpha) <= 0 and y1 != y0:
                t = (alpha - y0) / (y1 - y0)
                aum_be = x0 + t * (x1 - x0)
                breakeven = {
                    "aum": float(aum_be),
                    "mult": float(aum_be / nav) if nav > 0 else None,
                    "alpha": alpha,
                    "model": "sqrt",
                }
                break
        if breakeven is None:
            # 全程低于或高于 alpha
            if ys[-1] < alpha:
                breakeven = {
                    "aum": None,
                    "mult": None,
                    "alpha": alpha,
                    "note": "在考察倍数范围内成本始终低于 alpha，盈亏平衡点在更大 AUM",
                }
            else:
                breakeven = {
                    "aum": None,
                    "mult": None,
                    "alpha": alpha,
                    "note": "在考察倍数范围内成本始终不低于 alpha（含最小倍数）",
                }

    return {"points": points, "breakeven": breakeven, "span_years": years}


def concentration(trades: pd.DataFrame, top_n: int = 10) -> dict[str, Any]:
    by = trades.groupby("symbol")["notional"].apply(lambda s: float(s.abs().sum()))
    by = by.sort_values(ascending=False)
    total = float(by.sum()) or 1.0
    top = by.head(top_n)
    return {
        "top_n": top_n,
        "top_share": float(top.sum() / total),
        "names": [
            {"symbol": str(sym), "notional": float(v), "share": float(v / total)}
            for sym, v in top.items()
        ],
        "n_symbols": int(by.shape[0]),
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def build_report(
    enriched: pd.DataFrame,
    n_dropped: int,
    nav: float,
    sigma_daily: float,
    coef: float,
    alpha: Optional[float] = None,
    amihud: Optional[dict] = None,
) -> dict[str, Any]:
    years = _span_years(enriched["date"])
    tov = turnover_stats(enriched, nav)
    curve = capacity_curve(enriched, nav, sigma_daily, coef, alpha=alpha)
    conc = concentration(enriched)

    mean_part = float(enriched["participation"].mean())
    med_part = float(enriched["participation"].median())
    p95_part = float(enriched["participation"].quantile(0.95))
    n_agg = int(enriched["aggressive"].sum())
    n_gt1 = int(enriched["participation_gt_1"].sum())

    base_cost_sqrt = annualized_cost_drag(
        enriched["participation"], enriched["notional"], nav, years, sigma_daily, coef, "sqrt"
    )
    base_cost_lin = annualized_cost_drag(
        enriched["participation"], enriched["notional"], nav, years, sigma_daily, coef, "linear"
    )

    warnings = []
    if n_dropped:
        warnings.append(f"因缺失/无效 ADV 丢弃 {n_dropped} 笔成交")
    if n_agg:
        warnings.append(
            f"{n_agg} 笔参与率 > {AGGRESSIVE_PARTICIPATION:.0%}（aggressive），冲击估计更不确定"
        )
    if n_gt1:
        warnings.append(f"{n_gt1} 笔参与率 > 100%（超过当日 ADV），请核查数据")

    caveats = [
        "冲击成本模型（平方根/线性）是启发式估计，不是成交保证，也不是券商 TCA 实测。",
        f"默认日波动 σ={sigma_daily:.4f}、系数={coef}；更换品种/市场须重标定。",
        "容量曲线假设换手率随 AUM 同比放大且 ADV 不变——真实市场 ADV 会随规模变化。",
        "本结果不构成投资建议或买卖指令，仅供研究与风控参考。",
    ]
    if alpha is not None:
        caveats.append(
            f"盈亏平衡相对用户给定毛 alpha={alpha:.2%}；净 alpha 尚须扣佣金、滑点、税费等。"
        )

    hist_bins = [0, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, float("inf")]
    hist_labels = ["0–1%", "1–2%", "2–5%", "5–10%", "10–20%", "20–50%", "50–100%", ">100%"]
    cats = pd.cut(enriched["participation"], bins=hist_bins, labels=hist_labels, right=True)
    hist = [{"bin": str(lab), "count": int((cats == lab).sum())} for lab in hist_labels]

    return {
        "title": "策略容量与交易成本分析（TCA）",
        "n_trades": int(len(enriched)),
        "n_dropped_missing_adv": n_dropped,
        "date_start": str(enriched["date"].min().date()),
        "date_end": str(enriched["date"].max().date()),
        "span_years": years,
        "nav": nav,
        "alpha": alpha,
        "sigma_daily": sigma_daily,
        "impact_coef": coef,
        "participation": {
            "mean": mean_part,
            "median": med_part,
            "p95": p95_part,
            "n_aggressive": n_agg,
            "n_gt_1": n_gt1,
            "histogram": hist,
            "values": [float(x) for x in enriched["participation"].tolist()],
        },
        "turnover": tov,
        "cost_at_base": {
            "ann_cost_sqrt": base_cost_sqrt,
            "ann_cost_linear": base_cost_lin,
            "net_alpha_sqrt": None if alpha is None else alpha - base_cost_sqrt,
        },
        "capacity_curve": curve,
        "concentration": conc,
        "amihud": amihud or {"available": False, "note": "未计算"},
        "warnings": warnings,
        "caveats": caveats,
    }


def render_text(rep: dict[str, Any]) -> str:
    L = []
    L.append(rep["title"])
    L.append("=" * 56)
    L.append(
        f"样本期：{rep['date_start']} → {rep['date_end']} "
        f"（≈{rep['span_years']:.2f} 年）  可用成交 {rep['n_trades']} 笔"
    )
    L.append(f"AUM 代理（NAV）：{rep['nav']:,.0f}")
    if rep.get("alpha") is not None:
        L.append(f"给定毛 alpha：{rep['alpha']:.2%}")
    L.append(
        f"冲击参数：σ_daily={rep['sigma_daily']:.4f}  coef={rep['impact_coef']}"
    )
    if rep["warnings"]:
        L.append("")
        L.append("警告：")
        for w in rep["warnings"]:
            L.append(f"  [!] {w}")

    p = rep["participation"]
    L.append("")
    L.append("参与率（|shares|/ADV）：")
    L.append(
        f"  均值 {p['mean']:.2%}  中位 {p['median']:.2%}  P95 {p['p95']:.2%}  "
        f"aggressive>{AGGRESSIVE_PARTICIPATION:.0%}：{p['n_aggressive']} 笔"
    )

    t = rep["turnover"]
    L.append("")
    L.append("换手：")
    L.append(
        f"  单向 {t['one_way_turnover']:.2%}（年化 {t['one_way_turnover_ann']:.2%}）  "
        f"双向 {t['two_way_turnover']:.2%}（年化 {t['two_way_turnover_ann']:.2%}）"
    )

    c = rep["cost_at_base"]
    L.append("")
    L.append("基准规模年化成本拖累：")
    L.append(
        f"  平方根模型 {c['ann_cost_sqrt']:.2%}  |  线性对照 {c['ann_cost_linear']:.2%}"
    )
    if c.get("net_alpha_sqrt") is not None:
        L.append(f"  净 alpha（毛−平方根成本）≈ {c['net_alpha_sqrt']:.2%}")

    am = rep.get("amihud") or {}
    L.append("")
    if am.get("available"):
        L.append(
            f"Amihud 代理：均值 {am['mean']:.3e}  中位 {am['median']:.3e}  "
            f"n={am['n_obs']}"
        )
    else:
        L.append(f"Amihud：{am.get('note', '跳过')}")

    conc = rep["concentration"]
    L.append("")
    L.append(
        f"集中度：Top-{conc['top_n']} 占成交名义 {conc['top_share']:.1%} "
        f"（共 {conc['n_symbols']} 只）"
    )
    for row in conc["names"][:5]:
        L.append(f"  {row['symbol']:>12}  {row['share']:>6.1%}  notional={row['notional']:,.0f}")

    L.append("")
    L.append("容量曲线（平方根冲击，年化成本 vs AUM）：")
    L.append(f"{'倍数':>6} {'AUM':>14} {'均参与率':>10} {'年化成本':>10} {'净α':>10}")
    for pt in rep["capacity_curve"]["points"]:
        na = pt.get("net_alpha_sqrt")
        na_s = f"{na:+.2%}" if na is not None else "—"
        L.append(
            f"{pt['mult']:>5.2f}x {pt['aum']:>14,.0f} "
            f"{pt['mean_participation']:>9.2%} {pt['ann_cost_sqrt']:>9.2%} {na_s:>10}"
        )
    be = rep["capacity_curve"].get("breakeven")
    if be:
        L.append("")
        if be.get("aum") is not None:
            L.append(
                f"盈亏平衡容量（成本≈alpha）：AUM≈{be['aum']:,.0f} "
                f"（≈{be['mult']:.2f}× 当前）"
            )
        else:
            L.append(f"盈亏平衡：{be.get('note', '未找到交点')}")

    L.append("")
    L.append("说明：")
    for cv in rep["caveats"]:
        L.append(f"  - {cv}")
    return "\n".join(L)


def _html_escape(s: Any) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root {
    --ground:#f6f3ec; --surface:#fffdf8; --surface-2:#f0ebe0;
    --ink:#23201a; --ink-2:#6b655a; --ink-3:#9a9284;
    --hair:rgba(35,32,26,.12); --hair-strong:rgba(35,32,26,.26);
    --up:#c0392b; --down:#147d6f; --accent:#a9791f;
    --accent-soft:rgba(169,121,31,.12); --faded:0.30;
    --shadow:0 1px 2px rgba(35,32,26,.06),0 6px 20px rgba(35,32,26,.05);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --ground:#17150f; --surface:#201d16; --surface-2:#2a261d;
      --ink:#ece7db; --ink-2:#a9a293; --ink-3:#746d5e;
      --hair:rgba(236,231,219,.12); --hair-strong:rgba(236,231,219,.24);
      --up:#e15b4c; --down:#2aa697; --accent:#d6a94a;
      --accent-soft:rgba(214,169,74,.14);
      --shadow:0 1px 2px rgba(0,0,0,.3),0 6px 22px rgba(0,0,0,.35);
    }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--ground); color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",
      "Segoe UI","Noto Sans CJK SC",system-ui,sans-serif;
    line-height:1.6; font-variant-numeric:tabular-nums; -webkit-font-smoothing:antialiased; }
  .wrap { max-width:920px; margin:0 auto; padding:40px 24px 64px; }
  .eyebrow { font-size:12px; letter-spacing:.18em; text-transform:uppercase;
    color:var(--accent); font-weight:600; margin:0 0 8px; }
  h1 { font-size:clamp(24px,4vw,32px); font-weight:700; margin:0 0 6px;
    letter-spacing:-.01em; text-wrap:balance; }
  .meta { color:var(--ink-2); font-size:14px; margin:0; }
  .meta b { color:var(--ink); font-weight:600; }
  .warn { margin:16px 0 0; padding:10px 14px; border-radius:10px; font-size:13px;
    background:rgba(192,57,43,.10); border:1px solid rgba(192,57,43,.30); color:var(--up); }
  .callout { margin:24px 0 32px; padding:18px 20px; border-radius:12px;
    background:var(--accent-soft); border:1px solid var(--hair);
    display:flex; flex-wrap:wrap; gap:6px 28px; align-items:baseline; }
  .callout .lead { font-weight:600; font-size:14px; color:var(--accent);
    letter-spacing:.02em; width:100%; margin-bottom:2px; }
  .callout .stat { font-size:14px; color:var(--ink-2); }
  .callout .stat b { color:var(--ink); font-weight:700; font-size:16px; }
  section { margin-top:40px; }
  .sec-head { display:flex; align-items:baseline; justify-content:space-between;
    gap:12px; margin-bottom:6px; flex-wrap:wrap; }
  h2 { font-size:17px; font-weight:700; margin:0; letter-spacing:-.005em; }
  .sec-note { font-size:13px; color:var(--ink-3); margin:0; }
  .card { background:var(--surface); border:1px solid var(--hair); border-radius:14px;
    box-shadow:var(--shadow); padding:20px 20px 12px; margin-top:14px; }
  .chart-scroll { overflow-x:auto; }
  svg { display:block; width:100%; min-width:480px; height:auto; }
  .grid2 { display:grid; grid-template-columns:1fr; gap:16px; }
  @media (min-width:720px) { .grid2 { grid-template-columns:1fr 1fr; } }
  table { width:100%; border-collapse:collapse; font-size:13.5px; margin-top:8px; }
  th, td { text-align:right; padding:8px 10px; border-bottom:1px solid var(--hair); }
  th:first-child, td:first-child { text-align:left; }
  th { color:var(--ink-2); font-weight:600; font-size:12px; }
  .caveats { margin:12px 0 0; padding-left:18px; color:var(--ink-2); font-size:13px; }
  footer { margin-top:48px; padding-top:16px; border-top:1px solid var(--hair);
    font-size:12px; color:var(--ink-3); }
</style>
</head>
<body>
<div class="wrap">
  <p class="eyebrow">QuantSkills · Capacity &amp; TCA</p>
  <h1>__TITLE__</h1>
  <p class="meta">__META__</p>
  __WARN__
  __CALLOUT__

  <section>
    <div class="sec-head">
      <h2>参与率分布</h2>
      <p class="sec-note">|成交股数| / ADV</p>
    </div>
    <div class="card chart-scroll">
      __SVG_HIST__
    </div>
  </section>

  <section>
    <div class="sec-head">
      <h2>容量曲线</h2>
      <p class="sec-note">年化平方根冲击成本 vs AUM（点线为 alpha）</p>
    </div>
    <div class="card chart-scroll">
      __SVG_CURVE__
    </div>
  </section>

  <section>
    <div class="sec-head"><h2>容量表</h2></div>
    <div class="card">
      __TABLE__
    </div>
  </section>

  <section>
    <div class="sec-head"><h2>说明与边界</h2></div>
    <ul class="caveats">__CAVEATS__</ul>
  </section>

  <footer>冲击模型为启发式估计 · 非投资建议 · 自包含离线报告</footer>
</div>
</body>
</html>
"""


def _svg_participation_hist(hist: list[dict]) -> str:
    w, h = 640, 220
    pad_l, pad_r, pad_t, pad_b = 48, 16, 16, 48
    counts = [b["count"] for b in hist]
    labels = [b["bin"] for b in hist]
    ymax = max(counts) if counts else 1
    ymax = max(ymax, 1)
    iw = w - pad_l - pad_r
    ih = h - pad_t - pad_b
    n = len(hist) or 1
    bw = iw / n * 0.72
    gap = iw / n
    bars = []
    for i, (lab, c) in enumerate(zip(labels, counts)):
        x = pad_l + i * gap + (gap - bw) / 2
        bh = (c / ymax) * ih
        y = pad_t + ih - bh
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" '
            f'rx="3" fill="var(--accent)" opacity="0.85"/>'
        )
        bars.append(
            f'<text x="{x + bw/2:.1f}" y="{h - 28}" text-anchor="middle" '
            f'font-size="10" fill="var(--ink-2)">{_html_escape(lab)}</text>'
        )
        if c:
            bars.append(
                f'<text x="{x + bw/2:.1f}" y="{y - 4:.1f}" text-anchor="middle" '
                f'font-size="11" fill="var(--ink)" font-weight="600">{c}</text>'
            )
    return (
        f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="participation histogram">'
        f'<line x1="{pad_l}" y1="{pad_t + ih}" x2="{w - pad_r}" y2="{pad_t + ih}" '
        f'stroke="var(--hair-strong)"/>'
        + "".join(bars)
        + "</svg>"
    )


def _svg_capacity_curve(points: list[dict], alpha: Optional[float]) -> str:
    w, h = 640, 260
    pad_l, pad_r, pad_t, pad_b = 56, 24, 20, 44
    xs = [p["aum"] for p in points]
    ys = [p["ann_cost_sqrt"] for p in points]
    if not xs:
        return "<svg></svg>"
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = 0.0, max(max(ys), (alpha or 0) * 1.2, 1e-6)
    iw = w - pad_l - pad_r
    ih = h - pad_t - pad_b

    def sx(x):
        return pad_l + (math.log(x) - math.log(xmin)) / (math.log(xmax) - math.log(xmin) + 1e-15) * iw

    def sy(y):
        return pad_t + ih - (y - ymin) / (ymax - ymin) * ih

    pts = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in zip(xs, ys))
    dots = "".join(
        f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="4.5" fill="var(--accent)"/>'
        for x, y in zip(xs, ys)
    )
    alpha_line = ""
    if alpha is not None and ymin <= alpha <= ymax:
        yy = sy(alpha)
        alpha_line = (
            f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{w - pad_r}" y2="{yy:.1f}" '
            f'stroke="var(--up)" stroke-dasharray="6 4" stroke-width="1.5"/>'
            f'<text x="{w - pad_r}" y="{yy - 6:.1f}" text-anchor="end" '
            f'font-size="11" fill="var(--up)">alpha {alpha:.1%}</text>'
        )
    xticks = "".join(
        f'<text x="{sx(x):.1f}" y="{h - 14}" text-anchor="middle" font-size="10" '
        f'fill="var(--ink-2)">{x/1e6:.0f}M</text>'
        for x in xs
    )
    return (
        f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="capacity curve">'
        f'<line x1="{pad_l}" y1="{pad_t + ih}" x2="{w - pad_r}" y2="{pad_t + ih}" '
        f'stroke="var(--hair-strong)"/>'
        f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t + ih}" '
        f'stroke="var(--hair-strong)"/>'
        f'<polyline fill="none" stroke="var(--accent)" stroke-width="2.2" points="{pts}"/>'
        + dots
        + alpha_line
        + xticks
        + f'<text x="12" y="{pad_t + 8}" font-size="10" fill="var(--ink-3)">cost</text>'
        + f'<text x="{(pad_l + w - pad_r)/2:.0f}" y="{h - 2}" text-anchor="middle" '
        f'font-size="10" fill="var(--ink-3)">AUM</text>'
        + "</svg>"
    )


def render_html(rep: dict[str, Any]) -> str:
    title = _html_escape(rep["title"])
    meta = (
        f"样本 <b>{rep['date_start']}</b> → <b>{rep['date_end']}</b> "
        f"（≈{rep['span_years']:.2f} 年） &nbsp;·&nbsp; "
        f"成交 <b>{rep['n_trades']}</b> &nbsp;·&nbsp; "
        f"NAV <b>{rep['nav']:,.0f}</b>"
    )
    warn = ""
    for w in rep.get("warnings") or []:
        warn += f'<div class="warn">[!] {_html_escape(w)}</div>'

    c = rep["cost_at_base"]
    p = rep["participation"]
    callout = (
        '<div class="callout">'
        '<span class="lead">基准规模 · 平方根冲击</span>'
        f'<span class="stat">均参与率 <b>{p["mean"]:.2%}</b></span>'
        f'<span class="stat">年化成本 <b>{c["ann_cost_sqrt"]:.2%}</b></span>'
        f'<span class="stat">线性对照 <b>{c["ann_cost_linear"]:.2%}</b></span>'
    )
    if rep.get("alpha") is not None:
        na = c.get("net_alpha_sqrt")
        callout += f'<span class="stat">毛 α <b>{rep["alpha"]:.2%}</b></span>'
        if na is not None:
            callout += f'<span class="stat">净 α <b>{na:+.2%}</b></span>'
    be = rep["capacity_curve"].get("breakeven") or {}
    if be.get("aum") is not None:
        callout += f'<span class="stat">盈亏平衡 AUM <b>{be["aum"]:,.0f}</b></span>'
    callout += "</div>"

    rows = []
    for pt in rep["capacity_curve"]["points"]:
        na = pt.get("net_alpha_sqrt")
        na_s = f"{na:+.2%}" if na is not None else "—"
        rows.append(
            "<tr>"
            f"<td>{pt['mult']:.2f}×</td>"
            f"<td>{pt['aum']:,.0f}</td>"
            f"<td>{pt['mean_participation']:.2%}</td>"
            f"<td>{pt['ann_cost_sqrt']:.2%}</td>"
            f"<td>{na_s}</td>"
            "</tr>"
        )
    table = (
        "<table><thead><tr>"
        "<th>倍数</th><th>AUM</th><th>均参与率</th><th>年化成本(√)</th><th>净α</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )
    caveats = "".join(f"<li>{_html_escape(c)}</li>" for c in rep["caveats"])
    svg_hist = _svg_participation_hist(rep["participation"]["histogram"])
    svg_curve = _svg_capacity_curve(
        rep["capacity_curve"]["points"], rep.get("alpha")
    )

    html = _HTML_TEMPLATE
    for k, v in {
        "__TITLE__": title,
        "__META__": meta,
        "__WARN__": warn,
        "__CALLOUT__": callout,
        "__SVG_HIST__": svg_hist,
        "__SVG_CURVE__": svg_curve,
        "__TABLE__": table,
        "__CAVEATS__": caveats,
    }.items():
        html = html.replace(k, v)
    return html


def render_json(rep: dict[str, Any]) -> str:
    # values 可能很长，JSON 保留摘要直方图即可；完整 values 仍可要
    payload = dict(rep)
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="策略容量与交易成本分析（TCA）"
    )
    ap.add_argument("--trades", required=True, help="成交 CSV")
    ap.add_argument("--adv", required=True, help="ADV CSV")
    ap.add_argument("--price", default=None, help="可选价格 CSV date,symbol,close")
    ap.add_argument("--returns", default=None, help="可选收益 CSV（Amihud）")
    ap.add_argument("--alpha", type=float, default=None, help="预期毛年化 alpha，如 0.05")
    ap.add_argument("--nav", type=float, default=None, help="起始资金 / AUM 代理")
    ap.add_argument("--impact-coef", type=float, default=0.5, dest="impact_coef")
    ap.add_argument("--sigma-daily", type=float, default=DEFAULT_SIGMA_DAILY, dest="sigma_daily")
    ap.add_argument("--out", default=None, help="输出目录")
    ap.add_argument("--no-html", action="store_true")
    args = ap.parse_args(argv)

    price = load_price(args.price) if args.price else None
    trades = load_trades(args.trades, price=price)
    adv = load_adv(args.adv, price=price)
    returns = load_returns(args.returns) if args.returns else None

    enriched, n_drop = participation(trades, adv)

    # NAV 代理
    if args.nav is not None and args.nav > 0:
        nav = float(args.nav)
    else:
        # 用样本期日均成交名义 * 粗略杠杆估计；至少取总名义
        daily = enriched.groupby("date")["notional"].sum().abs()
        nav = float(max(daily.mean() * 20, enriched["notional"].abs().sum() * 0.5, 1.0))

    amihud = amihud_illiquidity(returns, enriched)
    rep = build_report(
        enriched,
        n_drop,
        nav=nav,
        sigma_daily=args.sigma_daily,
        coef=args.impact_coef,
        alpha=args.alpha,
        amihud=amihud,
    )

    text = render_text(rep)
    print(text)

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        txt_path = os.path.join(args.out, "capacity_tca.txt")
        json_path = os.path.join(args.out, "capacity_tca.json")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        with open(json_path, "w", encoding="utf-8") as f:
            f.write(render_json(rep) + "\n")
        if not args.no_html:
            html_path = os.path.join(args.out, "capacity_tca.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(render_html(rep))
        print(f"\n已写入 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
