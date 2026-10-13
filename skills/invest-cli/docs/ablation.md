# 消融实验记录：2026-09-03 invest-cli 数据源补齐

> 原则：每一项抽象/组件/机制都必须能证明其必要性，否则删除。
> 方法：每个机制做 A/B（无它 vs 有它），记录实际调用证据；"考虑过但没加"的复杂度列在末尾并说明删除理由。

## 1. yingmi 适配器数组解析修复（改 1 行 + 7 条单测）

**机制**：`sources/yingmi.py` call() 从只认 `{` 信封 → 兼容 `{`/`[`（对象信封 / 业务对象 / 数组直返）。

| 实验 | 结果 |
|---|---|
| A（修复前） | `GetPopularFund` 报"盈米未返回结构化数据"（2026-09-03 实测复现） |
| B（修复后） | `invest-cli yingmi GetPopularFund --input '{"size":5}'` → ok，返回数组 ✅ |
| 影响面 | 批量/列表类工具全被旧 bug 挡：GetPopularFund、BatchGetFundNavHistory、GetBatchFundPerformance、Search* 等约 20+ 工具 |

**保留判定**：✅ 保留。非新增抽象而是修复既有抽象的错误分支，A/B 差异是"完全不可用 → 可用"，无争议。
**守护**：test_yingmi.py（7 条离线 mock，覆盖数组/信封/业务对象/错误 4 形态 + 非 JSON）。

## 2. cmd_ttskill 透传命令（新 1 文件 + 注册 3 处）

**机制**：`invest-cli ttskill <skill_id> --input '<json>'`，复用既有 `sources/ttskill.invoke_scene()`。

| 实验 | 结果 |
|---|---|
| A（无命令） | 37 个官方包仅 4 个可达（fund 三合一 + GOLD 特判）；MANAGER_INFO/NAV_INFO/实时行情等 30+ 包"声明不可达"，Agent 只能现场翻官方包学调用（本次此前一轮实测踩坑） |
| B（有命令） | `invest-cli ttskill TTFUND_MANAGER_INFO --input '{"manager_name":"谢治宇"}'` → 经理画像+在管5只 ✅；`TTFUND_STOCK_PRICE_QUERY` → 东方财富 19.01 +0.53% @行情时间戳 ✅ |

**保留判定**：✅ 保留。接口面补齐（30+ 能力可达），非逐包封装，成本 1 个薄转发文件。
**为何不逐包收敛**：管理器/净值等高频包未来若被 invest-fund 场景反复消费，再收敛成高层入口（登记 capability-gap.md）；现在收敛是预支复杂度。
**守护**：test_capabilities.py（注册契约 + 边界标注）。

## 3. capabilities 能力发现层（新 1 文件 + 注册 3 处）

**机制**：`invest-cli capabilities [yingmi|ttskill]` 动态列官方能力 + 标注 ✅已收敛/⬜透传/🔒边界外。

| 实验 | 结果 |
|---|---|
| A（无发现层） | 盈米 69 工具只收敛 5 个、ttskill 37 包只封装 4 个，其余靠 Agent 记忆；本次此前一轮确实不知道有 `GetPopularFund`（走了弯路逐包翻 ttskill） |
| B（有发现层） | `capabilities yingmi` 命中 GetPopularFund/SearchFunds 等全部 69 工具 + 描述 + 收敛标注；`capabilities ttskill` 命中 37 包 + 本地描述 ✅ |

**保留判定**：✅ 保留。它是"根治不知道有什么"的最小机制：纯动态清单 + 一个白名单标注字典（~30 行），无第二份数据源。
**为何不做静态能力文档库**：官方工具会增删，静态表必然过期；动态 mcp list/skill list 为准，白名单只标注"收敛到哪"（低频变更）。

## 4. 消融"考虑过但删除"的复杂度

| 候选机制 | 删除理由（无法证明必要性） |
|---|---|
| `invest-cli hot [n]` 热门基金快捷命令 | GetPopularFund 一次透传即够，包一层只省 10 个字符，不构成独立意图 |
| CONDITION_SELECT 参数编码翻译层（自然语言→rsbType/orderField） | 编码含义未经官方释义逐字段核实，金融场景贸然翻译有误差风险；透传 + 官方 example 已可达，待 invest-fund 场景真实反复需求再收敛 |
| ttskill 返回结构归一化（各包 body 层级不同） | 透传语义=原样返回；归一化解释器需逐包维护字段表，纯增复杂度，等有消费方再说 |
| 账户/交易类包高层入口 | 边界纪律：invest-cli 是只读数据分析层，交易执行/持仓资金归账户职责；列为 🔒 标注防止误用 |

## 5. 回归基线

- pytest：67（改造前基线）→ 79 passed（+12：yingmi 7 + capabilities 5），全离线无网络依赖
- 实测冒烟：fund 主链（hithink）不受影响；wind/yingmi/argo 探测全绿
- 数据准确纪律：实时数据带行情时间戳（quote_timestamp）+ 来源标注（source_label），快照链整单回退不混字段（既有 route.py 约束，未改动）

