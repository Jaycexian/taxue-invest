/*
 * @taxueseek/invest-cli-dsh — 把 invest-cli 收敛成**单个** invest 工具。
 *
 * 形态选择：单入口 + CLI 单发（不是 21 个具名工具，也不挂 MCP）。
 *   · 工具定义（名称+描述+schema）每轮请求都注入上下文且压缩清不掉；
 *     21 个具名工具 ≈4K token/轮 的常驻开销，而本 CLI 的调用面是**平摊**的
 *     （会话日志实测 top5 是 stock/sec/us/cn/tenure，各占 6–9%），没有哪条
 *     够格做「专属工具」；
 *   · 单入口把常驻开销压到 ≈0.5K/轮，能力面一个不少（21 子命令全可达）。
 *   与 Argo 的取舍同源（那边是 2 个原生工具 + MCP 默认关）。
 *
 * 无 @deepseek-ai/* 依赖：只用公开的 ctx.tools / ctx.systemPrompt（与 argo-dsh 同）。
 *
 * 真源不变：CLI 脚本仍是唯一实现，本插件只做「参数校验 + 有界超时 + 截断」。
 */

import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

/** Cordis 插件名 —— 必须与 cordis.patch.yml 里的行 id 一致。 */
export const name = 'invest-cli'

/** 只依赖 tools；systemPrompt 是可选增强，缺席时跳过（headless 也能加载）。 */
export const inject = ['tools']

const text = (value) => [{ type: 'text', text: value }]
const clip = (value, maximum) =>
  value.length <= maximum ? value : `${value.slice(0, maximum - 1)}…`

/** invest-cli 的 21 个子命令（与 scripts/invest_cli.py 的 dispatch 表一一对应）。 */
export const COMMANDS = Object.freeze([
  'stock', 'fund', 'us', 'quote', 'kline',
  'sec', 'fmp', 'fund-class', 'cn', 'screen',
  'westock', 'datasources', 'wind', 'yingmi', 'ttskill',
  'capabilities', 'intent', 'info', 'watchlist', 'tenure', 'report',
])

/**
 * 慢命令的超时预算。tenure 要串「在管清单 → 逐只净值分段抓取 → 基准指数 →
 * 分年度统计」，冷跑实测 >60s（Wind 净值单次硬顶 100 行，6 只基金 53 次调用）；
 * report 要下载 PDF；sec 的 companyfacts 单文件约 3.7MB。默认 60s 会把它们误杀。
 */
const COMMAND_TIMEOUTS = Object.freeze({
  tenure: 180_000,
  report: 120_000,
  sec: 90_000,
  screen: 60_000,
  cn: 60_000,
  fmp: 60_000,
})

export const DEFAULTS = Object.freeze({
  timeoutMs: 60_000,
  maxOutputChars: 24_000,
  /** 默认给模型结构化输出；置 false 则拿终端表格（人读形态）。 */
  json: true,
  /** 单次调用的参数个数/长度上限（防呆；argv 直传不经 shell，无注入面）。 */
  maxArgs: 32,
  maxArgChars: 512,
})

export function expandHome(p) {
  if (p === '~') return homedir()
  return p.startsWith('~/') ? join(homedir(), p.slice(2)) : p
}

/** 本包所在位置 → invest-cli skill 根目录（packages/dsh-plugin/dsh/ → ../../..）。 */
export function skillRootFrom(moduleUrl) {
  const here = dirname(fileURLToPath(moduleUrl))
  return resolve(here, '..', '..', '..')
}

/**
 * 入口解析：优先 wrapper（它自己会选 venv，且认得 INVEST_CLI_ROOT），
 * 其次 skill 自带 venv 直跑脚本，最后系统 python3。
 *
 * 为什么把 INVEST_CLI_ROOT 显式传进去：wrapper 的三段目录搜索在
 * cron / sudo / 换 HOME 的场景下会落空（它自己的注释记着这个坑）。插件知道
 * skill 在哪，就顺手告诉它，省掉那段搜索的不确定性。
 */
export function resolveEntry(config = {}, deps = {}) {
  const skillRoot = expandHome(config.skillRoot ?? deps.skillRoot ?? '')
  const exists = deps.exists ?? existsSync
  if (config.cliPath) {
    return { command: expandHome(config.cliPath), args: [], env: rootEnv(skillRoot) }
  }
  const wrapper = join(homedir(), '.local', 'bin', 'invest-cli')
  if (exists(wrapper)) {
    return { command: wrapper, args: [], env: rootEnv(skillRoot) }
  }
  const script = join(skillRoot, 'scripts', 'invest_cli.py')
  const venvPython = join(skillRoot, '.venv', 'bin', 'python')
  const python = exists(venvPython) ? venvPython : 'python3'
  return { command: python, args: [script], env: {} }
}

