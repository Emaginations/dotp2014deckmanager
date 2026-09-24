/**
 * 打包向导 —— 把卡组工程变成游戏能加载的 WAD。
 *
 * 三步：① 选牌盒封面 → ② 选鹏洛客立绘 → ③ 打包并装入
 *
 * 两张图都从**卡池里选一张卡的插画**，然后走 Deck Builder 同一套合成管线
 * （`artgen.py`）。预览走 `/api/preview/*`，和打包时**逐像素一致**，
 * 不是"大概长这样"的示意图。
 *
 * 立绘一张派生四张贴图：圆形头像 / 未解锁头像（压暗去饱和）/
 * 大厅背板 / 全身立绘。选一张就够了。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import * as api from "../api";
import type { CardSlim, DeckData, PackPlan, PackResult } from "../api";
import { CardFace } from "./CardFace";

const TILE = 78;

type Step = 0 | 1 | 2;

interface Props {
  deck: DeckData;
  onClose: () => void;
  /** 打包成功后通知外层刷新（牌组列表多了一副） */
  onPacked?: (r: PackResult) => void;
}

export function PackWizard({ deck, onClose, onPacked }: Props) {
  const [step, setStep] = useState<Step>(0);
  const [cover, setCover] = useState(deck.cover || "");
  const [avatar, setAvatar] = useState(deck.avatar || deck.cover || "");
  const [plan, setPlan] = useState<PackPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [result, setResult] = useState<PackResult | null>(null);
  /** 这副牌组用到的卡（含解锁表）。封面/立绘只从这里选。 */
  const [deckCards, setDeckCards] = useState<CardSlim[]>([]);

  useEffect(() => {
    let dead = false;
    api.getDeck(deck.id).then((r) => {
      if (dead) return;
      // 张数多的排前面 —— 4 张的才是这副牌的"招牌"，
      // 而列表接口不带 count，得回牌表里查
      const cnt = (c: CardSlim) =>
        (deck.main[c.en] || deck.main[c.zh]) ??
        (deck.side[c.en] || deck.side[c.zh]) ?? 0;
      setDeckCards([...r.cards].sort(
        (a, b) => cnt(b) - cnt(a)
          || (a.zh || a.en || "").localeCompare(b.zh || b.en || "", "zh")
      ));
    }).catch(() => { /* 错误已进全局 toast */ });
    return () => { dead = true; };
  }, [deck.id, deck.main, deck.side]);

  // 每步都重新体检一次 —— 选完图 uid_num 会变，卡表也可能在这期间被改过
  const recheck = useCallback(async () => {
    try {
      setPlan(await api.packPlan(deck.id, cover, avatar));
    } catch (e) {
      setNote("体检失败：" + (e as Error).message);
    }
  }, [deck.id, cover, avatar]);

  useEffect(() => { recheck(); }, [recheck]);

  const doPack = async () => {
    setBusy(true);
    setNote(null);
    try {
      const r = await api.packDeck(deck.id, { cover, avatar, install: true });
      setResult(r);
      onPacked?.(r);
    } catch (e) {
      setNote("打包失败：" + (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const STEPS = ["选封面", "选立绘", "打包"];
  const errs = (plan?.problems || []).filter((p) => p.level === "error");
  const warns = (plan?.problems || []).filter((p) => p.level === "warn");
  const canNext = step === 0 ? !!cover : step === 1 ? !!avatar : plan?.ok;

  return (
    <motion.div
      className="fixed inset-0 z-[110] flex items-center justify-center bg-black/65 p-6"
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
      onClick={onClose}
    >
      <motion.div
        className="flex h-[min(760px,92vh)] w-[min(980px,94vw)] flex-col overflow-hidden
                   rounded-panel border border-hairline bg-panel-solid/98 backdrop-blur-xl"
        initial={{ opacity: 0, scale: 0.97, y: 8 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.98 }}
        transition={{ duration: 0.16 }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* 标题 + 步骤条 */}
        <div className="flex shrink-0 items-center gap-4 border-b border-hairline px-4 py-3">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold">
              打包「{deck.name_en || deck.name_cn}」
            </div>
            <div className="truncate text-[11px] text-fg-dim">
              做成游戏能加载的卡包，直接装进游戏目录
            </div>
          </div>
          <div className="ml-auto flex items-center gap-1">
            {STEPS.map((s, i) => (
              <div key={s} className="flex items-center gap-1">
                {i > 0 && <span className="mx-0.5 text-fg-faint">─</span>}
                <button
                  onClick={() => !result && i <= step && setStep(i as Step)}
                  disabled={!!result || i > step}
                  className={
                    "flex items-center gap-1.5 rounded-chip px-2.5 py-1 text-xs transition " +
                    (i === step
                      ? "bg-ember/20 text-ember-bright"
                      : i < step
                        ? "text-fg-muted hover:text-fg"
                        : "text-fg-faint")
                  }
                >
                  <span className={
                    "flex h-4 w-4 items-center justify-center rounded-full text-[10px] " +
                    (i <= step ? "bg-ember text-black" : "bg-white/10")
                  }>
                    {i < step ? "✓" : i + 1}
                  </span>
                  {s}
                </button>
              </div>
            ))}
          </div>
          <button
            onClick={onClose}
            className="rounded-tile px-2 py-0.5 text-fg-dim transition hover:text-fg"
          >✕</button>
        </div>

        {result ? (
          <Done result={result} onClose={onClose} />
        ) : (
          <>
            {/* 内容 */}
            {step === 2 ? (
              <Summary plan={plan} deck={deck} cover={cover} avatar={avatar} />
            ) : (
              <div className="flex min-h-0 flex-1">
                <Picker
                  cards={deckCards}
                  picked={step === 0 ? cover : avatar}
                  onPick={step === 0 ? setCover : setAvatar}
                />
                <div className="thin-scroll w-[320px] shrink-0 overflow-y-auto border-l border-hairline p-3">
                  {step === 0
                    ? <CoverPreview cardKey={cover} />
                    : <PortraitPreview cardKey={avatar} />}
                </div>
              </div>
            )}

            {/* 底栏 */}
            <div className="flex shrink-0 items-center gap-3 border-t border-hairline px-4 py-3">
              {note && <span className="text-xs text-red-300">{note}</span>}
              {!note && (errs.length > 0 ? (
                <span className="truncate text-xs text-red-300" title={errs[0].msg}>
                  ⚠ {errs[0].msg}
                </span>
              ) : warns.length > 0 ? (
                <span className="truncate text-xs text-amber-300" title={warns[0].msg}>
                  {warns[0].msg}
                </span>
              ) : plan ? (
                <span className="text-xs text-fg-dim">
                  {plan.info.n_main} 条牌 + 引擎补 {plan.info.n_basic} 张地 ={" "}
                  <b className="text-fg">{plan.info.total}</b> 张
                  <span className="ml-3 text-fg-faint">包名 {plan.uid}</span>
                </span>
              ) : null)}

              <div className="ml-auto flex items-center gap-2">
                {step > 0 && (
                  <button
                    onClick={() => setStep((s) => (s - 1) as Step)}
                    className="rounded-tile border border-hairline px-3 py-1.5 text-xs
                               transition hover:border-hairline-hover"
                  >上一步</button>
                )}
                {step < 2 ? (
                  <button
                    disabled={!canNext}
                    onClick={() => setStep((s) => (s + 1) as Step)}
                    className="rounded-tile bg-ember px-4 py-1.5 text-xs font-medium text-black
                               transition hover:bg-ember-bright disabled:opacity-30"
                  >下一步</button>
                ) : (
                  <button
                    disabled={busy || !plan?.ok}
                    onClick={doPack}
                    title={!plan?.ok ? "先解决上面的问题" : "打包并装进游戏目录"}
                    className="rounded-tile bg-ember px-4 py-1.5 text-xs font-medium text-black
                               transition hover:bg-ember-bright disabled:opacity-30"
                  >{busy ? "打包中…" : "打包并装入"}</button>
                )}
              </div>
            </div>
          </>
        )}
      </motion.div>
    </motion.div>
  );
}

/* ------------------------------------------------------------ 选卡器 */

/** 选卡器 —— **只列这副牌组里用到的卡**，不是整个卡池。
 *
 *  从两万多张卡里搜没有意义：封面/立绘要的是这副牌的"招牌"，
 *  而招牌一定在牌表里。顺带还快得多（21 张 vs 分页拉 175 张）。
 *  多出来的搜索框只是在这几十张里过滤。 */
function Picker({
  cards, picked, onPick,
}: {
  cards: CardSlim[];
  picked: string;
  onPick: (key: string) => void;
}) {
  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-hairline px-3 py-2
                      text-[11px] text-fg-dim">
        <span>
          这副牌组用了 <b className="text-fg-muted">{cards.length}</b> 种卡
        </span>
        <span className="ml-auto text-fg-faint">点一下选它</span>
      </div>

      <div className="thin-scroll min-h-0 flex-1 overflow-y-auto p-3">
        {cards.length === 0 ? (
          <div className="flex h-full items-center justify-center px-6 text-center
                          text-xs leading-relaxed text-fg-faint">
            这副牌组还是空的 —— 先去左边加几张卡，再回来选封面
          </div>
        ) : (
          <div
            className="grid gap-2"
            style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${TILE}px, 1fr))` }}
          >
            {cards.map((c) => {
              const on = c.key === picked;
              return (
                <div
                  key={c.key}
                  onClick={() => onPick(c.key)}
                  title={`${c.zh || c.en}\n${c.en}`}
                  className={
                    "relative cursor-pointer rounded-tile transition " +
                    (on
                      ? "ring-2 ring-ember ring-offset-1 ring-offset-panel-solid"
                      : "opacity-85 hover:opacity-100 hover:ring-1 hover:ring-white/25")
                  }
                >
                  <CardFace card={c} width={TILE} artOnly />
                  <div className="pointer-events-none absolute inset-x-[3%] bottom-[3%]
                                  truncate rounded-b-[3px] bg-gradient-to-t
                                  from-black/90 to-transparent px-1 pb-0.5 pt-3
                                  text-[9px] font-medium text-white/90">
                    {c.zh || c.en}
                  </div>
                  {on && (
                    <span className="absolute -right-1 -top-1 flex h-5 w-5 items-center
                                     justify-center rounded-full bg-ember text-[11px]
                                     font-bold text-black shadow">
                      ✓
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------ 预览 */

function PreviewBox({
  title, src, ratio, hint,
}: {
  title: string;
  src: string;
  ratio?: string;
  hint?: string;
}) {
  return (
    <div className="mb-3">
      <div className="mb-1 flex items-baseline gap-2">
        <span className="text-[11px] font-semibold text-fg-muted">{title}</span>
        {hint && <span className="text-[10px] text-fg-faint">{hint}</span>}
      </div>
      <div
        className="rounded-tile border border-hairline bg-[repeating-conic-gradient(#2a2a30_0%_25%,#222228_0%_50%)]
                   bg-[length:16px_16px]"
        style={{ aspectRatio: ratio }}
      >
        <img src={src} alt={title} className="h-full w-full object-contain" />
      </div>
    </div>
  );
}

function CoverPreview({ cardKey }: { cardKey: string }) {
  if (!cardKey)
    return <Empty text="左边点一张卡，这里会显示合成后的牌盒封面" />;
  // 卡图在盒面里的摆放由官方算法（`initial_adjust`）定，不给缩放了 ——
  // 想调应该去调那个算法，给个滑杆反而让人以为可以随便拉。
  return (
    <PreviewBox
      title="牌盒封面"
      hint="512×512 · 摆放走官方算法"
      src={api.previewCoverUrl(cardKey)}
    />
  );
}

function PortraitPreview({ cardKey }: { cardKey: string }) {
  if (!cardKey)
    return <Empty text="左边点一张卡，这里会显示四张立绘" />;
  return (
    <>
      <PreviewBox
        title="圆形头像" hint="256×256 · 大厅/对局用"
        src={api.previewPortraitUrl(cardKey, "avatar")} ratio="1 / 1"
      />
      <PreviewBox
        title="未解锁头像" hint="自动压暗去饱和"
        src={api.previewPortraitUrl(cardKey, "locked")} ratio="1 / 1"
      />
      <PreviewBox
        title="大厅背板" hint="256×512"
        src={api.previewPortraitUrl(cardKey, "backplate")} ratio="1 / 2"
      />
      <PreviewBox
        title="全身立绘" hint="1024×1024"
        src={api.previewPortraitUrl(cardKey, "full")} ratio="1 / 1"
      />
    </>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div className="flex h-full items-center justify-center px-4 text-center
                    text-xs leading-relaxed text-fg-faint">
      {text}
    </div>
  );
}

/* ------------------------------------------------------------ 确认页 */

function Summary({
  plan, deck, cover, avatar,
}: {
  plan: PackPlan | null;
  deck: DeckData;
  cover: string;
  avatar: string;
}) {
  if (!plan) return <Empty text="正在体检…" />;
  const i = plan.info;
  return (
    <div className="thin-scroll min-h-0 flex-1 overflow-y-auto p-4">
      <Row k="卡包名" v={deck.name_en || "（没填）"} ok={/^[ -~]*$/.test(deck.name_en || "")} />
      <Row k="中文备注" v={deck.name_cn || "（没写）"} />
      <Row k="包名 uid" v={plan.uid} mono />
      <Row k="牌组编号 uid_num" v={String(plan.uid_num)} mono />
      <Row k="产出文件" v={plan.filename} mono />
      <Row
        k="牌表"
        v={`${i.n_main} 条 <CARD> + 引擎补 ${i.n_basic} 张基本地 = ${i.total} 张`}
        ok={i.total === i.target}
      />
      <Row k="颜色" v={i.colors.join("、") || "（无色）"} />
      <Row k="基本地池" v={i.basics.join("、") || "（不需要）"} />
      <Row k="解锁表" v={`${i.n_unlock} / 30`} />
      <Row k="封面" v={cover} mono />
      <Row k="立绘" v={avatar} mono />

      {plan.problems.length > 0 && (
        <div className="mt-4 space-y-1.5">
          {plan.problems.map((p, n) => (
            <div
              key={n}
              className={
                "rounded-tile border px-3 py-2 text-xs leading-relaxed " +
                (p.level === "error"
                  ? "border-red-500/40 bg-red-500/10 text-red-300"
                  : "border-amber-500/40 bg-amber-500/10 text-amber-200")
              }
            >
              {p.level === "error" ? "⚠ " : "· "}{p.msg}
            </div>
          ))}
        </div>
      )}

      <div className="mt-4 rounded-tile border border-hairline bg-black/20 px-3 py-2.5
                      text-[11px] leading-relaxed text-fg-dim">
        <div className="mb-1 font-semibold text-fg-muted">装进去之后</div>
        · 文件写到 <code>{plan.filename}</code>，<b>直接在游戏目录里</b>（同名包先自动备份）
        <br />
        · 进游戏后要手动进牌组编辑器<b>保存一次</b>才算可用
        <br />
        · 牌组会出现在「自制」分组里
      </div>
    </div>
  );
}

function Row({
  k, v, mono, ok,
}: {
  k: string;
  v: string;
  mono?: boolean;
  ok?: boolean;
}) {
  return (
    <div className="flex items-start gap-3 border-b border-hairline/60 py-1.5 text-xs">
      <span className="w-32 shrink-0 text-fg-dim">{k}</span>
      <span
        className={
          "min-w-0 flex-1 break-all " +
          (mono ? "font-mono text-[11px] " : "") +
          (ok === undefined ? "text-fg" : ok ? "text-emerald-300" : "text-red-300")
        }
      >
        {v}
      </span>
    </div>
  );
}

/* ------------------------------------------------------------ 完成 */

function Done({ result, onClose }: { result: PackResult; onClose: () => void }) {
  const mb = useMemo(() => (result.bytes / 1e6).toFixed(2), [result.bytes]);
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
      <div className="text-5xl">📦</div>
      <div className="text-base font-semibold text-emerald-300">打包完成</div>
      <div className="max-w-[520px] text-xs leading-relaxed text-fg-muted">
        <div className="mb-2">
          <code className="text-fg">{result.filename}</code> · {result.entries} 个条目 · {mb} MB
        </div>
        <div className="break-all text-fg-dim">{result.installed || result.out_path}</div>
        {result.backup && (
          <div className="mt-2 text-amber-200/90">
            同名包已备份到 {result.backup.split(/[\\/]/).pop()}
          </div>
        )}
        <div className="mt-3 text-fg-faint">
          进游戏后要手动进牌组编辑器<b className="text-fg-muted">保存一次</b>才算可用。
        </div>
      </div>
      <button
        onClick={onClose}
        className="mt-2 rounded-tile bg-ember px-5 py-1.5 text-xs font-medium text-black
                   transition hover:bg-ember-bright"
      >好</button>
    </div>
  );
}
