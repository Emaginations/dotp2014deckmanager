/**
 * 卡面 —— **用 DOM 分层拼出一整张万智牌**，而不是把文字烤进位图。
 *
 * 为什么不用位图合成
 * ------------------
 * 参考实现（C# Deck Builder）是用 GDI+ 把牌名/规则文本画进一张 356×512 的位图。
 * 那样做的话，用户「鼠标悬浮放大到能读清」时看到的是**放大后发糊的字**。
 * 这里改成 HTML 分层：
 *
 *     第 1 层  <img> 插画      （裁切定位到插画窗口）
 *     第 2 层  <img> 卡框      （中心是镂空的，插画从下面透出来）
 *     第 3 层  HTML 文本       ← 牌名 / 类型行 / 规则文本 / 力防
 *     第 4 层  <img> 符号      （费用串、系列稀有度符号）
 *
 * 这样放大后字依然锐利、可以选中复制、换中文字体也不用重做素材，
 * 而且**不必为 2.2 万张卡各生成一张合成图**。
 *
 * 卡框镂空已实测：`DATA_CORE.WAD` 的 `W.TDX` 头写 512×356（横放），
 * 转 90° 成竖版后，插画窗口 (16,47,324,238) 区域 alpha 全是 0。
 *
 * 坐标来自 `CardInfo.cs:79-107` 的 Rectangle 常量表，基准 356×512。
 * 这里全部换算成百分比，所以任意 width 都能等比缩放。
 */

import { memo, useMemo, useState } from "react";
import type { CardSlim, CardFull } from "../api";
import { artUrl, frameUrl } from "../api";

/* 基准画布 */
const W = 356;
const H = 512;
const pct = (v: number, base: number) => `${(v / base) * 100}%`;

/* 排版区域（x, y, w, h）@ 356×512 —— 照抄参考实现 */
const R = {
  art: [16, 47, 324, 238],
  name: [12, 14, 330, 22],
  typeLine: [14, 296, 328, 18],
  text: [15, 326, 324, 149],
  pt: [245, 453, 130, 65],
  set: [302, 292, 50, 25],
} as const;

const box = (r: readonly number[]) => ({
  left: pct(r[0], W),
  top: pct(r[1], H),
  width: pct(r[2], W),
  height: pct(r[3], H),
});

/* 字号都按卡宽等比 —— 保证任意尺寸下版式一致 */
const fs = (ratio: number) => `calc(var(--cw) * ${ratio})`;

const WUBRG = ["W", "U", "B", "R", "G"] as const;
const COLOR_EN: Record<string, string> = {
  w: "white", u: "blue", b: "black", r: "red", g: "green",
};

/** 颜色 + 类型 → 卡框文件名（对齐 `CardInfo.DetermineFrameAndBox`）。 */
export function pickFrame(card: {
  colors?: string[];
  type?: string;
}): { frame: string; ptbox: string } {
  const colors = card.colors || [];
  const t = (card.type || "").toLowerCase();
  const isArt = t.includes("artifact");
  const isLand = t.includes("land");

  if (colors.length === 0) {
    if (isLand) {
      return isArt
        ? { frame: "c_artifact", ptbox: "ptbox_a" }
        : { frame: "c_land", ptbox: "" };
    }
    if (isArt) return { frame: "c_artifact", ptbox: "ptbox_a" };
    return { frame: "c", ptbox: "ptbox_c" };
  }
  if (colors.length === 1) {
    const c = colors[0].toLowerCase();
    return {
      frame: isArt ? `${c}_artifact` : c,
      ptbox: `ptbox_${c}`,
    };
  }
  if (colors.length === 2) {
    // 双色框有 10 种，按 WUBRG 顺序拼字母正好对上
    const pair = WUBRG.filter((x) => colors.includes(x))
      .map((x) => x.toLowerCase())
      .join("");
    return {
      frame: isArt ? `${pair}_artifact` : pair,
      ptbox: "ptbox_gold",
    };
  }
  // 三色及以上用通用多色框
  return { frame: "z", ptbox: "ptbox_gold" };
}

