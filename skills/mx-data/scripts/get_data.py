#!/usr/bin/env python3
"""Unified data access — in-process wrapper around invest-cli route layer.

Does NOT call APIs directly. Does NOT manage auth.
Calls route.fetch / cmd_* helpers in-process: no subprocess spawn,
no sys.executable mismatch, venv deps (yfinance) always present.

Fallback: subprocess to `invest-cli` binary if in-process import fails.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from query_router import detect_scenario, get_route

INVEST_CLI_SCRIPTS = Path.home() / ".agents" / "skills" / "invest-cli" / "scripts"

# scenario -> (route kind, market, fetch helper)
_FETCHERS = {
    "stock_a": ("stock", "a", "stock"),
    "stock_hk": ("stock", "hk", "stock"),
    "stock_us": ("us", None, "us"),
    "fund": ("fund", None, "fund"),
}


def _ensure_scripts_on_path() -> bool:
    p = str(INVEST_CLI_SCRIPTS)
    if INVEST_CLI_SCRIPTS.is_dir():
        if p not in sys.path:
            sys.path.insert(0, p)
        return True
    return False


def _fetch_in_process(scenario: str, code: str) -> dict:
    """Call invest-cli fetch helpers without spawning a process."""
    from sources.route import fetch, unwrap_snapshot

    kind, market, _ = _FETCHERS[scenario]
    if kind == "stock":
        from cmd_stock import _looks_like_hk

        market = "hk" if _looks_like_hk(code) else "a"
    res = fetch(kind, code, market=market)
    return {"status": "ok" if res.get("ok") else "error",
            "snapshot": unwrap_snapshot(res) if res.get("ok") else None,
            "message": None if res.get("ok") else res.get("error"),
            "source": res.get("source"), "cached": bool(res.get("cached"))}


def _fetch_subprocess(cmd: str) -> dict:
    try:
        result = subprocess.run(
            ["invest-cli"] + cmd.split(),
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            try:
                return {"status": "ok", "snapshot": json.loads(result.stdout)}
            except json.JSONDecodeError:
                return {"status": "ok", "snapshot": result.stdout}
        return {"status": "error", "message": result.stderr}
    except FileNotFoundError:
        return {"status": "error", "message": "invest-cli not found in PATH"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "invest-cli timeout"}


def _scenario_cmd(scenario: str, code: str, query_str: str) -> str:
    if scenario in ("stock_a", "stock_hk"):
        return f"stock {code} --json"
    if scenario == "stock_us":
        return f"us {code} --json"
    if scenario == "fund":
        return f"fund {code} --json"
    if scenario == "bond":
        return f"intent deep bond {code}"
    if scenario == "industry":
        return f"stock {code} --json"
    if scenario == "news":
        return f"info {query_str}"
    if scenario == "macro":
        return "intent macro"
    if scenario == "enterprise":
        return f"stock {code} --json"
    return f"stock {code} --json"


def get_data(code="", metrics=None, scenario="", query_str="", target_type=""):
    """Unified data access via invest-cli (in-process first)."""
    if not scenario:
        scenario = detect_scenario(query_str, target_type or "")
    if not code and query_str and scenario in _FETCHERS:
        import re as _re

        m = _re.search(r"[A-Za-z]{1,5}(?:\.[A-Za-z]{1,3})?|\d{5,6}", query_str)
        if m:
            code = m.group(0)
    route = get_route(scenario)
    cmd = _scenario_cmd(scenario, code, query_str)

    result = None
    via = "in-process"
    if scenario in _FETCHERS and _ensure_scripts_on_path():
        try:
            result = _fetch_in_process(scenario, code)
        except Exception as e:  # noqa: BLE001 —— 降级不断链
            result = None
            via = f"subprocess (in-process failed: {type(e).__name__})"
    if result is None:
        via = "subprocess" if via == "in-process" else via
        raw = _fetch_subprocess(cmd)
        result = {"status": raw["status"],
                  "snapshot": raw.get("snapshot") or raw.get("data"),
                  "message": raw.get("message")}

    return {"status": result["status"], "scenario": scenario,
            "command": f"invest-cli {cmd}", "via": via,
            "route_info": route, "data": result.get("snapshot"),
            "source": result.get("source"), "cached": result.get("cached"),
            "error": result.get("message")}


def main():
    parser = argparse.ArgumentParser(description="Unified data access via invest-cli")
    parser.add_argument("--type", choices=["stock", "fund", "bond", "industry", "enterprise", "news", "macro"], help="Target type")
    parser.add_argument("--code", default="", help="Security/fund/bond code")
    parser.add_argument("--query", default="", help="Natural language query")
    parser.add_argument("--metrics", nargs="*", default=[], help="Metrics to fetch (informational)")
    args = parser.parse_args()

    query_str = args.query or args.code
    result = get_data(code=args.code, metrics=args.metrics, query_str=query_str,
                      target_type=args.type or "")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
