/**
 * 卡牌平铺网格。
 *
 * **不做虚拟滚动** —— 这是从 phase 学到的：它 2 万多张卡一点不卡，
 * 靠的不是虚拟列表，而是**搜索结果限流**（默认一次 175 条）+ `loading="lazy"`。
 * DOM 里同时存在的卡片节点永远只有一页，滚动自然就快了。
 * 这里额外加一个「滚到底自动加载下一页」，需要看更多时不用手动点。
 *
 * 网格用 `auto-fill + minmax` 做响应式，不写死列数 —— 面板宽度变了自动重排。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import type { CardSlim } from "../api";
import { CardFace } from "./CardFace";
import { RarityDot } from "./bits";

interface Props {
  items: CardSlim[];
  total: number;
  tileWidth: number;
  loading?: boolean;
  hasMore?: boolean;
  onLoadMore?: () => void;
  onHover: (card: CardSlim) => (e: React.PointerEvent) => void;
  onLeave: (e: React.PointerEvent) => void;
  onAdd?: (card: CardSlim) => void;
  countOf?: (key: string) => number;
  emptyHint?: string;
  /** 从牌组面板拖一张卡到卡池区域 = 删掉它 */
  onDropRemove?: (name: string) => void;
  /** 滚到这张卡并闪一下。`token` 每次都要变 —— 同一张卡连续删两次也要能重新触发 */
  focusCard?: { key: string; token: number } | null;
  /** `items` 第一张卡在整个结果里的下标。>0 说明是「跳转定位」过来的，
   *  上面那些页没加载，得给条回到开头的路。 */
  baseOffset?: number;
  onJumpTop?: () => void;
}

