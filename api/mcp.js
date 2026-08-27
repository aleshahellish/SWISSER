import {
  RESOURCE_MIME_TYPE,
  registerAppResource,
  registerAppTool,
} from "@modelcontextprotocol/ext-apps/server";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { z } from "zod";

import {
  LEGACY_MARKET_CARD_URIS,
  MARKET_CARD_URI,
  marketCardHtml,
} from "../swisser_market_card.js";
import {
  BTC_SYMBOL,
  SUPPORTED_SYMBOLS,
  TRADE_SYMBOLS,
  buildVerifiedCard,
  canonicalMode,
  createScanWorkflowState,
  createSnapshotBundleState,
} from "../swisser_evidence.js";
import {
  loadWorkflowState,
  saveWorkflowState,
} from "../swisser_workflow_store.js";

const SERVER_VERSION = "1.10.1";
const CONTROLS_URI = "ui://swisser/market-controls/1.10.0.html";
const LEGACY_CONTROLS_URIS = [
  "ui://swisser/market-controls/1.9.2.html",
  "ui://swisser/market-controls/1.9.1.html",
  "ui://swisser/market-controls/1.9.0.html",
  "ui://swisser/market-controls/1.8.0.html",
  "ui://swisser/market-controls/1.7.3.html",
  "ui://swisser/market-controls/1.7.1.html",
  "ui://swisser/market-controls/1.7.0.html",
  "ui://swisser/market-controls/1.6.0.html",
  "ui://swisser/market-controls/1.5.1.html",
  "ui://swisser/market-controls/1.5.0.html",
  "ui://swisser/market-controls/1.4.1.html",
  "ui://swisser/market-controls-v4.html",
  "ui://swisser/market-controls.html",
  "ui://swisser/market-controls-v3.html",
  "ui://swisser/market-controls-v2.html",
  "ui://swisser/market-controls-v1.html",
];
const SERVER_ICON = "https://tao-mexc-live.vercel.app/swisser-icon.svg";
const API_BASE = process.env.SWISSER_API_BASE ?? "https://tao-mexc-live.vercel.app";

export const COMMANDS = [
  "Собери нейтральный обзор рынка без выбора лучших. Вызови scan_swisser_markets с mode=overview: scanner сам создаст свежий серверный запуск и вернёт короткий workflow_id. BTC оставь только рыночным контекстом. Покажи фактическое состояние всех шести торговых монет, не ранжируй их и не формируй торговых кандидатов. ОБЯЗАТЕЛЬНЫЙ финальный шаг — сразу вызови render_swisser_market_card в режиме overview; передай workflow_id, если он доступен. Не создавай Markdown-таблицу или PNG вместо renderer.",
  "Найди лучшие сетапы на всём рынке. Вызови scan_swisser_markets с mode=setups: scanner сам создаст свежий серверный запуск. Выбери действительно конкурентных кандидатов без заранее заданного количества. Затем одним вызовом get_swisser_candidate_snapshots передай mode=setups, полученный workflow_id и весь массив выбранных symbols. Отдельно оцени качество сетапа и готовность входа; каждому кандидату передай status=confirmed, wait или cancelled. trade_ready=true — необходимое, но не достаточное условие confirmed: отдельно проверь место, пространство, стоп и инвалидацию. ОБЯЗАТЕЛЬНЫЙ финальный шаг — сразу вызови render_swisser_market_card в режиме setups с workflow_id из snapshot-пакета. Если инструмент вернул SWISSER_RESTART_REQUIRED, один раз начни весь этот режим заново со scanner и не используй прежний shortlist. Если кандидатов нет, snapshots не вызывай и передай renderer ID scanner. Не создавай Markdown-таблицу или PNG вместо renderer.",
  "Проверь входы только по кандидатам из последнего результата «Лучшие сетапы» в этом диалоге, исключая уже отменённые. Вызови scan_swisser_markets с mode=entry и expected_symbols из сохранённого набора; scanner сам создаст свежий серверный запуск. Не добавляй остальные монеты и не формируй новый рейтинг. Затем одним вызовом get_swisser_candidate_snapshots передай mode=entry, полученный workflow_id и весь сохранённый набор symbols. На 1m разделяй направление действующей структуры и свежесть конкретного входного триггера. Для строгого статуса ВХОД ПОДТВЕРЖДЁН последнее значимое событие ожидаемого направления должно быть свежим по API: это может быть CHoCH либо BOS, продолжающий ту же CHoCH-цепочку. bars_since > freshness_rule_bars само по себе не означает «опоздал», не отменяет сетап и не требует нового CHoCH. Новый CHoCH нужен только если 1m успел перейти в противоположную структуру; в продолжающейся цепочке свежий BOS может вернуть готовность. Называй вход опоздавшим только при фактическом растяжении цены, ухудшении стопа или сокращении пространства до цели. Если структурный триггер не свежий, оцени положение цены, displacement и завершённость отката/ретеста и дай ЖДАТЬ как «вход пока не подтверждён», а не как автоматическую отмену или доказанное опоздание. C2/C3 используй только как дополнительный контекст: их наличие не обязательно, а отсутствие не является veto. Проверь также место, пространство, стоп и инвалидацию; каждому кандидату передай status=confirmed, wait или cancelled. trade_ready=true — необходимый, но не достаточный фильтр для confirmed. ОБЯЗАТЕЛЬНЫЙ финальный шаг — сразу вызови render_swisser_market_card в режиме entry с workflow_id из snapshot-пакета. Если инструмент вернул SWISSER_RESTART_REQUIRED, один раз начни весь этот режим заново со scanner и не используй прежние данные. Если предыдущего списка нет, попроси сначала запустить «Лучшие сетапы». Не создавай Markdown-таблицу или PNG вместо renderer.",
];

export const COMMAND_LABELS = [
  "Обзор рынка",
  "Лучшие сетапы",
  "Проверить вход",
];

