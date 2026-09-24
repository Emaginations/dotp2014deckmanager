/**
 * 悬浮放大看牌。
 *
 * 这套逻辑的几个关键点（都是 phase 那套 hover 预览踩出来的，照抄）：
 *
 * 1. **`pointer-events-none`** —— 浮层永远不吃鼠标事件。
 *    否则指针一移到浮层上，卡片立刻触发 `pointerleave`，浮层闪一下就没了。
 *    配合下面第 2 条，指针其实一直「在卡片上」，逻辑就闭合了。
 *
 * 2. **`relatedTarget.closest("[data-card-preview]")` 白名单** ——
 *    指针从卡片移到浮层时浏览器会发 `pointerleave`（`pointerType` 仍是 mouse）。
 *    如果此刻 `relatedTarget` 落在浮层内就忽略这次 leave。
 *
 * 3. **左右半屏翻转** —— 指针在屏幕右半边就把浮层摆左边，永远不盖住指针。
 *
 * 4. **用 `offsetWidth/offsetHeight` 实测夹紧**，不要用估算值 ——
 *    卡面内容（规则文本多少）会让实际高度变化，估算会溢出矮屏底部。
 *
 * 5. **rAF 节流** —— mousemove 每秒几十次，直接 setState 会拖垮渲染。
 */

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { CardFace, type FaceCard } from "./CardFace";

const GAP = 22;          // 浮层与指针的间距
const MARGIN = 14;       // 距视口边缘
const ZOOM_WIDTH = 400;  // 放大后的卡宽（356 基准下约等于真实卡牌大小）
const ASPECT = 512 / 356;

/* 指针位置缓存在模块级 —— 不触发 React 重渲染，
   浮层显示时直接读最新值定位，不用等 mousemove。 */
let pointer: { x: number; y: number } | null = null;
if (typeof window !== "undefined") {
  window.addEventListener(
    "mousemove",
    (e) => {
      pointer = { x: e.clientX, y: e.clientY };
    },
    { passive: true }
  );
}

interface Props {
  card: FaceCard | null;
  width?: number;
}

export function HoverZoom({ card, width = ZOOM_WIDTH }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const frame = useRef<number | null>(null);
  const [pos, setPos] = useState({ left: -9999, top: -9999, origin: "0% 50%" });

  const height = width * ASPECT;

  useEffect(() => {
    if (!card) {
      if (frame.current != null) cancelAnimationFrame(frame.current);
      frame.current = null;
      return;
    }

    const place = () => {
      frame.current = null;
      const p = pointer;
      if (!p) return;

      // 实测尺寸优先 —— 内容多少会影响高度
      const el = ref.current;
      const w = el?.offsetWidth || width;
      const h = el?.offsetHeight || height;

      const vw = window.innerWidth;
      const vh = window.innerHeight;

      // 右半屏 -> 摆左边；左半屏 -> 摆右边。永远不压住指针。
      const left =
        p.x > vw / 2
          ? Math.max(MARGIN, p.x - w - GAP)
          : Math.min(p.x + GAP, vw - w - MARGIN);
      // 垂直居中于指针，再夹到视口内
      const top = Math.min(
        Math.max(MARGIN, p.y - h / 2),
        Math.max(MARGIN, vh - h - MARGIN)
      );

      setPos({
        left,
        top,
        // 进场缩放从靠近指针那一边长出来
        origin: p.x > vw / 2 ? "100% 50%" : "0% 50%",
      });
    };

    const schedule = () => {
      if (frame.current != null) return;
      frame.current = requestAnimationFrame(place);
    };

    schedule();
    window.addEventListener("mousemove", schedule, { passive: true });
    window.addEventListener("scroll", schedule, { passive: true, capture: true });
    window.addEventListener("resize", schedule);
    return () => {
      window.removeEventListener("mousemove", schedule);
      window.removeEventListener("scroll", schedule, { capture: true } as never);
      window.removeEventListener("resize", schedule);
      if (frame.current != null) cancelAnimationFrame(frame.current);
      frame.current = null;
    };
    // card 变化也要重定位（换了张牌，高度可能差很多）
  }, [card, width, height]);

  return (
    <AnimatePresence>
      {card && (
        <motion.div
          ref={ref}
          data-card-preview=""
          className="pointer-events-none fixed z-[100]"
          style={{
            left: pos.left,
            top: pos.top,
            transformOrigin: pos.origin,
            filter: "drop-shadow(0 24px 30px rgba(0,0,0,0.65))",
          }}
          initial={{ opacity: 0, scale: 0.86 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, scale: 0.94, transition: { duration: 0.1 } }}
          transition={{ duration: 0.16, ease: [0.16, 1, 0.3, 1] }}
        >
          <CardFace card={card} width={width} artSize="full" />
        </motion.div>
      )}
    </AnimatePresence>
  );
}

