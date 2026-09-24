/**
 * 主界面：三栏 —— 筛选 | 卡牌网格 | 牌组。
 *
 * 搜索有 300ms 防抖 + AbortController 作陈旧性守卫（新搜索会作废旧结果，
 * 否则慢的那次返回晚了会盖掉新的）。这是 phase 的做法，必踩的坑。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import * as api from "./api";
import type { ApiError } from "./api";
import type {
  CardSlim, DeckData, DeckEntry, DeckStats, GameDeck, GameDeckGroup, Meta,
} from "./api";
import { ColorPips } from "./components/bits";
import { setManaFiles } from "./components/CardFace";
import { CardGrid } from "./components/CardGrid";
import { SortBar } from "./components/SortBar";
import { DeckPanel } from "./components/DeckPanel";
import { FilterPanel, EMPTY_FILTERS, hasCriteria, type Filters } from "./components/FilterPanel";
import { HoverZoom, useHoverTrigger } from "./components/HoverZoom";
import { PackWizard } from "./components/PackWizard";
import { Spinner } from "./components/bits";

const PAGE = 175;
const DEBOUNCE = 300;

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [health, setHealth] = useState<api.Health | null>(null);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);

  const [items, setItems] = useState<CardSlim[]>([]);
  const [total, setTotal] = useState(0);
  // `items` 第一张卡在整个结果里的下标。平常是 0；「跳转到某张卡」之后
  // 会直接加载那一页，于是不再是 0 —— 分页必须按它算，不能拿 items.length 当偏移。
  const [baseOffset, setBaseOffset] = useState(0);
  const [loading, setLoading] = useState(false);

  const [deckList, setDeckList] = useState<DeckEntry[]>([]);
  const [gameGroups, setGameGroups] = useState<GameDeckGroup[]>([]);
  /** 正在直接编辑的游戏牌组（保存 = 写回 WAD，不是写工程文件） */
  const [gameDeck, setGameDeck] = useState<GameDeck | null>(null);
  const [gameWritable, setGameWritable] = useState<api.WritableInfo | null>(null);
  const [confirmWrite, setConfirmWrite] = useState<{ reason: string } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<GameDeck | null>(null);
  /** 双击「系统自带」牌组时的拦截 —— 那是战役的一部分 */
  const [confirmOpenSystem, setConfirmOpenSystem] = useState<GameDeck | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [deck, setDeck] = useState<DeckData | null>(null);
  const [stats, setStats] = useState<DeckStats | null>(null);
  const [dirty, setDirty] = useState(false);

  const [tileWidth, setTileWidth] = useState(132);
  // 卡池要滚到哪张卡。token 递增 —— 同一张卡连删两次也得能重新触发
  const [focusCard, setFocusCard] = useState<{ key: string; token: number } | null>(null);
  const focusToken = useRef(1);
  const [error, setError] = useState<ApiError | null>(null);
  const [showDiag, setShowDiag] = useState(false);
  const [showDecks, setShowDecks] = useState(false);
  const [showExport, setShowExport] = useState(false);
  const [showPack, setShowPack] = useState(false);

  // 悬浮时按需拉完整数据 —— 网格里的 `CardSlim` 没有规则文本，
  // 直接拿它渲染浮层会是个空框。
  const loadFull = useCallback(async (key: string) => api.getCard(key), []);
  const hover = useHoverTrigger(140, loadFull);
  const abort = useRef<AbortController | null>(null);
  const cardCache = useRef<Record<string, CardSlim>>({});

  /* ---------------------------------------------------------- 启动 */

  useEffect(() => {
    api.setErrorHandler((e) => setError(e));
    // 卡面素材清单先要过来 —— 规则文本里的法术力符号靠它判断有没有图，
    // 拿不到就会把 `{C}` `{E}` 渲染成裂图。失败也不影响其它功能。
    api.getFrameList()
      .then((fl) => setManaFiles(fl.mana || []))
      .catch(() => { /* 拿不到就靠 onError 兜底 */ });
    (async () => {
      try {
        const [h, m, d, g] = await Promise.all([
          api.getHealth(), api.getMeta(), api.listDecks(),
          // 游戏牌组扫描失败不该拖垮整个启动 —— 单独兜底
          api.listGameDecks().catch(() => ({ groups: [], total: 0 })),
        ]);
        setHealth(h);
        setMeta(m);
        setDeckList(d);
        setGameGroups(g.groups);
        if (d.length) openDeck(d[0].deck.id);
      } catch {
        /* 错误已经进 setError 了 */
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* ---------------------------------------------------------- 搜索 */

  const runSearch = useCallback(
    async (f: Filters, offset: number, append: boolean) => {
      abort.current?.abort();
      const ctl = new AbortController();
      abort.current = ctl;
      setLoading(true);
      try {
        const r = await api.searchCards({ ...f, limit: PAGE, offset });
        if (ctl.signal.aborted) return;
        setTotal(r.total);
        setBaseOffset(offset);
        setItems((prev) => (append ? [...prev, ...r.items] : r.items));
        for (const it of r.items) cardCache.current[it.key] = it;
        return r;
      } catch (e) {
        if (!ctl.signal.aborted) setError(e as ApiError);
        return null;
      } finally {
        if (!ctl.signal.aborted) setLoading(false);
      }
    },
    []
  );

  useEffect(() => {
    const t = window.setTimeout(() => runSearch(filters, 0, false), DEBOUNCE);
    return () => window.clearTimeout(t);
  }, [filters, runSearch]);

  /* ---------------------------------------------------------- 卡组 */

  const openDeck = async (id: string) => {
    try {
      const r = await api.getDeck(id);
      setDeck(r.deck);
      setStats(r.stats);
      setDirty(false);
      setShowDecks(false);
      setGameDeck(null);
      setGameWritable(null);
      resetHistory();
      for (const c of (r as unknown as { cards?: CardSlim[] }).cards || [])
        cardCache.current[c.key] = c;
    } catch (e) {
      setError(e as ApiError);
    }
  };

  /** **直接打开游戏里的牌组来改** —— 保存时写回 WAD，不产生工程文件。 */
  const openGameDeck = async (gid: string) => {
    try {
      const g = await api.getGameDeck(gid);
      const main: Record<string, number> = {};
      for (const c of g.card_details || []) {
        const nm = c.en || c.zh || c.key;
        main[nm] = c.count;
        cardCache.current[c.key] = c;
      }
      // 解锁表也要带上 —— 写死 `{}` 的话「解锁表」标签永远是 0，
      // 而官方那十副玩家牌组（裂片妖、Jace…）其实都有 30 张。
      const side: Record<string, number> = {};
      for (const c of g.unlock_details || []) {
        const nm = c.en || c.zh || c.key;
        side[nm] = c.count;
        cardCache.current[c.key] = c;
      }
      const asDeck: DeckData = {
        id: g.id,
        name_cn: g.name,
        name_en: g.name_tag,
        uid: g.uid,
        colors: g.colors,
        // 不要拿 `g.deck_box_image` 当 cover —— 那是**合成好的牌盒贴图名**
        // （`D14_KRUFA`），而 cover 存的是**卡池 key**，两个命名空间。
        // 游戏牌组也不走打包向导（它只能「写回游戏包」），留空即可。
        cover: "",
        avatar: "",
        main,
        min_lands: g.land_config || {},
        side,
        guide: {},
        source: `${g.wad} · ${g.name_tag}`,
        created: "",
        updated: "",
      };
      setDeck(asDeck);
      setGameDeck(g);
      setDeckList((prev) => prev);
      setShowDecks(false);
      setDirty(false);
      resetHistory();
      setStats(await api.deckStats(asDeck.id, asDeck));
      setGameWritable(await api.deckWritable(gid));
    } catch (e) {
      setError(e as ApiError);
    }
  };

  /** 双击游戏牌组的入口 —— **系统自带的先拦一道**。
   *
   *  系统牌组是战役推进时对手用的那批，改了以后战役对局会变得莫名其妙
   *  （官方难度曲线是按原牌表配的）。社区包不拦：它跟战役无关，
   *  而且保存时本来就会为「覆盖原作者内容」再弹一次。 */
  const openGameDeckGuarded = (d: GameDeck) => {
    if (d.group === "system") setConfirmOpenSystem(d);
    else openGameDeck(d.id);
  };

  const flash = (msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast(null), 4000);
  };

  /* ---------------------------------------------------------- 撤销 / 重做 */

  // 用 ref 存栈（改动不同步触发渲染），但**每次 push 后必定紧跟一次 setDeck**，
  // 所以按钮的禁用态总能跟着刷新 —— 不需要额外的版本号状态。
  const hist = useRef<DeckData[]>([]);
  const future = useRef<DeckData[]>([]);
  const renaming = useRef(false);

  /** 换了一副牌就清空 —— 撤销不该跨卡组。 */
  const resetHistory = useCallback(() => {
    hist.current = [];
    future.current = [];
    renaming.current = false;
  }, []);

  /** 每次改动**之前**把当前状态压栈。 */
  const pushHistory = useCallback((d: DeckData) => {
    hist.current.push(d);
    if (hist.current.length > 200) hist.current.shift();
    future.current = [];                       // 新操作会让重做链失效
  }, []);

  const applyDeck = useCallback((d: DeckData) => {
    setDeck(d);
    setDirty(true);
    refreshStats(d);
  }, []);

  const undo = useCallback(() => {
    if (!deck || hist.current.length === 0) return;
    const prev = hist.current.pop()!;
    future.current.push(deck);
    applyDeck(prev);
    flash(`撤销（还能回退 ${hist.current.length} 步）`);
  }, [deck, applyDeck]);

  const redo = useCallback(() => {
    if (!deck || future.current.length === 0) return;
    const next = future.current.pop()!;
    hist.current.push(deck);
    applyDeck(next);
  }, [deck, applyDeck]);

  // Ctrl+Z 撤销，Ctrl+Alt+Z 重做
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "z") return;
      // 在输入框里按 Ctrl+Z 交给浏览器做文本撤销，别抢
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable))
        return;
      e.preventDefault();
      if (e.altKey) redo();
      else undo();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [undo, redo]);

  const askDeleteDeck = (d: GameDeck) => setConfirmDelete(d);

  /** 删除自制牌组 —— 后端是把整个 WAD 挪到 `data/trash/`，可恢复。 */
  const doDeleteDeck = async () => {
    if (!confirmDelete) return;
    const target = confirmDelete;
    setConfirmDelete(null);
    try {
      const r = await api.deleteGameDeck(target.id);
      setGameGroups((await api.listGameDecks()).groups);
      if (gameDeck?.id === target.id) {
        setGameDeck(null);
        setDeck(null);
        setStats(null);
      }
      flash(`已删除「${r.deleted}」· 可在 data/trash 找回`);
    } catch (e) {
      setError(e as ApiError);
    }
  };

  /** 从牌组里拖一张卡到卡池区域 = 删掉它，**并把卡池滚到那张卡**。
   *  拖出去以后视线还留在原处，不滚过去的话不知道删的是哪张。
   *
   *  卡池两万多张、一页只有 175，所以**绝大多数卡根本不在 DOM 里** ——
   *  先问后端它排在完整结果的第几位，再直接加载那一页。 */
  const dropRemoveCard = async (name: string) => {
    if (!deck) return;
    const jump = (key: string) =>
      setFocusCard({ key, token: focusToken.current++ });

    // 已经在当前这一页里 —— 直接滚，不用多跑一趟
    const shown = items.find((c) => c.en === name || c.zh === name);
    if (shown) {
      remove(name);
      jump(shown.key);
      return;
    }

    // 没显示出来，得先拿到它的 key。
    //
    // ⚠️ **不能只信 `cardCache`**。后端对「卡池里查不到的卡」会塞一条
    // `{key: 原始bag键, missing: true}`（`api.py` 的 `details()`），前端原样存进来，
    // 于是缓存里可能存在 `key` 是空串/undefined 的条目 —— 而 `URLSearchParams`
    // 对 undefined **是静默丢弃**的，`focus` 根本没发出去，后端返回的
    // `focus_index` 永远是 null。表现出来就是「不管怎么筛都跳不过去」，
    // 而且提示还赖在筛选头上，查半天查不出来。
    // 所以以 `/api/cardkey` 为**权威来源**，缓存只当快路。
    remove(name);

    let key =
      Object.values(cardCache.current).find(
        (c) => c.en === name || c.zh === name
      )?.key || "";
    if (!key) {
      try {
        key = (await api.cardKey(name)).key;
      } catch (e) {
        setError(e as ApiError);
        return;
      }
    }
    if (!key) {
      flash(`已从牌组移除「${name}」· 卡池里找不到这张卡，跳不过去`);
      return;
    }

    // 先定位再翻页。**探针不能走 `runSearch`** —— 那会把列表换成第 0 页
    // 再换成目标页，中间闪一下。直接调接口，只要 `focus_index`。
    let res: { focus_index?: number | null; total: number };
    try {
      res = await api.searchCards({
        ...filters, limit: 1, offset: 0, focus: key,
      });
    } catch (e) {
      setError(e as ApiError);
      return;
    }
    const idx = res.focus_index;
    if (idx === null || idx === undefined) {
      // 把**命中数**一起报出来：等于卡池总数说明没在筛、是 key 没送到；
      // 命中很少才真是筛选挡住了。没有这个数字根本分不清是哪种。
      const poolSize = meta?.count || 0;
      const why =
        poolSize && res.total >= poolSize
          ? "卡池里定位不到这张卡"
          : "不符合当前筛选条件";
      flash(`已从牌组移除「${name}」· ${why}（当前命中 ${res.total} 张），跳不过去`);
      return;
    }
    // 加载包含它的那一页，替换掉当前列表（滚动条会跟着页面重建）
    const r = await runSearch(filters, Math.floor(idx / PAGE) * PAGE, false);
    if (r) jump(key);
  };

  /** 保存 = 写回游戏 WAD。官方/社区包要先过确认弹窗。 */
  const doSaveGameDeck = async (allowSystem: boolean) => {
    if (!gameDeck || !deck) return;
    try {
      // 一张一行展开成有序列表（顺序 = deckOrderId）。传卡名，
      // 后端用 `key_of()` 归一化后翻成卡池的 <FILENAME>。
      const cards: string[] = [];
      for (const [name, cnt] of Object.entries(deck.main)) {
        for (let i = 0; i < cnt; i++) cards.push(name);
      }
      const r = await api.saveGameDeck(
        gameDeck.id, cards, deck.min_lands, allowSystem
      );
      setDirty(false);
      setConfirmWrite(null);
      flash(
        `已写回 ${r.wad}` +
          (r.backup ? ` · 备份 ${r.backup.split(/[\\/]/).pop()}` : "") +
          (r.unknown?.length ? ` · ${r.unknown.length} 张未识别` : "")
      );
    } catch (e) {
      setConfirmWrite(null);
      setError(e as ApiError);
    }
  };

  /** 改动后立刻重算统计 —— 编辑时要实时看到张数和曲线。 */
  const refreshStats = async (d: DeckData) => {
    try {
      setStats(await api.deckStats(d.id, d));
    } catch {
      /* 统计失败不影响编辑 */
    }
  };

  const addCard = (c: CardSlim) => {
    if (!deck) return;
    const name = c.en || c.zh;
    const d = { ...deck, main: { ...deck.main, [name]: (deck.main[name] || 0) + 1 } };
    pushHistory(deck);
    applyDeck(d);
  };

  const bump = (name: string, delta: number) => {
    if (!deck) return;
    const main = { ...deck.main };
    const n = (main[name] || 0) + delta;
    if (n <= 0) delete main[name];
    else main[name] = n;
    pushHistory(deck);
    applyDeck({ ...deck, main });
  };

  const remove = (name: string) => {
    if (!deck) return;
    const main = { ...deck.main };
    delete main[name];
    pushHistory(deck);
    applyDeck({ ...deck, main });
  };

  /** 改名 —— 连续打字算**一次**编辑。
   *  不防抖的话每敲一个字母就压一层栈，Ctrl+Z 要按十几下才能退掉一次改名。
   *
   *  改的是 **`name_en`（卡包名）** —— 那才是游戏里显示的那一栏，
   *  而且必须是 ASCII（游戏字体没有中文字形）。中文名是备注，界面上不给编辑。 */
  const renameTimer = useRef<number | null>(null);
  const renameDeck = (patch: { name_en?: string; name_cn?: string }) => {
    if (!deck) return;
    if (!renaming.current) {
      renaming.current = true;
      pushHistory(deck);
    }
    if (renameTimer.current) window.clearTimeout(renameTimer.current);
    renameTimer.current = window.setTimeout(() => {
      renaming.current = false;
    }, 900);
    applyDeck({ ...deck, ...patch });
  };

  const doSave = async () => {
    if (!deck) return;
    // 编辑的是游戏牌组 -> 写回 WAD（官方/社区包先弹警告）
    if (gameDeck) {
      if (gameWritable && !gameWritable.writable) {
        setConfirmWrite({ reason: gameWritable.reason });
        return;
      }
      await doSaveGameDeck(false);
      return;
    }
    try {
      const saved = await api.saveDeck(deck.id, deck);
      setDeck(saved);
      setDirty(false);
      setDeckList(await api.listDecks());
      flash("已保存到 projects/");
    } catch (e) {
      setError(e as ApiError);
    }
  };

  /** 把游戏里的牌组复制成可编辑工程 —— 不改原牌组。
   *  `silent` 用于批量导入：只建工程，不切换当前卡组、不关抽屉。 */
  const doImportGameDeck = async (gid: string, silent = false) => {
    try {
      const r = await api.importGameDeck(gid);
      if (!silent) {
        // 走 openDeck 而不是直接 setDeck —— 导入接口只回牌表（英文卡名），
        // **不带卡的详情**。不走这一趟的话 `cardCache` 是空的，
        // 牌组面板就会：显示英文名、类型归到「其他」、卡面画不出来。
        await openDeck(r.deck.id);
      }
      setDeckList(await api.listDecks());
      if (r.unmapped?.length && !silent) {
        setError(
          Object.assign(new Error(
            `有 ${r.unmapped.length} 张卡在卡池里找不到，已跳过：` +
            r.unmapped.slice(0, 5).join("、")), { type: "导入提示" }) as ApiError
        );
      }
    } catch (e) {
      setError(e as ApiError);
    }
  };

  /** 复制草稿 —— 后端负责 id / 名字防撞，连点多次不会互相覆盖。 */
  const doCopyDraft = async (id: string) => {
    try {
      const r = await api.duplicateDeck(id);
      setDeckList(await api.listDecks());
      await openDeck(r.deck.id);      // 复制完直接打开副本，省得再找一遍
      flash(`已复制成「${r.deck.name_cn}」`);
    } catch (e) {
      setError(e as ApiError);
    }
  };

  /** 复制游戏牌组 —— 走 import，落成一份独立工程（不改原包）。 */
  const doCopyGame = async (gid: string) => {
    try {
      const r = await api.importGameDeck(gid);
      setDeckList(await api.listDecks());
      await openDeck(r.deck.id);
      flash(`已复制成「${r.deck.name_cn}」`);
      if (r.unmapped?.length) {
        setError(Object.assign(new Error(
          `有 ${r.unmapped.length} 张卡在卡池里找不到，已跳过：` +
          r.unmapped.slice(0, 5).join("、")), { type: "导入提示" }) as ApiError);
      }
    } catch (e) {
      setError(e as ApiError);
    }
  };

  const doNew = async (name_cn: string) => {
    try {
      const d = await api.newDeck(name_cn, name_cn);
      setDeck(d);
      setStats(await api.deckStats(d.id, d));
      setDeckList(await api.listDecks());
      setShowDecks(false);
      setDirty(false);
      resetHistory();
    } catch (e) {
      setError(e as ApiError);
    }
  };

  /* ---------------------------------------------------------- 渲染 */

  const countOf = useCallback(
    (key: string) => {
      if (!deck) return 0;
      const c = cardCache.current[key];
      const name = c?.en || c?.zh;
      return (name && deck.main[name]) || 0;
    },
    [deck]
  );

  const criteria = hasCriteria(filters);
  const leftWidth = 264;
  // 「牌组 N」= 游戏里的牌组数 + 还没打包的草稿数
  const deckTotal =
    gameGroups.reduce((s, g) => s + g.count, 0) + deckList.length;

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden">
      {/* 顶栏 */}
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-hairline px-4">
        <div className="flex items-center gap-2">
          <span className="text-lg">🃏</span>
          <span className="text-sm font-semibold tracking-wide">
            万智牌 2014 卡组管理器
          </span>
        </div>

        {/* 撤销 / 重做 —— 放最左边，和右边的牌组/设置区分开 */}
        <div className="ml-1 flex items-center gap-0.5 border-l border-hairline pl-2">
          <HistoryBtn
            onClick={undo}
            disabled={hist.current.length === 0}
            title={`撤销 (Ctrl+Z)${hist.current.length ? ` · 还能退 ${hist.current.length} 步` : ""}`}
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" strokeWidth="2.2"
                 strokeLinecap="round" strokeLinejoin="round">
              <path d="M9.5 14.5 4 9l5.5-5.5" />
              <path d="M4 9h9.5a6.5 6.5 0 0 1 0 13H10" />
            </svg>
          </HistoryBtn>
          <HistoryBtn
            onClick={redo}
            disabled={future.current.length === 0}
            title={`重做 (Ctrl+Alt+Z)${future.current.length ? ` · 可前进 ${future.current.length} 步` : ""}`}
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" strokeWidth="2.2"
                 strokeLinecap="round" strokeLinejoin="round">
              <path d="M14.5 14.5 20 9l-5.5-5.5" />
              <path d="M20 9h-9.5a6.5 6.5 0 0 0 0 13H14" />
            </svg>
          </HistoryBtn>
        </div>

        <div className="ml-auto flex items-center gap-2 text-xs text-fg-dim">
          {health && !health.game_dir_ok && (
            <span className="rounded-tile border border-red-500/40 bg-red-500/10 px-2 py-0.5 text-red-300">
              游戏目录不存在
            </span>
          )}
          <span className="tabular-nums">
            {criteria ? `${items.length} / ${total}` : `${total}`} 张命中
          </span>
          {loading && <Spinner />}
        </div>

        {/* 卡组入口 —— 右上角，显示**牌组总数**而不是当前那副
            （当前卡组名在右侧牌组面板顶部一直看得到，这里再占一块反而挤） */}
        <button
          onClick={() => setShowDecks(true)}
          title="打开卡组列表（双击列表里的牌组即可编辑）"
          className="ml-3 flex h-8 items-center gap-2 rounded-panel border border-hairline
                     bg-black/20 pl-2.5 pr-2 transition
                     hover:border-ember/50 hover:bg-ember/[0.07]"
        >
          <span className="text-sm leading-none">📚</span>
          <span className="text-sm font-medium">牌组</span>
          <span className="text-sm tabular-nums text-fg-muted">{deckTotal}</span>
          {dirty && (
            <span
              className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400"
              title="当前卡组有未保存的改动"
            />
          )}
          <span className="shrink-0 text-[10px] text-fg-dim">▾</span>
        </button>

        {/* 设置按钮在卡组按钮**右边** */}
        <button
          onClick={() => setShowDiag(true)}
          className="ml-2 flex h-8 items-center gap-1.5 rounded-panel border border-hairline
                     bg-black/20 px-2.5 transition
                     hover:border-hairline-hover hover:bg-white/[0.05]"
          title="游戏目录 / 索引 / WAD 备份 / .bsf 修复 / 诊断日志"
        >
          <span className="text-[13px] leading-none">⚙</span>
          <span className="text-sm">设置</span>
        </button>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* 左：筛选 */}
        <aside
          className="shrink-0 border-r border-hairline"
          style={{ width: leftWidth }}
        >
          <FilterPanel
            meta={meta}
            value={filters}
            onChange={setFilters}
            tileWidth={tileWidth}
            onTileWidth={setTileWidth}
          />
        </aside>

        {/* 中：排序条（置顶）+ 卡牌网格 */}
        <main className="flex min-w-0 flex-1 flex-col">
          <SortBar value={filters} onChange={setFilters} />
          {/* ⚠️ 网格是 `h-full`，得套一层有确定高度的 flex 子元素，
              否则它会去量整个 `<main>` 的高度、把排序条压在下面。 */}
          <div className="min-h-0 flex-1">
            <CardGrid
              items={items}
              total={total}
              tileWidth={tileWidth}
              loading={loading}
              hasMore={baseOffset + items.length < total}
              onLoadMore={() => runSearch(filters, baseOffset + items.length, true)}
              onHover={hover.enter}
              onLeave={hover.leave}
              onAdd={addCard}
              countOf={countOf}
              onDropRemove={dropRemoveCard}
              focusCard={focusCard}
              baseOffset={baseOffset}
              onJumpTop={() => runSearch(filters, 0, false)}
            />
          </div>
        </main>

        {/* 右：牌组 */}
        <aside className="w-[340px] shrink-0 border-l border-hairline">
          <DeckPanel
            deck={deck}
            stats={stats}
            cards={cardCache.current}
            onBump={bump}
            onRemove={remove}
            onHover={hover.enter}
            onLeave={hover.leave}
            onRename={renameDeck}
            onSave={doSave}
            onExport={() => setShowExport(true)}
            onBuild={() => setShowPack(true)}
            dirty={dirty}
            gameMode={
              gameDeck
                ? {
                    name: gameDeck.name,
                    group_cn:
                      gameDeck.group === "custom" ? "自制"
                        : gameDeck.group === "system" ? "系统自带" : "社区包",
                    writable: gameWritable?.writable ?? true,
                  }
                : null
            }
            onDropCard={(key) => {
              const c = cardCache.current[key];
              if (c) addCard(c);
            }}
          />
        </aside>
      </div>

      <HoverZoom card={hover.card} />

      {/* 卡组列表 */}
      <AnimatePresence>
        {showDecks && (
          <DeckDrawer
            decks={deckList}
            gameGroups={gameGroups}
            current={deck?.id}
            onOpenGame={openGameDeckGuarded}
            onCopyDraft={doCopyDraft}
            onCopyGame={doCopyGame}
            onOpenDraft={openDeck}
            onNew={doNew}
            onDelete={askDeleteDeck}
            onDeleteDraft={async (id: string, nm: string) => {
              if (!window.confirm(`删除草稿「${nm}」？`)) return;
              try {
                await api.deleteDeck(id);
                setDeckList(await api.listDecks());
                if (deck?.id === id && !gameDeck) {
                  setDeck(null);
                  setStats(null);
                }
                flash(`已删除草稿「${nm}」`);
              } catch (e) {
                setError(e as ApiError);
              }
            }}
            onClose={() => setShowDecks(false)}
          />
        )}
      </AnimatePresence>

      {/* 错误提示 */}
      <AnimatePresence>
        {error && (
          <ErrorToast error={error} onClose={() => setError(null)}
                     onDiag={() => { setShowDiag(true); setError(null); }} />
        )}
      </AnimatePresence>

      <AnimatePresence>
        {showDiag && <SettingsDrawer
          onClose={() => setShowDiag(false)}
          onGameDirChanged={() => {
            setDeckList([]); setGameGroups([]);
            api.listDecks().then(setDeckList).catch(() => {});
            api.listGameDecks().then((g) => setGameGroups(g.groups)).catch(() => {});
          }}
        />}
      </AnimatePresence>

      <AnimatePresence>
        {showExport && deck && (
          <ExportDrawer deck={deck} onClose={() => setShowExport(false)} />
        )}
      </AnimatePresence>

      {/* 打包向导 —— 做成游戏能加载的 WAD 并装进游戏目录 */}
      <AnimatePresence>
        {showPack && deck && !gameDeck && (
          <PackWizard
            deck={deck}
            onClose={() => setShowPack(false)}
            onPacked={async () => {
              // 装完游戏目录多了一个包 —— 刷新牌组列表和索引状态
              setDeckList(await api.listDecks());
              setHealth(await api.getHealth());
            }}
          />
        )}
      </AnimatePresence>

      {/* 双击系统自带牌组 —— 战役体验的提醒 */}
      <AnimatePresence>
        {confirmOpenSystem && (
          <ConfirmDialog
            title="要改系统自带的牌组？"
            confirmText="确定修改"
            onConfirm={() => {
              const d = confirmOpenSystem;
              setConfirmOpenSystem(null);
              openGameDeck(d.id);
            }}
            onCancel={() => setConfirmOpenSystem(null)}
            body={
              <>
                <p className="text-fg-muted">
                  <b className="text-fg">{confirmOpenSystem.name}</b> 是「系统自带」里的牌组
                  <span className="text-fg-dim">（{confirmOpenSystem.wad}）</span>。
                </p>
                <p className="mt-2 text-amber-200/90">
                  修改系统卡组可能破坏游戏战役体验。
                </p>
                <ul className="mt-3 space-y-1 text-fg-dim">
                  <li>· 战役里对手用的就是这批牌，官方难度曲线按原牌表配的</li>
                  <li>· 打开本身不改动任何文件，只有点了「写回游戏包」才会</li>
                  <li>· 真要写回时还会再弹一次，并自动整包备份到 <code>data/backups/</code></li>
                </ul>
              </>
            }
          />
        )}
      </AnimatePresence>

      {/* 改官方/社区包的确认 —— 这是不可逆的覆盖，必须拦一道 */}
      <AnimatePresence>
        {confirmWrite && (
          <ConfirmDialog
            title="要覆盖游戏自带的包？"
            body={
              <>
                <p className="text-fg-muted">{confirmWrite.reason}</p>
                <ul className="mt-3 space-y-1 text-fg-dim">
                  <li>· 装了这个包的所有存档都会看到改动</li>
                  <li>· 重装那个包，改动就没了</li>
                  <li>· 写之前会自动整包备份到 <code>data/backups/</code></li>
                </ul>
                <p className="mt-3 text-fg-muted">
                  想保留原包的话，改用「复制副本」把它复制成自己的卡组。
                </p>
              </>
            }
            confirmText="我明白，覆盖它"
            onConfirm={() => doSaveGameDeck(true)}
            onCancel={() => setConfirmWrite(null)}
          />
        )}
      </AnimatePresence>

      {/* 删除自制牌组的确认 */}
      <AnimatePresence>
        {confirmDelete && (
          <ConfirmDialog
            title={`删除「${confirmDelete.name}」？`}
            confirmText="删除"
            onConfirm={doDeleteDeck}
            onCancel={() => setConfirmDelete(null)}
            body={
              <>
                <p className="text-fg-muted">
                  这个牌组在 <code className="text-fg-dim">{confirmDelete.wad}</code> 里，
                  而且是包里唯一的一副，所以删除 = <b>把整个包移出游戏目录</b>。
                </p>
                <ul className="mt-3 space-y-1 text-fg-dim">
                  <li>· 游戏里将不再出现这副牌组</li>
                  <li>· <b>不是真删</b> —— 文件挪到 <code>data/trash/</code>，可以拿回来</li>
                  <li>· 这个包里的封面、解锁表会一起消失</li>
                </ul>
              </>
            }
          />
        )}
      </AnimatePresence>

      {/* 成功提示 */}
      <AnimatePresence>
        {toast && (
          <motion.div
            className="fixed bottom-4 left-1/2 z-[105] -translate-x-1/2 rounded-panel
                       border border-emerald-500/40 bg-emerald-950/90 px-4 py-2
                       text-xs text-emerald-200 backdrop-blur-xl"
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 16 }}
          >
            ✓ {toast}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/** 牌组封面 —— 铺满行右侧，**左缘 30% 溶图渐变**融进行背景。
 *
 *  用 `<img>` 而不是 `background-image`：`h-full w-auto` 会让宽度**跟着图片
 *  自身的宽高比**走，后端调裁剪比例（`paths.DECKBOX_CROP_TOP`）时前端不用改。
 *  早先用 `aspectRatio: "162 / 256"` 写死，改后端就得记得同步改这里。 */