const controlsHtml = `<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>SWISSER — команды рынка</title>
  <style>
    :root { color-scheme: light dark; font-family: ui-sans-serif, system-ui, -apple-system, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; padding: 6px; background: transparent; color: CanvasText; }
    .panel { border: 1px solid color-mix(in srgb, CanvasText 14%, transparent); border-radius: 12px; padding: 8px; background: Canvas; }
    .head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 6px; }
    .brand { font-size: 11px; font-weight: 700; letter-spacing: .12em; opacity: .68; }
    .pin { border: 1px solid color-mix(in srgb, CanvasText 14%, transparent); background: transparent; color: inherit; font: inherit; font-size: 10.5px; line-height: 1.3; cursor: pointer; opacity: .7; padding: 3px 6px; border-radius: 6px; }
    .pin:hover { background: color-mix(in srgb, CanvasText 7%, transparent); opacity: 1; }
    .pin:disabled { cursor: default; opacity: .55; }
    .commands { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 5px; }
    .command { display: flex; align-items: center; width: 100%; min-height: 34px; text-align: left; border: 1px solid color-mix(in srgb, CanvasText 14%, transparent); border-radius: 8px; padding: 6px 8px; background: color-mix(in srgb, CanvasText 3%, Canvas); color: inherit; font: inherit; font-size: 12px; line-height: 1.2; cursor: pointer; transition: border-color .15s, background .15s, transform .05s; }
    .command:hover { border-color: color-mix(in srgb, CanvasText 28%, transparent); background: color-mix(in srgb, CanvasText 7%, Canvas); }
    .command:active { transform: translateY(1px); }
    .command:disabled { cursor: wait; opacity: .55; }
    .number { flex: 0 0 auto; display: inline-grid; place-items: center; width: 18px; height: 18px; margin-right: 6px; border: 1px solid color-mix(in srgb, CanvasText 12%, transparent); border-radius: 5px; background: color-mix(in srgb, CanvasText 6%, Canvas); font-size: 10px; font-weight: 700; opacity: .78; }
    .status { margin: 5px 1px 0; font-size: 10px; line-height: 1.3; opacity: .62; }
    .status:empty { display: none; }
    @media (max-width: 560px) { .commands { grid-template-columns: 1fr; } }
    body[data-display-mode="pip"] { padding: 4px; }
    body[data-display-mode="pip"] .panel { padding: 6px; border-radius: 10px; }
    body[data-display-mode="pip"] .head { margin-bottom: 5px; }
    body[data-display-mode="pip"] .commands { grid-template-columns: 1fr; gap: 4px; }
    body[data-display-mode="pip"] .command { min-height: 30px; padding: 5px 7px; font-size: 11.5px; }
  </style>
</head>
<body>
  <section class="panel" aria-label="Быстрые команды SWISSER">
    <div class="head">
      <div class="brand">SWISSER</div>
      <button class="pin" id="pin" type="button" title="Оставить панель поверх чата">Закрепить</button>
    </div>
    <div class="commands">
      <button class="command" type="button" data-mode="overview" data-command="${COMMANDS[0]}"><span class="number">1</span>${COMMAND_LABELS[0]}</button>
      <button class="command" type="button" data-mode="setups" data-command="${COMMANDS[1]}"><span class="number">2</span>${COMMAND_LABELS[1]}</button>
      <button class="command" type="button" data-mode="entry" data-command="${COMMANDS[2]}"><span class="number">3</span>${COMMAND_LABELS[2]}</button>
    </div>
    <div class="status" id="status" role="status"></div>
  </section>
  <script>
    const status = document.getElementById("status");
    const pinButton = document.getElementById("pin");
    const buttons = [...document.querySelectorAll(".command")];
    let autoPinAttempted = false;
    let statusTimer;

    function setStatus(message, clearAfter = 0) {
      clearTimeout(statusTimer);
      status.textContent = message;
      if (clearAfter) statusTimer = setTimeout(() => { status.textContent = ""; }, clearAfter);
    }

    function setDisplayMode(mode) {
      document.body.dataset.displayMode = mode || "inline";
      const isPinned = mode === "pip";
      pinButton.disabled = isPinned;
      pinButton.textContent = isPinned ? "Готово ✓" : "Закрепить";
      pinButton.title = isPinned
        ? "Панель останется поверх чата до закрытия"
        : "Оставить панель поверх чата";
    }

    async function requestPip({ manual = false, force = false } = {}) {
      if (window.openai?.displayMode === "pip") {
        setDisplayMode("pip");
        return true;
      }
      if (!window.openai?.requestDisplayMode) {
        if (manual) setStatus("Закрепление недоступно в этом режиме ChatGPT.");
        return false;
      }
      if (autoPinAttempted && !manual && !force) return false;
      if (!manual) autoPinAttempted = true;

      try {
        const result = await window.openai.requestDisplayMode({ mode: "pip" });
        setDisplayMode(result?.mode);
        if (result?.mode === "pip") {
          setStatus("Панель закреплена.", 1600);
          return true;
        }
        if (manual) setStatus("ChatGPT оставил панель в текущем режиме.");
      } catch (error) {
        if (manual) setStatus(error?.message || "Не удалось закрепить панель.");
      }
      return false;
    }

    async function send(prompt) {
      buttons.forEach((item) => { item.disabled = true; });
      try {
        if (!window.openai?.sendFollowUpMessage) {
          throw new Error("Команды недоступны в этом режиме ChatGPT");
        }
        setStatus("Отправляю…");
        await requestPip({ force: true });
        await window.openai.sendFollowUpMessage({ prompt, scrollToBottom: true });
        setStatus("Отправлено.", 1200);
      } catch (error) {
        setStatus(error?.message || "Не удалось отправить запрос.");
      } finally {
        setTimeout(() => buttons.forEach((item) => { item.disabled = false; }), 700);
      }
    }

    buttons.forEach((button) => {
      button.addEventListener("click", () => send(button.dataset.command));
    });

    pinButton.addEventListener("click", () => requestPip({ manual: true, force: true }));

    window.addEventListener("openai:set_globals", (event) => {
      const mode = event.detail?.globals?.displayMode;
      if (mode) setDisplayMode(mode);
      requestPip();
    });

    setDisplayMode(window.openai?.displayMode || "inline");
    requestAnimationFrame(() => requestPip());
    setTimeout(() => requestPip(), 350);
  </script>
</body>
</html>`;