---

# 消融实验记录：2026-09-24 原文级数据补齐（sec 五动作 + cn 收敛）

> 背景：drillr 类「原文级 + 带溯源」能力对照评估后，确认其中大部分可由 invest 自有源
> 复刻（免费官方通道），无需采购。本次按「官方免费源能覆盖的先自建」原则落地。

## 1. SEC EDGAR 从「年报快照」扩到五个动作（改 sec_edgar.py + cmd_sec.py）

**机制**：快照（24 指标 + 5 年序列 + 可选季度）、全文检索、Form 4 内部人、8-K 事件、13F 持仓。

| 实验 | 结果 |
|---|---|
| A（扩展前） | 9 个指标 + 最新一期；三关的 5 年趋势表、治理核查（内部人/股东）、事件节奏、原文检索全缺，只能靠 yfinance/Wind 或 web 搜索 |
| B（扩展后） | AAPL 实测：24 指标 + FY2021-2025 序列（营收 3658→4162 亿）；"supply chain" 命中 23 条 10-K 并给出原文段落；Form 4 逐笔（含 M/F 行）；8-K items 逐事项（2.02 业绩/5.02 高管变动）；BRK 13F 29 个持仓 $2992 亿、Bridgewater 997 个持仓（名称检索） |

**保留判定**：✅ 保留。全部走 SEC 官方免费通道（companyfacts/submissions/full-text/Archives），零新增依赖、零 key。
**实测踩坑（已修）**：13F 申报人解析曾把 `BRK` 落到无关机构（Integral Derivatives 的 2018 年表）→ 加「名字必须对得上 + 确实报过 13F」双校验 + 类别股回退（BRK→BRK-B 同 CIK）；13F 信息表存在 `<ns1:informationTable>` 前缀命名空间写法 → 改为解析后判根元素名；13F 封面日期是 MM-DD-YYYY → 归一到 ISO 才能判「2023 年起美元口径」。
**守护**：test_sec_edgar.py（+13 条离线：序列/派生 FCF/季度 Q4 推导/Form 4/13F 命名空间/日期归一/前缀歧义）。

## 2. CN 公告检索与 westock 高频五收敛（新 md_parse.py + cmd_cn.py）

**机制**：`invest-cli cn notice|consensus|holders|disclosure|report`；`notice --search` 走 argo 的 cninfo 引擎。

| 实验 | 结果 |
|---|---|
| A（收敛前） | CN 侧无公告检索能力；一致预期/股东/披露日历/研报只能 `westock <子命令>` 透传，输出是 markdown 表格（agent 要自己解析），且多代码逗号串不归一（`600519,000858` 直接被服务端拒绝） |
| B（收敛后） | `cn notice 600519` → 929 条公告结构化（total/page/rows）；`cn notice --search "会计政策变更"` → 巨潮 PDF 直链；`cn consensus 600519` → 目标价 1660.60 + 三年预测；`cn holders` 十大股东；`cn disclosure 600519,000858` 双标的成功 |

**保留判定**：✅ 保留。五个都是分析链路反复消费的（三关治理核查 / 一致预期工作流），透传仍留长尾。
**为何不直连巨潮**：argo 的 cninfo 引擎已覆盖关键词检索（免维护），westock 覆盖个股清单（可翻页）；直连接口是非官方接口，会烂，先不引入。
**守护**：test_cn_cmd.py（+12 条：解析 fail-open、信封契约、argo 降级路径、参数边界）、capabilities westock 登记。

## 3. 消融"考虑过但没做"的部分

| 候选机制 | 不做理由 |
|---|---|
| 直连巨潮 `hisAnnouncement/query` 抓取 | 非官方接口、会变；argo cninfo 引擎 + westock 清单已覆盖两个主场景，等真不够再说 |
| 13F「谁持有某股」反向查询 | 需要全市场 13F 索引（SEC 季度数据集为数百 MB 级下载），单次问答不值得；先做「机构→持仓」方向 |
| 电话会转写/摘要 | 语料无稳定免费源，是采购（drillr 类）的核心差异点，自建=复刻产品 |
| 全文检索默认拉全部命中的段落 | 每条一次文档请求；默认只抽前 5 条，避免一次检索打十几 MB |

## 4. 回归基线（2026-09-24）

- pytest：350 passed + 1 skipped（改造前）→ **373 passed + 1 skipped**（+23：sec_edgar 13 + cn/md 12 − 1 条既有用例改写）
- 真机冒烟：`sec AAPL`（快照/检索/内部人/事件）、`sec BRK --holdings`、`cn notice/consensus/holders/disclosure/report`、`cn notice --search` 全部 ok
- 边界纪律未变：新能力**不进快照链**（`route.pick` 只认 stock/fund/us/screen），按需直取；`sec` 四动作互斥