function DeckCover({ name }: { name?: string }) {
  if (!name) return null;
  // 左边 30% 从全透明渐到不透明。两侧都写：WebKit 内核只认 `-webkit-mask-image`
  const mask = "linear-gradient(to right, transparent 0%, #000 30%, #000 100%)";
  return (
    <img
      src={api.deckboxUrl(name)}
      alt=""
      aria-hidden
      draggable={false}
      className="pointer-events-none absolute inset-y-0 right-0 h-full w-auto
                 select-none object-cover"
      style={{ maskImage: mask, WebkitMaskImage: mask }}
    />
  );
}

/** 顶栏图标按钮（撤销 / 重做）。纯图标 + tooltip，不给文字。 */
function HistoryBtn({
  children, onClick, disabled, title,
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
  title: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className="flex h-7 w-7 items-center justify-center rounded-tile text-fg-muted
                 transition enabled:hover:bg-white/[0.08] enabled:hover:text-ember-bright
                 enabled:active:scale-95 disabled:opacity-25"
    >
      {children}
    </button>
  );
}

function ConfirmDialog({
  title, body, confirmText, onConfirm, onCancel,
}: {
  title: string;
  body: React.ReactNode;
  confirmText: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);
  return (
    <>
      <motion.div
        className="fixed inset-0 z-[120] bg-black/60 backdrop-blur-[2px]"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        onClick={onCancel}
      />
      <motion.div
        className="fixed left-1/2 top-1/2 z-[121] w-[min(520px,92vw)] -translate-x-1/2 -translate-y-1/2
                   rounded-panel border border-amber-500/40 bg-panel-solid/97 p-5 backdrop-blur-xl"
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.96 }}
        transition={{ duration: 0.16 }}
      >
        <div className="mb-2 flex items-center gap-2">
          <span className="text-xl">⚠️</span>
          <span className="text-base font-semibold text-amber-200">{title}</span>
        </div>
        <div className="text-xs leading-relaxed">{body}</div>
        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="rounded-tile border border-hairline px-3 py-1.5 text-xs
                       transition hover:border-hairline-hover hover:text-fg"
          >
            取消
          </button>
          <button
            onClick={onConfirm}
            className="rounded-tile border border-amber-500/50 bg-amber-500/15 px-3 py-1.5
                       text-xs font-medium text-amber-200 transition hover:bg-amber-500/25"
          >
            {confirmText}
          </button>
        </div>
      </motion.div>
    </>
  );
}

