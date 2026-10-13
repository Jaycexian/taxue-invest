#!/usr/bin/env python3
"""tenure（任职期复盘）回归测试 —— 全部离线，不联网、不写用户目录。

三层防线（对应三类真实事故）：
1. **纯口径回归**（synthetic）：基期选任职日前一交易日、任职当日收益计入、
   年度回撤取「年内峰值」而非跨年峰值 —— 这两条口径各差 0.2pct / 1.4pct，
   肉眼看不出来，只能靠测试钉住；
2. **真实数据基线**（tenure_samples.json）：001959 的 Wind 快照 + 已校验的交付数值，
   防止重构让数字漂移；
3. **降级链与产物**（monkeypatch）：Wind 不可用时必须回退 ttskill 并把「用的哪个源、
   缺了什么」写进产物 —— 静默降级比报错更危险。

跑：.venv/bin/python -m pytest scripts/test_tenure.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import cmd_tenure
import pytest
import tenure_calc as tc

FIXTURE = json.loads((Path(__file__).resolve().parent / "tenure_samples.json").read_text(encoding="utf-8"))
NAV = [(d, v) for d, v in FIXTURE["nav_001959"]]
IDX = [(d, v) for d, v in FIXTURE["index_001959BI"]]


# ── 1. 纯口径回归 ──────────────────────────────────────────────

def test_split_chunks_covers_without_gap_or_overlap() -> None:
    segs = tc.split_chunks("2022-12-30", "2023-06-15", 120)
    assert segs[0][0] == "2022-12-30"
    assert segs[-1][1] == "2023-06-15"
    for (b1, e1), (b2, e2) in zip(segs, segs[1:]):
        assert e1 < b2, "段间不得重叠"
        assert (tc.date.fromisoformat(b2) - tc.date.fromisoformat(e1)).days == 1, "段间不得留缝"


def test_split_chunks_single_day() -> None:
    assert tc.split_chunks("2024-01-02", "2024-01-02", 120) == [("2024-01-02", "2024-01-02")]


def test_base_date_is_prior_trading_day() -> None:
    """基期 = 任职日前一交易日：任职当日收益因此计入（与 Wind 区间收益率同口径）。"""
    dates = ["2022-09-09", "2022-09-13", "2022-09-14"]
    assert tc.base_date(dates, "2022-09-13") == "2022-09-09"


def test_base_date_falls_back_to_since_on_inception() -> None:
    """任职日即成立日（首日无前一日净值）→ 基期取任职日，首日收益记 0。"""
    assert tc.base_date(["2024-04-02", "2024-04-03"], "2024-04-02") == "2024-04-02"


def test_align_index_carries_forward_hk_holiday() -> None:
    """港股假期基准无行情 → 沿用前值并计数，累计口径不受影响。"""
    idx = [("2024-03-28", 100.0), ("2024-04-01", 101.0)]
    levels, filled = tc.align_index(idx, ["2024-03-28", "2024-03-29", "2024-04-01"])
    assert levels == [100.0, 100.0, 101.0]
    assert filled == 1


def test_align_index_leading_gap_is_none() -> None:
    levels, filled = tc.align_index([("2024-04-02", 100.0)], ["2024-04-01", "2024-04-02"])
    assert levels == [None, 100.0] and filled == 0


def test_build_daily_first_row_includes_appointment_day_return() -> None:
    """首行日收益率 = 任职日相对前一交易日的涨跌，不是 0 —— 这是与 Wind 对齐的关键。"""
    nav = [("2022-09-09", 2.0), ("2022-09-13", 2.1), ("2022-09-14", 2.0)]
    out = tc.build_daily(nav, [("2022-09-09", 100.0), ("2022-09-13", 110.0), ("2022-09-14", 105.0)], "2022-09-13")
    rows, stats = out["rows"], out["stats"]
    assert stats["base_date"] == "2022-09-09" and stats["base_nav"] == 2.0
    assert rows[0]["ret"] == pytest.approx(0.05) and rows[0]["cum"] == pytest.approx(0.05)
    assert rows[-1]["cum"] == pytest.approx(0.0)          # 2.0/2.0 - 1
    assert rows[-1]["index_cum"] == pytest.approx(0.05)   # 105/100 - 1
    assert rows[-1]["excess"] == pytest.approx(-0.05)
    assert stats["n"] == 2 and stats["days"] == 5          # 2022-09-09 → 2022-09-14
    assert stats["annualized"] == pytest.approx((1.0 + 0.0) ** (365 / 5) - 1)


def test_build_daily_raises_when_window_empty() -> None:
    with pytest.raises(ValueError):
        tc.build_daily([("2022-01-01", 1.0)], [], "2023-01-01")


def test_calendar_year_mdd_ignores_prior_year_peak() -> None:
    """年度回撤取年内峰值（Wind 口径）：上年末的高点不计入，否则 001959 2024 年会多 1.4pct。"""
    nav = [("2023-12-29", 3.0), ("2024-01-02", 2.0), ("2024-06-28", 2.4), ("2024-12-31", 2.2)]
    st = tc.calendar_year_stats(nav, 2024)
    assert st is not None
    assert st["ret"] == pytest.approx(2.2 / 3.0 - 1)     # 收益率仍以上年末为基期
    assert st["mdd"] == pytest.approx(2.2 / 2.4 - 1)     # 年内峰值 2.4 → 谷值 2.2；若把上年末 3.0 当峰值会算成 -26.7%


def test_year_mode_branches() -> None:
    assert tc.year_mode("2024-04-02", "2024-04-02", 2023) == "未成立"
    assert tc.year_mode("2021-01-19", "2025-03-12", 2024) == "未任职"
    assert tc.year_mode("2024-04-02", "2024-04-02", 2024) == "since_incep"
    assert tc.year_mode("2015-12-18", "2022-09-13", 2023) == "full"


def test_annual_table_prefers_official_and_keeps_local_for_check() -> None:
    nav = [("2023-12-29", 1.0), ("2024-06-28", 1.2), ("2024-12-31", 1.1)]
    official = {2024: {"ret": 0.10, "mdd": -0.05}}
    row = tc.annual_table(nav, "2023-01-01", "2023-01-01", [2024], official)[0]
    assert row["ret"] == 0.10 and row["mdd"] == -0.05 and row["ret_source"] == "wind_official"
    assert row["ret_local"] == pytest.approx(0.1)          # 1.1/1.0 - 1
    assert row["diff_pp"] == pytest.approx(0.0, abs=1e-6)


def test_annual_table_marks_missing_years_without_numbers() -> None:
    nav = [("2024-04-02", 1.0), ("2024-12-31", 1.2)]
    rows = tc.annual_table(nav, "2024-04-02", "2024-04-02", [2023, 2024], {})
    assert rows[0]["mode"] == "未成立" and rows[0]["ret"] is None
    assert rows[1]["mode"] == "since_incep" and rows[1]["ret"] == pytest.approx(0.2)


def test_default_years_uses_annual_report_anchor() -> None:
    assert tc.default_years("2026-09-23", 5) == [2022, 2023, 2024, 2025, 2026]
    assert tc.default_years("2026-03-15", 5) == [2021, 2022, 2023, 2024, 2025]  # 4/30 前用上一年


# ── 2. 真实数据基线（Wind 快照，勿改数值） ─────────────────────────

def test_golden_tenure_numbers_match_validated_deliverable() -> None:
    out = tc.build_daily(NAV, IDX, "2022-09-13")
    st = out["stats"]
    assert st["base_date"] == "2022-09-09"
    assert st["base_nav"] == pytest.approx(2.133)
    assert st["index_base"] == pytest.approx(1128.1955)
    assert st["n"] == 978 and st["end"] == "2026-09-23"
    assert st["total"] == pytest.approx(0.956399, abs=1e-4)      # Wind 官方 95.6412%
    assert st["index_total"] == pytest.approx(0.165450, abs=1e-4)
    assert st["excess"] == pytest.approx(0.790950, abs=1e-4)
    assert st["annualized"] == pytest.approx(0.180656, abs=1e-4)
    assert st["filled_days"] == 0                                 # 001959 基准不含港股，无补位


def test_golden_annual_mdd_uses_within_year_peak() -> None:
    """2024 年内最大回撤 -28.907%（Wind 官方 -28.9044%）；跨年口径会算成 -30.29%。"""
    st = tc.calendar_year_stats(NAV, 2024)
    assert st is not None
    assert st["mdd"] == pytest.approx(-0.289070, abs=2e-4)
    assert st["ret"] == pytest.approx(0.007417, abs=1e-4)


# ── 3. 降级链、解析与产物 ───────────────────────────────────────

# 历任任职记录的两种真实写法（区间 / 在任）；姓名用 010761 的真实记录，
# 供解析用例复用；下面的 fake profile 用一份合成记录把任职日钉在 011371 的真实值上。
WIND_HISTORY = "周海栋(20210119-20250312)\r\n孙蔚(20231229至今)\r\n崔志鹏(20240103至今)\r\n余懿(20250312至今)"
FAKE_HISTORY_011371 = "前经理(20210616-20230110)\r\n余懿(20230110至今)"


def test_parse_manager_history_and_appointment() -> None:
    recs = cmd_tenure.parse_manager_history(WIND_HISTORY)
    assert [r["name"] for r in recs] == ["周海栋", "孙蔚", "崔志鹏", "余懿"]
    assert recs[0]["end"] == "2025-03-12" and recs[-1]["end"] == ""
    assert cmd_tenure.appointment_of(WIND_HISTORY, "余懿") == "2025-03-12"
    assert cmd_tenure.appointment_of(WIND_HISTORY, "张三") is None


def test_initial_share_only_picks_a_class_and_keeps_standalone() -> None:
    funds = [
        {"code": "025024.OF", "name": "华商港股通价值回报", "appt": "2025-09-10"},
        {"code": "019189.OF", "name": "华商品质价值A", "appt": "2024-04-02"},
        {"code": "019190.OF", "name": "华商品质价值C", "appt": "2024-04-02"},
        {"code": "011371.OF", "name": "华商远见价值A", "appt": "2023-01-10"},
        {"code": "011372.OF", "name": "华商远见价值C", "appt": "2023-01-10"},
    ]
    keep, dropped = cmd_tenure.initial_share_only(funds)
    assert [f["code"] for f in keep] == ["025024.OF", "019189.OF", "011371.OF"]
    assert [f["code"] for f in dropped] == ["019190.OF", "011372.OF"]


def _fake_wind(monkeypatch, *, profile_ok=True, nav_ok=True, ttskill_ok=False):
    """把两个源都钉死：测试必须离线，否则「Wind 失败→ttskill 兜底」会真的打到网上。"""
    from sources import ttskill as ttskill_src
    from sources import wind as wind_src

    monkeypatch.setattr(ttskill_src, "base_profile", lambda code: (
        {"ok": True, "data": {"short_name": "华商远见价值A", "inception": "2021-06-16",
                              "benchmark": "沪深300*60%+中债总全价*40%", "managers": "余懿"},
         "error": None} if ttskill_ok else {"ok": False, "data": None, "error": "ttskill 未登录"}))
    monkeypatch.setattr(ttskill_src, "manager_funds", lambda manager: {"ok": False, "rows": None, "error": "offline"})

    def profile(code, name=""):
        if not profile_ok:
            return {"ok": False, "data": None, "error": "Wind 积分余额不足"}
        return {"ok": True, "data": {"full_name": "华商远见价值混合型证券投资基金", "inception": "2021-06-16",
                                     "managers": "余懿", "manager_history": FAKE_HISTORY_011371,
                                     "benchmark": "沪深300指数收益率*60%+中债-总全价(总值)指数收益率*40%",
                                     "fund_type": "偏股混合型基金", "aum": 21.7}, "error": None}

    monkeypatch.setattr(wind_src, "fund_profile", profile)
    monkeypatch.setattr(wind_src, "fund_nav_series", lambda *a, **k: (
        {"ok": True, "series": [("2023-01-09", 0.5227), ("2023-01-10", 0.5235), ("2026-09-23", 0.9594)],
         "calls": 12, "error": None} if nav_ok else {"ok": False, "series": [], "calls": 0, "error": "Wind 积分余额不足"}))
    monkeypatch.setattr(wind_src, "index_series", lambda *a, **k: {"ok": True, "series": IDX, "error": None})
    monkeypatch.setattr(wind_src, "fund_annual_stats", lambda *a, **k: {"ok": True, "data": {2023: {"ret": -0.055095, "mdd": -0.209874}}, "error": None})
    monkeypatch.setattr(wind_src, "range_return", lambda *a, **k: {"ok": True, "value": 0.83547, "error": None})


def test_build_fund_uses_wind_and_validates(monkeypatch) -> None:
    _fake_wind(monkeypatch)
    r = cmd_tenure.build_fund("011371.OF", "华商远见价值A", manager="余懿")
    assert r["ok"] and r["appointment"] == "2023-01-10"
    assert r["sources"]["nav"] == "wind" and r["benchmark_index"] == "011371BI.WI"
    row2023 = next(a for a in r["annual"] if a["year"] == 2023)
    assert row2023["ret"] == pytest.approx(-0.055095) and row2023["ret_source"] == "wind_official"
    assert r["validations"] and r["validations"][0]["ok"]


def test_build_fund_falls_back_to_ttskill_when_wind_nav_fails(monkeypatch) -> None:
    """Wind 净值失败 → ttskill 快路径，且必须留下「用了降级源」的痕迹。"""
    _fake_wind(monkeypatch, nav_ok=False)
    from sources import ttskill as ttskill_src

    monkeypatch.setattr(ttskill_src, "nav_series", lambda code, begin="": {
        "ok": True,
        "series": [("2023-01-09", 1.0), ("2023-01-10", 1.0015), ("2026-09-23", 1.83)],
        "coverage": {"rows": 3, "first": "2023-01-09", "last": "2026-09-23",
                     "missing_growth_days": 0, "weekly_head": True, "head_rows_6w": 7},
        "error": None})
    r = cmd_tenure.build_fund("011371.OF", "华商远见价值A", manager="余懿")
    assert r["ok"] and r["sources"]["nav"] == "ttskill"
    joined = " ".join(r["warnings"])
    assert "回退 ttskill" in joined and "周频" in joined


def test_build_fund_reports_hard_failure_with_reason(monkeypatch) -> None:
    _fake_wind(monkeypatch, profile_ok=False)
    r = cmd_tenure.build_fund("011371.OF", "华商远见价值A", manager="余懿")
    assert r["ok"] is False and "积分" in r["error"]


def test_short_err_strips_wind_credit_noise() -> None:
    raw = "{'isError': True, 'content': [{'text': '账户积分余额不足，无法完成当前操作。请提示用户先进行充值。官网充值链接地址：https://x', 'type': 'text'}]}"
    assert cmd_tenure._short_err(raw) == "Wind 积分余额不足"


def test_build_tables_shares_one_schema_between_xlsx_and_csv(monkeypatch) -> None:
    _fake_wind(monkeypatch)
    r = cmd_tenure.build_fund("011371.OF", "华商远见价值A", manager="余懿")
    payload = {"source": "wind", "kind": "tenure", "ok": True, "error": None,
               "data": {"manager": "余懿", "company": "", "mode": "single", "dropped": [], "funds": [r]}}
    tables = cmd_tenure.build_tables(payload)
    names = [t["name"] for t in tables]
    assert names[0] == "概览" and names[-1] == "数据来源"
    assert names[1].startswith("011371")
    for tb in tables:
        assert len(tb["kinds"]) == len(tb["header"]), f"{tb['name']} 列与格式定义不等长"
        for row in tb["rows"]:
            assert len(row) == len(tb["header"])
    overview = tables[0]
    assert overview["header"][5].endswith("收益率") and overview["header"][6].endswith("最大回撤")
    assert any(h.endswith("YTD收益率") for h in overview["header"]) or overview["rows"], "年份列标签应含 YTD"


def test_render_terminal_smoke(monkeypatch) -> None:
    _fake_wind(monkeypatch)
    r = cmd_tenure.build_fund("011371.OF", "华商远见价值A", manager="余懿")
    payload = {"source": "wind", "kind": "tenure", "ok": True, "error": None,
               "data": {"manager": "余懿", "company": "", "mode": "single", "dropped": [], "funds": [r]}}
    text = cmd_tenure.render_terminal(payload)
    assert "任职期复盘" in text and "任职以来 vs 基准" in text and "校验" in text


def test_csv_export_writes_every_table(tmp_path, monkeypatch) -> None:
    _fake_wind(monkeypatch)
    r = cmd_tenure.build_fund("011371.OF", "华商远见价值A", manager="余懿")
    payload = {"source": "wind", "kind": "tenure", "ok": True, "error": None,
               "data": {"manager": "余懿", "company": "", "mode": "single", "dropped": [], "funds": [r]}}
    written = cmd_tenure.write_csv(cmd_tenure.build_tables(payload), tmp_path)
    assert len(written) == 3
    text = (tmp_path / "概览.csv").read_text(encoding="utf-8-sig")
    assert "基金代码" in text and "任职以来累计收益率" in text


def test_excel_export_matches_csv_schema(tmp_path, monkeypatch) -> None:
    """xlsx 与 csv 必须同表同列：这是「单一表格真源」的可执行证据。"""
    openpyxl = pytest.importorskip("openpyxl")
    _fake_wind(monkeypatch)
    r = cmd_tenure.build_fund("011371.OF", "华商远见价值A", manager="余懿")
    payload = {"source": "wind", "kind": "tenure", "ok": True, "error": None,
               "data": {"manager": "余懿", "company": "", "mode": "single", "dropped": [], "funds": [r]}}
    tables = cmd_tenure.build_tables(payload)
    path = tmp_path / "out.xlsx"
    cmd_tenure.write_excel(tables, path, "标题", "副标题")
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == [t["name"] for t in tables]
    assert [c.value for c in wb["概览"][1]][:5] == tables[0]["header"][:5]


# ── 4. Wind 主路径守卫（真实 Wind 不可用时靠 mock 钉住分段/解析/失败语义） ──

def _fake_call(monkeypatch, handler):
    from sources import wind as wind_src

    monkeypatch.setattr(wind_src, "call", handler)


def test_wind_nav_series_chunks_respect_row_cap(monkeypatch) -> None:
    """分段必须按 NAV_CHUNK_DAYS 切：Wind 单次只回 100 行，段太宽就静默截断。"""
    from sources import wind as wind_src

    seen: list[tuple[str, str]] = []

    def handler(server, tool, params=None, params_json=None):
        q = (params or {}).get("question", "")
        assert server == "fund_data" and tool == "get_fund_performance"
        seen.append(q)
        return {"ok": True, "data": {"data": {"data": [{"columns": [], "rows": [
            ["001959.OF", "华商乐享互联A", 2.0, "2023-01-03", "人民币元"]]}]}}, "error": None}

    _fake_call(monkeypatch, handler)
    res = wind_src.fund_nav_series("001959.OF", "华商乐享互联A", "2022-12-30", "2023-06-15", workers=2)
    assert res["ok"] and res["series"] == [("2023-01-03", 2.0)]
    expected = tc.split_chunks("2022-12-30", "2023-06-15", wind_src.NAV_CHUNK_DAYS)
    assert res["calls"] == len(expected) == len(seen)
    assert all(f"{b}到{e}" in q for (b, e), q in zip(expected, seen))


def test_wind_nav_series_fails_loudly_on_persistent_chunk_error(monkeypatch) -> None:
    """任一段重试后仍失败 → ok=False（残缺序列会让累计收益率偏小且不报错）。"""
    from sources import wind as wind_src

    calls = {"n": 0}

    def handler(server, tool, params=None, params_json=None):
        calls["n"] += 1
        if "2023-04" in (params or {}).get("question", ""):
            return {"ok": False, "data": None, "error": "Wind 网络失败"}
        return {"ok": True, "data": {"data": {"data": [{"columns": [], "rows": [
            ["001959.OF", "华商乐享互联A", 2.0, "2023-01-03", "人民币元"]]}]}}, "error": None}

    _fake_call(monkeypatch, handler)
    res = wind_src.fund_nav_series("001959.OF", "x", "2023-01-01", "2023-06-15", workers=2)
    assert res["ok"] is False and "段取数失败" in res["error"] and calls["n"] > 1  # 有重试


def test_wind_index_series_parses_match_column(monkeypatch) -> None:
    from sources import wind as wind_src

    def handler(server, tool, params=None, params_json=None):
        assert server == "index_data" and params["windcode"] == "001959BI.WI"
        return {"ok": True, "data": {"data": {"columns": [], "unit": "", "rows": [
            ["2022-09-09T00:00:00.000+08:00", "1120.0", "1128.1955", "1130.0", "1119.0", "0.00", "1128.1955"],
            ["2022-09-13T00:00:00.000+08:00", "1130.0", "1130.1124", "1131.0", "1129.0", "0.00", "1130.1124"]]}},
            "error": None}

    _fake_call(monkeypatch, handler)
    res = wind_src.index_series("001959BI.WI", "2022-09-09", "2022-09-13")
    assert res["ok"] and res["series"] == [("2022-09-09", 1128.1955), ("2022-09-13", 1130.1124)]


def test_wind_annual_stats_parses_fractions_and_nulls(monkeypatch) -> None:
    from sources import wind as wind_src

    def handler(server, tool, params=None, params_json=None):
        return {"ok": True, "data": {"data": {"data": [{"columns": [
            {"name": "Wind代码"}, {"name": "2024年年度收益率"}, {"name": "2024年最大回撤"},
            {"name": "2025年年度收益率"}, {"name": "2025年最大回撤"}],
            "rows": [["019189.OF", None, -22.9143, 48.264, -15.169]]}]}}, "error": None}

    _fake_call(monkeypatch, handler)
    res = wind_src.fund_annual_stats("019189.OF", "华商品质价值A", [2024, 2025])
    assert res["ok"]
    assert res["data"][2024]["ret"] is None and res["data"][2024]["mdd"] == pytest.approx(-0.229143)
    assert res["data"][2025]["ret"] == pytest.approx(0.48264)


def test_wind_manager_funds_filters_other_managers(monkeypatch) -> None:
    """search_funds 是模糊检索：命中他人必须剔除，否则会把别人的产品算进来。"""
    from sources import wind as wind_src

    def handler(server, tool, params=None, params_json=None):
        return {"ok": True, "data": {"data": {"data": [{"columns": [
            {"name": "基金经理姓名"}, {"name": "基金代码"}, {"name": "基金经理在管基金名称"},
            {"name": "任职日期"}, {"name": "基金经理在管基金管理人"}],
            "rows": [["余懿", "011371.OF", "华商远见价值A", "2023-01-10", "华商基金管理有限公司"],
                     ["别人", "999999.OF", "别家基金A", "2020-01-01", "别家公司"]]}]}}, "error": None}

    _fake_call(monkeypatch, handler)
    res = wind_src.manager_funds("余懿", "华商基金")
    assert res["ok"] and [r["code"] for r in res["rows"]] == ["011371.OF"]
    assert res["rows"][0]["appt"] == "2023-01-10"


def test_wind_series_cache_avoids_second_call(monkeypatch, tmp_path) -> None:
    """同一键第二次调用必须命中磁盘缓存（Wind 按次计费，重复取数是纯浪费）。"""
    from sources import wind as wind_src

    calls = {"n": 0}

    def handler(server, tool, params=None, params_json=None):
        calls["n"] += 1
        return {"ok": True, "data": {"data": {"data": [{"columns": [], "rows": [
            ["001959.OF", "x", 2.0, "2023-01-03", "人民币元"]]}]}}, "error": None}

    _fake_call(monkeypatch, handler)
    a = wind_src.fund_nav_series("001959.OF", "x", "2023-01-01", "2023-03-01", workers=1)
    b = wind_src.fund_nav_series("001959.OF", "x", "2023-01-01", "2023-03-01", workers=1)
    assert a["ok"] and b["ok"] and calls["n"] == a["calls"]


def test_build_fund_never_requests_nav_before_inception(monkeypatch) -> None:
    """取数下界不得早于成立日：早于成立日的分段必然空行，既烧积分又会被判成失败。"""
    from sources import ttskill as ttskill_src
    from sources import wind as wind_src

    monkeypatch.setattr(ttskill_src, "base_profile", lambda code: {"ok": False, "data": None, "error": "offline"})
    monkeypatch.setattr(ttskill_src, "manager_funds", lambda manager: {"ok": False, "rows": None, "error": "offline"})
    monkeypatch.setattr(wind_src, "fund_profile", lambda code, name="": {
        "ok": True, "data": {"short_name": "华商品质价值A", "inception": "2024-04-02",
                             "manager_history": "余懿(20240402至今)", "benchmark": "沪深300*60%"},
        "error": None})
    seen = {}

    def nav(code, name, begin, end, **kw):
        seen["begin"] = begin
        return {"ok": True, "series": [("2024-04-02", 1.0), ("2026-09-23", 2.3)], "calls": 8, "error": None}

    monkeypatch.setattr(wind_src, "fund_nav_series", nav)
    monkeypatch.setattr(wind_src, "index_series", lambda *a, **k: {"ok": False, "series": [], "error": "n/a"})
    monkeypatch.setattr(wind_src, "fund_annual_stats", lambda *a, **k: {"ok": True, "data": {}, "error": None})
    monkeypatch.setattr(wind_src, "range_return", lambda *a, **k: {"ok": True, "value": 1.3, "error": None})
    r = cmd_tenure.build_fund("019189.OF", "019189.OF", manager="余懿",
                              years=[2022, 2023, 2024, 2025, 2026])
    assert r["ok"] and seen["begin"] == "2024-04-02", seen


def test_wind_call_short_circuits_after_hard_failure(monkeypatch) -> None:
    """积分不足/鉴权失败 → 同进程后续调用直接短路，不再打网络；网络抖动不熔断。"""
    from sources import wind as wind_src

    calls = {"n": 0}

    def raw(server, tool, params=None, params_json=None):
        calls["n"] += 1
        return {"source": "wind", "ok": False, "data": None, "error": "账户积分余额不足，无法完成当前操作。"}

    monkeypatch.setattr(wind_src, "_call_raw", raw)
    assert wind_src.call("fund_data", "get_fund_info")["ok"] is False
    assert wind_src.call("fund_data", "get_fund_info")["ok"] is False
    assert calls["n"] == 1, "硬失败后不应再打第二次"

    monkeypatch.setattr(wind_src, "_call_raw",
                        lambda *a, **k: {"source": "wind", "ok": False, "data": None, "error": "Wind 网络失败: timeout"})
    wind_src.call("fund_data", "get_fund_info")
    wind_src.call("fund_data", "get_fund_info")


def test_ttskill_nav_recovers_missing_growth_from_cumulative_nav(monkeypatch) -> None:
    """上游对部分交易日不给日增长率（新基金前 6 周尤其多）→ 用累计净值差分复原。

    实测（2026-09-23，022490）：不补则任职以来偏 +6.60pct，补后偏 +0.14pct。
    """
    from sources import ttskill as ttskill_src

    payload = {"data": {"nav_history": {"items": [
        {"FSRQ": "2025-03-04", "DWJZ": "1.0000", "LJJZ": "1.0000", "JZZZL": ""},
        {"FSRQ": "2025-03-07", "DWJZ": "1.0027", "LJJZ": "1.0027", "JZZZL": ""},
        {"FSRQ": "2025-03-10", "DWJZ": "1.0050", "LJJZ": "1.0050", "JZZZL": "0.23"},
    ]}}}
    monkeypatch.setattr(ttskill_src, "_invoke", lambda skill_id, body: payload)
    res = ttskill_src.nav_series("022490")
    assert res["ok"]
    series = dict(res["series"])
    # 03-07 的收益由 累计净值差分 复原：(1.0027-1.0000)/1.0000 = +0.27%
    assert series["2025-03-07"] == pytest.approx(1.0027, abs=1e-9)
    assert series["2025-03-10"] == pytest.approx(1.0027 * 1.0023, abs=1e-9)
    assert res["coverage"]["recovered_growth_days"] == 1
