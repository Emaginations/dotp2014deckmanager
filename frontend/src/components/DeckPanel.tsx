/**
 * 牌组侧栏：牌表 + 统计。
 *
 * 统计口径照抄 Deck Builder 的 `BasicLandAmount`（`Deck.cs:829`）：
 * **还差多少张到 60，引擎就补多少张基本地**；玩家手工加的基本地会把自动补的量减掉。
 * 所以这里同时显示「`<CARD>` 条数」和「引擎会补多少地」——
 * 这是这个游戏最反直觉的一条规则（见 dotp2014decks/readme.md）。
 */

import { useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import type { CardSlim, DeckData, DeckStats } from "../api";
import { ColorPips, ManaCost, RarityDot, SectionTitle } from "./bits";

const GROUP_ORDER = [
  "Creature", "Planeswalker", "Instant", "Sorcery",
  "Enchantment", "Artifact", "Land", "Other",
];
const GROUP_CN: Record<string, string> = {
  Creature: "生物", Planeswalker: "鹏洛克", Instant: "瞬间", Sorcery: "法术",
  Enchantment: "结界", Artifact: "神器", Land: "地", Other: "其它",
};
const GROUP_ACCENT: Record<string, string> = {
  Creature: "#9ed3a8", Planeswalker: "#f0a04b", Instant: "#a5d3f0",
  Sorcery: "#8fa8e0", Enchantment: "#d8c0e8", Artifact: "#c9cdd4",
  Land: "#b8a98a", Other: "#7a8496",
};
const CURVE_LABELS = ["0", "1", "2", "3", "4", "5", "6+"];

interface Props {
  deck: DeckData | null;
  stats: DeckStats | null;
  cards: Record<string, CardSlim>;   // key -> 卡（用来显示费用/颜色）
  onBump: (name: string, delta: number) => void;
  onRemove: (name: string) => void;
  onHover: (card: CardSlim) => (e: React.PointerEvent) => void;
  onLeave: (e: React.PointerEvent) => void;
  /** 改名。`name_en` = 卡包名（游戏里显示的），`name_cn` = 中文备注 */
  onRename: (patch: { name_en?: string; name_cn?: string }) => void;
  onSave: () => void;
  onExport: () => void;
  /** 打开打包向导。编辑游戏牌组时不传 —— 那条路只能「写回游戏包」 */
  onBuild?: () => void;
  dirty: boolean;
  /** 正在编辑的是游戏里的牌组时，保存按钮的含义不同（写回 WAD） */
  gameMode?: { name: string; group_cn: string; writable: boolean } | null;
  /** 从网格拖过来的卡牌 key */
  onDropCard?: (key: string) => void;
}

export function DeckPanel({
  deck, stats, cards, onBump, onRemove, onHover, onLeave,
  onRename, onSave, onExport, onBuild, dirty, gameMode, onDropCard,
}: Props) {
  const [tab, setTab] = useState<"main" | "side">("main");
  const [groupBy, setGroupBy] = useState<"type" | "cmc">("type");
  const [dragOver, setDragOver] = useState(false);

  const entries = useMemo(() => {
    if (!deck) return [];
    const src = tab === "main" ? deck.main : deck.side;
    return Object.entries(src)
      .map(([name, cnt]) => {
        const card = Object.values(cards).find(
          (c) => c.en === name || c.zh === name
        );
        return { name, cnt, card };
      })
      .sort((a, b) => (a.card?.cmc ?? 0) - (b.card?.cmc ?? 0));
  }, [deck, tab, cards]);

  const groups = useMemo(() => {
    const m = new Map<string, typeof entries>();
    for (const e of entries) {
      let g: string;
      if (groupBy === "cmc") {
        g = String(Math.min(e.card?.cmc ?? 0, 6));
      } else {
        const t = (e.card?.type || "").split("—")[0].trim();
        g = GROUP_ORDER.includes(t) ? t : "Other";
      }
      if (!m.has(g)) m.set(g, []);
      m.get(g)!.push(e);
    }
    const keys = groupBy === "cmc"
      ? [...m.keys()].sort((a, b) => Number(a) - Number(b))
      : GROUP_ORDER.filter((k) => m.has(k));
    return keys.map((k) => [k, m.get(k)!] as const);
  }, [entries, groupBy, groupBy]);

  if (!deck) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-fg-dim">
        还没有打开卡组
      </div>
    );
  }

  // 主数字显示 `<CARD>` 条数（这是你能编辑的部分），
  // 引擎补的地是**引擎算出来的**，只作说明。
  const nmain = stats?.n_main ?? 0;
  const engineLand = stats?.auto_land ?? 0;
  const t = stats?.total ?? 0;
  const over = (stats?.over_60 ?? false) || nmain > 60;

  return (
    <div
      className="relative flex h-full flex-col"
      onDragOver={(e) => {
        if (!onDropCard) return;
        // 只接「从卡池拖过来的卡」。从牌组里往外拖时指针也在面板上，
        // 不判类型的话会一边往外拖、一边显示「松手加入牌组」。
        if (!e.dataTransfer.types.includes("application/x-card-key")) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
        setDragOver(true);
      }}
      onDragLeave={(e) => {
        // 只在真正离开面板时取消高亮（子元素之间移动也会触发 leave）
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragOver(false);
      }}
      onDrop={(e) => {
        if (!onDropCard) return;
        e.preventDefault();
        setDragOver(false);
        const key = e.dataTransfer.getData("application/x-card-key");
        if (key) onDropCard(key);
      }}
    >
      {/* 拖拽高亮 —— 整块描边 + 提示，不然不知道能不能放。
          **故意不加 `backdrop-blur`** —— 从牌组里往外拖卡时指针也在这个面板上，
          一糊整块牌表就看不见了。 */}
      {dragOver && (
        <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center
                        rounded-panel border-2 border-dashed border-ember bg-ember/10">
          <span className="rounded-panel bg-black/70 px-3 py-1.5 text-xs font-medium text-ember-bright">
            松手加入牌组
          </span>
        </div>
      )}

      {/* 组名 + 保存 */}
      <div className="border-b border-hairline px-3 py-2.5">
        {/* 大字是**英文卡包名** —— 那是游戏里真正显示的那一栏
            （游戏那套字体没有中文字形，中文会渲染成一排 `[····]`）。
            中文名放下面当备注。 */}
        <input
          value={deck.name_en}
          onChange={(e) => onRename({ name_en: e.target.value })}
          className="w-full bg-transparent text-base font-semibold text-fg outline-none
                     placeholder:text-fg-faint focus:text-ember-bright"
          placeholder="卡包名（英文，游戏里显示的就是它）"
          title="卡包名 —— 游戏里显示的就是它，必须是英文（那栏字体没有中文字形）"
        />
        <div className="mt-0.5 flex items-center justify-between">
          {/* 中文备注 —— 游戏里看不到它，只是给你自己认的。**也可改**。 */}
          <span className="flex min-w-0 items-center gap-1">
            {gameMode && (
              <span className="shrink-0 text-[11px] text-fg-dim">
                游戏牌组 · {gameMode.group_cn} ·
              </span>
            )}
            <input
              value={deck.name_cn}
              onChange={(e) => onRename({ name_cn: e.target.value })}
              placeholder="中文备注（可留空）"
              title="中文备注 —— 只在程序里看得到，不会写进游戏"
              className="min-w-0 flex-1 bg-transparent text-[11px] text-fg-dim outline-none
                         placeholder:text-fg-faint focus:text-ember-bright"
            />
          </span>
          <div className="flex items-center gap-1.5">
            {dirty && <span className="text-[10px] text-ember">未保存</span>}
            <button
              onClick={onSave}
              disabled={!dirty}
              title={gameMode
                ? "把改动写回游戏包（会自动备份）"
                : "保存到项目文件 projects/（**还没进游戏**，要进游戏得点「构建」）"}
              className={
                "rounded-tile border px-2 py-0.5 text-[11px] transition disabled:opacity-30 " +
                (gameMode
                  ? "border-ember/50 text-ember hover:bg-ember hover:text-black disabled:hover:bg-transparent disabled:hover:text-ember"
                  : "border-hairline hover:border-ember/70 hover:text-ember-bright disabled:hover:border-hairline")
              }
            >
              {gameMode ? "写回游戏包" : "保存草稿"}
            </button>
            {/* 编辑游戏牌组时不显示「构建」—— 那条路是写回原包，
                不是重新打一个包，两个动作别混在一起 */}
            {!gameMode && onBuild && (
              <button
                onClick={onBuild}
                title="做成游戏能加载的卡包，直接装进游戏目录"
                className="rounded-tile border border-ember/50 bg-ember/10 px-2 py-0.5
                           text-[11px] text-ember-bright transition hover:bg-ember/25"
              >
                构建
              </button>
            )}
            <button
              onClick={onExport}
              className="rounded-tile border border-hairline px-2 py-0.5 text-[11px]
                         transition hover:border-hairline-hover hover:text-fg"
            >
              导出
            </button>
          </div>
        </div>
      </div>

      {/* 主牌 / 解锁表 */}
      <div className="flex gap-1 border-b border-hairline px-3 py-1.5">
        {([["main", "主牌"], ["side", "解锁表"]] as const).map(([k, label]) => (
          <button
            key={k}
            onClick={() => setTab(k)}
            className={
              "rounded-tile px-2.5 py-1 text-xs transition " +
              (tab === k
                ? "bg-ember/20 text-ember-bright"
                : "text-fg-muted hover:text-fg")
            }
          >
            {label}
            <span className="ml-1 text-fg-faint">
              {k === "main" ? stats?.n_main ?? 0 : stats?.n_side ?? 0}
            </span>
          </button>
        ))}
        <div className="ml-auto flex gap-1">
          {([["type", "按类别"], ["cmc", "按费用"]] as const).map(([k, label]) => (
            <button
              key={k}
              onClick={() => setGroupBy(k)}
              className={
                "rounded-tile px-2 py-1 text-[10px] transition " +
                (groupBy === k ? "text-ember" : "text-fg-faint hover:text-fg-muted")
              }
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* 牌表 */}
      <div className="thin-scroll min-h-0 flex-1 overflow-y-auto px-2 py-2">
        {entries.length === 0 && (
          <div className="py-10 text-center text-xs text-fg-faint">
            {tab === "main" ? "双击左边的卡牌加入牌组" : "解锁表还是空的"}
          </div>
        )}
        {groups.map(([g, list]) => (
          <div key={g} className="mb-2.5">
            <div className="mb-1 flex items-center gap-1.5 px-1">
              <span
                className="h-2.5 w-[3px] rounded-full"
                style={{
                  background: groupBy === "cmc"
                    ? "var(--color-ember-dim)"
                    : GROUP_ACCENT[g] || "#7a8496",
                }}
              />
              <span className="text-[11px] font-semibold text-fg-muted">
                {groupBy === "cmc" ? `${CURVE_LABELS[Number(g)]} 费` : GROUP_CN[g] || g}
              </span>
              <span className="text-[10px] text-fg-faint">
                {list.reduce((s, e) => s + e.cnt, 0)}
              </span>
            </div>
            <AnimatePresence initial={false}>
              {list.map(({ name, cnt, card }) => (
                <motion.div
                  key={name}
                  layout
                  initial={{ opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 8, height: 0, marginBottom: 0 }}
                  transition={{ duration: 0.13 }}
                >
                  {/* 拖拽挂在普通 div 上 —— `motion.div` 的 `onDragStart` 是
                      Framer Motion 自己的拖拽回调，类型里没有 `dataTransfer`。 */}
                  <div
                    // 拖到左边的卡池区域 = 从牌组里删掉。独立的 MIME 类型，
                    // 免得和「从卡池拖进牌组」那条链互相误触。
                    draggable
                    onDragStart={(e) => {
                      e.dataTransfer.setData("application/x-deck-card", name);
                      e.dataTransfer.effectAllowed = "move";
                    }}
                    title={`${card?.zh || name}\n拖到左边的卡池可以移除`}
                    className="group flex cursor-grab items-center gap-1.5 rounded-tile
                               px-1.5 py-1 transition hover:bg-white/[0.06]
                               active:cursor-grabbing"
                  >
                    {/* **可悬浮区只包到费用为止，操作按钮在外面** ——
                        按钮原本在这个 `data-card-hover` 元素里面，鼠标一移上去
                        放大浮层就赖着不走，把 `− + ×` 全挡住了。
                        结构上分出去之后，全局那条 `[data-card-hover]:hover` 判断
                        自然就不成立了。 */}
                    <div
                      data-card-hover=""
                      onPointerEnter={card ? onHover(card) : undefined}
                      onPointerLeave={onLeave}
                      className="flex min-w-0 flex-1 items-center gap-1.5"
                    >
                      <span className="w-5 shrink-0 text-center text-xs font-bold text-ember">
                        {cnt}
                      </span>
                      <RarityDot rarity={card?.rarity} size={5} />
                      <span className="min-w-0 flex-1 truncate text-xs text-fg">
                        {card?.zh || name}
                      </span>
                      <span className="shrink-0 opacity-80">
                        <ManaCost cost={card?.cost || ""} size={12} />
                      </span>
                    </div>
                    <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition group-hover:opacity-100">
                      <IconBtn onClick={() => onBump(name, -1)} title="减一张">−</IconBtn>
                      <IconBtn onClick={() => onBump(name, 1)} title="加一张">+</IconBtn>
                      <IconBtn onClick={() => onRemove(name)} title="全部移除">×</IconBtn>
                    </div>
                  </div>
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
        ))}
      </div>

      {/* 统计 */}
      <div className="border-t border-hairline px-3 py-2.5">
        <div className="mb-2 flex items-baseline gap-2">
          <span
            className={
              "text-2xl font-bold tabular-nums " +
              (over ? "text-red-400" : "text-fg")
            }
            title="牌表里 <CARD> 的条数 —— 这才是你能编辑的部分"
          >
            {nmain}
          </span>
          <span className="text-xs text-fg-dim">条卡牌</span>
          <span className="ml-auto text-[11px] text-fg-dim">
            全场 {t} 张 · 地 {stats?.n_land ?? 0}（{stats?.land_pct ?? 0}%）
          </span>
        </div>

        <div
          className={
            "mb-2 rounded-tile border px-2 py-1.5 text-[10px] leading-relaxed " +
            (over
              ? "border-red-500/40 bg-red-500/10 text-red-300"
              : "border-ember/30 bg-ember/10 text-ember-bright")
          }
        >
          引擎会按 <code className="opacity-80">&lt;CARD&gt;</code> 条数补{" "}
          <b>{engineLand}</b> 张基本地凑满 60
          {Object.keys(stats?.min_lands_cn || {}).length > 0 && (
            <>
              {" "}（下限：
              {Object.entries(stats!.min_lands_cn)
                .map(([k, v]) => `${k} ${v}`)
                .join("、")}
              ）
            </>
          )}
          {over && <b>　⚠ 已超过 60 张</b>}
        </div>

        <SectionTitle>法术力曲线</SectionTitle>
        <Curve data={stats?.curve || [0, 0, 0, 0, 0, 0, 0]} />

        <div className="mt-2.5">
          <SectionTitle
            right={
              <span className="text-[10px] text-fg-faint">
                均费 {stats?.avg_cmc ?? 0}
              </span>
            }
          >
            颜色分布
          </SectionTitle>
          <ColorBar dist={stats?.color_dist || {}} />
        </div>
      </div>
    </div>
  );
}

function IconBtn({
  children, onClick, title,
}: {
  children: React.ReactNode;
  onClick: () => void;
  title: string;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className="flex h-5 w-5 items-center justify-center rounded-tile border
                 border-hairline text-[11px] text-fg-muted transition
                 hover:border-hairline-hover hover:bg-white/10 hover:text-fg"
    >
      {children}
    </button>
  );
}

/** 法术力曲线 —— 手写柱状图，不引图表库（50 行的事）。 */
function Curve({ data }: { data: number[] }) {
  const max = Math.max(...data, 1);
  const H = 62;
  return (
    <div className="flex items-end gap-1" style={{ height: H + 16 }}>
      {data.map((n, i) => (
        <div key={i} className="flex flex-1 flex-col items-center justify-end gap-0.5">
          <span className="text-[9px] tabular-nums text-fg-dim">
            {n > 0 ? n : ""}
          </span>
          <div className="flex w-full items-end rounded-t bg-white/[0.06]" style={{ height: H }}>
            <motion.div
              className="w-full rounded-t bg-ember"
              initial={false}
              animate={{ height: n === 0 ? 0 : Math.max((n / max) * H, 4) }}
              transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
            />
          </div>
          <span className="text-[9px] text-fg-faint">{CURVE_LABELS[i]}</span>
        </div>
      ))}
    </div>
  );
}

function ColorBar({ dist }: { dist: Record<string, number> }) {
  const total = Object.values(dist).reduce((a, b) => a + b, 0) || 1;
  const items = Object.entries(dist).filter(([, v]) => v > 0);
  if (!items.length) {
    return <div className="text-[10px] text-fg-faint">牌组还是空的</div>;
  }
  const HEX: Record<string, string> = {
    W: "var(--color-mtg-w)", U: "var(--color-mtg-u)", B: "var(--color-mtg-b)",
    R: "var(--color-mtg-r)", G: "var(--color-mtg-g)", C: "var(--color-fg-dim)",
  };
  const CN: Record<string, string> = {
    W: "白", U: "蓝", B: "黑", R: "红", G: "绿", C: "无色",
  };
  return (
    <>
      <div className="flex h-2.5 overflow-hidden rounded-full" data-color-bar="">
        {items.map(([c, v]) => (
          <div
            key={c}
            data-color-key={c}
            style={{ width: `${(v / total) * 100}%`, background: HEX[c] }}
            title={`${CN[c]} ${Math.round((v / total) * 100)}%`}
          />
        ))}
      </div>
      <div className="mt-1 flex flex-wrap gap-2" data-color-legend="">
        {items.map(([c, v]) => (
          <span
            key={c}
            className="flex items-center gap-1 text-[10px] text-fg-dim"
            data-legend-key={c}
          >
            <ColorPips colors={[c]} size={7} />
            {CN[c]} {Math.round((v / total) * 100)}%
          </span>
        ))}
      </div>
    </>
  );
}
