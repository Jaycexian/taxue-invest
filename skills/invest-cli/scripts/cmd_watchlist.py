"""watchlist 子命令：本地自选股（自含，不依赖东财账户）。

存储：<state_root>/watchlist.json（本地，无版权问题）。
    state_root() 由 _common 统一决定（可用 INVEST_CLI_STATE_DIR 覆盖）——
    这是**用户数据**而非缓存，不能落在会被系统清理的 ~/Library/Caches 下，
    也不该让本模块自己再记一份路径规则。
    旧位置 ~/.cache/invest-cli/watchlist.json 仍会被读一次并自动迁移。
行情预览：走 route.fetch（A 股/基金快照链），按 --type 选 stock 或 fund。

用法:
    invest-cli watchlist add <code> [--name N] [--type fund|stock]
    invest-cli watchlist remove <code>
    invest-cli watchlist list [--with-quote] [--json]
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from _common import state_root

# 2026-09 前的落点（缓存目录下），仅用于一次性迁移
_LEGACY_FILE = Path.home() / ".cache" / "invest-cli" / "watchlist.json"


def watch_file() -> Path:
    return state_root() / "watchlist.json"


def _migrate_legacy() -> None:
    """把 2026-09 前落在缓存目录下的自选股搬到 state_root（旧文件保留不删）。"""
    if watch_file().is_file() or not _LEGACY_FILE.is_file():
        return
    try:
        data = json.loads(_LEGACY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if isinstance(data, list) and data:
        _save(data)


def _load() -> list[dict]:
    _migrate_legacy()
    path = watch_file()
    if not path.is_file():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        # **不要把「读不出来」当成「空列表」**。自选股是用户资产：文件被
        # 截断/损坏时返回 []，随后任意一次 add 就会用只含新标的的内容
        # 整体覆盖写回，旧标的静默永久消失（实测复现）。
        # 损坏时把坏文件留成 .corrupt 备份并让调用方明确看到，
        # 而不是静默丢数据。
        _quarantine(path)
        return []


def _quarantine(path) -> None:
    """把损坏的自选股文件改名留档，不删除。best-effort，不抛。

    留档名带序号：固定用 `.corrupt` 会在**第二次**损坏时把上一份备份覆盖掉，
    而上一份很可能就是唯一还含用户原始标的的那份（实测两次损坏后
    备份 #1 内容被 #2 顶掉）。带时间戳/序号才不会互相踩。
    """
    try:
        base = path.with_suffix(path.suffix + ".corrupt")
        if not base.exists():
            path.rename(base)
            return
        for i in range(1, 100):
            cand = path.with_suffix(f"{path.suffix}.corrupt.{i}")
            if not cand.exists():
                path.rename(cand)
                return
    except OSError:
        pass


def _save(items: list[dict]) -> None:
    """原子写：先写 pid 临时文件再 replace。

    为什么必须原子：CLI 每进程独立，直接 `open(w)` 覆盖时若中途崩溃
    （断电/被杀），留下的是**半截 JSON**——下一次 `_load` 读到它，
    解析失败返回 []，再写就把用户全部自选股换成一行。自选股是本地
    唯一的用户数据，这里没有上游可以重取。

    做法与 `_common.cache_set` 一致（tmp 带 pid + replace），不再各写一套。
    """
    path = watch_file()
    # 路径闸：state_root/watch_file 可被 env 与测试覆盖，但不允许「..」穿越片段；
    # tmp 必须与目标同目录、且名字是「目标名 + pid」派生，动态拼名前先拦
    if ".." in path.parts:
        raise ValueError(f"自选股路径含穿越片段，拒绝写入: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    if tmp.parent != path.parent or not tmp.name.startswith(path.name + "."):
        raise ValueError(f"自选股临时路径异常，拒绝写入: {tmp}")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def add(code: str, name: str = "", typ: str = "") -> dict:
    _guard_pending_recovery()
    items = _load()
    code = code.strip()
    if any(i.get("code") == code for i in items):
        return {"source": "watchlist", "ok": False, "data": None, "error": f"{code} 已在自选" }
    items.append({"code": code, "name": name, "type": typ})
    _save(items)
    return {"source": "watchlist", "ok": True, "data": {"code": code, "added": True}, "error": None}


def _pending_backups() -> list:
    """列出因损坏而被留档的自选股文件。"""
    try:
        path = watch_file()
        if not path.parent.is_dir():
            return []
        return sorted(
            p for p in path.parent.glob(f"{path.name}.corrupt*") if p.is_file()
        )
    except OSError:
        return []


def _guard_pending_recovery() -> None:
    """有留档未恢复时，**拒绝**任何写操作。

    为什么必须拦：只留档不拦写入，用户感知到的仍然是「自选股没了」——
    损坏 → add → 写回一条 → 原数据被覆盖（实测）。留档只有被用户看见、
    并且写入被拦住，才能真正阻止覆盖。自选股没有上游可重取，这一步不可省。
    恢复方式写进报错里，用户照做即可。
    """
    backups = _pending_backups()
    if not backups:
        return
    names = "、".join(p.name for p in backups[:3])
    more = f" 等 {len(backups)} 份" if len(backups) > 3 else ""
    raise SystemExit(
        f"自选股文件损坏，已留档为 {names}{more}。\n"
        f"为避免覆盖你原有的自选股，本次写入已中止。\n"
        f"请先手动检查并恢复备份（能解析的话改回 {watch_file().name}），"
        f"或确认不要了再删除这些 .corrupt 文件。"
    )


def remove(code: str) -> dict:
    _guard_pending_recovery()
    items = _load()
    new = [i for i in items if i.get("code") != code.strip()]
    if len(new) == len(items):
        return {"source": "watchlist", "ok": False, "data": None, "error": f"{code} 不在自选" }
    _save(new)
    return {"source": "watchlist", "ok": True, "data": {"code": code, "removed": True}, "error": None}


def _quote(code: str, typ: str = "") -> str:
    try:
        from sources.route import fetch

        kind = "fund" if (typ or "").lower() == "fund" else "stock"
        market = None
        if kind == "stock":
            market = "hk" if len((code or "").strip()) == 5 else "a"
        r = fetch(kind, code, market=market)
        if r.get("ok"):
            d = r.get("data") or {}
            inner = d.get("data") if isinstance(d.get("data"), dict) else {}
            last = (
                (d.get("quote") or {}).get("last")
                or inner.get("单位净值")
                or inner.get("最新价")
                or inner.get("收盘价")
                or ""
            )
            return f"{d.get('name', code)} {last}".strip()
    except Exception:
        pass
    return code


def run(action: str, code: str = "", name: str = "", typ: str = "",
        with_quote: bool = False, as_json: bool = False) -> int:
    # 空代码必须挡在写盘之前。实测：`watchlist add ""` 会往自选里写一条
    # `{"code": "", ...}` 并以 rc=0 返回 `{'code': '', 'added': True}`——
    # 既污染用户数据（自选是持久数据，不是缓存），又让调用方以为成功了。
    if action in ("add", "remove") and not str(code or "").strip():
        print(f"watchlist {action}: 缺少标的代码", file=sys.stderr)
        return 1
    if action == "add":
        out = add(code, name, typ)
    elif action == "remove":
        out = remove(code)
    elif action == "list":
        items = _load()
        # 损坏留档必须在 list 里**说出来**：否则用户看到「自选列表为空」，
        # 合理地以为从没存过，下一次 add 就把原数据覆盖掉。
        backups = _pending_backups()
        if backups:
            names = "、".join(p.name for p in backups[:3])
            more = f" 等 {len(backups)} 份" if len(backups) > 3 else ""
            msg = (f"自选股文件损坏，内容已留档为 {names}{more}；"
                   f"请手动检查并恢复后再做增删。")
            if as_json:
                print(json.dumps(
                    {"source": "watchlist", "ok": False, "data": None,
                     "error": msg, "corrupt_backups": [p.name for p in backups]},
                    ensure_ascii=False, indent=2))
                return 1
            print(f"watchlist: {msg}", file=sys.stderr)
            return 1
        rows = []
        for it in items:
            row = {"code": it.get("code"), "name": it.get("name", ""), "type": it.get("type", "")}
            if with_quote:
                row["quote"] = _quote(it.get("code", ""), it.get("type", ""))
            rows.append(row)
        out = {"source": "watchlist", "ok": True, "data": {"items": rows}, "error": None}
    else:
        print("用法: invest-cli watchlist <add|remove|list> ...", file=sys.stderr)
        return 1

    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        # 退出码必须与信封一致：此前 `--json` 分支无论 ok 与否都 return 0，
        # 于是「已在自选」「不在自选」这些失败在机器可读路径上被读成成功
        # （终端路径反而正确地 return 1）——同一个结论两条路径相反。
        return 0 if out.get("ok") else 1
    else:
        if not out.get("ok"):
            print(f"watchlist: {out.get('error')}", file=sys.stderr)
            return 1
        if action == "list":
            items = out["data"].get("items", [])
            if not items:
                print("自选列表为空")
            else:
                print("自选股:")
                for it in items:
                    print(f"  {it.get('code')}  {it.get('name', '')}  {it.get('type','')}  {it.get('quote','')}")
        else:
            # 终端分支此前直接 print(dict)，输出 Python repr（单引号、非 JSON），
            # 与 `list` 的表格、与 `--json` 的合法 JSON 三方不一致。
            # 这里给一行人话；机器可读的统一走 `--json`。
            verb = "已加入自选" if action == "add" else "已从自选移除"
            print(f"{verb}: {(out.get('data') or {}).get('code', '')}")
    return 0
