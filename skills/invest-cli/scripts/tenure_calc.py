#!/usr/bin/env python3
"""任职期复盘（tenure）纯计算层 —— 无网络、无重库、可单测。

口径真源（改这里必须同步 test_tenure.py 的口径回归）：
- 基期日 = 任职日前一交易日；任职日即成立日（无前一日）时取任职日。
  该口径与 Wind「区间收益率（复权单位净值增长率）」一致：任职当日收益计入。
- 累计收益率 = 期末复权净值 / 基期复权净值 - 1；日收益率 = 复权净值日环比。
- 年度最大回撤 = 年内峰值→谷值最大回撤（Wind 口径：峰值只在本年内取，
  不回溯上年末；实测 001959 2024 年两种口径差 1.4pct，必须与官方字段同口径）。
- 年化 = (1 + 累计收益率)^(365 / 区间自然日数) - 1。

为什么单独一层：这些口径是整个交付物的可信度所在，却与取数完全无关。
放在命令层会被网络代码裹住，单测就得 mock HTTP；独立出来后可纯函数回归。
"""
from __future__ import annotations

from datetime import date, timedelta

# 本地复算与 Wind 官方字段的容许偏差（百分点）。超过即视为口径/数据异常，要在产物里暴露。
TOLERANCE_PP = 0.05


def split_chunks(begin: str, end: str, days: int) -> list[tuple[str, str]]:
    """按自然日切成 [b, e] 闭区间段，覆盖 begin~end，段间不重叠不留缝。

    段宽 `days` 必填：它是**数据源约束**，不是通用常量 —— Wind get_fund_performance
    单次硬顶 100 行（实测 2026-09-23，问「全部」也只回 100 行），段宽由
    sources/wind.py 的 NAV_CHUNK_DAYS 决定，本层不复制这个知识。
    """
    b, e = date.fromisoformat(begin), date.fromisoformat(end)
    out: list[tuple[str, str]] = []
    cur = b
    while cur <= e:
        stop = min(cur + timedelta(days=days - 1), e)
        out.append((cur.isoformat(), stop.isoformat()))
        cur = stop + timedelta(days=1)
    return out


def base_date(nav_dates: list[str], since: str) -> str:
    """基期日：since 之前最后一个交易日；没有（since 即首个净值日）就用 since。"""
    prior = [d for d in nav_dates if d < since]
    return prior[-1] if prior else since


def align_index(idx: list[tuple[str, float]], dates: list[str]) -> tuple[list[float | None], int]:
    """把基准指数点位对齐到基金净值日：缺失日沿用前一可用值，返回 (点位, 补位天数)。

    为什么是沿用而不是留空：含港股仓位的基准指数（基金代码+BI.WI）在港股假期
    （圣诞/复活节/佛诞/七一/重阳…）没有行情，实测 011371BI.WI 在 2023-10~2026-09
    缺 21 天。沿用前值等价于当日 0 收益，累计口径不受影响；留空会让累计列出现断点。
    """
    m = dict(idx)
    avail = sorted(m)
    out: list[float | None] = []
    filled = 0
    cursor = -1  # avail 上的单调游标：日期递增，不必每天二分
    for d in dates:
        if d in m:
            while cursor + 1 < len(avail) and avail[cursor + 1] <= d:
                cursor += 1
            out.append(m[d])
            continue
        while cursor + 1 < len(avail) and avail[cursor + 1] <= d:
            cursor += 1
        if cursor >= 0:
            out.append(m[avail[cursor]])
            filled += 1
        else:
            out.append(None)
    return out, filled


def annualize(total: float, days: int) -> float | None:
    """按自然日 365 口径年化；区间不足 1 天返回 None。"""
    if days <= 0:
        return None
    return (1.0 + total) ** (365.0 / days) - 1.0