function rootEnv(skillRoot) {
  return skillRoot ? { INVEST_CLI_ROOT: skillRoot } : {}
}

/** 单次命令的超时（按命令分档，见 COMMAND_TIMEOUTS）。 */
export function pickTimeout(command, config = {}) {
  const perCommand = config.timeouts ?? {}
  const explicit = perCommand[command]
  if (Number.isFinite(explicit) && explicit > 0) return explicit
  const byClass = COMMAND_TIMEOUTS[command]
  if (Number.isFinite(byClass)) {
    const base = config.timeoutMs ?? DEFAULTS.timeoutMs
    return Math.max(base, byClass)
  }
  const base = config.timeoutMs ?? DEFAULTS.timeoutMs
  return Number.isFinite(base) && base > 0 ? base : DEFAULTS.timeoutMs
}

/** 参数校验：全部转成字符串，丢掉空串，拒绝 NUL（spawn 会抛 ValueError）。 */
export function sanitizeArgs(args, config = {}) {
  const maxArgs = config.maxArgs ?? DEFAULTS.maxArgs
  const maxArgChars = config.maxArgChars ?? DEFAULTS.maxArgChars
  if (args === undefined || args === null) return []
  if (!Array.isArray(args)) throw new Error('invest: args must be an array of strings')
  if (args.length > maxArgs) throw new Error(`invest: too many args (max ${maxArgs})`)
  const out = []
  for (const raw of args) {
    if (raw === undefined || raw === null) continue
    if (typeof raw === 'object') throw new Error('invest: args entries must be strings')
    const s = String(raw)
    if (s === '') continue
    if (s.includes('\u0000')) throw new Error('invest: args must not contain NUL bytes')
    if (s.length > maxArgChars) throw new Error(`invest: arg too long (max ${maxArgChars})`)
    out.push(s)
  }
  return out
}

/** 组装 argv（命令 + 参数 + --json）。 */
export function buildArgv(command, args, wantJson) {
  const argv = [command, ...sanitizeArgs(args)]
  if (wantJson) argv.push('--json')
  return argv
}

/** 跑一次 CLI；返回 {code, stdout, stderr}，超时/启动失败也走同一形状。 */
export function runCli(entry, argv, timeoutMs, deps = {}) {
  const spawnFn = deps.spawn ?? spawn
  return new Promise((resolvePromise) => {
    let child
    try {
      child = spawnFn(entry.command, [...entry.args, ...argv], {
        stdio: ['ignore', 'pipe', 'pipe'],
        env: { ...process.env, ...entry.env },
      })
    } catch (err) {
      resolvePromise({ code: -1, stdout: '', stderr: `spawn failed: ${err?.message ?? err}` })
      return
    }
    let stdout = ''
    let stderr = ''
    const timer = setTimeout(() => {
      child.kill('SIGKILL')
      resolvePromise({
        code: -1,
        stdout,
        stderr: `invest-cli ${argv[0]} timed out after ${timeoutMs}ms (SIGKILL)`,
      })
    }, timeoutMs)
    child.stdout?.on('data', (chunk) => { stdout += chunk.toString('utf8') })
    child.stderr?.on('data', (chunk) => { stderr += chunk.toString('utf8') })
    child.on('error', (err) => {
      clearTimeout(timer)
      resolvePromise({ code: -1, stdout: '', stderr: `spawn error: ${err?.message ?? err}` })
    })
    child.on('close', (code) => {
      clearTimeout(timer)
      resolvePromise({ code: code ?? -1, stdout, stderr })
    })
  })
}

/** 失败时的错误文案：stderr 尾部优先，其次 stdout 尾部（两者都可能承载原因）。 */
export function failureText(argv, result) {
  const tail = (s) => s.trim().split('\n').slice(-12).join('\n')
  const body = tail(result.stderr) || tail(result.stdout)
  return `invest-cli ${argv.join(' ')} failed (rc=${result.code})${body ? `:\n${body}` : ''}`
}

