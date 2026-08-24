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
  createRunToken,
  createScanEvidenceToken,
  createSnapshotEvidenceToken,
  verifyRunToken,
  verifyScanEvidenceToken,
} from "../swisser_evidence.js";

const SERVER_VERSION = "1.7.1";
const CONTROLS_URI = "ui://swisser/market-controls/1.7.1.html";
const LEGACY_CONTROLS_URIS = [
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
  "Собери нейтральный обзор рынка без выбора лучших. Непосредственно перед scanner вызови start_swisser_run с mode=overview. Передай run_token в полный scan_swisser_markets по всем поддерживаемым символам; BTC оставь только рыночным контекстом. Покажи фактическое состояние всех шести торговых монет, не ранжируй их и не формируй торговых кандидатов. ОБЯЗАТЕЛЬНЫЙ финальный шаг — вызови render_swisser_market_card в режиме overview с теми же run_token и scan_evidence_token; structural fields и время renderer возьмёт из проверенного evidence. Не создавай Markdown-таблицу или PNG вместо renderer.",
  "Найди лучшие сетапы на всём рынке. Непосредственно перед scanner вызови start_swisser_run с mode=setups. Передай run_token в полный scan_swisser_markets, затем вместе с scan_evidence_token — в get_swisser_market_snapshot для каждого действительно конкурентного кандидата. Не задавай количество заранее и не назначай единственного победителя, если несколько монет равноценны. Отдельно оцени качество сетапа и готовность входа. ОБЯЗАТЕЛЬНЫЙ финальный шаг — вызови render_swisser_market_card в режиме setups с токенами этого же запуска; без свежего snapshot_evidence_token кандидат не может попасть в карточку. Не создавай Markdown-таблицу или PNG вместо renderer.",
  "Проверь входы только по кандидатам из последнего результата «Лучшие сетапы» в этом диалоге, исключая уже отменённые. Непосредственно перед scanner вызови start_swisser_run с mode=entry и expected_symbols из сохранённого набора. Не добавляй остальные монеты и не формируй новый рейтинг. Передай run_token в scanner только по сохранённому набору, затем вместе с scan_evidence_token вызови свежий snapshot каждого. На 1m проверь CHoCH/BOS, displacement, окончание отката/ретест, C2/C3, опоздание и инвалидацию; дай статус ВХОД ПОДТВЕРЖДЁН, ЖДАТЬ или ОТМЕНА. ОБЯЗАТЕЛЬНЫЙ финальный шаг — render_swisser_market_card в режиме entry с токенами текущего запуска. Если предыдущего списка нет, попроси сначала запустить «Лучшие сетапы». Не создавай Markdown-таблицу или PNG вместо renderer.",
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

function sameSymbolSet(left, right) {
  const a = [...new Set(left)].sort();
  const b = [...new Set(right)].sort();
  return a.length === b.length && a.every((symbol, index) => symbol === b[index]);
}

function requestedSymbolsForRun(run, supplied = []) {
  if (run.mode !== "entry") return SUPPORTED_SYMBOLS;
  const suppliedTrades = [...new Set(supplied.filter((symbol) => symbol !== BTC_SYMBOL))];
  const expected = run.expected_symbols || [];
  if (expected.length && suppliedTrades.length && !sameSymbolSet(expected, suppliedTrades)) {
    throw new Error("SWISSER integrity error: entry scanner symbols do not match saved candidates");
  }
  const tradeSymbols = expected.length ? expected : suppliedTrades;
  if (!tradeSymbols.length) {
    throw new Error(
      "SWISSER integrity error: entry run has no saved candidates; run «Лучшие сетапы» first",
    );
  }
  return [...tradeSymbols, BTC_SYMBOL];
}

function commandPayloads(card) {
  const currentCandidates = (card.candidates || []).map(
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
      "ОБЯЗАТЕЛЬНО: каждый рыночный запуск SWISSER начинай с start_swisser_run и используй выданный run_token " +
      "во всех последующих инструментах этого запуска. Не смешивай токены разных запусков. Каждый завершённый " +
      "рыночный анализ заканчивай вызовом render_swisser_market_card с scan_evidence_token и всеми нужными " +
      "snapshot_evidence_tokens; без проверенного свежего evidence renderer обязан отказать. Структура, цены и " +
      "время среза в карточке являются серверными полями: не передавай и не подменяй их вручную. " +
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
      "для каждого обнови scanner и snapshot, особенно свежий 1m CHoCH/BOS, displacement, откат/ретест, C2/C3, " +
      "опоздание и инвалидацию, затем дай статус ВХОД ПОДТВЕРЖДЁН, ЖДАТЬ или ОТМЕНА. Если предыдущего списка нет, " +
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

  registerAppTool(
    server,
    "start_swisser_run",
    {
      title: "Начать свежий запуск SWISSER",
      description:
        "Создаёт токен одного запуска. Всегда вызывай непосредственно перед scanner для overview, setups или entry. " +
        "Для entry передай ровно кандидатов из " +
        "последней карточки setups; для overview/setups expected_symbols должен быть пустым.",
      inputSchema: {
        mode: z.enum(["overview", "setups", "entry"]),
        expected_symbols: z
          .array(z.enum(TRADE_SYMBOLS))
          .optional()
          .describe("Только для entry: сохранённые кандидаты предыдущей карточки setups."),
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        openWorldHint: false,
      },
      _meta: {
        ui: { visibility: ["model", "app"] },
        "openai/widgetAccessible": true,
      },
    },
    async ({ mode, expected_symbols = [] }, extra) => {
      const { token, payload } = createRunToken({
        mode,
        expectedSymbols: expected_symbols,
        session: sessionId(extra),
      });
      return {
        content: [
          {
            type: "text",
            text: "Свежий запуск SWISSER создан. Передай run_token в scanner, snapshot и финальный renderer; не заменяй его токеном другого запуска.",
          },
        ],
        structuredContent: {
          run_token: token,
          run_id: payload.run_id,
          mode: payload.mode,
          expected_symbols: payload.expected_symbols,
          expires_at_unix: Math.floor(payload.exp / 1000),
        },
      };
    },
  );

  server.registerTool(
    "scan_swisser_markets",
    {
      title: "Сканировать рынок SWISSER",
      description:
        "Получает scanner только внутри текущего start_swisser_run и возвращает подписанный scan_evidence_token. " +
        "В overview/setups сервер всегда сканирует все шесть торговых монет плюс BTC. В entry — только сохранённых " +
        "кандидатов плюс BTC. Для текущего сценария используй active_trade_scenario, не continuation_bias.",
      inputSchema: {
        run_token: z.string().min(20).describe("Токен текущего start_swisser_run."),
        symbols: z
          .array(z.enum(SUPPORTED_SYMBOLS))
          .optional()
          .describe("Только для entry без привязанного списка; overview/setups всегда принудительно полные."),
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        openWorldHint: true,
      },
    },
    async ({ run_token, symbols = [] }, extra) => {
      const run = verifyRunToken(run_token, { session: sessionId(extra) });
      const requestedSymbols = requestedSymbolsForRun(run, symbols);
      const data = await fetchSwisser("/api/scanner_action_v6", {
        symbols: requestedSymbols.join(","),
      });
      const evidence = createScanEvidenceToken({ run, data, requestedSymbols });
      return {
        content: [{ type: "text", text: "Свежий SWISSER scanner проверен. Передай run_token и scan_evidence_token в snapshots и финальный renderer." }],
        structuredContent: {
          ...data,
          run_id: run.run_id,
          scan_evidence_token: evidence.token,
        },
      };
    },
  );

  server.registerTool(
    "get_swisser_market_snapshot",
    {
      title: "Проверить кандидата SWISSER",
      description:
        "Получает snapshot только после scanner того же запуска. Snapshot старше scanner отклоняется, а успешный " +
        "ответ получает snapshot_evidence_token для финального renderer. Не вызывай в overview.",
      inputSchema: {
        run_token: z.string().min(20).describe("Токен текущего start_swisser_run."),
        scan_evidence_token: z.string().min(20).describe("Токен scanner текущего запуска."),
        symbol: z.enum(TRADE_SYMBOLS).describe("Символ кандидата, например TAO_USDT."),
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        openWorldHint: true,
      },
    },
    async ({ run_token, scan_evidence_token, symbol }, extra) => {
      const run = verifyRunToken(run_token, { session: sessionId(extra) });
      if (run.mode === "overview") {
        throw new Error("SWISSER integrity error: overview must not use snapshots");
      }
      const scan = verifyScanEvidenceToken(scan_evidence_token, { run });
      let data;
      let evidence;
      for (let attempt = 0; attempt < 2; attempt += 1) {
        data = await fetchSwisser("/api/snapshot_action_v6", { symbol });
        try {
          evidence = createSnapshotEvidenceToken({ run, scan, data, symbol });
          break;
        } catch (error) {
          if (attempt === 0 && /older than the current scanner/.test(error.message)) continue;
          throw error;
        }
      }
      return {
        content: [{ type: "text", text: `Свежий snapshot ${symbol} проверен. Передай snapshot_evidence_token в renderer этого же запуска.` }],
        structuredContent: {
          ...data,
          run_id: run.run_id,
          snapshot_evidence_token: evidence.token,
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
        "Финальный renderer с жёсткой проверкой происхождения данных. Требует run_token и scan_evidence_token одного " +
        "текущего запуска; каждый торговый кандидат в setups и каждая строка entry требуют свежий snapshot token. " +
        "Цена, 4h/1h/15m/1m, активная идея, BTC и время карточки строятся сервером из evidence и не принимаются " +
        "от модели. В overview — шесть монет без кандидатов; в setups — шесть монет и любое число реальных " +
        "кандидатов; в entry — только сохранённый набор. Не показывай RR.",
      inputSchema: {
        mode: z
          .enum(["overview", "setups", "entry", "quick", "trades", "day"])
          .describe("Используй overview, setups или entry; старые aliases сохранены для совместимости."),
        run_token: z.string().min(20),
        scan_evidence_token: z.string().min(20),
        snapshot_evidence_tokens: z.array(z.string().min(20)).max(6),
        lead: z.string().min(1).max(180).describe("Один главный вывод над таблицей."),
        market_rows: z
          .array(
            z.object({
              symbol: z.enum(["TAO", "HYPE", "SOL", "XRP", "DOGE", "ETH"]),
              priority: z
                .enum(["top", "secondary", "watch", "none"])
                .optional()
                .describe("Только аналитический tier в setups; structural fields добавит сервер."),
              note: z.string().min(1).max(260),
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
                entry_condition: z.string().min(1).max(320),
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
          .describe("Пусто для overview; каждый кандидат требует snapshot token текущего запуска."),
        conclusion: z.string().min(1).max(420),
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
      const card = buildVerifiedCard({
        runToken: input.run_token,
        scanEvidenceToken: input.scan_evidence_token,
        snapshotEvidenceTokens: input.snapshot_evidence_tokens,
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