/** `{4}{R}{R}` -> `['4','R','R']` */
export function parseCost(cost: string): string[] {
  return [...(cost || "").matchAll(/\{([^}]+)\}/g)].map((m) => m[1]);
}

/** 素材里**真的存在**哪些法术力符号。App 启动时调 `/api/framelist` 灌进来。
 *
 *  为什么必须查一遍：`manaFile` 只能凭名字猜，而**猜错就是一张裂图**。
 *  `.tdx` 素材是 2013 年的 DotP 2014 自带的，`{C}`（无色，333 次）、
 *  `{E}`（能量，247 次）这些 2016 年才引入的符号**根本没有图**。
 *  `null` 表示还没拿到清单 —— 这时乐观地试一下，裂了有 `onError` 兜底。 */
let MANA_FILES: Set<string> | null = null;

export function setManaFiles(names: string[]) {
  MANA_FILES = new Set(names);
}

/** 拿到清单就按清单判，没拿到就放行（交给 `onError` 兜底）。 */
function available(f: string | null): string | null {
  if (!f) return null;
  if (MANA_FILES && !MANA_FILES.has(f)) return null;
  return f;
}

/** 单个法术力符号 -> 素材文件名（不含 .png）。素材里没有的返回 null。 */
export function manaFile(sym: string): string | null {
  const s = sym.trim();
  if (!s) return null;
  if (s.includes("/")) {
    const [a, b] = s.split("/");
    if (b.toUpperCase() === "P") {
      const c = COLOR_EN[a.toLowerCase()];
      return c ? `phyrexian_${c}_mana` : null;
    }
    // 混血 {W/U} -> mana_wu。**两种顺序都要试**：素材是按游戏的规范顺序命名的
    // （`mana_gu`、`mana_ur`），卡片 XML 里写 `{U/G}` `{R/U}` 就反过来了。
    const ab = `mana_${(a + b).toLowerCase()}`;
    const ba = `mana_${(b + a).toLowerCase()}`;
    if (MANA_FILES) return available(ab) || available(ba);
    return ab;
  }
  return available(`mana_${s.toLowerCase()}`);
}

const RARITY_FILE: Record<string, string> = {
  C: "expansion_common",
  U: "expansion_uncommon",
  R: "expansion_rare",
  M: "expansion_mythic",
};

const SYMBOL_RE = /(\{[^}]+\})/g;

/** 素材里没图的符号 —— 画个圆底 + 字母，比裂图或者裸花括号强得多。
 *
 *  尺寸分两层写：外层用**继承的字号**撑圆底（`em`），内层单独缩小字号。
 *  写一层的话 `height: 1.15em` 会按缩小后的字号算，圆点小一圈。 */
/** 法术力符号图。**加载失败就退回圆底字母** —— 清单还没到、或者素材清单
 *  和我们不一致时，也绝不会在卡面上留一个裂图图标。 */
function ManaImg({ file, sym }: { file: string; sym: string }) {
  const [bad, setBad] = useState(false);
  if (bad) return <SymbolFallback sym={sym} />;
  return (
    <img
      src={frameUrl("mana", file)}
      alt={sym}
      draggable={false}
      onError={() => setBad(true)}
      className="inline-block select-none"
      style={{
        width: "1.3em", height: "1.3em",
        verticalAlign: "-0.28em", margin: "0 0.05em",
      }}
    />
  );
}

export function SymbolFallback({ sym }: { sym: string }) {
  return (
    <span
      className="inline-flex select-none items-center justify-center rounded-full
                 bg-black/55 ring-1 ring-white/25"
      style={{
        height: "1.15em", minWidth: "1.15em", padding: "0 0.18em",
        verticalAlign: "-0.2em", margin: "0 0.05em",
      }}
    >
      <span className="font-bold text-white/90" style={{ fontSize: "0.72em" }}>
        {sym}
      </span>
    </span>
  );
}