def build_daily(
    nav: list[tuple[str, float]],
    idx: list[tuple[str, float]],
    since: str,
) -> dict:
    """任职以来逐日序列 + 汇总。nav/idx 均为按日期升序的 (日期, 值) 列表。"""
    if not nav:
        raise ValueError("净值序列为空")
    nav_map = dict(nav)
    b = base_date([d for d, _ in nav], since)
    base_nav = nav_map[b]
    dates = [d for d, _ in nav if d >= since]
    if not dates:
        raise ValueError(f"任职日 {since} 之后没有净值数据")

    levels, filled = align_index(idx, dates)
    idx_map = dict(idx)
    base_idx = idx_map.get(b)
    idx_base_date = b
    if base_idx is None:  # 基期日基准无行情 → 顺延取最近可用日
        for d in sorted(idx_map):
            if d >= b:
                base_idx, idx_base_date = idx_map[d], d
                break

    rows: list[dict] = []
    prev_nav: float | None = None
    prev_idx: float | None = None
    for d, level in zip(dates, levels):
        v = nav_map[d]
        ret = (v / prev_nav - 1.0) if prev_nav else (v / base_nav - 1.0)
        cum = v / base_nav - 1.0
        note = ""
        if level is None:
            i_ret = i_cum = None
        else:
            if d not in idx_map:
                note = "基准指数当日无行情（港股假期），点位沿用前值"
            i_ret = (level / prev_idx - 1.0) if prev_idx else (level / base_idx - 1.0 if base_idx else None)
            i_cum = (level / base_idx - 1.0) if base_idx else None
        rows.append({
            "date": d, "nav": v, "ret": ret, "cum": cum,
            "index": level, "index_ret": i_ret, "index_cum": i_cum,
            "excess": (cum - i_cum) if i_cum is not None else None,
            "note": note,
        })
        prev_nav = v
        if level is not None:
            prev_idx = level

    total = rows[-1]["cum"]
    idx_total = rows[-1]["index_cum"]
    span = (date.fromisoformat(rows[-1]["date"]) - date.fromisoformat(b)).days
    return {
        "rows": rows,
        "stats": {
            "base_date": b, "base_nav": base_nav, "start": rows[0]["date"], "end": rows[-1]["date"],
            "days": span, "n": len(rows), "total": total, "annualized": annualize(total, span),
            "index_base": base_idx, "index_base_date": idx_base_date,
            "index_total": idx_total,
            "index_annualized": annualize(idx_total, span) if idx_total is not None else None,
            "excess": (total - idx_total) if idx_total is not None else None,
            "filled_days": filled,
        },
    }


def calendar_year_stats(nav: list[tuple[str, float]], year: int) -> dict | None:
    """某自然年的收益率与年内最大回撤（本地复算，用于与官方字段交叉校验）。

    收益率基期 = 上一年最后一个交易日；基金年内新成立时退化为「成立日至年末」。
    回撤按 Wind 口径：峰值只在本年内取。
    """
    y0, y1 = f"{year}-01-01", f"{year}-12-31"
    cur = [p for p in nav if y0 <= p[0] <= y1]
    if not cur:
        return None
    prior = [p for p in nav if p[0] < y0]
    base = prior[-1] if prior else None
    ret = (cur[-1][1] / base[1] - 1.0) if base else (cur[-1][1] / cur[0][1] - 1.0)
    peak = cur[0][1]
    mdd = 0.0
    for _, v in cur:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1.0)
    return {"ret": ret, "mdd": mdd, "start": (base[0] if base else cur[0][0]), "end": cur[-1][0], "n": len(cur)}


def year_mode(inception: str, since: str, year: int) -> str:
    """年度口径标签：未成立 / 未任职 / since_incep（成立日至年末）/ full（完整年度）。"""
    y0, y1 = f"{year}-01-01", f"{year}-12-31"
    if inception > y1:
        return "未成立"
    if since > y1:
        return "未任职"
    if inception > y0:
        return "since_incep"
    return "full"


def annual_table(
    nav: list[tuple[str, float]],
    inception: str,
    since: str,
    years: list[int],
    official: dict[int, dict] | None = None,
) -> list[dict]:
    """年度业绩表：官方字段优先（与 Wind 终端一致），本地复算只做校验与补位。"""
    official = official or {}
    out: list[dict] = []
    for y in years:
        mode = year_mode(inception, since, y)
        local = calendar_year_stats(nav, y)
        off = official.get(y) or {}
        ret = off.get("ret")
        mdd = off.get("mdd")
        src = "wind_official"
        if ret is None and local is not None and mode not in ("未成立", "未任职"):
            ret, src = local["ret"], "local"
        if mdd is None and local is not None and mode not in ("未成立", "未任职"):
            mdd = local["mdd"]
        diff = None
        if local is not None and off.get("ret") is not None:
            diff = (local["ret"] - off["ret"]) * 100.0
        out.append({
            "year": y, "ret": ret, "mdd": mdd, "mode": mode,
            "ret_local": local["ret"] if local else None,
            "mdd_local": local["mdd"] if local else None,
            "diff_pp": diff, "ret_source": src if ret is not None else None,
        })
    return out


def default_years(as_of: str, span: int = 5) -> list[int]:
    """方法论口径 [N-4, N]：N = 当年（4/30 前用上一年，年报尚未出全）。"""
    t = date.fromisoformat(as_of)
    n = t.year if (t.month, t.day) > (4, 30) else t.year - 1
    return list(range(n - span + 1, n + 1))
