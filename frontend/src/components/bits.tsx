/** 零碎小部件。集中放一处，省得每个组件各写一遍。 */

import { useState, type ReactNode } from "react";
import { frameUrl } from "../api";
import { SymbolFallback, manaFile, parseCost } from "./CardFace";

export const RARITY_COLOR: Record<string, string> = {
  C: "var(--color-rarity-c)",
  U: "var(--color-rarity-u)",
  R: "var(--color-rarity-r)",
  M: "var(--color-rarity-m)",
  S: "var(--color-rarity-s)",
  T: "var(--color-rarity-t)",
};

export const COLOR_HEX: Record<string, string> = {
  W: "var(--color-mtg-w)",
  U: "var(--color-mtg-u)",
  B: "var(--color-mtg-b)",
  R: "var(--color-mtg-r)",
  G: "var(--color-mtg-g)",
  C: "var(--color-fg-dim)",
  M: "var(--color-ember)",
};

export const COLOR_CN: Record<string, string> = {
  W: "白", U: "蓝", B: "黑", R: "红", G: "绿", C: "无色", M: "多色",
};

/** 稀有度小圆点。 */
export function RarityDot({ rarity, size = 6 }: { rarity?: string; size?: number }) {
  const r = (rarity || "").toUpperCase();
  if (!r || !RARITY_COLOR[r]) return null;
  return (
    <span
      className="inline-block shrink-0 rounded-full"
      style={{
        width: size, height: size,
        background: RARITY_COLOR[r],
        boxShadow: `0 0 5px ${RARITY_COLOR[r]}`,
      }}
      title={r}
    />
  );
}

/** 法术力费用 —— 用游戏自带的符号图拼。 */
export function ManaCost({ cost, size = 15 }: { cost: string; size?: number }) {
  const syms = parseCost(cost);
  if (!syms.length) return <span className="text-xs text-fg-faint">—</span>;
  return (
    <span className="inline-flex items-center gap-[2px]">
      {syms.map((s, i) => {
        const f = manaFile(s);
        if (!f) return <SymbolFallback key={i} sym={s} />;
        return <CostIcon key={i} file={f} sym={s} size={size} />;
      })}
    </span>
  );
}

/** 费用里的一个符号。固定像素尺寸（不是 `em`）—— 卡表行里字号是固定的。
 *  加载失败退回圆底字母，绝不留裂图。 */
function CostIcon({ file, sym, size }: { file: string; sym: string; size: number }) {
  const [bad, setBad] = useState(false);
  if (bad) return <SymbolFallback sym={sym} />;
  return (
    <img
      src={frameUrl("mana", file)}
      alt={sym}
      draggable={false}
      onError={() => setBad(true)}
      style={{ width: size, height: size }}
    />
  );
}

/** 全名/小写/单字母 -> 单字母。**颜色有两种写法在项目里并存**：
 *   - 卡池的 `CardSlim.colors` 是单字母（`["W","U"]`）
 *   - 牌组数据的 `colors` 是小写英文名（`["white","blue"]`，和 `decks_data.py` 一致，
 *     导出 WAD 时要用）
 *  不归一化的话，牌组那侧查不到色值会统统 fallback 成灰色 —— 看起来就是
 *  「一排空白的点」。 */
const FULL2LETTER: Record<string, string> = {
  white: "W", blue: "U", black: "B", red: "R", green: "G",
  w: "W", u: "U", b: "B", r: "R", g: "G",
};

export function normColor(c: string): string {
  const s = String(c ?? "").trim();
  if (!s) return "C";
  if (COLOR_HEX[s]) return s;                       // 已经是单字母（大写）
  const up = s.toUpperCase();
  if (COLOR_HEX[up]) return up;                     // 小写单字母
  return FULL2LETTER[s.toLowerCase()] || "C";
}

/** 法术力颜色点（WUBRG）。有两种颜色的写法都认。 */
export function ColorPips({ colors, size = 9 }: { colors: string[]; size?: number }) {
  const cs = colors?.length ? colors.map(normColor) : ["C"];
  return (
    <span className="inline-flex items-center gap-[3px]">
      {cs.map((c, i) => (
        <span
          key={c + i}
          className="inline-block rounded-full ring-1 ring-black/40"
          style={{ width: size, height: size, background: COLOR_HEX[c] || COLOR_HEX.C }}
          title={COLOR_CN[c] || c}
        />
      ))}
    </span>
  );
}

/** 通用小标签按钮。 */
export function Chip({
  active, onClick, children, title, color, className = "",
}: {
  active?: boolean;
  onClick?: () => void;
  children: ReactNode;
  title?: string;
  color?: string;
  className?: string;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className={
        "rounded-chip border px-2 py-[3px] text-xs transition-all duration-150 " +
        (active
          ? "border-ember/70 bg-ember/20 text-ember-bright"
          : "border-hairline text-fg-muted hover:border-hairline-hover hover:text-fg") +
        " " + className
      }
      style={active && color ? { borderColor: color, color } : undefined}
    >
      {children}
    </button>
  );
}

export function NumberInput({
  value, onChange, placeholder, min, max, width = 56,
}: {
  value: number | undefined;
  onChange: (v: number | undefined) => void;
  placeholder?: string;
  min?: number;
  max?: number;
  width?: number;
}) {
  return (
    <input
      type="number"
      value={value ?? ""}
      min={min}
      max={max}
      placeholder={placeholder}
      onChange={(e) => {
        const v = e.target.value;
        onChange(v === "" ? undefined : Number(v));
      }}
      style={{ width }}
      className="rounded-tile border border-hairline bg-black/30 px-2 py-1 text-xs
                 text-fg outline-none transition focus:border-ember/60"
    />
  );
}

export function SectionTitle({
  children, right,
}: {
  children: ReactNode;
  right?: ReactNode;
}) {
  return (
    <div className="mb-1.5 flex items-center justify-between">
      <span className="text-[11px] font-semibold uppercase tracking-wider text-fg-dim">
        {children}
      </span>
      {right}
    </div>
  );
}

export function Spinner({ size = 12 }: { size?: number }) {
  return (
    <span
      className="inline-block animate-spin rounded-full border-2 border-hairline border-t-ember"
      style={{ width: size, height: size }}
    />
  );
}
