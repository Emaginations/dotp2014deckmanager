/**
 * 后端调用封装。
 *
 * 后端所有「只读」端点都返回 `{ok, data, ms}`，出错也返回 200 + `ok:false`，
 * 所以这里统一解包 —— 调用方拿到的要么是 data，要么抛出一个带 hint/trace 的错误。
 */

export interface ApiError extends Error {
  type?: string;
  hint?: string;
  trace?: string;
  where?: string;
}

let onError: ((e: ApiError) => void) | null = null;

/** 注册一个全局错误回调 —— App 用它弹 toast + 记进诊断面板。 */
export function setErrorHandler(fn: (e: ApiError) => void) {
  onError = fn;
}

function fail(e: ApiError): never {
  onError?.(e);
  throw e;
}

async function unwrap<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    return fail(
      Object.assign(new Error(`HTTP ${res.status}`), {
        type: "HttpError",
        hint: txt.slice(0, 300),
      })
    );
  }
  const body = await res.json();
  if (body && body.ok === false) {
    const e = body.error || {};
    return fail(
      Object.assign(new Error(e.msg || "未知错误"), {
        type: e.type,
        hint: e.hint,
        trace: e.trace,
        where: e.where,
      })
    );
  }
  return (body?.data ?? body) as T;
}

async function get<T>(path: string): Promise<T> {
  return unwrap<T>(await fetch(path));
}