/* ---------------------------------------------------------------- 抽屉 */

function Drawer({
  children, onClose, title, width = 420,
}: {
  children: React.ReactNode;
  onClose: () => void;
  title: string;
  width?: number;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <motion.div
        className="fixed inset-0 z-40 bg-black/50 backdrop-blur-[2px]"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        onClick={onClose}
      />
      <motion.aside
        className="fixed inset-y-0 right-0 z-50 flex flex-col border-l border-hairline bg-panel-solid/95 backdrop-blur-xl"
        style={{ width }}
        initial={{ x: width }}
        animate={{ x: 0 }}
        exit={{ x: width }}
        transition={{ type: "spring", stiffness: 380, damping: 36 }}
      >
        <div className="flex h-12 shrink-0 items-center justify-between border-b border-hairline px-4">
          <span className="text-sm font-semibold">{title}</span>
          <button
            onClick={onClose}
            className="rounded-tile px-2 py-0.5 text-fg-dim transition hover:text-fg"
          >
            ✕
          </button>
        </div>
        <div className="thin-scroll min-h-0 flex-1 overflow-y-auto">{children}</div>
      </motion.aside>
    </>
  );
}

/** 卡组列表 —— **一个列表，按来源分组**，不再分页签。
 *
 * 之前分「我的卡组 / 游戏牌组」两个页签，但取消「复制副本」之后这两者
 * 其实是同一批东西（都是游戏包里的牌组），分成两栏只会让人找不到。
 */