export function CardGrid({
  items, total, tileWidth, loading, hasMore, onLoadMore,
  onHover, onLeave, onAdd, countOf, emptyHint, onDropRemove, focusCard,
  baseOffset = 0, onJumpTop,
}: Props) {
  const sentinel = useRef<HTMLDivElement>(null);
  const [dropActive, setDropActive] = useState(false);
  const [flashKey, setFlashKey] = useState<string | null>(null);
  // key -> 卡牌格子。用来在删除时把卡池滚到那张卡的位置
  const tiles = useRef(new Map<string, HTMLDivElement>());

  useEffect(() => {
    if (!focusCard) return;
    const el = tiles.current.get(focusCard.key);
    if (!el) return;
    el.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
    setFlashKey(focusCard.key);
    const t = window.setTimeout(() => setFlashKey(null), 1800);
    return () => window.clearTimeout(t);
  }, [focusCard]);

  // 滚到底自动加载
  useEffect(() => {
    if (!hasMore || !onLoadMore) return;
    const el = sentinel.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) onLoadMore();
      },
      { rootMargin: "600px" }
    );
    io.observe(el);
    return () => io.disconnect();
  }, [hasMore, onLoadMore, items.length]);

  const cardH = (tileWidth * 512) / 356;

  const style = useMemo(
    () => ({
      gridTemplateColumns: `repeat(auto-fill, minmax(${tileWidth}px, 1fr))`,
      ["--cw" as string]: `${tileWidth}px`,
    }),
    [tileWidth]
  );

  if (!loading && items.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-fg-dim">
        <div className="text-4xl opacity-30">🃏</div>
        <div className="text-sm">{emptyHint || "没有符合条件的卡牌"}</div>
        <div className="text-xs text-fg-faint">试试放宽筛选条件</div>
      </div>
    );
  }

  return (
    <div
      className="thin-scroll relative h-full overflow-y-auto px-4 pb-24 pt-3"
      onDragOver={(e) => {
        // 只接「从牌组拖出来的卡」，别把网格里卡牌自己的拖拽也当成删除
        if (!onDropRemove || !e.dataTransfer.types.includes("application/x-deck-card"))
          return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        setDropActive(true);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setDropActive(false);
      }}
      onDrop={(e) => {
        if (!onDropRemove) return;
        const name = e.dataTransfer.getData("application/x-deck-card");
        if (!name) return;
        e.preventDefault();
        setDropActive(false);
        onDropRemove(name);
      }}
    >
      {/* 跳转定位过来的：上面那些页没加载，往上滚是空的，给条退路 */}
      {baseOffset > 0 && (
        <div className="mb-2 flex items-center gap-2 rounded-tile border border-ember/30
                        bg-ember/[0.07] px-2.5 py-1.5 text-[11px] text-ember-bright">
          <span>
            已跳到第 <b className="tabular-nums">{baseOffset + 1}</b> 张开始显示
            <span className="text-fg-dim">（共 {total} 张，前面的没加载）</span>
          </span>
          {onJumpTop && (
            <button
              onClick={onJumpTop}
              className="ml-auto shrink-0 rounded-tile border border-ember/40 px-2 py-0.5
                         transition hover:bg-ember hover:text-black"
            >
              回到开头
            </button>
          )}
        </div>
      )}

      {dropActive && (
        <div className="pointer-events-none sticky top-0 z-30 mb-2 flex justify-center">
          <span className="rounded-panel border border-red-500/50 bg-red-950/90 px-3 py-1.5
                           text-xs font-medium text-red-300 backdrop-blur">
            松手从牌组移除这张卡
          </span>
        </div>
      )}
      <div className="grid gap-3" style={style}>
        {items.map((c, i) => {
          const n = countOf?.(c.key) ?? 0;
          return (
            <motion.div
              key={c.key}
              ref={(el) => {
                if (el) tiles.current.set(c.key, el);
                else tiles.current.delete(c.key);
              }}
              layout="position"
              initial={{ opacity: 0, scale: 0.94 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ duration: 0.14, delay: Math.min(i, 24) * 0.004 }}
              data-card-hover=""
              className="group relative cursor-pointer active:cursor-grabbing"
              style={{ height: cardH }}
              onPointerEnter={onHover(c)}
              onPointerLeave={onLeave}
              onDoubleClick={() => onAdd?.(c)}
              title={`${c.zh || c.en}\n双击加入牌组，或拖到右边的牌组面板`}
            >
              {/* 从牌组拖过来删掉时，闪一圈告诉用户「就是这张」 */}
              {flashKey === c.key && (
                <motion.div
                  key={focusCard?.token}
                  className="pointer-events-none absolute -inset-1 z-10 rounded-[8px]
                             ring-2 ring-ember shadow-[0_0_26px_var(--color-ember)]"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: [0, 1, 1, 0] }}
                  transition={{ duration: 1.8, times: [0, 0.1, 0.6, 1] }}
                />
              )}

              {/* 拖拽挂在**这个普通 div** 上，不能挂外层的 `motion.div` ——
                  Framer Motion 会把 `onDragStart` 当成它自己拖拽系统的回调，
                  类型是 `MouseEvent | TouchEvent | PointerEvent`，没有 `dataTransfer`。
                  用原生 HTML5 DnD：跨面板拖放开箱即用，也不和「双击加入」打架。 */}
              <div
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData("application/x-card-key", c.key);
                  e.dataTransfer.setData("text/plain", c.en || c.zh || c.key);
                  e.dataTransfer.effectAllowed = "copy";
                  const img = e.currentTarget.querySelector("img");
                  if (img) e.dataTransfer.setDragImage(img, 30, 40);
                }}
                className="h-full w-full transition-transform duration-150
                           ease-[var(--ease-spring)] group-hover:-translate-y-1
                           group-hover:scale-[1.03]"
              >
                <CardFace card={c} width={tileWidth} artSize="thumb" />
              </div>

              {/* 卡名条 —— 缩略卡面上的字太小，这里保证读得到 */}
              <div className="pointer-events-none absolute inset-x-[3%] bottom-[3%] flex items-center gap-1 rounded-b-[4px] bg-gradient-to-t from-black/85 to-transparent px-1.5 pb-0.5 pt-3">
                <RarityDot rarity={c.rarity} />
                <span className="truncate text-[10px] font-medium text-white/90">
                  {c.zh || c.en}
                </span>
              </div>

              {/* 数量徽章 */}
              {n > 0 && (
                <div className="absolute -right-1 -top-1 flex h-6 min-w-6 items-center justify-center rounded-full bg-ember px-1.5 text-[11px] font-bold text-black shadow-lg">
                  {n}
                </div>
              )}

              {/* 悬浮操作 */}
              {onAdd && (
                <button
                  className="absolute left-1 top-1 hidden h-6 w-6 items-center justify-center rounded-full bg-black/70 text-sm font-bold text-white backdrop-blur transition hover:bg-ember hover:text-black group-hover:flex"
                  onClick={(e) => {
                    e.stopPropagation();
                    onAdd(c);
                  }}
                  title="加入牌组"
                >
                  +
                </button>
              )}
            </motion.div>
          );
        })}
      </div>

      <div ref={sentinel} className="h-10" />

      <div className="flex items-center justify-center gap-3 py-3 text-xs text-fg-dim">
        {loading ? (
          <span className="flex items-center gap-2">
            <Spinner /> 载入中…
          </span>
        ) : hasMore ? (
          <button
            className="rounded-chip border border-hairline px-3 py-1 transition hover:border-hairline-hover hover:text-fg"
            onClick={onLoadMore}
          >
            加载更多（已显示 {items.length} / {total}）
          </button>
        ) : (
          <span className="text-fg-faint">
            共 {total} 张 · 已全部显示
          </span>
        )}
      </div>
    </div>
  );
}

function Spinner() {
  return (
    <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-hairline border-t-ember" />
  );
}