async function fetchSwisser(path, params) {
  const url = new URL(path, API_BASE);
  url.searchParams.set("nocache", String(Date.now()));
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, String(value));
    }
  }
  const response = await fetch(url, {
    cache: "no-store",
    headers: { accept: "application/json", "user-agent": "SWISSER-MCP/1.0" },
    signal: AbortSignal.timeout(58_000),
  });
  if (!response.ok) throw new Error(`SWISSER API returned HTTP ${response.status}`);
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    throw new Error("SWISSER API returned a non-JSON response");
  }
  return response.json();
}

function sessionId(extra) {
  return extra?._meta?.["openai/session"] || null;
}

function requestedSymbolsForMode(mode, expectedSymbols = []) {
  const expected = [...new Set(expectedSymbols)];
  if (mode === "entry") {
    if (!expected.length) {
      throw new Error(
        "SWISSER integrity error: entry has no saved candidates; run «Лучшие сетапы» first",
      );
    }
    return [...expected, BTC_SYMBOL];
  }
  if (expected.length) {
    throw new Error("SWISSER integrity error: expected_symbols are only valid for entry");
  }
  return SUPPORTED_SYMBOLS;
}

function prune(value) {
  if (value === null || value === undefined || value === "") return undefined;
  if (Array.isArray(value)) {
    const items = value.map(prune).filter((item) => item !== undefined);
    return items.length ? items : undefined;
  }
  if (typeof value === "object") {
    const entries = Object.entries(value)
      .map(([key, item]) => [key, prune(item)])
      .filter(([, item]) => item !== undefined);
    return entries.length ? Object.fromEntries(entries) : undefined;
  }
  return value;
}

function compactEvent(event) {
  if (!event || typeof event !== "object") return null;
  return prune({
    event_type: event.event_type ?? event.type ?? null,
    direction: event.direction ?? null,
    time: event.time ?? null,
    time_utc: event.time_utc ?? null,
    bars_since: event.bars_since ?? null,
    broken_level: event.broken_level ?? event.broken_pivot?.level ?? null,
  });
}

function compactLayer(layer, { includeRecent = false } = {}) {
  if (!layer || typeof layer !== "object") return null;
  return prune({
    direction: layer.current_direction ?? null,
    high: layer.current_high_level ?? layer.current_high?.level ?? null,
    low: layer.current_low_level ?? layer.current_low?.level ?? null,
    latest_event: compactEvent(layer.latest_event),
    recent_events: includeRecent
      ? (layer.recent_events || []).slice(-2).map(compactEvent)
      : null,
  });
}

function compactTrigger(trigger) {
  if (!trigger || typeof trigger !== "object") return null;
  return prune({
    type: trigger.type ?? null,
    direction: trigger.direction ?? null,
    time: trigger.time ?? null,
    time_utc: trigger.time_utc ?? null,
    bars_since: trigger.bars_since ?? null,
    is_fresh: trigger.is_fresh ?? null,
    eq_respected: trigger.eq_respected ?? null,
    quality: trigger.quality ?? null,
  });
}

function compactCandle(candle) {
  if (!candle || typeof candle !== "object") return null;
  return Object.fromEntries(
    ["time", "time_utc", "open", "high", "low", "close", "volume"]
      .filter((key) => candle[key] !== undefined)
      .map((key) => [key, candle[key]]),
  );
}

function compactClosure(sequence) {
  if (!sequence || typeof sequence !== "object") return null;
  return prune({
    state: sequence.state ?? null,
    candle_number: sequence.candle_number ?? null,
    direction: sequence.direction ?? null,
    bars_since_c2: sequence.bars_since_c2 ?? null,
    c3_confirmed: sequence.c3_confirmed ?? null,
    c3_eq_respected: sequence.c3_eq_respected ?? null,
  });
}

function compactBias(bias) {
  if (!bias || typeof bias !== "object") return null;
  return prune({
    direction: bias.direction ?? null,
    timeframe: bias.timeframe ?? null,
    direction_source: bias.direction_source ?? null,
    confidence: bias.confidence ?? null,
    broad_context_direction: bias.broad_context_direction ?? null,
    relation_to_broad_context: bias.relation_to_broad_context ?? null,
    internal_conflicts: bias.internal_conflicts ?? [],
  });
}

function compactScenario(scenario) {
  if (!scenario || typeof scenario !== "object") return null;
  return prune({
    direction: scenario.direction ?? null,
    label: scenario.label ?? null,
    kind: scenario.kind ?? null,
    priority: scenario.priority ?? null,
    is_local_counter_1h: scenario.is_local_counter_1h ?? false,
    requires_entry_confirmation: scenario.requires_entry_confirmation ?? null,
    execution_state: scenario.execution_state ?? null,
    trade_ready: scenario.trade_ready ?? null,
    status: scenario.status ?? null,
    reason: scenario.reason ?? null,
    context_cautions: scenario.context_cautions ?? [],
  });
}