export function makeInvestTool(config = {}, deps = {}) {
  const skillRoot = deps.skillRoot ?? skillRootFrom(import.meta.url)
  const run = deps.run ?? runCli
  const maxChars = config.maxOutputChars ?? DEFAULTS.maxOutputChars
  const wantJsonDefault = config.json ?? DEFAULTS.json

  return {
    name: 'invest',
    description:
      '查询中国与美股市场数据（行情/财报/基金/美股/SEC 原文/A股结构/经理任职复盘），'
      + '直接返回结构化 JSON。用于任何需要真实金融数据的场景：'
      + '「茅台现在多少钱」「分析 005827」「AAPL 的 ROE」「这只基金是谁在管、任职以来赚了多少」'
      + '「600519 最近公告」。一次只做一件事；command 见 systemPrompt 里的清单。',
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        command: {
          type: 'string',
          enum: [...COMMANDS],
          description: '子命令，决定取哪类数据（清单与参数见 systemPrompt 的 invest 一节）。',
        },
        args: {
          type: 'array',
          items: { type: 'string' },
          description:
            '子命令的参数，原样传给 CLI（标的代码/名称、子动作、旗标）。'
            + '例：["600519"]、["notice","600519"]、["--manager","余懿"]。'
            + '不要传 --json（由本工具的 json 参数控制）。',
        },
        json: {
          type: 'boolean',
          description: `是否要结构化 JSON 输出（默认 ${wantJsonDefault}）。置 false 得到终端表格，适合直接展示给人看。`,
        },
      },
      required: ['command'],
    },
    isConcurrencySafe: () => true,
    output: {
      schema: { type: 'object' },
      render: (_args, value) => text(value?.text ?? ''),
    },
    async execute(args) {
      const command = typeof args?.command === 'string' ? args.command.trim() : ''
      if (!COMMANDS.includes(command)) {
        throw new Error(
          `invest: unknown command "${command}". Known: ${COMMANDS.join(', ')}`,
        )
      }
      const wantJson = args?.json === undefined ? wantJsonDefault : args.json === true
      const argv = buildArgv(command, args?.args, wantJson)
      const entry = resolveEntry(config, { skillRoot })
      const result = await run(entry, argv, pickTimeout(command, config), deps)
      if (result.code !== 0) throw new Error(failureText(argv, result))
      const body = result.stdout.trim()
      if (body === '') {
        // 与 CLI 自身纪律一致：rc=0 但没有输出 = 假成功，不能当空结果传下去。
        throw new Error(`invest-cli ${argv.join(' ')} 退出码为 0 但没有输出（非空结果才可信）`)
      }
      return { stdout: body, text: clip(body, maxChars), command }
    },
  }
}

export function apply(ctx, providedConfig = {}) {
  const config = { ...DEFAULTS, ...(isObject(providedConfig) ? providedConfig : {}) }
  ctx.tools.register(makeInvestTool(config))

  // 命令目录：这是「不读 23KB SKILL.md 也能用」的那一层。刻意写成紧凑分组
  // （≈150 token/轮），而不是把 SKILL.md 搬进来——常驻开销才是单入口方案
  // 要守住的东西。
  const prompt = ctx.get?.('systemPrompt') ?? ctx.systemPrompt
  if (prompt && typeof prompt.section === 'function') {
    prompt.section({
      name: 'tool:invest',
      order: 116.4,
      text: () => [
        'Use the `invest` tool for real market data instead of guessing or searching the web.',
        'Commands (pass as `command`, extra flags as `args`):',
        'stock <代码|名称> · fund <代码|名称> [--analytics] · us <代码> [--history --years N] — A股/港股、公募基金、美股快照',
        'quote <代码[,代码...]> · kline <代码> [--period day|week|month] — 免鉴权行情/K线（最快）',
        'sec <美股代码> [--search 词|--insiders|--events|--holdings] — SEC EDGAR 原文级（免费官方源）',
        'fmp <美股代码> [--statements|--estimates|--dividends] — 美股 TTM 比率/评级/预测',
        'cn <notice|consensus|holders|disclosure|report> <代码> — A股公告/一致预期/股东/披露日历/研报',
        'screen <自然语言条件> — 选股（股票）；fund-class <基金代码> — 基金三层分类',
        'tenure <基金代码|名称> [--manager 姓名] [--excel 路径] — 基金经理任职期复盘（慢，>60s）',
        'report <基金代码> [--latest N] — 基金定期报告 PDF；info <关键词> — 财经资讯检索',
        'watchlist [add|remove|list] · capabilities · datasources — 自选股 / 能力清单 / 数据源状态',
        'westock <子命令> <代码> · wind · yingmi · ttskill · intent — 长尾透传（筹码/龙虎榜/资金流/Wind/盈米/天天基金）',
        'Ask for `capabilities` when unsure what a long-tail command accepts.',
      ].join('\n'),
    })
  }
}

function isObject(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
