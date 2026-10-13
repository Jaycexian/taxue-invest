#!/usr/bin/env python3
"""Query router — maps scenarios to invest-cli commands."""
from typing import Dict, List

SCENARIO_COMMANDS = {
    "stock_a": {
        "name": "A股基本面",
        "commands": ["stock"],
        "example": "invest-cli stock 600519 --json",
    },
    "stock_hk": {
        "name": "港股",
        "commands": ["stock"],
        "example": "invest-cli stock 00700 --json",
    },
    "stock_us": {
        "name": "美股",
        "commands": ["us"],
        "example": "invest-cli us AAPL --json",
    },
    "fund": {
        "name": "基金",
        "commands": ["fund"],
        "example": "invest-cli fund 005827 --json",
    },
    "bond": {
        "name": "债券",
        "commands": ["intent deep bond"],
        "example": "invest-cli intent deep bond 240001",
    },
    "industry": {
        "name": "行业板块",
        "commands": ["stock", "cn report"],
        "example": "invest-cli stock 600519 --json && invest-cli cn report 600519",
    },
    "news": {
        "name": "资讯舆情",
        "commands": ["info"],
        "example": "invest-cli info 茅台 最新",
    },
    "macro": {
        "name": "宏观",
        "commands": ["intent macro"],
        "example": "invest-cli intent macro",
    },
    "enterprise": {
        "name": "企业深度",
        "commands": ["stock", "cn holders", "cn notice"],
        "example": "invest-cli stock 600519 --json && invest-cli cn holders 600519",
    },
}

def detect_scenario(query: str = "", target_type: str = "") -> str:
    """Detect scenario from query or target type."""
    if target_type:
        mapping = {
            "stock": "stock_a",
            "fund": "fund",
            "bond": "bond",
            "industry": "industry",
            "enterprise": "enterprise",
            "news": "news",
            "macro": "macro",
        }
        return mapping.get(target_type, "stock_a")

    q = query.lower()
    if any(k in q for k in ["港股", "hk"]):
        return "stock_hk"
    elif any(k in q for k in ["美股", "us", "aapl", "tsla", "nvda"]):
        return "stock_us"
    elif any(k in q for k in ["基金", "etf", "fund"]):
        return "fund"
    elif any(k in q for k in ["债券", "bond", "国债", "城投"]):
        return "bond"
    elif any(k in q for k in ["行业", "板块", "industry"]):
        return "industry"
    elif any(k in q for k in ["企业", "公司深度", "enterprise"]):
        return "enterprise"
    elif any(k in q for k in ["资讯", "舆情", "新闻"]):
        return "news"
    elif any(k in q for k in ["宏观", "流动性", "fed", "macro"]):
        return "macro"
    else:
        return "stock_a"

def get_route(scenario: str) -> Dict:
    """Get routing info for scenario."""
    return SCENARIO_COMMANDS.get(scenario, {
        "name": "未知",
        "commands": ["stock"],
        "example": "invest-cli stock <代码> --json",
    })
