"""妙想(MX) MCP 数据源适配器测试（全部离线：_call_tool/_mcp_call 整体替换）。

线上行为契约（2026-10-11 实测，真实 key）：
- 服务端 NLU 不认裸 ticker："AAPL" 返回空数组，"AAPL 股价" 正常——us() 对
  ticker 形态输入包装查询模板；
- 单次调用 1.2s+（服务端 NLU 主导，连接复用零收益），auto() 按查询文本缓存
  300s（相邻调用 ≤300s 占 96.7%，第五轮治理报告口径）；
- MCP StreamableHttp 服务端可返回 SSE（spec 2025-06-18），_extract_payload 双支持。
"""
from __future__ import annotations

import pytest

import sources.mxds as mxds


# ---------- 路由（auto） ----------

def _capture_call(monkeypatch):
    calls = []

    def fake(tool, q):
        calls.append((tool, q))
        return {"source": "mxds", "kind": tool, "ok": True,
                "data": {"data": [{"columns": [], "items": []}]}, "error": None}

    monkeypatch.setattr(mxds, "_call_tool", fake)
    return calls


def test_auto_pe_routes_to_screener(monkeypatch):
    """「PE 最低的银行股」曾因 \\b[A-Za-z]{2,5}\\b 误路由到美股工具（选股分支排在其后永远轮不到）。"""
    calls = _capture_call(monkeypatch)
    mxds.auto("PE 最低的银行股")
    assert calls[0][0] == "mx_stocks_screener"


def test_auto_roe_pb_routes_to_screener(monkeypatch):
    calls = _capture_call(monkeypatch)
    mxds.auto("ROE 大于 20 的消费股")
    assert calls[0][0] == "mx_stocks_screener"
    mxds.auto("PB 最小的钢铁股")
    assert calls[-1][0] == "mx_stocks_screener"


def test_auto_hk_0prefix_4digit(monkeypatch):
    """0700 是港股惯写；「3000亿」这类金额表达不得误入港股分支。"""
    calls = _capture_call(monkeypatch)
    mxds.auto("0700 股价")
    assert calls[0][0] == "mx_hk_finance_data"
    mxds.auto("3000亿市值的公司")
    assert calls[-1][0] != "mx_hk_finance_data"


def test_auto_year_not_hk(monkeypatch):
    calls = _capture_call(monkeypatch)
    mxds.auto("贵州茅台2026年目标价")
    assert calls[0][0] == "mx_ashare_finance_data"


def test_auto_ashare_default(monkeypatch):
    calls = _capture_call(monkeypatch)
    mxds.auto("贵州茅台最新价")
    assert calls[0][0] == "mx_ashare_finance_data"


def test_auto_lowercase_english_not_us(monkeypatch):
    """小写英文词不是美股信号（旧判据 [A-Za-z]{2,5} 会命中）。"""
    calls = _capture_call(monkeypatch)
    mxds.auto("茅台的 apple 供应链")
    assert calls[0][0] == "mx_ashare_finance_data"


# ---------- us() ticker 模板包装 ----------

def test_us_bare_ticker_wrapped(monkeypatch):
    """裸 ticker 服务端 NLU 不识别（实测 "AAPL" 返回空数组），必须包装查询模板。"""
    calls = _capture_call(monkeypatch)
    mxds.us("aapl")
    assert calls[0][1] == "AAPL 股价"


def test_us_ticker_with_suffix(monkeypatch):
    calls = _capture_call(monkeypatch)
    mxds.us("BRK.A")
    assert calls[0][1] == "BRK.A 股价"


def test_us_natural_language_untouched(monkeypatch):
    calls = _capture_call(monkeypatch)
    mxds.us("苹果公司最新股价")
    assert calls[0][1] == "苹果公司最新股价"


# ---------- auto() 查询缓存 ----------

def test_auto_cache_hit_skips_second_call(monkeypatch):
    """同查询 300s 内第二次不再打网，命中带 cached=True。"""
    n = {"count": 0}

    def fake(tool, q):
        n["count"] += 1
        return {"source": "mxds", "kind": tool, "ok": True,
                "data": {"data": [{"v": q}]}, "error": None}

    monkeypatch.setattr(mxds, "_call_tool", fake)
    r1 = mxds.auto("贵州茅台最新价")
    assert "cached" not in r1
    r2 = mxds.auto("贵州茅台最新价")
    assert n["count"] == 1
    assert r2["cached"] is True
    assert r2["data"] == r1["data"]


def test_auto_failed_call_not_cached(monkeypatch):
    """失败结果不写缓存——偶发服务端波动不该被固化为 5 分钟失败。"""
    n = {"count": 0}

    def fake(tool, q):
        n["count"] += 1
        return {"source": "mxds", "kind": tool, "ok": False,
                "data": None, "error": "HTTP 503: Service Unavailable: up"}

    monkeypatch.setattr(mxds, "_call_tool", fake)
    mxds.auto("贵州茅台最新价")
    mxds.auto("贵州茅台最新价")
    assert n["count"] == 2


# ---------- SSE 兼容（_extract_payload） ----------

def test_extract_payload_json_passthrough():
    body = '{"jsonrpc":"2.0","id":1}'
    assert mxds._extract_payload(body, "application/json") == body


def test_extract_payload_sse_takes_last_data_line():
    body = 'event: message\ndata: {"a":1}\n\ndata: {"b":2}\n'
    assert mxds._extract_payload(body, "text/event-stream") == '{"b":2}'


def test_extract_payload_sse_without_data_is_error():
    with pytest.raises(ValueError):
        mxds._extract_payload("event: ping\n", "text/event-stream")


# ---------- 失败信封 raw 透传 ----------

def test_call_tool_failure_passes_raw(monkeypatch):
    """raw 透传：失败时调用方能看到服务端原始返回，排障不再断链。"""

    def fake_mcp(tool, query, timeout=30):
        return {"ok": False, "error": f"服务端未识别查询或无数据: {query!r}",
                "raw": {"data": []}}

    monkeypatch.setattr(mxds, "_mcp_call", fake_mcp)
    r = mxds._call_tool("mx_us_finance_data", "AAPL")
    assert r["ok"] is False
    assert r["raw"] == {"data": []}


# ---------- _ticker_like ----------

def test_ticker_like():
    assert mxds._ticker_like("AAPL 最新价") is True
    assert mxds._ticker_like("PE 最低的银行股") is False
    assert mxds._ticker_like("苹果公司") is False