function DeckDrawer({
  decks, gameGroups, current, onOpenGame, onOpenDraft, onNew,
  onCopyDraft, onCopyGame, onDelete, onDeleteDraft, onClose,
}: {
  decks: DeckEntry[];
  gameGroups: GameDeckGroup[];
  current?: string;
  /** 传整张牌组而不是 id —— 上层要按 `group` 决定拦不拦 */
  onOpenGame: (d: GameDeck) => void;
  onOpenDraft: (id: string) => void;
  onNew: (name: string) => void;
  onCopyDraft: (id: string) => void;
  onCopyGame: (id: string) => void;
  onDelete: (d: GameDeck) => void;
  onDeleteDraft: (id: string, name: string) => void;
  onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  // 复制模式：点「复制」后进这个状态，**单击**任意牌组就复制它。
  // 用单击而不是双击 —— 和「双击打开」区分开，不会误开原牌组。
  const [copyMode, setCopyMode] = useState(false);

  const total = gameGroups.reduce((s, g) => s + g.count, 0);

  // `projects/` 里还没打包进游戏的草稿。已经打包成自制包的（名字对得上）
  // 就不再单列 —— 否则同一副牌会出现两次。
  const customNames = new Set(
    (gameGroups.find((g) => g.group === "custom")?.decks ?? [])
      .map((d) => d.name.replace(/[[\]【】]/g, "").trim().toLowerCase())
  );
  const drafts = decks.filter(
    (e) => !customNames.has((e.deck.name_cn || "").trim().toLowerCase())
  );
  const query = q.trim().toLowerCase();
  const match = (d: GameDeck) =>
    !query ||
    d.name.toLowerCase().includes(query) ||
    d.name_tag.toLowerCase().includes(query) ||
    d.wad.toLowerCase().includes(query);

  const GROUP_STYLE: Record<string, string> = {
    custom: "text-ember-bright",
    system: "text-fg-muted",
    community: "text-fg-muted",
  };

  return (
    <Drawer title="卡组" onClose={onClose} width={520}>
      <div className="border-b border-hairline p-3">
        <div className="flex gap-2">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="搜牌组名 / 标识 / 包名…"
            className="min-w-0 flex-1 rounded-tile border border-hairline bg-black/30 px-2.5 py-1.5
                       text-sm outline-none focus:border-ember/60"
          />
          {/* 新建**一键完成**，不再先让你填名字 —— 名字默认 `NEW`，
              建完在牌组面板顶部改。id 撞车由后端加后缀（`new` → `new-2`），
              连点不会覆盖。 */}
          <button
            onClick={() => onNew("NEW")}
            title="直接新建一副空牌组，名字默认 NEW（建完可在牌组面板改）"
            className="shrink-0 rounded-tile bg-ember px-3 py-1.5 text-sm font-medium text-black
                       transition hover:bg-ember-bright"
          >
            新建
          </button>
          <button
            onClick={() => setCopyMode((v) => !v)}
            title="复制一副已有牌组"
            className={
              "shrink-0 rounded-tile border px-3 py-1.5 text-sm font-medium transition " +
              (copyMode
                ? "border-ember bg-ember/20 text-ember-bright"
                : "border-hairline hover:border-ember/60 hover:text-ember-bright")
            }
          >
            复制
          </button>
        </div>

        {/* 复制模式提示 —— 不说清楚的话，点一下牌组就变了会莫名其妙 */}
        {copyMode && (
          <div className="mt-2 flex items-center gap-2 rounded-tile border border-ember/40
                          bg-ember/10 px-2.5 py-1.5 text-[11px] text-ember-bright">
            <span>↓ 在下面<b>点一下</b>要复制哪副牌组</span>
            <button
              onClick={() => setCopyMode(false)}
              className="ml-auto shrink-0 rounded-tile border border-hairline px-2 py-0.5
                         text-fg-muted transition hover:text-fg"
            >
              取消
            </button>
          </div>
        )}
      </div>

      <div className="p-2">
        {/* 还没打包的草稿 —— 归在「自制」里，标出来 */}
        {drafts.length > 0 && (
          <div className="mb-3">
            <div className="mb-1 flex items-center gap-2 px-2 py-1.5">
              <span className="text-xs font-semibold text-amber-300">草稿</span>
              <span className="text-[10px] text-fg-faint">{drafts.length}</span>
              <span className="ml-auto text-[10px] text-fg-faint">
                还没打包进游戏，只在 projects/ 里
              </span>
            </div>
            {drafts.map(({ deck: d, stats: s }) => (
              <div
                key={d.id}
                onClick={copyMode ? () => { onCopyDraft(d.id); setCopyMode(false); } : undefined}
                onDoubleClick={() => { if (!copyMode) onOpenDraft(d.id); }}
                title={copyMode ? `点一下复制「${d.name_cn}」` : "双击编辑这份草稿"}
                className={
                  "group mb-0.5 flex cursor-pointer items-center gap-2 rounded-tile px-2 py-1.5 " +
                  "transition " +
                  (copyMode
                    ? "cursor-copy ring-1 ring-inset ring-ember/30 hover:bg-ember/15 "
                    : "hover:bg-white/[0.06] ") +
                  (current === d.id ? "bg-ember/15" : "")
                }
              >
                <ColorPips colors={d.colors} size={8} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-xs">{d.name_cn}</div>
                  <div className="truncate text-[10px] text-fg-faint">
                    {d.name_en} · {s.n_main} 条牌
                  </div>
                </div>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteDraft(d.id, d.name_cn);
                  }}
                  title="删除这份草稿"
                  className="shrink-0 rounded-tile border border-hairline px-1.5 py-0.5
                             text-[11px] leading-none text-fg-dim opacity-0 transition
                             hover:border-red-500/60 hover:text-red-400 group-hover:opacity-100"
                >
                  🗑
                </button>
              </div>
            ))}
          </div>
        )}

        {gameGroups.map((g) => {
          const ds = g.decks.filter(match);
          if (!ds.length) return null;
          const open = !collapsed[g.group];
          return (
            <div key={g.group} className="mb-3">
              <button
                onClick={() => setCollapsed((c) => ({ ...c, [g.group]: !c[g.group] }))}
                className="mb-1 flex w-full items-center gap-2 rounded-tile px-2 py-1.5
                           text-left transition hover:bg-white/[0.04]"
              >
                <span className="text-[10px] text-fg-faint">{open ? "▾" : "▸"}</span>
                <span className={"text-xs font-semibold " + (GROUP_STYLE[g.group] || "text-fg-muted")}>
                  {g.name_cn}
                </span>
                <span className="text-[10px] text-fg-faint">
                  {ds.length}
                  {ds.length !== g.count ? ` / ${g.count}` : ""}
                </span>
                <span className="ml-auto truncate text-[10px] text-fg-faint">{g.desc}</span>
              </button>
              {open &&
                ds.map((d) => (
                  <div
                    key={d.id}
                    onClick={copyMode ? () => { onCopyGame(d.id); setCopyMode(false); } : undefined}
                    onDoubleClick={() => { if (!copyMode) onOpenGame(d); }}
                    title={copyMode
                      ? `点一下复制「${d.name}」`
                      : `双击编辑这副牌组\n保存时写回 ${d.wad}`}
                    className={
                      "group relative mb-0.5 flex cursor-pointer items-center gap-2 " +
                      "overflow-hidden rounded-tile px-2 py-1.5 transition " +
                      (copyMode
                        ? "cursor-copy ring-1 ring-inset ring-ember/30 hover:bg-ember/15 "
                        : "hover:bg-white/[0.06] active:bg-white/[0.10] ") +
                      (current === d.id ? "bg-ember/15" : "")
                    }
                  >
                    <ColorPips colors={d.colors} size={8} />
                    <div className="min-w-0 flex-1 pr-7">
                      <div className="flex items-center gap-1.5 truncate text-xs">
                        {d.name}
                        {!d.playable && (
                          <span className="shrink-0 text-[9px] text-fg-faint"
                                title="游戏里选不到（boss 战 / 被封印的牌池）">
                            不可玩
                          </span>
                        )}
                        {g.group !== "custom" && (
                          <span className="shrink-0 text-[9px] text-fg-faint"
                                title={`属于「${g.name_cn}」，保存时会先弹警告`}>
                            {g.name_cn}
                          </span>
                        )}
                      </div>
                      <div className="truncate text-[10px] text-fg-faint">
                        {d.name_tag} · {d.n_card} 张 · {d.wad}
                      </div>
                    </div>
                    {/* 复制模式下的删除按钮要压在封面上面才点得到 */}
                    {g.group === "custom" && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onDelete(d);
                        }}
                        title="删除这副牌组（整个包移到回收站，可恢复）"
                        className="relative z-10 shrink-0 rounded-tile border border-hairline
                                   bg-black/70 px-1.5 py-0.5 text-[11px] leading-none
                                   text-fg-dim opacity-0 transition
                                   hover:border-red-500/60 hover:text-red-400
                                   group-hover:opacity-100"
                      >
                        🗑
                      </button>
                    )}
                    <DeckCover name={d.deck_box_image} />
                  </div>
                ))}
            </div>
          );
        })}
        {total === 0 && (
          <div className="px-4 py-10 text-center text-xs text-fg-faint">
            没扫到牌组 —— 检查游戏目录设置
          </div>
        )}
      </div>
    </Drawer>
  );
}

