# invest-cli-dsh

DeepSeek Harness 插件：把 [invest-cli](../SKILL.md) 的 21 个子命令收敛为**单个** `invest` 工具。

## 安装

```sh
dsh plugin --profile web add "file:$HOME/.agents/skills/invest-cli/packages/dsh-plugin"
```

重启 dsh web 后生效。

## 为什么是单入口

工具定义（名称 + 描述 + schema）**每轮请求都注入上下文**，且压缩清不掉。所以
「注册多少个工具」直接等于常驻 token 开销：

| 形态 | 常驻开销 | 能力面 |
|---|---:|---|
| 21 个具名工具 | ≈4K token/轮 | 全 |
| **单入口 `invest`（本插件）** | **≈0.5K token/轮** | 全（21 子命令全可达） |
| 挂 MCP 服务 | 同「具名工具」量级 | 全 |

本 CLI 的调用面是**平摊**的（会话日志实测 top5 是 stock / sec / us / cn / tenure，
各占 6–9%），没有哪条命令够格做「专属工具」。所以收敛成一个入口、把命令清单放进
systemPrompt 的紧凑一节（≈150 token），比铺开 21 个 schema 划算得多。

## 用法

```
invest(command="stock",  args=["600519"])                     # A股快照
invest(command="fund",   args=["005827"])                     # 公募基金
invest(command="us",     args=["AAPL"])                       # 美股快照
invest(command="quote",  args=["600519,00700,AAPL"])          # 免鉴权行情（最快）
invest(command="sec",    args=["AAPL", "--insiders"])          # SEC EDGAR 原文级
invest(command="cn",     args=["notice", "600519"])            # A股公告/一致预期/股东
invest(command="tenure", args=["001959", "--manager", "余懿"])  # 经理任职期复盘（慢）
invest(command="capabilities")                                 # 不确定长尾命令怎么用时先问它
```

`json` 默认 `true`（给模型结构化数据）；置 `false` 拿终端表格，适合直接展示给人看。

## 契约

- **真源不变**：CLI 脚本仍是唯一实现，本插件只做「参数校验 + 有界超时 + 截断」。
- **入口解析**：`config.cliPath` → `~/.local/bin/invest-cli`（wrapper，自带 venv 选择）
  → `<skill>/.venv/bin/python` → 系统 `python3`。用 wrapper 时会显式传
  `INVEST_CLI_ROOT`，省掉它在 cron / 换 HOME 场景下的目录搜索不确定性。
- **超时分档**：默认 60s；`tenure` 180s、`report` 120s、`sec` 90s（冷跑实测
  tenure >60s、sec 3.4s、report 要下 PDF）。
- **失败即失败**：非零退出抛错并带 stderr 尾部；**rc=0 但输出为空也算失败**
  （与 CLI 自身纪律一致，避免「假成功」被当成空结果）。
- **输出截断**：默认 24000 字符，超出加 `…` 标记（`screen --json` 这类载荷可达 88KB）。

## 依赖

- Node.js ≥ 18
- Python 3.10+；skill 自带 venv 时 `us` 走 yfinance 全量快照，无 venv 时回退系统
  `python3`（`us` 降级为腾讯行情 / Bitget 报价）

## 自定义

`cordis.patch.yml` 里同 id + name 才能替换：

```yaml
- insert:
    - id: invest-cli
      name: '@taxueseek/invest-cli-dsh'
      config:
        cliPath: /custom/path/invest-cli
        timeoutMs: 90000
        maxOutputChars: 40000
        timeouts:
          tenure: 300000
        json: true
```

## 测试

```sh
node --test test/index.test.js
```
