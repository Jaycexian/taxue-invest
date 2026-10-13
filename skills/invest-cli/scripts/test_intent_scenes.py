#!/usr/bin/env python3
"""intent 场景路由契约 — 不打外部网络。

覆盖 2026-08-30 review 修复的回归点：
1. portfolio/plan/present 的 ROUTES 必须是可调用 lambda（曾是裸 tuple → TypeError）
2. deep bond 路由到 wind bond_data 且保留标的代码
3. present 路由到本地 cmd_present（不再引用不存在的 Wind RenderHtmlToPdf）
4. classify 英文类型词精确匹配（bond/gold/commodity 不误判美股）
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from cmd_intent import (  # noqa: E402
    ROUTES,
    _dispatch,
    classify,
)


def test_scene_routes_are_callable() -> None:
    """present/portfolio/plan 的 default 必须是 lambda，不能是裸 tuple。"""
    for scene in ("portfolio", "plan", "present"):
        r = ROUTES[scene]["default"]
        assert callable(r), f"{scene} default 不可调用: {r!r}"


def test_dispatch_scene_shapes() -> None:
    for scene, kind in (
        ("portfolio", "yingmi"),
        ("plan", "yingmi"),
        ("present", "present_html"),
    ):
        d = _dispatch(scene, "示例输入")
        assert d["route"][0] == kind, f"{scene} 应路由到 {kind}，实际 {d}"
    # JSON 输入交给 run() 层解析（这里不重复解析，避免两份口径）
    d = _dispatch("portfolio", '{"totalAssets": 1000000}')
    assert d["route"][0] == "yingmi"
    assert d["route"][2] == {}


def test_portfolio_natural_language_becomes_fund_list() -> None:
    """SKILL 承诺「持仓 json 或自然语言」；旧实现把整串当 input 发出去，服务端必回 400。"""
    d = _dispatch("portfolio", "110011:50,005827:50")
    assert d["route"][2] == {"fundList": [
        {"fundCode": "110011", "amount": 50.0},
        {"fundCode": "005827", "amount": 50.0},
    ]}
    # 不写金额时给等权默认，至少能诊断出持仓结构
    d2 = _dispatch("portfolio", "110011 005827")
    assert [i["fundCode"] for i in d2["route"][2]["fundList"]] == ["110011", "005827"]
    assert all(i["amount"] > 0 for i in d2["route"][2]["fundList"])


def test_plan_natural_language_becomes_three_params() -> None:
    """「能承受 20% 回撤 / 5 年 / 年化 8%」要换算成三个查询参数（百分比→小数）。"""
    d = _dispatch("plan", "能承受20%回撤 5年 预期年化8%")
    params = d["route"][2]
    assert params["expectedDrawdown"] == 0.2
    assert params["expectedAnnualizedReturnRate"] == 0.08
    assert params["expectedInvestTime"] == "5y"


def test_present_no_ghost_wind_tool() -> None:
    """不得再引用 wind-mcp-skill 中不存在的 RenderHtmlToPdf。"""
    assert "RenderHtmlToPdf" not in str(ROUTES)


def test_deep_bond_keeps_target() -> None:
    """deep bond 必须把标的代码带进路由（wind bond_data question）。"""
    d = _dispatch("deep", "bond 019547.SH")
    assert d["route"][0] == "wind_bond"
    assert "019547.SH" in str(d["route"][1])
    assert d["type"] == "bond"


def test_deep_commodity_routes_ttskill_gold() -> None:
    # ttfund 已退役：黄金深取改走官方 TTFUND_GOLD_INFO
    d = _dispatch("deep", "commodity AU9999")
    assert d["route"][0] == "ttskill_scene"
    assert d["route"][1] == "TTFUND_GOLD_INFO"
    assert d["route"][2] == {"query_scope": "gold"}


def test_classify_english_type_words() -> None:
    assert classify("bond") == "bond"
    assert classify("BOND") == "bond"
    assert classify("gold") == "commodity"
    assert classify("commodity") == "commodity"
    assert classify("AAPL") == "us"  # 真美股代码不受影响


def test_screen_fund_search_carries_pagination_and_keyword() -> None:
    """SearchFunds 两个静默空结果来源（2026-10-11 实测回归点）必须都兜住。

    ① 旧路由只传 {"keyword": …}，盈米按空页处理，静默返回
       {"funds": [], "total": 0} 且 rc=0——「没带分页」被读成「没有匹配基金」。
    ② 值是自然语言短语时（SKILL 承诺的用法），整串原样喂给基金名**子串搜索**
       同样零命中——「红利主题基金」→ 0 条，「红利」→ 10 条。
    """
    from cmd_intent import _fund_search_keyword

    d = _dispatch("screen", "红利主题基金")
    assert d["route"][0] == "yingmi", f"基金语义应路由盈米: {d}"
    assert d["route"][1] == "SearchFunds"
    params = d["route"][2]
    assert params.get("pageIndex") == 1 and params.get("pageSize"), (
        f"SearchFunds 缺分页参数——盈米会静默回空页: {params}"
    )
    # 短语要提炼成名字关键词：剥通用修饰词，不做语义猜测
    assert params["keyword"] == "红利", f"应提炼出「红利」: {params}"
    # 名字里真含这些字的查询不受影响（剥完为空则原样返回）
    assert _fund_search_keyword("红利主题基金") == "红利"
    assert _fund_search_keyword("医药基金") == "医药"
    assert _fund_search_keyword("基金") == "基金"
    # 「混合/优选/精选」是真名用词，不在剥离词表里
    assert _fund_search_keyword("华商红利优选混合") == "华商红利优选混合"


if __name__ == "__main__":
    test_scene_routes_are_callable()
    test_dispatch_scene_shapes()
    test_present_no_ghost_wind_tool()
    test_deep_bond_keeps_target()
    test_deep_commodity_routes_ttskill_gold()
    test_classify_english_type_words()
    test_screen_fund_search_carries_pagination()
    print("test_intent_scenes: OK")