function ErrorToast({
  error, onClose, onDiag,
}: {
  error: ApiError;
  onClose: () => void;
  onDiag: () => void;
}) {
  return (
    <motion.div
      className="fixed bottom-4 left-1/2 z-[110] w-[min(560px,90vw)] -translate-x-1/2
                 rounded-panel border border-red-500/40 bg-red-950/90 p-3 backdrop-blur-xl"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 20 }}
    >
      <div className="flex items-start gap-2">
        <span className="text-lg leading-none">⚠️</span>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-red-200">
            {error.type || "错误"}{error.where ? ` · ${error.where}` : ""}
          </div>
          <div className="mt-0.5 break-words text-xs text-red-300/90">
            {error.message}
          </div>
          {error.hint && (
            <div className="mt-1 text-[11px] text-red-300/70">💡 {error.hint}</div>
          )}
        </div>
        <button onClick={onDiag} className="shrink-0 text-[11px] text-red-300 hover:underline">
          详情
        </button>
        <button onClick={onClose} className="shrink-0 text-red-300 hover:text-red-100">
          ✕
        </button>
      </div>
    </motion.div>
  );
}

/** 设置面板 —— 游戏目录 / 索引 / WAD 维护 / 诊断，四块。 */
function SettingsDrawer({
  onClose, onGameDirChanged,
}: {
  onClose: () => void;
  onGameDirChanged: () => void;
}) {
  const [health, setHealth] = useState<api.Health | null>(null);
  const [logs, setLogs] = useState<api.LogEntry[]>([]);
  const [dir, setDir] = useState("");
  const [dirOk, setDirOk] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const [wads, setWads] = useState<api.WadInfo[]>([]);
  const [wadBytes, setWadBytes] = useState(0);
  const [wadBackups, setWadBackups] = useState<{ name: string; size: number; time: string }[]>([]);

  const [bsf, setBsf] = useState<{ bad: api.BsfRow[]; clean: number; wads_scanned: number } | null>(null);

  const reload = async () => {
    try {
      const h = await api.getHealth();
      setHealth(h);
      setDir(h.game_dir);
      setDirOk(h.game_dir_ok);
      setLogs(await api.getLogs(200));
    } catch { /* 错误已经进全局 toast */ }
  };

  const reloadWads = async () => {
    try {
      const r = await api.listWads();
      setWads(r.wads);
      setWadBytes(r.bytes);
      setWadBackups(await api.listWadBackups());
    } catch { /* ignore */ }
  };

  useEffect(() => {
    reload();
    reloadWads();
  }, []);

  /** 所有危险/耗时操作都从这里走，统一管 loading 和提示。 */
  const run = async (key: string, fn: () => Promise<string | void>) => {
    setBusy(key);
    setNote(null);
    try {
      const msg = await fn();
      if (msg) setNote(msg);
      await reload();
      await reloadWads();
    } catch (e) {
      setNote("失败：" + (e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const Section = ({
    title, desc, children,
  }: { title: string; desc?: string; children: React.ReactNode }) => (
    <section className="mb-5 rounded-panel border border-hairline bg-black/20 p-3">
      <div className="mb-2">
        <div className="text-sm font-semibold text-fg">{title}</div>
        {desc && <div className="mt-0.5 text-[11px] leading-relaxed text-fg-dim">{desc}</div>}
      </div>
      {children}
    </section>
  );

  const Btn = ({
    onClick, children, primary, danger, disabled, k,
  }: {
    onClick: () => void; children: React.ReactNode; primary?: boolean;
    danger?: boolean; disabled?: boolean; k?: string;
  }) => (
    <button
      onClick={onClick}
      disabled={disabled || (k != null && busy === k)}
      className={
        "rounded-tile border px-2.5 py-1 text-xs transition disabled:opacity-40 " +
        (danger
          ? "border-red-500/40 text-red-300 hover:bg-red-500/15"
          : primary
            ? "border-ember/50 bg-ember/10 text-ember-bright hover:bg-ember/20"
            : "border-hairline text-fg-muted hover:border-hairline-hover hover:text-fg")
      }
    >
      {k != null && busy === k ? "处理中…" : children}
    </button>
  );

  return (
    <Drawer title="设置" onClose={onClose} width={640}>
      <div className="p-3">
        {note && (
          <div className="mb-3 rounded-tile border border-ember/40 bg-ember/10 px-3 py-2 text-xs text-ember-bright">
            {note}
          </div>
        )}

        {/* ---------------------------------------------------- 游戏目录 */}
        <Section
          title="游戏目录"
          desc="WAD 所在的文件夹。改完之后索引会按新目录的指纹自动重建。"
        >
          <div className="flex gap-2">
            <input
              value={dir}
              onChange={(e) => setDir(e.target.value)}
              spellCheck={false}
              className="min-w-0 flex-1 rounded-tile border border-hairline bg-black/40 px-2.5 py-1.5
                         font-mono text-xs outline-none focus:border-ember/60"
            />
            <Btn
              k="browse"
              onClick={() => run("browse", async () => {
                const r = await api.browseDir(dir);
                if (r.path) setDir(r.path);
                else if (r.reason) setNote(r.reason);
              })}
            >
              浏览…
            </Btn>
            <Btn
              k="savedir"
              primary
              disabled={!dir.trim() || dir === health?.game_dir}
              onClick={() => run("savedir", async () => {
                const r = await api.saveSettings({ game_dir: dir.trim() });
                if (r.game_dir_changed) {
                  onGameDirChanged();
                  return "游戏目录已改，索引需要重建 —— 点下面的「重建索引」";
                }
                return "已保存";
              })}
            >
              保存
            </Btn>
          </div>
          <div className="mt-1.5 flex items-center gap-2 text-[11px]">
            <span className={dirOk ? "text-emerald-400" : "text-red-400"}>
              {dirOk ? "✓ 目录存在" : "✗ 目录不存在"}
            </span>
            {health?.log_file && (
              <span className="truncate text-fg-faint">日志：{health.log_file}</span>
            )}
          </div>
        </Section>

        {/* ---------------------------------------------------- 索引 */}
        <Section
          title="索引"
          desc="卡池 / 卡牌详情 / 插画 / 卡面素材 / 游戏牌组，共 5 份。带 WAD 指纹，没变就跳过。"
        >
          <div className="mb-2 flex flex-wrap gap-2 text-[11px]">
            {Object.entries(health?.indexes || {}).map(([k, v]) => (
              <span
                key={k}
                className={
                  "rounded-chip border px-2 py-0.5 " +
                  (v ? "border-emerald-500/30 text-emerald-400"
                     : "border-red-500/40 text-red-400")
                }
              >
                {k} {v ? "✓" : "✗"}
              </span>
            ))}
          </div>
          <div className="flex gap-2">
            <Btn k="reindex" onClick={() => run("reindex", async () => {
              await api.reindex(false);
              return "索引已刷新（没变化的会跳过）";
            })}>
              刷新索引
            </Btn>
            <Btn k="reforce" onClick={() => run("reforce", async () => {
              await api.reindex(true);
              return "索引已强制重建";
            })}>
              强制重建
            </Btn>
          </div>
        </Section>

        {/* ---------------------------------------------------- WAD 备份 */}
        <Section
          title="WAD 文件"
          desc={`游戏目录里 ${wads.length} 个包，共 ${(wadBytes / 1e9).toFixed(2)} GB。备份到 data/wad_backup/，已存在的按大小跳过。`}
        >
          <div className="mb-2 flex gap-2">
            <Btn k="bak" primary onClick={() => run("bak", async () => {
              const r = await api.backupWads();
              return `备份了 ${r.count} 个包（${(r.bytes / 1e6).toFixed(0)} MB），跳过 ${r.skipped} 个已备份的`;
            })}>
              备份全部
            </Btn>
            <span className="self-center text-[11px] text-fg-faint">
              已有备份 {wadBackups.length} 个
            </span>
          </div>
          {wadBackups.length > 0 && (
            <details>
              <summary className="cursor-pointer text-[11px] text-fg-dim hover:text-fg">
                备份列表（点开）
              </summary>
              <div className="thin-scroll mt-1.5 max-h-52 overflow-y-auto">
                {wadBackups.map((b) => (
                  <div key={b.name} className="flex items-center gap-2 py-0.5 text-[11px]">
                    <span className="min-w-0 flex-1 truncate font-mono text-fg-muted">{b.name}</span>
                    <span className="shrink-0 tabular-nums text-fg-faint">
                      {(b.size / 1e6).toFixed(1)} MB
                    </span>
                    <span className="shrink-0 text-fg-faint">{b.time}</span>
                    <Btn k={"rst" + b.name} onClick={() => run("rst" + b.name, async () => {
                      await api.restoreWad(b.name);
                      return `已还原 ${b.name}（覆盖了游戏目录里那份）`;
                    })}>
                      还原
                    </Btn>
                  </div>
                ))}
              </div>
            </details>
          )}
        </Section>

        {/* ---------------------------------------------------- .bsf 检测 */}
        <Section
          title="检测并修复 .bsf（游戏随机崩溃时可以试试）"
          desc="UI 文字表的解析循环只检查「条目起点」是否越界，不检查数据本身。文件末尾多一个 0x00 就会让引擎读出缓冲区外 3 字节，读到什么全看堆布局 —— 表现是「约 40% 概率、启动几秒后随机崩溃」。中文包 DATA_DECKS_D910.WAD 历史上中过这个招。"
        >
          <div className="mb-2 flex flex-wrap gap-2">
            <Btn k="scan" primary onClick={() => run("scan", async () => {
              const r = await api.scanBsf();
              setBsf(r);
              return r.need_fix
                ? `发现 ${r.bad.length} 个会越界读的 .bsf`
                : `扫了 ${r.wads_scanned} 个包、${r.clean} 个 .bsf，全部干净`;
            })}>
              扫描
            </Btn>
          </div>

          {bsf && (
            <div className="text-[11px]">
              {bsf.bad.length === 0 ? (
                <div className="rounded-tile border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-1.5 text-emerald-300">
                  ✓ 没有需要修的（扫过 {bsf.clean} 个 .bsf）
                </div>
              ) : (
                <>
                  {bsf.bad.map((b) => (
                    <div key={b.wad + b.path}
                         className="mb-1 flex items-center gap-2 rounded-tile border border-red-500/40 bg-red-500/10 px-2.5 py-1.5">
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-red-200">
                          <span className="font-mono">{b.wad}</span> ::{" "}
                          <span className="font-mono">{b.name}</span>
                        </div>
                        <div className="text-[10px] text-red-300/70">
                          {b.size} 字节 · {b.entries} 条目 · 尾部多 {b.tail_len} 字节 [{b.tail}]
                        </div>
                      </div>
                      <Btn k={"fix" + b.wad} danger onClick={() => run("fix" + b.wad, async () => {
                        const r = await api.fixBsf(b.wad);
                        if (!r.changed) return r.msg || "没有改动";
                        return `修了 ${r.fixed?.length} 个：${r.size_before} → ${r.size_after} 字节` +
                          (r.still_bad ? `　⚠ 还剩 ${r.still_bad} 个` : "　✓ 已全部干净") +
                          (r.header_xml_ok && r.dirs_ok ? "　headerXml 和目录结构完好" : "　⚠ 头部校验没过");
                      })}>
                        修复
                      </Btn>
                    </div>
                  ))}
                  <div className="mt-1 text-[10px] leading-relaxed text-fg-faint">
                    修复会自动整包备份到 <code>data/wad_backup/</code>。
                    内部走 <code>Wad.rebuild()</code> 做外科手术 —— 不能重新打包，
                    那会丢 headerXml 和空目录，导致游戏变英文、汉字全变方块。
                  </div>
                </>
              )}
            </div>
          )}
        </Section>

        {/* ---------------------------------------------------- 诊断 */}
        <Section title="诊断日志" desc="最近的警告和错误。出问题时截图这一段就行。">
          {logs.length === 0 ? (
            <div className="py-6 text-center text-xs text-fg-faint">没有警告或错误 🎉</div>
          ) : (
            logs.slice(0, 60).map((l, i) => (
              <div key={i} className="mb-1.5 rounded-tile border border-hairline bg-black/25 p-2">
                <div className="flex items-center gap-2 text-[11px]">
                  <span className="tabular-nums text-fg-faint">{l.t}</span>
                  <span className={l.level === "ERROR" || l.level === "CRITICAL"
                    ? "text-red-400" : "text-amber-400"}>
                    {l.level}
                  </span>
                  <span className="text-fg-faint">{l.where}</span>
                </div>
                <div className="mt-0.5 break-words text-xs text-fg-muted">{l.msg}</div>
                {l.trace && (
                  <details className="mt-1">
                    <summary className="cursor-pointer text-[10px] text-fg-faint hover:text-fg-dim">
                      堆栈
                    </summary>
                    <pre className="thin-scroll mt-1 max-h-52 overflow-auto whitespace-pre-wrap
                                    rounded bg-black/50 p-2 text-[10px] leading-relaxed text-fg-dim">
                      {l.trace}
                    </pre>
                  </details>
                )}
              </div>
            ))
          )}
        </Section>
      </div>
    </Drawer>
  );
}

function Row({ k, v, ok }: { k: string; v: string; ok?: boolean }) {
  return (
    <div className="flex gap-2 py-0.5">
      <span className="w-16 shrink-0 text-fg-faint">{k}</span>
      <span
        className={
          "min-w-0 flex-1 break-all " +
          (ok === false ? "text-red-400" : "text-fg-muted")
        }
      >
        {v}
      </span>
    </div>
  );
}

/** 导出抽屉 —— 文本牌表，方便复制给别人或喂给别的工具。 */
function ExportDrawer({ deck, onClose }: { deck: DeckData; onClose: () => void }) {
  const text = useMemo(() => {
    const lines: string[] = [`// ${deck.name_cn} / ${deck.name_en}`, ""];
    for (const [n, c] of Object.entries(deck.main)) lines.push(`${c} ${n}`);
    if (Object.keys(deck.min_lands).length) {
      lines.push("", "// 基本地下限");
      for (const [k, v] of Object.entries(deck.min_lands))
        lines.push(`${v} ${k}`);
    }
    if (Object.keys(deck.side).length) {
      lines.push("", "// 解锁表");
      for (const [n, c] of Object.entries(deck.side)) lines.push(`${c} ${n}`);
    }
    return lines.join("\n");
  }, [deck]);

  const [copied, setCopied] = useState(false);
  return (
    <Drawer title="导出牌表" onClose={onClose} width={560}>
      <div className="flex h-full flex-col p-3">
        <textarea
          readOnly
          value={text}
          className="thin-scroll min-h-0 flex-1 resize-none rounded-panel border border-hairline
                     bg-black/30 p-3 font-mono text-xs leading-relaxed text-fg-muted outline-none"
        />
        <div className="mt-2 flex gap-2">
          <button
            onClick={() => {
              navigator.clipboard?.writeText(text);
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1500);
            }}
            className="rounded-tile bg-ember px-3 py-1.5 text-sm font-medium text-black
                       transition hover:bg-ember-bright"
          >
            {copied ? "已复制 ✓" : "复制"}
          </button>
          <button
            onClick={() => {
              const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
              const a = document.createElement("a");
              a.href = URL.createObjectURL(blob);
              a.download = `${deck.id}.txt`;
              a.click();
            }}
            className="rounded-tile border border-hairline px-3 py-1.5 text-sm transition
                       hover:border-hairline-hover"
          >
            下载 .txt
          </button>
        </div>
      </div>
    </Drawer>
  );
}
