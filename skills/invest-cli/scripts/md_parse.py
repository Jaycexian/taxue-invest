"""westock 输出的 markdown 结构解析（表格 / 标题 / 键值行）。

为什么需要：westock CLI 出的是**给人读的** markdown（`#### 标题` + 表格 +
「共 N 条（第 X 页…）」分页行）。透传对终端友好，但 agent 消费要的是结构化行
（按字段取值、算数、喂给下游）。本模块只做这一件事，不做任何取数。

两条纪律：
1. **fail-open**：解析失败/格式变了，调用方必须能退回原文（`raw` 始终保留），
   绝不让「解析器看不懂」变成「数据丢了」。
2. **不猜**：只认明确的形状（管道表格的 header + 分隔行、单行 `key: value`、
   分页行、`####` 标题）；其余非空行原样进 `notes`，不硬塞进字段。
"""
from __future__ import annotations

import re

_SEP_ROW = re.compile(r"^\|[\s:\-|]+\|$")
_PAGINATION = re.compile(r"^共\s*(\d+)\s*条（第\s*(\d+)\s*页，每页\s*(\d+)\s*条，共\s*(\d+)\s*页）")
_KV = re.compile(r"^([^|：:]{1,24})[：:]\s*(\S.*)$")


def _split_row(line: str) -> list[str]:
    """拆一行管道表格；去掉首尾空单元格（markdown 的边框管道）。"""
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return cells


def _is_table_start(lines: list[str], i: int) -> bool:
    return (i + 1 < len(lines) and lines[i].lstrip().startswith("|")
            and _SEP_ROW.match(lines[i + 1].strip()) is not None)


def parse(text: str) -> dict:
    """markdown → {"title", "meta", "tables": [{"header", "rows"}], "notes"}。

    rows 是 list[dict]（按 header 键）；header 为空或行宽对不上时退化为
    `{"_": [...]}` 原样保留单元格，避免丢列。
    """
    lines = (text or "").splitlines()
    title = ""
    meta: dict[str, object] = {}
    notes: list[str] = []
    tables: list[dict] = []
    i = 0
    while i < len(lines):
        raw_line = lines[i]
        line = raw_line.strip()
        if not line:
            i += 1
            continue
        if line.startswith("#"):
            if not title:
                title = line.lstrip("#").strip()
            else:
                notes.append(line.lstrip("#").strip())
            i += 1
            continue
        m = _PAGINATION.match(line)
        if m:
            meta.update({
                "total": int(m.group(1)), "page": int(m.group(2)),
                "page_size": int(m.group(3)), "pages": int(m.group(4)),
            })
            i += 1
            continue
        if _is_table_start(lines, i):
            header = _split_row(lines[i])
            rows: list[dict] = []
            i += 2  # 跳过 header 与分隔行
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                cells = _split_row(lines[i])
                if len(cells) == len(header):
                    rows.append({h or f"col{k}": v for k, (h, v) in enumerate(zip(header, cells))})
                else:
                    rows.append({"_": cells})
                i += 1
            tables.append({"header": header, "rows": rows})
            continue
        m = _KV.match(line)
        if m and not line.startswith("|"):
            key, val = m.group(1).strip().strip("*"), m.group(2).strip()
            if key and val:
                meta[key] = val
                i += 1
                continue
        notes.append(line)
        i += 1
    return {"title": title, "meta": meta, "tables": tables, "notes": notes,
            "raw": text or ""}