/**
 * 富文本：把规则文本里的 `{R}` `{T}` `{2}` 渲染成**游戏自带的法术力符号图**，
 * 文字部分加粗。真牌上这些就是嵌在句子里的彩色符号，直接显示花括号很难读。
 *
 * 符号尺寸用 `em`，自动跟着字号缩放 —— 卡面在网格里是 100px 宽、悬浮时是 400px，
 * 两处共用这一个组件，不用各调一套数。
 */
function RichText({ text }: { text: string }) {
  const parts = useMemo(() => text.split(SYMBOL_RE), [text]);
  return (
    <>
      {parts.map((p, i) => {
        if (!p) return null;
        const m = p.length > 2 && p.startsWith("{") && p.endsWith("}")
          ? p.slice(1, -1)
          : null;
        if (m) {
          const f = manaFile(m);
          if (f) {
            return (
              <ManaImg key={i} file={f} sym={m} />
            );
          }
          // 素材里没有的符号（{C} {E} {2/W} 等）画成圆底字母，别显示裂图或裸花括号
          return <SymbolFallback key={i} sym={m} />;
        }
        return <span key={i}>{p}</span>;
      })}
    </>
  );
}

export type FaceCard = Partial<CardFull & CardSlim> & { key: string };

interface Props {
  card: FaceCard;
  /** 目标宽度（px）。高度按 512/356 自动算。 */
  width: number;
  /** 缩略图用 thumb（走磁盘缓存），放大看用 full。 */
  artSize?: "thumb" | "full";
  /** 只画插画不画框（牌盒封面预览之类用得上）。 */
  artOnly?: boolean;
  className?: string;
  onClick?: () => void;
  onDoubleClick?: () => void;
  onContextMenu?: (e: React.MouseEvent) => void;
}