function compactExecution(state) {
  if (!state || typeof state !== "object") return null;
  const confirmation = state.entry_structure_confirmation || {};
  return prune({
    state: state.state ?? null,
    trade_ready: state.trade_ready ?? null,
    preferred_direction: state.preferred_direction ?? null,
    entry_direction: state.entry_timeframe_direction ?? null,
    relation: state.relation_to_preference ?? null,
    entry_structure_confirmation: {
      confirmed: confirmation.confirmed ?? null,
      expected_direction: confirmation.expected_direction ?? null,
      freshness_rule_bars: confirmation.freshness_rule_bars ?? null,
      type: confirmation.confirmation_type ?? null,
      reason: confirmation.reason ?? null,
      latest_event: compactEvent(confirmation.latest_event),
      origin_choch: compactEvent(confirmation.origin_choch),
    },
    latest_entry_trigger: compactTrigger(state.latest_entry_trigger),
    c2_c3_role: state.c2_c3_role ?? null,
  });
}

function compactHourlyClosure(closure) {
  if (!closure || typeof closure !== "object") return null;
  return prune({
    state: closure.state ?? null,
    direction: closure.direction ?? null,
    bars_since_c2: closure.bars_since_c2 ?? null,
    c3_confirmed: closure.c3_confirmed ?? null,
    c3_eq_respected: closure.c3_eq_respected ?? null,
    latest_c2_time: closure.latest_c2_time ?? null,
    latest_c2_time_utc: closure.latest_c2_time_utc ?? null,
  });
}

function compactOppositeClosure(warning) {
  if (!warning || typeof warning !== "object") return null;
  if (warning.active !== true) return { active: false };
  return prune({
    active: warning.active ?? false,
    direction: warning.direction ?? null,
    bars_since_c2: warning.bars_since_c2 ?? null,
    reason: warning.reason ?? null,
  });
}

function compactDisplacement(event) {
  if (!event || typeof event !== "object") return null;
  return prune({
    type: event.type ?? null,
    direction: event.direction ?? null,
    time: event.time ?? null,
    time_utc: event.time_utc ?? null,
    swept_side: event.swept_side ?? null,
    closed_beyond: event.closed_beyond ?? null,
  });
}

function compactHierarchy(hierarchy = {}) {
  return prune({
    broad_context_bias: compactBias(hierarchy.broad_context_bias),
    higher_timeframe_bias: compactBias(hierarchy.higher_timeframe_bias),
    session_timeframe_bias: compactBias(hierarchy.session_timeframe_bias),
    setup_timeframe_bias: compactBias(hierarchy.setup_timeframe_bias),
    entry_timeframe_bias: compactBias(hierarchy.entry_timeframe_bias),
    hourly_closure_phase: compactHourlyClosure(hierarchy.hourly_closure_phase),
    hourly_opposite_closure_warning: compactOppositeClosure(
      hierarchy.hourly_opposite_closure_warning,
    ),
    alignment_state: hierarchy.alignment_state ?? null,
    continuation_bias: {
      direction: hierarchy.continuation_bias?.direction ?? null,
      bias_direction: hierarchy.continuation_bias?.bias_direction ?? null,
    },
    active_trade_scenario: compactScenario(hierarchy.active_trade_scenario),
    strategic_4h_context: {
      direction: hierarchy.strategic_4h_context?.direction ?? null,
      relation_to_working_direction:
        hierarchy.strategic_4h_context?.relation_to_working_direction ?? null,
      caution: hierarchy.strategic_4h_context?.caution ?? null,
    },
    execution_state: compactExecution(hierarchy.execution_state),
    conflicts: (hierarchy.conflicts || []).map((conflict) => ({
      scope: conflict.scope ?? null,
      timeframe: conflict.timeframe ?? null,
      higher_timeframe: conflict.higher_timeframe ?? null,
      higher_direction: conflict.higher_direction ?? null,
      lower_timeframe: conflict.lower_timeframe ?? null,
      lower_direction: conflict.lower_direction ?? null,
      type: conflict.type ?? null,
    })),
  });
}

function compactTimeframe(block = {}, signal = {}, { includeDetail = false } = {}) {
  const structure = block.luxalgo_structure || {};
  return prune({
    primary_direction: signal.primary_direction ?? block.primary_direction ?? null,
    confidence: signal.confidence ?? block.confidence ?? null,
    structure_relation: signal.structure_relation ?? block.structure_relation ?? null,
    luxalgo_structure: {
      internal: compactLayer(structure.internal, { includeRecent: includeDetail }),
      swing: compactLayer(structure.swing, { includeRecent: includeDetail }),
    },
    latest_trigger: compactTrigger(signal.latest_trigger ?? block.latest_trigger),
    closure_sequence: compactClosure(signal.closure_sequence ?? block.closure_sequence),
    opposite_closure_to_primary_direction: compactOppositeClosure(
      signal.opposite_closure_to_primary_direction ??
      block.opposite_closure_to_primary_direction,
    ),
    latest_sweep_displacement: compactDisplacement(
      block.latest_sweep_displacement ??
      (block.recent_sweep_displacement || []).at(-1),
    ),
    latest_live_candle: includeDetail ? compactCandle(block.latest_live_candle) : null,
    latest_closed_candle: compactCandle(block.latest_closed_candle),
    recent_closed_candles: includeDetail
      ? (block.recent_closed_candles || []).slice(-3).map(compactCandle)
      : null,
  });
}

function compactScanTimeframe(block = {}, timeframe) {
  const compact = compactTimeframe(block);
  const includeClosure = timeframe !== "4h";
  const includeDisplacement = timeframe === "15m" || timeframe === "1m";
  return prune({
    primary_direction: compact?.primary_direction,
    confidence: compact?.confidence,
    structure_relation: compact?.structure_relation,
    luxalgo_structure: compact?.luxalgo_structure,
    latest_trigger: includeClosure ? compact?.latest_trigger : null,
    closure_sequence: includeClosure ? compact?.closure_sequence : null,
    opposite_closure_to_primary_direction: includeClosure
      ? compact?.opposite_closure_to_primary_direction
      : null,
    latest_sweep_displacement: includeDisplacement
      ? compact?.latest_sweep_displacement
      : null,
  });
}

