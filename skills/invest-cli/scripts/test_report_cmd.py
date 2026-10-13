#!/usr/bin/env python3
"""cmd_report 的反回归测试——下载跳过、清单缓存、并发保序，全程无网络。"""
from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import cmd_report  # noqa: E402

_ITEM = {"id": "AN202501123456789012", "title": "某基金2025年第4季度报告",
         "date": "2026-01-20"}


def _fake_guarded_open(body: bytes):
    """guarded_open 的假实现：返回带 read() 的响应体，并记录是否被调。"""

    @contextlib.contextmanager
    def _cm(req, **kw):
        yield _FakeResp(body)

    return _cm


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body


def test_download_one_skips_complete_existing_pdf(tmp_path, monkeypatch) -> None:
    """目标已是完整 PDF（%PDF 头 + 达阈值）时必须跳过——重跑「补齐」不得全量重下。"""
    target = tmp_path / f"{_ITEM['date']}_{_ITEM['title']}.pdf"
    pdf = b"%PDF-1.7" + b"x" * cmd_report._MIN_VALID_PDF_BYTES
    target.write_bytes(pdf)
    monkeypatch.setattr(cmd_report, "guarded_open",
                        _fake_guarded_open(b""))  # 被调即失败：不该发起网络请求
    r = cmd_report.download_one(_ITEM, tmp_path)
    assert r["ok"] is True and r.get("skipped") is True
    assert r["file"] == str(target) and r["size"] == len(pdf)
    assert r["error"] is None


def test_download_one_redownloads_truncated_file(tmp_path, monkeypatch) -> None:
    """半截文件（<阈值）不得被当成已下载——重跑必须重新取。"""
    target = tmp_path / f"{_ITEM['date']}_{_ITEM['title']}.pdf"
    target.write_bytes(b"%PDF-1.7 broken")
    pdf = b"%PDF-1.7" + b"y" * cmd_report._MIN_VALID_PDF_BYTES
    monkeypatch.setattr(cmd_report, "guarded_open", _fake_guarded_open(pdf))
    r = cmd_report.download_one(_ITEM, tmp_path)
    assert r["ok"] is True and "skipped" not in r
    assert target.read_bytes() == pdf


def test_fetch_reports_uses_list_cache(tmp_path, monkeypatch) -> None:
    """清单是结构类数据：5min 内重复列出不得重付 API 往返。"""
    monkeypatch.setenv("INVEST_CLI_CACHE_DIR", str(tmp_path))
    payload = json.dumps({"Data": [
        {"ID": "ID1", "TITLE": "T1", "PUBLISHDATEDesc": "2026-01-20"},
        {"ID": "ID2", "TITLE": "T2", "PUBLISHDATEDesc": "2025-10-21"},
    ]}).encode("utf-8")
    hits = []

    def _guard(req, **kw):
        hits.append(req)
        return _fake_guarded_open(payload)(req, **kw)

    monkeypatch.setattr(cmd_report, "guarded_open", _guard)
    r1 = cmd_report.fetch_reports("005827")
    assert len(r1) == 2 and len(hits) == 1
    r2 = cmd_report.fetch_reports("005827")
    assert len(hits) == 1, "第二次列出仍发了网络请求——清单缓存未生效"
    assert r2 == r1


def test_select_latest_year_all() -> None:
    reports = [{"date": f"20{y}-{m:02d}-01", "title": "t", "id": str(y) + str(m)}
               for y, m in [(25, 10), (25, 7), (25, 4), (24, 10)]]
    assert [r["id"] for r in cmd_report._select(reports, 2, 0, False)] == ["2510", "257"]
    assert len(cmd_report._select(reports, 0, 2025, False)) == 3
    assert len(cmd_report._select(reports, 0, 0, True)) == 4
    assert cmd_report._select(reports, 0, 0, False) == []
