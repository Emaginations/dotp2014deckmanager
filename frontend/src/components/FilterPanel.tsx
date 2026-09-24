/**
 * 筛选面板。
 *
 * 「有没有在筛」只有一个权威判据（`hasCriteria`）—— 从 phase 学到的：
 * 多处各判各的，早晚不同步。
 *
 * 关键词分两档说清楚：
 *   - **特征性异能**（30 种，来自 XML 的 `INTRINSIC`）是精确匹配
 *   - 其余异能只能靠上面的**文字搜索**去撞规则文本
 * 这个区别在界面上要写明，否则用户会以为「搜不到 = 这游戏没这张牌」。
 */

import { useMemo, useState } from "react";
import type { Meta, SearchParams } from "../api";
import { Chip, COLOR_HEX, COLOR_CN, NumberInput, SectionTitle } from "./bits";

export interface Filters extends SearchParams {
  color_mode: string;
}

export const EMPTY_FILTERS: Filters = {
  q: "",
  colors: [],
  color_mode: "any",
  type: "",
  sub: "",
  cmc_min: undefined,
  cmc_max: undefined,
  rarity: [],
  kw: [],
  set: [],
  src: [],
  legal: [],
  sort: "name",
  desc: false,
};

export function hasCriteria(f: Filters): boolean {
  return Boolean(
    (f.q || "").trim() ||
      f.colors?.length ||
      f.type ||
      f.sub ||
      f.rarity?.length ||
      f.kw?.length ||
      f.set?.length ||
      f.src?.length ||
      f.legal?.length ||
      f.cmc_min !== undefined ||
      f.cmc_max !== undefined
  );
}

const MAIN_TYPES = [
  "Creature", "Instant", "Sorcery", "Enchantment",
  "Artifact", "Land", "Planeswalker",
];
const TYPE_CN: Record<string, string> = {
  Creature: "生物", Instant: "瞬间", Sorcery: "法术", Enchantment: "结界",
  Artifact: "神器", Land: "地", Planeswalker: "鹏洛客", Tribal: "部族",
};

// 排序挪到卡池上方的 `SortBar` 了 —— 它是最常改的一项，埋在面板底部要滚才够得着。

interface Props {
  meta: Meta | null;
  value: Filters;
  onChange: (f: Filters) => void;
  tileWidth: number;
  onTileWidth: (w: number) => void;
}