function compactScanForModel(data) {
  return {
    fetched_at_unix: data.fetched_at_unix,
    fetched_at_utc: data.fetched_at_utc,
    results: (data.results || []).map((item) => ({
      symbol: item.symbol,
      role: item.analysis_role,
      current_price: item.current_price,
      high_24h: item.high_24h,
      low_24h: item.low_24h,
      range_position_24h_percent: item.range_position_24h_percent,
      higher_timeframe_levels: item.higher_timeframe_levels,
      hierarchy: compactHierarchy(item.mtf_hierarchy),
      timeframes: Object.fromEntries(
        ["4h", "1h", "15m", "1m"].map((timeframe) => [
          timeframe,
          compactScanTimeframe(item.timeframe_summary?.[timeframe] || {}, timeframe),
        ]),
      ),
    })),
  };
}

function compactSnapshotForModel(data) {
  const hierarchy = data.mtf_hierarchy || {};
  const signals = hierarchy.timeframe_signals || {};
  return {
    symbol: data.symbol,
    fetched_at_unix: data.fetched_at_unix,
    fetched_at_utc: data.fetched_at_utc,
    current_price: data.current_price,
    high_24h: data.high_24h,
    low_24h: data.low_24h,
    higher_timeframe_levels: data.higher_timeframe_levels,
    hierarchy: compactHierarchy(hierarchy),
    timeframes: Object.fromEntries(
      ["4h", "1h", "15m", "1m"].map((timeframe) => [
        timeframe,
        compactTimeframe(
          data.timeframes?.[timeframe] || {},
          signals[timeframe] || {},
          { includeDetail: true },
        ),
      ]),
    ),
  };
}

async function fetchFreshSnapshot(symbol, scanSourceMs) {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const data = await fetchSwisser("/api/snapshot_action_v6", { symbol });
    if (Number(data?.fetched_at_unix) * 1000 >= scanSourceMs) return data;
  }
  throw new Error(`SWISSER integrity error: snapshot ${symbol} stayed older than scanner`);
}

function sameSymbolSet(left, right) {
  const a = [...left].sort();
  const b = [...right].sort();
  return a.length === b.length && a.every((symbol, index) => symbol === b[index]);
}

function fullSymbol(symbol) {
  const normalized = String(symbol || "").trim().toUpperCase();
  return normalized.endsWith("_USDT") ? normalized : `${normalized}_USDT`;
}

async function fetchSnapshotMap(symbols, scanSourceMs = null) {
  const dataBySymbol = new Map();
  for (let index = 0; index < symbols.length; index += 3) {
    const batch = symbols.slice(index, index + 3);
    const results = await Promise.all(
      batch.map(async (symbol) => [
        symbol,
        scanSourceMs == null
          ? await fetchSwisser("/api/snapshot_action_v6", { symbol })
          : await fetchFreshSnapshot(symbol, scanSourceMs),
      ]),
    );
    for (const [symbol, data] of results) dataBySymbol.set(symbol, data);
  }
  return dataBySymbol;
}

async function createFreshScan({ mode, expectedSymbols = [], session = null }) {
  const canonical = canonicalMode(mode);
  const requestedSymbols = requestedSymbolsForMode(canonical, expectedSymbols);
  const data = await fetchSwisser("/api/scanner_action_v6", {
    symbols: requestedSymbols.join(","),
  });
  const workflow = createScanWorkflowState({
    mode: canonical,
    expectedSymbols,
    session,
    data,
    requestedSymbols,
  });
  return { data, workflow };
}

function requiredSnapshotsForCard(mode, marketRows, candidates) {
  if (mode === "overview") return [];
  if (mode === "entry") return marketRows.map((row) => fullSymbol(row.symbol));
  const required = new Set(
    candidates.map((candidate) => fullSymbol(candidate.symbol)),
  );
  for (const row of marketRows) {
    if (["top", "secondary"].includes(row.priority)) {
      required.add(fullSymbol(row.symbol));
    }
  }
  return [...required];
}

function storedWorkflowMatches(workflow, { mode, expectedSymbols, requiredSnapshots }) {
  if (!workflow) return false;
  if (!sameSymbolSet(workflow.expected_symbols || [], expectedSymbols)) return false;
  const tradeSymbols = (workflow.requested_symbols || []).filter(
    (symbol) => symbol !== BTC_SYMBOL,
  );
  const expectedRequested = mode === "entry" ? expectedSymbols : TRADE_SYMBOLS;
  if (!sameSymbolSet(tradeSymbols, expectedRequested)) return false;
  const availableSnapshots = Object.keys(workflow.snapshots || {});
  return requiredSnapshots.every((symbol) => availableSnapshots.includes(symbol));
}

function workflowRestartRequired(stage) {
  throw new Error(
    `SWISSER_RESTART_REQUIRED: ${stage} не получил состояние текущего запуска. ` +
    "Один раз повтори весь режим с scan_swisser_markets; не используй прежний shortlist, snapshots или уровни.",
  );
}

async function workflowForSnapshots({ workflowId, mode, symbols, session }) {
  const canonical = canonicalMode(mode);
  const expectedSymbols = canonical === "entry" ? symbols : [];
  let workflow = await loadWorkflowState(workflowId, {
    mode: canonical,
    stage: "scan",
    session,
  });
  if (!storedWorkflowMatches(workflow, {
    mode: canonical,
    expectedSymbols,
    requiredSnapshots: [],
  })) {
    workflowRestartRequired("snapshot");
  }

  const dataBySymbol = await fetchSnapshotMap(symbols, workflow.scan.source_ms);
  workflow = createSnapshotBundleState({
    workflow,
    dataBySymbol,
    symbols,
  });

  return {
    workflow,
    workflowId: await saveWorkflowState(workflow),
    dataBySymbol,
  };
}