/**
 * 悬浮触发钩子 —— 带延迟，鼠标快速划过不弹窗。
 *
 * `data-card-hover` 属性是给全局兜底判断用的（指针离开所有卡片时收浮层）。
 */
export function useHoverTrigger(
  delay = 140,
  /** 按需拉完整卡数据。网格里的列表项是精简版（没有规则文本），
   *  直接用会导致悬浮放大后**卡面是空框**。 */
  loadFull?: (key: string) => Promise<FaceCard>
) {
  const [card, setCard] = useState<FaceCard | null>(null);
  const timer = useRef<number | null>(null);
  const shown = useRef(false);
  const fullCache = useRef<Record<string, FaceCard>>({});
  const pending = useRef<Set<string>>(new Set());

  const clear = () => {
    if (timer.current != null) window.clearTimeout(timer.current);
    timer.current = null;
  };

  /** 先拿已有数据立刻显示，再异步补全（到了就原地替换，不闪）。 */
  const enrich = (c: FaceCard) => {
    const cached = fullCache.current[c.key];
    if (cached) {
      setCard(cached);
      return;
    }
    if (!loadFull) {
      setCard(c);
      return;
    }
    setCard(c);                       // 先显示插画和牌名，别等
    if (pending.current.has(c.key)) return;
    pending.current.add(c.key);
    loadFull(c.key)
      .then((f) => {
        fullCache.current[c.key] = f;
        // 只在「用户还停在这张卡上」时替换，否则会闪出别的牌
        setCard((cur) => (cur && cur.key === c.key ? f : cur));
      })
      .catch(() => { /* 拉不到就保持精简版，不打断浏览 */ })
      .finally(() => pending.current.delete(c.key));
  };

  const enter = (c: FaceCard) => (e: React.PointerEvent) => {
    // 只认鼠标 —— 触摸走点击。触摸合成的 enter 会让浮层反复开关。
    if (e.pointerType !== "mouse") return;
    clear();
    if (shown.current) {
      // 已经在看牌了，在卡间扫动是即时的，不再等延迟
      enrich(c);
      return;
    }
    timer.current = window.setTimeout(() => {
      shown.current = true;
      enrich(c);
    }, delay);
  };

  const leave = (e: React.PointerEvent) => {
    if (e.pointerType !== "mouse") return;
    const rel = e.relatedTarget;
    // 指针移到浮层上也算「还在卡片上」
    if (rel instanceof Element && rel.closest("[data-card-preview]")) return;
    clear();
    shown.current = false;
    setCard(null);
  };

  // 全局兜底：网格行可能在指针底下被替换掉，React 收不到 pointerleave
  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      if (e.pointerType !== "mouse") return;
      if (e.target instanceof Element && e.target.closest("[data-card-preview]"))
        return;
      if (document.querySelector("[data-card-hover]:hover") == null) {
        clear();
        shown.current = false;
        setCard(null);
      }
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    return () => window.removeEventListener("pointermove", onMove);
  }, []);

  return { card, enter, leave, dismiss: () => setCard(null) };
}