function CardFaceImpl({
  card, width, artSize = "thumb", artOnly = false,
  className = "", onClick, onDoubleClick, onContextMenu,
}: Props) {
  const height = (width * H) / W;
  const { frame, ptbox } = pickFrame(card);
  const cost = parseCost(card.cost || "");
  const name = card.zh || card.en || "";
  const typeLine = card.line || [card.type, card.sub].filter(Boolean).join(" — ");
  const text = card.text || "";
  const flavor = card.flavor || "";
  const pt = card.power && card.tough ? `${card.power}/${card.tough}` : "";
  const rarity = (card.rarity || "").toUpperCase();

  /* 规则文本区高度只占卡面的 29%，字数多的卡（尤其分裂牌）会溢出。
     按总字数分档缩字号 —— 短文本保持大而清晰，长文本自动让步。 */
  const fit = useMemo(() => {
    const n = text.length + flavor.length * 0.85;
    if (n <= 150) return 1;
    if (n <= 210) return 0.92;
    if (n <= 300) return 0.84;
    if (n <= 420) return 0.76;
    return 0.68;
  }, [text, flavor]);

  return (
    <div
      className={`card-face relative shrink-0 ${className}`}
      style={{ width, height, ["--cw" as string]: `${width}px` }}
      onClick={onClick}
      onDoubleClick={onDoubleClick}
      onContextMenu={onContextMenu}
    >
      {/* 1. 插画 —— **精确放进插画窗口**，不是铺满整张卡。
             ⚠️ 早先写成 `inset-0 object-cover`（铺满整张卡再用卡框遮四周），
             那样会**横着裁掉一半**：插画是 512×376（横），卡是 356×512（竖），
             cover 按高度缩放后宽度变成 697，水平居中只剩中间 51% ——
             分裂牌那种横向构图的卡看起来就像「截到了两张画面的中间」。
             插画窗口 324×238 的宽高比 1.361 和插画 1.362 几乎一致，精确放置几乎不裁。 */}
      <div className="absolute overflow-hidden" style={box(R.art)}>
        <img
          src={artUrl(card.key, artSize)}
          alt=""
          draggable={false}
          loading="lazy"
          className="h-full w-full object-cover"
          onError={(e) => {
            (e.currentTarget as HTMLImageElement).style.visibility = "hidden";
          }}
        />
      </div>

      {!artOnly && (
        <>
          {/* 2. 卡框（中心镂空，插画从下面透出来） */}
          <img
            src={frameUrl("frames", frame)}
            alt=""
            draggable={false}
            className="pointer-events-none absolute inset-0 h-full w-full"
            onError={(e) => {
              (e.currentTarget as HTMLImageElement).src =
                frameUrl("frames", "c");
            }}
          />

          {/* 3a. 牌名 */}
          <div
            className="absolute flex items-center overflow-hidden"
            style={box(R.name)}
          >
            <span
              className="truncate tracking-tight"
              style={{
                fontSize: fs(0.056),
                fontWeight: 800,
                lineHeight: 1.08,
                color: "#0a0a0a",
                fontFamily: "var(--font-card)",
                textShadow: "0 1px 0 rgba(255,255,255,0.45)",
              }}
            >
              {name}
            </span>
          </div>

          {/* 3b. 费用符号（右上角，右对齐） */}
          <div
            className="absolute flex items-center justify-end"
            style={{
              left: pct(180, W),
              top: pct(12, H),
              width: pct(164, W),
              height: pct(24, H),
              gap: fs(0.006),
            }}
          >
            {cost.map((sym, i) => {
              const f = manaFile(sym);
              if (!f) {
                return (
                  <span
                    key={i}
                    className="rounded-full bg-black/70 px-1 font-bold text-white"
                    style={{ fontSize: fs(0.034) }}
                  >
                    {sym}
                  </span>
                );
              }
              return (
                <img
                  key={i}
                  src={frameUrl("mana", f)}
                  alt={sym}
                  draggable={false}
                  className="shrink-0"
                  style={{ width: fs(0.070), height: fs(0.070) }}
                />
              );
            })}
          </div>

          {/* 3c. 类型行 */}
          <div
            className="absolute flex items-center overflow-hidden"
            style={box(R.typeLine)}
          >
            <span
              className="truncate"
              style={{
                fontSize: fs(0.043),
                fontWeight: 600,
                color: "#0f0f0f",
                fontFamily: "var(--font-card)",
              }}
            >
              {typeLine}
            </span>
          </div>

          {/* 3d. 系列 + 稀有度符号 */}
          {RARITY_FILE[rarity] && (
            <img
              src={frameUrl("misc", RARITY_FILE[rarity])}
              alt={rarity}
              draggable={false}
              className="pointer-events-none absolute object-contain"
              style={{ ...box(R.set), right: pct(W - R.set[0] - R.set[2], W), left: "auto" }}
            />
          )}

          {/* 3e. 规则文本 + 风味 */}
          <div
            className="absolute overflow-hidden"
            style={box(R.text)}
          >
            <div
              style={{
                fontSize: `calc(${fs(0.042)} * ${fit})`,
                fontWeight: 700,
                lineHeight: 1.34,
                color: "#0a0a0a",
                fontFamily: "var(--font-card)",
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              <RichText text={text} />
            </div>
            {flavor && (
              <div
                className="italic"
                style={{
                  fontSize: `calc(${fs(0.038)} * ${fit})`,
                  fontWeight: 600,
                  lineHeight: 1.26,
                  color: "#2e2e2e",
                  fontFamily: "var(--font-card)",
                  marginTop: fs(0.018),
                }}
              >
                {flavor}
              </div>
            )}
          </div>

          {/* 3f. 力量/防御 */}
          {pt && (
            <>
              <img
                src={frameUrl("ptbox", ptbox || "ptbox_c")}
                alt=""
                draggable={false}
                className="pointer-events-none absolute h-full w-full"
                style={box(R.pt)}
              />
              <div
                className="pointer-events-none absolute flex items-center justify-center font-bold"
                style={{ ...box(R.pt), paddingTop: pct(14, H) }}
              >
                <span style={{ fontSize: fs(0.074), fontWeight: 800, color: "#080808" }}>
                  {pt}
                </span>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}

export const CardFace = memo(CardFaceImpl);