export function FilterPanel({ meta, value, onChange, tileWidth, onTileWidth }: Props) {
  const [showAllKw, setShowAllKw] = useState(false);
  const [setQuery, setSetQuery] = useState("");

  const set = (patch: Partial<Filters>) => onChange({ ...value, ...patch });

  const toggleIn = (arr: string[] | undefined, v: string): string[] => {
    const a = arr || [];
    return a.includes(v) ? a.filter((x) => x !== v) : [...a, v];
  };

  const kws = useMemo(() => {
    const list = meta?.keywords || [];
    return showAllKw ? list : list.slice(0, 14);
  }, [meta, showAllKw]);

  const sets = useMemo(() => {
    const list = meta?.sets || [];
    if (!setQuery.trim()) return list.slice(0, 40);
    const q = setQuery.trim().toUpperCase();
    return list.filter(([s]) => s.toUpperCase().includes(q)).slice(0, 60);
  }, [meta, setQuery]);

  const colorCount = value.colors?.length || 0;

  const activeCount =
    (value.colors?.length || 0) + (value.rarity?.length || 0) +
    (value.kw?.length || 0) + (value.set?.length || 0) +
    (value.src?.length || 0) + (value.type ? 1 : 0) + (value.sub ? 1 : 0);

  return (
    <div className="thin-scroll flex h-full flex-col gap-4 overflow-y-auto px-3 py-3">
      {/* 搜索框 */}
      <div>
        <input
          value={value.q || ""}
          onChange={(e) => set({ q: e.target.value })}
          placeholder="搜卡名或规则文本…"
          className="w-full rounded-panel border border-hairline bg-black/30 px-3 py-2 text-sm
                     text-fg outline-none transition placeholder:text-fg-faint
                     focus:border-ember/60"
        />
      </div>

      {/* 颜色 */}
      <div>
        <SectionTitle>颜色</SectionTitle>
        <div className="flex flex-wrap gap-1.5">
          {["W", "U", "B", "R", "G", "C", "M"].map((c) => {
            const on = value.colors?.includes(c);
            return (
              <button
                key={c}
                onClick={() => set({ colors: toggleIn(value.colors, c) })}
                className={
                  "flex h-8 w-8 items-center justify-center rounded-full text-xs font-bold " +
                  "transition-all duration-150 " +
                  (on ? "ring-2 ring-white/70" : "opacity-45 hover:opacity-80")
                }
                style={{
                  background: COLOR_HEX[c],
                  color: c === "W" || c === "M" ? "#1a1a1a" : "#0b0b0b",
                  boxShadow: on ? `0 0 10px ${COLOR_HEX[c]}` : "none",
                }}
                title={COLOR_CN[c]}
              >
                {c}
              </button>
            );
          })}
        </div>
        {/* 三档**常显**，不再只在多色时出现。
            曾经包了 `colors.length > 1 &&`，于是从多色退回单色时 chip 集体消失、
            `color_mode` 却还停在 `only` —— 用户看到「只选了 B 却只出混血牌」，
            而且界面上没有任何线索说明为什么。筛选状态必须一直看得见。
            没选颜色时这一档本来就不起作用（后端整个颜色块会跳过），置灰并说明。 */}
        <div className="mt-1.5 flex flex-wrap gap-1">
          {([
            // 三档是「交 / 等 / 或」三种关系，别让它们互相重叠 ——
            // 这里出过三次事故，其中一次是 both 变 dead code。
            ["any", "含所选色",
             "含其中任意一个所选色（并集，「交」）。\n" +
             "例：选 B+G → 含 B 或含 G 的都出，带局外色的 {B}{U} 也出。"],
            ["exact", "恰好",
             "颜色正好是这些，不多不少 —— 「且」的关系。\n" +
             "例：选 B+G + 恰好 → 只出金卡 {B}{G}，单黑、单绿、无色都不出。"],
            ["only", "仅限混血",
             "只出「含混血/替代法术力符号，且符号只用到所选色」的牌 —— 「或」的关系。\n" +
             "例：选 B+G + 仅限混血 → {B/G}、{2/B}、{B/G}{B/G} 这类出。\n" +
             "单色（如只选 B）时出 {2/B}、{B/P} 这类，{B/G} 不算（它用了 G）。"],
          ] as const).map(([m, label, hint]) => (
            <Chip
              key={m}
              title={colorCount ? hint : "先选一个颜色 —— 没选颜色时这一档不起作用"}
              active={value.color_mode === m}
              onClick={() => set({ color_mode: m })}
              className={colorCount ? "" : "pointer-events-none opacity-30"}
            >
              {label}
            </Chip>
          ))}
        </div>
      </div>

      {/* 稀有度 —— 用户点名要的 */}
      <div>
        <SectionTitle>稀有度</SectionTitle>
        <div className="flex flex-wrap gap-1.5">
          {(meta?.rarities || []).map(([code, n, cn]) => (
            <Chip
              key={code}
              active={value.rarity?.includes(code)}
              onClick={() => set({ rarity: toggleIn(value.rarity, code) })}
              title={`${cn}（${n} 张）`}
              color={undefined}
            >
              <span className="flex items-center gap-1">
                <span
                  className="inline-block h-2 w-2 rounded-full"
                  style={{ background: `var(--color-rarity-${code.toLowerCase()})` }}
                />
                {cn}
                <span className="text-fg-faint">{n}</span>
              </span>
            </Chip>
          ))}
        </div>
      </div>

      {/* 类型 */}
      <div>
        <SectionTitle>类型</SectionTitle>
        <div className="flex flex-wrap gap-1.5">
          {MAIN_TYPES.map((t) => (
            <Chip
              key={t}
              active={value.type === t}
              onClick={() => set({ type: value.type === t ? "" : t })}
            >
              {TYPE_CN[t] || t}
            </Chip>
          ))}
        </div>
      </div>

      {/* 费用 */}
      <div>
        <SectionTitle>法术力值</SectionTitle>
        <div className="flex items-center gap-2">
          <NumberInput
            value={value.cmc_min}
            onChange={(v) => set({ cmc_min: v })}
            placeholder="最小"
            min={0}
            max={20}
          />
          <span className="text-fg-faint">–</span>
          <NumberInput
            value={value.cmc_max}
            onChange={(v) => set({ cmc_max: v })}
            placeholder="最大"
            min={0}
            max={20}
          />
        </div>
        {meta?.cmc_hist && (
          <div className="mt-2 flex h-8 items-end gap-[2px]">
            {Array.from({ length: Math.min(meta.cmc_max + 1, 17) }, (_, i) => {
              const n = meta.cmc_hist.find(([c]) => c === i)?.[1] || 0;
              const max = Math.max(...meta.cmc_hist.map(([, v]) => v), 1);
              return (
                <div
                  key={i}
                  className="flex-1 cursor-pointer rounded-t bg-ember/40 transition hover:bg-ember"
                  style={{ height: `${Math.max((n / max) * 100, n ? 8 : 0)}%` }}
                  title={`法术力值 ${i}：${n} 张`}
                  onClick={() => set({ cmc_min: i, cmc_max: i })}
                />
              );
            })}
          </div>
        )}
      </div>

      {/* 关键词 */}
      <div>
        <SectionTitle
          right={
            (meta?.keywords?.length || 0) > 14 && (
              <button
                className="text-[10px] text-ember hover:underline"
                onClick={() => setShowAllKw((v) => !v)}
              >
                {showAllKw ? "收起" : `全部 ${meta?.keywords.length}`}
              </button>
            )
          }
        >
          特征性异能
        </SectionTitle>
        <div className="flex flex-wrap gap-1.5">
          {kws.map(([k, n, cn]) => (
            <Chip
              key={k}
              active={value.kw?.includes(k)}
              onClick={() => set({ kw: toggleIn(value.kw, k) })}
              title={`${k}（${n} 张）`}
            >
              {cn}
              <span className="ml-1 text-fg-faint">{n}</span>
            </Chip>
          ))}
        </div>
        <p className="mt-1.5 text-[10px] leading-relaxed text-fg-faint">
          只覆盖 30 种特征性异能（飞行/践踏/死触…）。其余异能请用上面的
          <span className="text-fg-dim"> 文字搜索 </span>去匹配规则文本。
        </p>
      </div>

      {/* 系列 */}
      <div>
        <SectionTitle>系列</SectionTitle>
        <input
          value={setQuery}
          onChange={(e) => setSetQuery(e.target.value)}
          placeholder="筛选系列代码，如 M15"
          className="mb-1.5 w-full rounded-tile border border-hairline bg-black/30 px-2 py-1
                     text-xs text-fg outline-none focus:border-ember/60"
        />
        <div className="flex max-h-32 flex-wrap gap-1 overflow-y-auto thin-scroll">
          {sets.map(([s, n]) => (
            <Chip
              key={s}
              active={value.set?.includes(s)}
              onClick={() => set({ set: toggleIn(value.set, s) })}
              title={`${n} 张`}
            >
              {s}
            </Chip>
          ))}
        </div>
      </div>

      {/* 来源包 */}
      <div>
        <SectionTitle>来源包</SectionTitle>
        <div className="flex flex-wrap gap-1.5">
          {(meta?.srcs || []).map(([s, n]) => (
            <Chip
              key={s}
              active={value.src?.includes(s)}
              onClick={() => set({ src: toggleIn(value.src, s) })}
              title={`${n} 张`}
            >
              {s}
            </Chip>
          ))}
        </div>
      </div>

      {/* 卡牌大小 */}
      <div>
        <SectionTitle>卡牌大小</SectionTitle>
        <input
          type="range"
          min={88}
          max={260}
          value={tileWidth}
          onChange={(e) => onTileWidth(Number(e.target.value))}
          className="w-full"
        />
      </div>

      <div className="mt-auto pt-2">
        <button
          className="w-full rounded-panel border border-hairline py-1.5 text-xs text-fg-muted
                     transition hover:border-hairline-hover hover:text-fg disabled:opacity-30"
          disabled={activeCount === 0 && !(value.q || "").trim()}
          onClick={() => onChange({ ...EMPTY_FILTERS, sort: value.sort, desc: value.desc })}
        >
          清空筛选{activeCount ? `（${activeCount} 项）` : ""}
        </button>
      </div>
    </div>
  );
}