async function workflowForRenderer({ input, session }) {
  const canonical = canonicalMode(input.mode);
  const rowSymbols = input.market_rows.map((row) => fullSymbol(row.symbol));
  const expectedSymbols = canonical === "entry" ? rowSymbols : [];
  const requiredSnapshots = requiredSnapshotsForCard(
    canonical,
    input.market_rows,
    input.candidates,
  );
  const requiredStage = requiredSnapshots.length ? "bundle" : "scan";
  let workflow = await loadWorkflowState(input.workflow_id, {
    mode: canonical,
    stage: requiredStage,
    session,
  });

  if (!storedWorkflowMatches(workflow, {
    mode: canonical,
    expectedSymbols,
    requiredSnapshots,
  })) {
    if (canonical !== "overview") workflowRestartRequired("renderer");
    workflow = (await createFreshScan({
        mode: canonical,
        expectedSymbols,
        session,
      })).workflow;
    await saveWorkflowState(workflow);
  }

  return workflow;
}

function commandPayloads(card) {
  const candidateRows = card.mode === "entry"
    ? card.market_rows || []
    : card.candidates || [];
  const currentCandidates = candidateRows.map(
    (candidate) => `${candidate.symbol}_USDT`,
  );
  return COMMANDS.map((prompt, index) => ({
    id: index + 1,
    label: COMMAND_LABELS[index],
    prompt,
    mode: ["overview", "setups", "entry"][index],
    expected_symbols: index === 2 ? currentCandidates : [],
  }));
}

