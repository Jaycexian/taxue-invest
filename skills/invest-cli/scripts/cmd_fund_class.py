#!/usr/bin/env python3
"""
基金三层分类器 — 评价友好分类（L1 大类资产 / L2 产品形态 / L3 投资策略 + 辅助标签）。

定位：只输出稳定的分类事实（含依据与口径原文，未知显式「未定」），不做评价
结论——筛选/三关审查/同类对比各自消费。规则真源 fund-classification.yaml，
数据底座 ttskill TTFUND_BASE_INFOS（单请求）。

用法：
  invest-cli fund-class 005827 [--json]
"""

from __future__ import annotations

import json
import sys
from typing import Any


def run(args: Any) -> int:
    """invest-cli 入口。返回进程退出码；错误走 stderr。"""
    from fund_classifier import classify

    res = classify(args.code)
    if not res.get("ok") or not isinstance(res.get("data"), dict):
        print(f"错误: {res.get('error') or '基金分类失败'}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(res["data"], ensure_ascii=False, indent=2))
        return 0
    print(_format(res["data"]))
    return 0


def _format(data: dict[str, Any]) -> str:
    """终端渲染：三层分类 + 标签 + 依据 + 冲突/未定。"""
    c = data.get("classification") or {}
    t = data.get("tags") or {}
    b = data.get("basis") or {}
    lines = [
        f"\n{'=' * 60}",
        f"  {data.get('name', data.get('code', '?'))}（{data.get('code', '?')}）— 基金三层分类",
        f"{'=' * 60}",
        "",
        f"  {'层':<14} {'分类':>18}",
        f"  {'-' * 34}",
    ]
    for label, key in (("L1 大类资产", "L1"), ("L2 产品形态", "L2"), ("L3 投资策略", "L3")):
        val = c.get(key)
        shown = "-" if val is None else str(val)
        lines.append(f"  {label:<14} {shown:>18}")
    if data.get("type_raw"):
        lines.append(f"\n  类型原文: {data['type_raw']}（{data.get('type_source', '')}）")

    if t.get("运作方式"):
        lines.append(f"\n  辅助标签: 运作方式 {t['运作方式']} | " +
                     " | ".join(f"{label} {'是' if t.get(label) is True else '否'}"
                                for label in ("固收+", "指数型", "QDII", "FOF", "养老", "REITs")
                                if label in t))
    else:
        bits = " | ".join(f"{label} {'是' if t.get(label) is True else '否'}"
                          for label in ("固收+", "指数型", "QDII", "FOF", "养老", "REITs")
                          if label in t)
        if bits:
            lines.append(f"\n  辅助标签: {bits}")
    if t.get("目标ETF"):
        lines.append(f"  目标ETF: {t['目标ETF']}")
    if t.get("风险等级"):
        lines.append(f"  风险等级: {t['风险等级']}")
    sc = t.get("申赎状态")
    if isinstance(sc, dict):
        lines.append(f"  申赎状态: {sc.get('subscribe', '-')} / {sc.get('redeem', '-')}")

    basis_bits = [f"{k}: {v}" for k, v in b.items() if v]
    if basis_bits:
        lines.append(f"\n  依据:")
        for bit in basis_bits:
            lines.append(f"  · {bit}")

    for cf in data.get("conflicts") or []:
        lines.append(f"\n  ⚠ 口径冲突: {cf.get('detail', '')}")
    for u in data.get("unresolved") or []:
        lines.append(f"  ⚠ 未定: {u}")

    lines.append(f"\n  数据来源: ttskill TTFUND_BASE_INFOS expansion（单请求）")
    return "\n".join(lines)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="基金三层分类（评价友好分类）")
    parser.add_argument("code", help="6 位基金代码，如 005827")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