async function send<T>(path: string, method: string, body?: unknown): Promise<T> {
  return unwrap<T>(
    await fetch(path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  );
}

/* ---------------------------------------------------------------- 类型 */

export interface CardSlim {
  key: string;
  en: string;
  zh: string;
  cost: string;
  cmc: number;
  colors: string[];
  type: string;
  sub: string;
  line: string;
  rarity: string;
  rarity_cn: string;
  set: string;
  src: string;
  power: string;
  tough: string;
  kw: string[];
  /** 规则文本（截到 600 字）—— 缩略卡面也要渲染它 */
  text: string;
  flavor: string;
}

export interface CardFull extends CardSlim {
  cardname: string;
  mv: string;
  artist: string;
  legal: string[];
  kw_cn: string[];
  title: string;
  title_cn: string;
  has_art?: boolean;
  file: string;
  wad?: string;
}

export interface SearchResult {
  total: number;
  offset: number;
  limit: number;
  /** 传了 `focus` 时才有：那张卡在**完整结果**里排第几（不满足筛选时为 null） */
  focus_index?: number | null;
  items: CardSlim[];
}

export interface Meta {
  count: number;
  types: [string, number][];
  sets: [string, number][];
  srcs: [string, number][];
  rarities: [string, number, string][];
  legal: [string, number][];
  keywords: [string, number, string][];
  cmc_max: number;
  cmc_hist: [number, number][];
  colors: string[];
  color_names: Record<string, string>;
  top_subs: [string, number][];
}

export interface DeckStats {
  n_spell: number;
  n_nonbasic_land: number;
  n_land: number;
  /** 引擎实际会补的基本地张数 */
  n_basic: number;
  /** 声明的 `min*` 下限之和 */
  min_lands_sum: number;
  /** `<CARD>` 条数 —— 用户能编辑的部分 */
  n_main: number;
  /** 全场张数 = n_main + n_basic（正常是 60） */
  total: number;
  n_side: number;
  n_side_kinds: number;
  curve: number[];
  avg_cmc: number;
  color_dist: Record<string, number>;
  auto_land: number;
  to_60: number;
  land_pct: number;
  over_60: boolean;
  min_lands_cn: Record<string, number>;
}

export interface DeckData {
  id: string;
  name_cn: string;
  name_en: string;
  uid: string;
  colors: string[];
  /** 牌盒封面用的卡（**卡池 key**，不是 ARTID） */
  cover: string;
  /** 鹏洛客立绘用的卡（空则跟 `cover`） */
  avatar: string;
  main: Record<string, number>;
  min_lands: Record<string, number>;
  side: Record<string, number>;
  guide: Record<string, string>;
  source: string;
  created: string;
  updated: string;
}

export interface DeckEntry {
  deck: DeckData;
  stats: DeckStats;
}

/** `GET /api/decks/{id}` —— 比列表多带一份**这副牌组用到的卡**的详情。
 *  打包向导的封面/立绘只从这里面选，不是从整个卡池搜。 */
export interface DeckDetail extends DeckEntry {
  cards: CardSlim[];
}

export interface LogEntry {
  t: string;
  level: string;
  where: string;
  msg: string;
  trace?: string;
}

/* ---------------------------------------------------------------- 端点 */

export interface SearchParams {
  q?: string;
  colors?: string[];
  color_mode?: string;
  type?: string;
  sub?: string;
  cmc_min?: number;
  cmc_max?: number;
  rarity?: string[];
  kw?: string[];
  set?: string[];
  src?: string[];
  legal?: string[];
  has_art?: boolean;
  limit?: number;
  offset?: number;
  sort?: string;
  desc?: boolean;
  /** 卡池 key。传了就额外返回 `focus_index`，用来定位该卡在第几页 */
  focus?: string;
}

export function searchCards(p: SearchParams): Promise<SearchResult> {
  const qs = new URLSearchParams();
  const put = (k: string, v: unknown) => {
    if (v === undefined || v === null || v === "" || v === false) return;
    if (Array.isArray(v)) {
      if (v.length) qs.set(k, v.join(","));
    } else qs.set(k, String(v));
  };
  put("q", p.q);
  put("colors", p.colors);
  put("color_mode", p.color_mode);
  put("type", p.type);
  put("sub", p.sub);
  put("cmc_min", p.cmc_min);
  put("cmc_max", p.cmc_max);
  put("rarity", p.rarity);
  put("kw", p.kw);
  put("set", p.set);
  put("src", p.src);
  put("legal", p.legal);
  put("has_art", p.has_art);
  put("limit", p.limit);
  put("offset", p.offset);
  put("sort", p.sort);
  put("desc", p.desc);
  put("focus", p.focus);
  return get<SearchResult>(`/api/cards?${qs}`);
}

export const getCard = (key: string) => get<CardFull>(`/api/card/${encodeURIComponent(key)}`);

/** 卡名（中英文都认）-> 卡池 key。查不到 `key` 是空串。 */
export const cardKey = (name: string) =>
  get<{ key: string; name: string }>(`/api/cardkey?name=${encodeURIComponent(name)}`);
export const getMeta = () => get<Meta>("/api/meta");
export const getFrameList = () =>
  get<Record<string, string[]>>("/api/framelist");
export const getLogs = (limit = 100) =>
  get<LogEntry[]>(`/api/logs?limit=${limit}`);

/* ------------------------------------------------- 游戏里已有的牌组 */

export interface GameDeck {
  id: string;
  name: string;
  name_tag: string;
  uid: string;
  wad: string;
  file: string;
  /** `custom` 自制 / `system` 系统自带 / `community` 社区包 */
  group: "custom" | "system" | "community";
  colors: string[];
  content_pack: string;
  always_available: boolean;
  /** `never_available` 的牌组在游戏里选不到（boss 战、被封印的牌池） */
  playable: boolean;
  personality: string;
  deck_box_image: string;
  n_card: number;
  land_config: Record<string, number>;
  card_details?: (CardSlim & { count: number; missing?: boolean })[];
  /** 解锁表（游戏里「已解锁」那 30 张）翻好的详情。**大部分官方牌组是空的** ——
   *  只有 uid 1~10 那十副玩家牌组和几副 DLC 才有。 */
  unlock_details?: (CardSlim & { count: number; missing?: boolean })[];
}

export interface GameDeckGroup {
  group: string;
  name_cn: string;
  desc: string;
  count: number;
  decks: GameDeck[];
}

export const listGameDecks = () =>
  get<{ groups: GameDeckGroup[]; total: number }>("/api/gamedecks");

export const getGameDeck = (id: string) =>
  get<GameDeck>(`/api/gamedecks/${encodeURIComponent(id)}`);

export interface WritableInfo {
  writable: boolean;
  reason: string;
  group?: string;
  wad?: string;
}

export const deckWritable = (id: string) =>
  get<WritableInfo>(`/api/gamedecks/${encodeURIComponent(id)}/writable`);

export interface SaveResult {
  wad: string;
  file?: string;
  backup?: string;
  size_before?: number;
  size_after?: number;
  cards?: number;
  changed?: boolean;
  group?: string;
  group_cn?: string;
  unknown?: string[];
}

/** **把卡表直接写回游戏 WAD**（覆盖原牌组，不是复制）。 */
export const saveGameDeck = (
  id: string,
  cards: string[],
  land_config: Record<string, number>,
  allow_system: boolean
) =>
  send<SaveResult>(`/api/gamedecks/${encodeURIComponent(id)}/save`, "POST", {
    cards,
    land_config,
    allow_system,
  });

/** 删除自制牌组 —— 整个包挪到 `data/trash/`，可恢复。 */
export const deleteGameDeck = (id: string) =>
  send<{ deleted: string; wad: string; moved_to: string; restore_hint: string }>(
    `/api/gamedecks/${encodeURIComponent(id)}`,
    "DELETE"
  );

/* ------------------------------------------------- WAD 维护 / 设置 */

export interface WadInfo {
  name: string;
  size: number;
  mtime: string;
  backed_up: boolean;
}

export const listWads = () =>
  get<{ dir: string; count: number; bytes: number; wads: WadInfo[] }>("/api/wads");

export const listWadBackups = () =>
  get<{ name: string; size: number; time: string }[]>("/api/wads/backups");

export const backupWads = (names?: string[]) =>
  send<{ dir: string; backed: string[]; skipped: number; count: number; bytes: number }>(
    "/api/wads/backup", "POST", names ? { names } : {}
  );

export const restoreWad = (name: string) =>
  send<{ restored: string }>("/api/wads/restore", "POST", { name });

export interface BsfRow {
  wad: string;
  path: string;
  name: string;
  size: number;
  entries: number;
  overflow: boolean;
  tail: string;
  tail_len: number;
}

export const scanBsf = () =>
  get<{ bad: BsfRow[]; clean: number; wads_scanned: number; need_fix: boolean }>(
    "/api/bsf/scan"
  );

export interface BsfFixResult {
  changed: boolean;
  wad: string;
  msg?: string;
  fixed?: { path: string; name: string; removed: number }[];
  size_before?: number;
  size_after?: number;
  backup?: string;
  still_bad?: number;
  header_xml_ok?: boolean;
  dirs_ok?: boolean;
}

export const fixBsf = (wad: string) =>
  send<BsfFixResult>("/api/bsf/fix", "POST", { wad });

export const reindex = (force: boolean) =>
  send<Record<string, unknown>>("/api/reindex", "POST", force);

export const saveSettings = (patch: Record<string, unknown>) =>
  send<{ settings: Record<string, unknown>; game_dir_changed: boolean }>(
    "/api/settings", "POST", patch
  );

export const browseDir = (start?: string) =>
  send<{ path: string | null; reason?: string }>("/api/browse-dir", "POST", {
    start,
  });

export const listBackups = () =>
  get<{ name: string; size: number; time: string }[]>("/api/backups");

export const listTrash = () =>
  get<{ stamp: string; name: string; size: number }[]>("/api/trash");

export const restoreTrash = (stamp: string, name: string) =>
  send<{ restored: string }>("/api/trash/restore", "POST", { stamp, name });

/** 复制成一份可编辑的工程（不改原牌组）。 */
export const importGameDeck = (id: string) =>
  send<{ deck: DeckData; stats: DeckStats; unmapped: string[] }>(
    `/api/gamedecks/${encodeURIComponent(id)}/import`,
    "POST",
    {}
  );

export const listDecks = () => get<DeckEntry[]>("/api/decks");
export const getDeck = (id: string) =>
  get<DeckDetail>(`/api/decks/${encodeURIComponent(id)}`);
export const newDeck = (name_cn: string, name_en: string) =>
  send<DeckData>("/api/decks", "POST", { name_cn, name_en });
export const saveDeck = (id: string, d: DeckData) =>
  send<DeckData>(`/api/decks/${encodeURIComponent(id)}`, "PUT", d);
export const deleteDeck = (id: string) =>
  send<{ deleted: boolean }>(`/api/decks/${encodeURIComponent(id)}`, "DELETE");
/** 复制一份工程。id 和名字都会自动加后缀防撞，连点多次不会互相覆盖。 */
export const duplicateDeck = (id: string) =>
  send<{ deck: DeckData; stats: DeckStats }>(
    `/api/decks/${encodeURIComponent(id)}/duplicate`, "POST", {}
  );
export const deckStats = (id: string, d?: DeckData) =>
  send<DeckStats>(`/api/decks/${encodeURIComponent(id)}/stats`, "POST", d ?? null);

export interface Health {
  version: string;
  game_dir: string;
  game_dir_ok: boolean;
  indexes: Record<string, boolean>;
  log_file: string;
}
export const getHealth = () => get<Health>("/api/health");

/** 图片走原生 <img>，不进 JSON —— 2.2 万张图不能塞进响应体。 */
export const artUrl = (key: string, size: "thumb" | "full" = "thumb") =>
  `/api/art/${encodeURIComponent(key)}?size=${size}`;
export const frameUrl = (group: string, name: string) =>
  `/api/frames/${group}/${name}.png`;

/* ------------------------------------------------- 打包成游戏 WAD */

/** 合成后的牌盒封面预览（带透明）。走的是和打包时**同一条**渲染管线。 */
export const previewCoverUrl = (key: string, zoom = 1) =>
  `/api/preview/cover?key=${encodeURIComponent(key)}&zoom=${zoom}`;

/** 立绘预览。`kind`：avatar / locked / backplate / full */
export const previewPortraitUrl = (key: string, kind: string) =>
  `/api/preview/portrait?key=${encodeURIComponent(key)}&kind=${kind}`;

export interface PackProblem {
  level: "error" | "warn";
  msg: string;
}

export interface PackPlan {
  ok: boolean;
  problems: PackProblem[];
  info: {
    n_main: number;
    n_basic: number;
    total: number;
    n_unlock: number;
    colors: string[];
    basics: string[];
    target: number;
  };
  cover: string;
  avatar: string;
  uid: string;
  uid_num: number;
  filename: string;
}

export const packPlan = (id: string, cover: string, avatar: string) =>
  get<PackPlan>(
    `/api/decks/${encodeURIComponent(id)}/pack-plan` +
    `?cover=${encodeURIComponent(cover)}&avatar=${encodeURIComponent(avatar)}`
  );

export interface PackResult {
  uid: string;
  uid_num: number;
  tag: string;
  cover_name: string;
  pw_name: string;
  filename: string;
  entries: number;
  bytes: number;
  out_path: string;
  installed: string | null;
  backup: string | null;
  plan: PackPlan;
}

export const packDeck = (
  id: string,
  p: { cover: string; avatar: string; install?: boolean; overwrite?: boolean }
) => send<PackResult>(`/api/decks/${encodeURIComponent(id)}/pack`, "POST", p);

/** 牌盒封面。`name` 是牌组 XML 里的 `deck_box_image`。
 *  后端已经裁到**正面**了（原图是 512×512 的 3D 盒子渲染，四周全是透明留白）。 */
export const deckboxUrl = (name: string) =>
  `/api/deckbox/${encodeURIComponent(name)}`;