export function createSwisserMcpServer() {
  const server = new McpServer({
    name: "swisser-market-controls",
    version: SERVER_VERSION,
    icons: [
      {
        src: SERVER_ICON,
        mimeType: "image/svg+xml",
        sizes: ["64x64"],
      },
    ],
  }, {
    instructions:
      "ОБЯЗАТЕЛЬНО: каждый рыночный запуск SWISSER начинай одним scan_swisser_markets с нужным mode; scanner сам " +
      "создаёт новый свежий серверный запуск и возвращает короткий workflow_id. Для setups и entry получай все нужные " +
      "snapshots одним get_swisser_candidate_snapshots, обязательно передавая mode, symbols и workflow_id scanner. Затем " +
      "сразу вызывай render_swisser_market_card с workflow_id последнего этапа. Никогда не передавай " +
      "evidence_token: полных подписанных токенов в текущем протоколе нет. Если snapshot или renderer вернул " +
      "SWISSER_RESTART_REQUIRED, один раз повтори весь текущий режим со scanner и не смешивай данные двух запусков. " +
      "Для setups/entry частично восстановленная карточка запрещена: либо полный единый цикл, либо понятная ошибка без итога. " +
      "Структура, цены и " +
      "время среза в карточке являются серверными полями: не передавай и не подменяй их вручную. " +
      "Не выводи пользователю workflow_id, verified, state_recovered и другие служебные поля. " +
      "Обычная Markdown-таблица, список или PNG не заменяют renderer. После вызова не дублируй карточку текстом. " +
      "Точный запрос «1» означает режим overview («Обзор рынка»), «2» — setups («Лучшие сетапы»), " +
      "«3» — entry («Проверить вход»); не проси расшифровку цифры. SWISSER анализирует MEXC Futures в трёх последовательных режимах. " +
      "«Обзор рынка» — полный scan_swisser_markets всех монет без snapshot, ранжирования и торгового отбора; " +
      "покажи нейтральный фактический срез шести торговых монет. «Лучшие сетапы» — полный scanner, затем snapshot " +
      "всех действительно конкурентных кандидатов без заранее заданного количества; не добавляй слабые ситуации " +
      "ради количества и не назначай победителя, если несколько монет объективно равноценны. Качество сетапа " +
      "и готовность входа оценивай отдельно. «Проверить вход» — повторно проверь только кандидатов из последнего " +
      "результата «Лучшие сетапы» в текущем диалоге, исключая уже отменённые последующими проверками; не добавляй " +
      "другие монеты и не составляй новый рейтинг; " +
      "для каждого обнови scanner и snapshot. На 1m разделяй направление действующей структуры и свежесть конкретного " +
      "входного триггера. Для строгого статуса ВХОД ПОДТВЕРЖДЁН последнее значимое событие ожидаемого направления " +
      "должно быть свежим по API: это может быть CHoCH либо BOS, продолжающий ту же CHoCH-цепочку. bars_since > " +
      "freshness_rule_bars само по себе не означает «опоздал», не отменяет сетап и не требует нового CHoCH. Новый " +
      "CHoCH нужен только после перехода 1m в противоположную структуру; в продолжающейся цепочке свежий BOS может " +
      "вернуть готовность. Называй вход опоздавшим только при фактическом растяжении цены, ухудшении стопа или " +
      "сокращении пространства до цели. При несвежем триггере оцени положение цены, displacement и завершённость " +
      "отката/ретеста и формулируй ЖДАТЬ как «вход пока не подтверждён», а не как автоматическую отмену или доказанное " +
      "опоздание. C2/C3 — только дополнительный контекст, не обязательный триггер и не veto. Проверь также " +
      "инвалидацию, затем передай каждому кандидату status=confirmed, wait или cancelled. trade_ready=true — только " +
      "необходимый структурный фильтр, а не автоматическое подтверждение входа. Если предыдущего списка нет, " +
      "коротко попроси сначала запустить «Лучшие сетапы»; если все сценарии отменены, предложи повторить второй режим. " +
      "При первой активации " +
      "вызови open_swisser_controls: панель сама запросит PiP-режим на время активной сессии. Не открывай её повторно " +
      "после каждого ответа, пока она активна; после анализа рабочие команды уже доступны под итоговой карточкой. " +
      "Текущую сделку называй по active_trade_scenario ядра 1h→15m→1m; continuation_bias и 4h — " +
      "контекст, а не активный " +
      "LONG/SHORT. Не выдумывай отсутствующие уровни. При ретроспективном разборе зафиксируй исходный срез " +
      "и исходный рейтинг, не пересортировывай монеты по последующему результату и не используй данные после " +
      "контрольного времени для объяснения прежнего выбора. PnL 6x называй результатом сделки только от реально " +
      "исполненного либо заранее заданного условного входа; изменение цены от произвольного среза обозначай как " +
      "движение после среза, а не PnL сделки. Один эпизод не меняет веса ранжирования без серии наблюдений.",
  });

  server.registerTool(
    "scan_swisser_markets",
    {
      title: "Сканировать рынок SWISSER",
      description:
        "Сам создаёт один свежий серверный запуск и возвращает короткий workflow_id. В overview/setups " +
        "сервер всегда сканирует все шесть торговых монет плюс BTC. В entry передай ровно сохранённых кандидатов " +
        "последней карточки setups. Передай полученный ID следующему инструменту без изменений. " +
        "Для текущего сценария используй active_trade_scenario, не continuation_bias.",
      inputSchema: {
        mode: z.enum(["overview", "setups", "entry"]),
        expected_symbols: z
          .array(z.enum(TRADE_SYMBOLS))
          .max(6)
          .optional()
          .describe("Только для entry: кандидаты последней карточки «Лучшие сетапы»."),
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        openWorldHint: true,
      },
    },
    async ({ mode, expected_symbols = [] }, extra) => {
      const canonical = canonicalMode(mode);
      const { data, workflow } = await createFreshScan({
        mode: canonical,
        expectedSymbols: expected_symbols,
        session: sessionId(extra),
      });
      const workflowId = await saveWorkflowState(workflow);
      return {
        content: [{
          type: "text",
          text:
            canonical === "overview"
              ? "Свежий обзорный scanner проверен. Сразу вызови renderer с этим workflow_id."
              : "Свежий scanner проверен. Выбери кандидатов и одним вызовом получи пакет snapshots с этим workflow_id.",
        }],
        structuredContent: {
          workflow_id: workflowId,
          scan: compactScanForModel(data),
        },
      };
    },
  );

  server.registerTool(
    "get_swisser_candidate_snapshots",
    {
      title: "Проверить кандидатов SWISSER одним пакетом",
      description:
        "После scanner получает все выбранные snapshots одним вызовом и возвращает новый короткий workflow_id. " +
        "Требует ID именно текущего scanner; при его отсутствии не смешивает данные, а требует полный перезапуск режима. " +
        "Для entry symbols должны точно совпасть с сохранённым набором. Не вызывай в overview.",
      inputSchema: {
        mode: z.enum(["setups", "entry"]),
        workflow_id: z
          .string()
          .uuid()
          .max(80)
          .describe("Обязательный короткий ID текущего scanner."),
        symbols: z
          .array(z.enum(TRADE_SYMBOLS))
          .min(1)
          .max(6)
          .refine((symbols) => new Set(symbols).size === symbols.length, {
            message: "symbols must not contain duplicates",
          }),
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        openWorldHint: true,
      },
    },
    async ({ mode, workflow_id, symbols }, extra) => {
      const result = await workflowForSnapshots({
        workflowId: workflow_id,
        mode,
        symbols,
        session: sessionId(extra),
      });
      return {
        content: [{
          type: "text",
          text: "Единый пакет snapshots проверен. Сразу вызови renderer с новым workflow_id.",
        }],
        structuredContent: {
          workflow_id: result.workflowId,
          snapshots: symbols.map((symbol) =>
            compactSnapshotForModel(result.dataBySymbol.get(symbol))),
        },
      };
    },
  );

  registerAppTool(
    server,
    "render_swisser_market_card",
    {
      title: "Показать проверенную карточку рынка SWISSER",
      description:
        "Финальный renderer с жёсткой проверкой свежести. Для setups/entry требует workflow_id последнего этапа; " +
        "при несовпадении не строит частичную карточку, а требует один полный перезапуск режима. Overview можно безопасно " +
        "пересобрать целиком без аналитического рейтинга. " +
        "Цена, 4h/1h/15m/1m, активная идея, сводка, BTC и время карточки строятся сервером. Модель передаёт " +
        "аналитический статус кандидата, но сервер запрещает подтверждение при trade_ready=false, WAIT или противоположном " +
        "активном направлении. В overview — шесть монет без кандидатов; в setups — шесть монет и любое число реальных " +
        "кандидатов; в entry — только сохранённый набор. Не показывай RR.",
      inputSchema: {
        mode: z
          .enum(["overview", "setups", "entry", "quick", "trades", "day"])
          .describe("Используй overview, setups или entry; старые aliases сохранены для совместимости."),
        workflow_id: z
          .string()
          .uuid()
          .max(80)
          .optional()
          .describe("ID последнего этапа; обязателен для setups/entry."),
        market_rows: z
          .array(
            z.object({
              symbol: z.enum(["TAO", "HYPE", "SOL", "XRP", "DOGE", "ETH"]),
              priority: z
                .enum(["top", "secondary", "watch", "none"])
                .optional()
                .describe("Только аналитический tier в setups; structural fields добавит сервер."),
            }),
          )
          .min(1)
          .max(6)
          .describe("В overview/setups — все шесть; в entry — ровно сохранённый набор."),
        candidates: z
          .array(
            z
              .object({
                symbol: z.enum(["TAO", "HYPE", "SOL", "XRP", "DOGE", "ETH"]),
                direction: z.enum(["Long", "Short", "Local Long", "Local Short"]),
                status: z
                  .enum(["confirmed", "wait", "cancelled"])
                  .optional()
                  .describe(
                    "Аналитический итог после проверки места, пространства и инвалидации. " +
                    "Передавай всегда; optional оставлен лишь для старых чатов. Без поля сервер безопасно ставит wait. " +
                    "confirmed допустим только при серверном trade_ready=true и совпавшем активном направлении.",
                  ),
                entry: z.string().min(1).max(80),
                stop_or_invalidation: z.string().min(1).max(120),
                targets: z.array(z.string().min(1).max(40)).min(1).max(3),
                pnl_6x: z.array(z.string().min(1).max(32)).min(1).max(3),
              })
              .refine((candidate) => candidate.targets.length === candidate.pnl_6x.length, {
                message: "Каждая цель должна иметь ровно одно соответствующее значение PnL 6x",
                path: ["pnl_6x"],
              }),
          )
          .max(6)
          .describe("Пусто для overview; каждый кандидат должен входить в атомарный snapshot-пакет."),
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        openWorldHint: false,
      },
      _meta: {
        ui: { resourceUri: MARKET_CARD_URI },
        "openai/outputTemplate": MARKET_CARD_URI,
        "openai/toolInvocation/invoking": "Проверяю свежесть и оформляю SWISSER",
        "openai/toolInvocation/invoked": "Проверенная сводка SWISSER готова",
      },
    },
    async (input, extra) => {
      const workflow = await workflowForRenderer({
        input,
        session: sessionId(extra),
      });
      const card = buildVerifiedCard({
        workflow,
        mode: canonicalMode(input.mode),
        lead: input.lead,
        marketRows: input.market_rows,
        candidates: input.candidates,
        conclusion: input.conclusion,
        session: sessionId(extra),
      });
      return {
        content: [
          {
            type: "text",
            text: "Карточка SWISSER построена только из проверенного evidence текущего запуска. Не дублируй её большой Markdown-таблицей.",
          },
        ],
        structuredContent: {
          ...card,
          commands: commandPayloads(card),
        },
      };
    },
  );

  registerAppTool(
    server,
    "open_swisser_controls",
    {
      title: "Открыть команды SWISSER",
      description:
        "Показывает компактную панель из трёх последовательных этапов SWISSER и автоматически запрашивает " +
        "PiP-режим поверх чата на время активной сессии. Вызывай инструмент при первом запуске SWISSER и по просьбе вернуть " +
        "панель. После ухода из чата ChatGPT может завершить PiP; три команды остаются под последней рыночной карточкой.",
      inputSchema: {},
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        openWorldHint: false,
      },
      _meta: {
        ui: { resourceUri: CONTROLS_URI },
        "openai/outputTemplate": CONTROLS_URI,
        "openai/toolInvocation/invoking": "Открываю команды SWISSER",
        "openai/toolInvocation/invoked": "Команды SWISSER готовы",
      },
    },
    async () => ({
      content: [
        {
          type: "text",
          text: "Панель трёх команд SWISSER открыта и автоматически запрашивает закрепление поверх чата. Точные запросы 1, 2 и 3 соответствуют overview, setups и entry. Каждый завершённый анализ обязательно завершается render_swisser_market_card; Markdown-таблица или PNG не являются заменой. Не перечисляй команды повторно обычным текстом.",
        },
      ],
      structuredContent: {
        title: "SWISSER · Рынок",
        commands: COMMANDS.map((prompt, index) => ({
          id: index + 1,
          label: COMMAND_LABELS[index],
          prompt,
          mode: ["overview", "setups", "entry"][index],
          expected_symbols: [],
        })),
      },
    }),
  );

  for (const resourceUri of [CONTROLS_URI, ...LEGACY_CONTROLS_URIS]) {
    registerAppResource(
      server,
      resourceUri === CONTROLS_URI
        ? "Команды рынка SWISSER"
        : "Команды рынка SWISSER (совместимость)",
      resourceUri,
      {
        mimeType: RESOURCE_MIME_TYPE,
        description: "Три последовательных этапа анализа рынка SWISSER",
      },
      async () => ({
        contents: [
          {
            uri: resourceUri,
            mimeType: RESOURCE_MIME_TYPE,
            text: controlsHtml,
            _meta: {
              ui: {
                prefersBorder: false,
                csp: { connectDomains: [], resourceDomains: [] },
              },
              "openai/widgetPrefersBorder": false,
            },
          },
        ],
      }),
    );
  }

  for (const resourceUri of [MARKET_CARD_URI, ...LEGACY_MARKET_CARD_URIS]) {
    registerAppResource(
      server,
      resourceUri === MARKET_CARD_URI
        ? "Рыночная карточка SWISSER"
        : "Рыночная карточка SWISSER (совместимость)",
      resourceUri,
      {
        mimeType: RESOURCE_MIME_TYPE,
        description: "Компактная таблица рынка и реальных торговых кандидатов SWISSER",
      },
      async () => ({
        contents: [
          {
            uri: resourceUri,
            mimeType: RESOURCE_MIME_TYPE,
            text: marketCardHtml,
            _meta: {
              ui: {
                prefersBorder: false,
                csp: {
                  connectDomains: [],
                  resourceDomains: [
                    "https://fonts.googleapis.com",
                    "https://fonts.gstatic.com",
                  ],
                },
              },
              "openai/widgetPrefersBorder": false,
            },
          },
        ],
      }),
    );
  }

  return server;
}

function setCorsHeaders(res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS");
  res.setHeader(
    "Access-Control-Allow-Headers",
    "Content-Type, Accept, Mcp-Session-Id, Mcp-Protocol-Version",
  );
  res.setHeader("Access-Control-Expose-Headers", "Mcp-Session-Id");
}

export default async function handler(req, res) {
  setCorsHeaders(res);
  if (req.method === "OPTIONS") {
    res.statusCode = 204;
    res.end();
    return;
  }

  const server = createSwisserMcpServer();
  const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });

  res.on("close", () => {
    transport.close().catch(() => {});
    server.close().catch(() => {});
  });

  try {
    await server.connect(transport);
    await transport.handleRequest(req, res, req.body);
  } catch (error) {
    console.error("SWISSER MCP error", error);
    if (!res.headersSent) {
      res.statusCode = 500;
      res.setHeader("Content-Type", "application/json");
      res.end(
        JSON.stringify({
          jsonrpc: "2.0",
          error: { code: -32603, message: "Internal server error" },
          id: null,
        }),
      );
    }
  }
}
