/**
 * 排序条 —— 卡池正上方的**置顶栏**，不跟着网格滚。
 *
 * 原来这一组 chip 在左侧筛选面板的倒数第二段，要滚到面板底部才够得着。
 * 但「按什么排、正序还是倒序」是组牌时改得最勤的一项（找完颜色想按费用看曲线，
 * 找完费用想按稀有度看强度），每次都要先滚下去、点完再滚回来看结果，
 * 一来一回把网格顶出去了。挪到卡池上方之后一直可见，点完立刻看到结果。
 *
 * 只搬位置，没动语义 —— `sort` / `desc` 还是 `Filters` 里的同一个字段，
 * 走的还是 `App` 那个 `setFilters`。
 */

import type { Filters } from "./FilterPanel";
import { Chip } from "./bits";

/** 和后端 `cardset.search()` 的 `sort` 参数一一对应。 */
const SORTS: [string, string, string][] = [
  ["name", "名称", "按卡名排序（中文名优先，没有中文名用英文名）"],
  ["cmc", "费用", "按法术力值从低到高，同费用的按名称"],
  ["rarity", "稀有度", "神话 → 稀有 → 非普通 → 普通，同稀有度按费用"],
  ["set", "系列", "按系列代码，同系列内按费用"],
];

export function SortBar({
  value,
  onChange,
}: {
  value: Filters;
  onChange: (f: Filters) => void;
}) {
  const set = (patch: Partial<Filters>) => onChange({ ...value, ...patch });

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-hairline
                    bg-black/20 px-4 py-2">
      <span className="mr-0.5 text-[11px] text-fg-faint">排序</span>
      {SORTS.map(([k, label, hint]) => (
        <Chip
          key={k}
          title={hint}
          active={value.sort === k}
          onClick={() => set({ sort: k })}
        >
          {label}
        </Chip>
      ))}

      {/* 升/降是**独立的开关**，不是第五个排序键 —— 所以和上面隔开一点 */}
      <span className="mx-0.5 h-4 w-px bg-hairline" />
      <Chip
        title={value.desc ? "当前降序，点击切成升序" : "当前升序，点击切成降序"}
        active={value.desc}
        onClick={() => set({ desc: !value.desc })}
      >
        {value.desc ? "↓ 降序" : "↑ 升序"}
      </Chip>
    </div>
  );
}
