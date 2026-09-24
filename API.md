# 对外接口

给**外部程序 / agent** 用的。三种调用方式，能力一样：

| 方式 | 适合 | 入口 |
|---|---|---|
| **HTTP** | 远程、多语言、浏览器 | `python backend/main.py` → `http://127.0.0.1:<port>/api/…` |
| **CLI** | shell 脚本、agent 工具调用 | `python backend/cli.py <子命令>` |
| **Python** | 同进程集成 | `import cardset, deck, render` |

在线文档（FastAPI 自动生成）：`http://127.0.0.1:<port>/api/docs`

---

## 0. 准备

```bash
cd backend
python tools/rebuild.py        # 建索引（首次约 40 秒，之后按 WAD 指纹自动跳过）
python cli.py doctor           # 自检
```

**游戏目录不是写死的**——在 `data/settings.json` 里：

```json
{ "game_dir": "E:\\game\\Magic 2014" }
```

改了之后所有索引会按新目录的 **WAD 指纹**（每个包的大小 + mtime）判断要不要重建。
`data/settings.json` 不存在时会用默认值自动创建。

HTTP 方式可以直接改：

```bash
curl -X POST localhost:PORT/api/settings -H 'Content-Type: application/json' \
     -d '{"game_dir": "D:\\Games\\Magic 2014"}'
```

---

## 1. 卡牌查询

### `GET /api/meta`

筛选项的取值域 —— **只列卡池里真的出现过的**。

返回 `count` / `types` / `sets` / `srcs` / `rarities` / `legal` / `keywords` /
`cmc_max` / `cmc_hist`。

```bash
curl 'localhost:PORT/api/meta'
```

### `GET /api/cards`

| 参数 | 说明 |
|---|---|
| `q` | 自由文本，中英文卡名 + 规则文本 + 风味，空格分词 AND |
| `colors` | 逗号分隔。`W,U,B,R,G` 是**含**该色；`C` = **一个颜色都没有**；`M` = 至少两色 |
| `color_mode` | `any`（默认，并集）/ `exact`（恰好这些色，「且」）/ `only`（**仅限混血**，「或」） |

`any` 模式下每个勾选项是一个**桶**，取并集：

```bash
colors=C          # 只有纯无色 → 4502 张
colors=W          # 含白（白、白蓝、五色都算）→ 3810 张
colors=W,C        # 含白 **或** 纯无色 → 3810 + 4502 = 8312 张
colors=M          # 至少两色 → 1812 张
```

`color_mode` 三种模式（以 `colors=B,G` 为例，实测全池 21,386）：

| 模式 | 规则 | 命中 | 关系 | 例 |
|---|---|---|---|---|
| `any` | 含其中任意一个 | 7549 | 交 | `{B}{U}`、单黑、金卡都出 |
| `exact` | 颜色**正好**是这些 | 117 | **且** | 金卡 `{B}{G}` |
| `only` | 含混血符号 **且** 符号只用到这些色 | 36 | **或** | `{B/G}`、`{2/B}`、`{B/P}`、`{G/P}` |

> [!IMPORTANT] 「且」和「或」是两回事，别把它们做成同一个谓词
> `{B}{G}` 是**金卡** —— 两个色都得有，`exact` 管这个。
> `{B/G}` 是**混血** —— 拿 B 或 G 任一个都能付，`only` 管这个。
>
> 判据必须读 **`cost` 字符串**，不能读 `colors`：
> 这份数据里混血符号**不赋予颜色**，`{B/G}{B/G}` 的 `colors` 是 `[]`，
> 连 `{3}{G/P}` 的**诞生荚**（真卡是绿牌）都是 `[]`。
> 全池 355 张带混血符号的卡都是这个待遇。
> 看 `colors` 的话这 36 张会被当成无色牌，`only` 就退化成 `exact`（都是 117）——
> 这个 bug 真出现过，界面上表现为**两个按钮点哪个都一样**。
>
> `only` 模式下 `C` / `M` 不参与（混血符号必定带颜色）。
> 想单独要无色牌用 `any` + `C`（4502 张）。
>
> `python backend/cardset.py` 有自检，**逐张**验证返回的卡符合谓词，
> 并在末尾把三档摆一起对比，命中数撞车会告警。

| `type` / `sub` | 主类别 / 副类别，子串匹配 |
| `cmc_min` / `cmc_max` | 法术力值区间 |
| **`rarity`** | **逗号分隔的稀有度 `C,U,R,M,T,S`** |
| `kw` | 特征性异能，逗号分隔，**全部满足**才算命中 |
| `set` / `src` / `legal` | 系列 / 来源包 / 赛制合法性 |
| `has_art` | 只看有插画的 |
| `limit` / `offset` | **默认 175 条一页**，`total` 是全量命中数 |
| `sort` | `name` / `cmc` / `rarity` / `set`，配 `desc=true` |
| **`focus`** | 卡池 key。传了会多返回 `focus_index` —— 它在**完整结果**里排第几位 |

> [!WARNING] `C` 是「无颜色」，不是「含无色法术力」
> 虚色（devoid）牌有法术力颜色，**不算** C。
> 这个筛选曾经整段短路（只勾 C 时 `plain_colors` 是空集，`if` 直接跳过），
> 返回**全卡池 21386 张** —— 界面上表现为「筛无色却混进一堆蓝牌」。
> `python backend/cardset.py` 有自检，会**逐张**验证返回的卡符合谓词。

**`focus` 是给「跳到某张卡」用的。** 两万多张卡一次只加载 175 条，
要定位一张卡得先知道它排第几，才能算出该拉哪一页：

```bash
# 它排第几？（limit 给 1 就行，只要那个下标）
curl 'localhost:PORT/api/cards?limit=1&focus=NATURAL_ORDER_CW_3671'
# → {"total":21386, "focus_index":16067, "items":[...]}

# 拉包含它的那一页
curl 'localhost:PORT/api/cards?limit=175&offset=15925'
```

- `focus_index` 是 **`null`** 表示这张卡**存在，但不满足当前筛选条件**
  （和「卡池里根本没这张卡」是两回事，调用方要分开处理）
- 不传 `focus` 时该字段为 `null`，也不会有额外开销

```bash
# 蓝 + 费用≤3 + 飞行
curl 'localhost:PORT/api/cards?colors=U&cmc_max=3&kw=CHARACTERISTIC_FLYING'

# 神话或稀有 + 白蓝双色
curl 'localhost:PORT/api/cards?colors=W,U&color_mode=exact&rarity=R,M'

# 名字含 dragon 的红色牌
curl 'localhost:PORT/api/cards?q=dragon&colors=R&limit=10'
```

### `GET /api/deckbox/{name}`

牌盒封面。`name` 是牌组 XML 里的 `deck_box_image`（大小写无所谓）。

```bash
curl 'localhost:PORT/api/deckbox/D14_KRUFA' -o box.webp
```

**已经裁好了**（512 的源是 **162×102**，比例 1.59）。原始贴图是 512×512 的
**3D 盒子渲染** —— 四周大量透明留白，左边还有条带鹏洛客符号的盒脊，
直接铺到列表条上会是个飘着的盒子，也没法做左侧溶图渐变。

两步裁切，都在 `paths.py`：

| 常量 | 值 | 说明 |
|---|---|---|
| `RC_DECKBOX_FRONT` | `(203, 92, 162, 256)` | 正面整块（512 基准）。左缘 203 是盒脊右边，下缘 348 是透视底面 |
| `DECKBOX_CROP_TOP` | `0.40` | 再取正面**上面这一截** |

正面整块是竖的（0.63），铺到列表条上只有 26px 宽太窄；截到 40% 变成 1.59，
行高 42px 时有 66px 宽。**改 `DECKBOX_CROP_TOP` 不用手动清缩略图缓存** ——
文件名里带了份额（`box40_<名字>.webp`）。D240 那批源是 256×256，按比例缩成 81×51。

封面索引见下面「牌盒封面索引」。

### `GET /api/card/{key}`

单卡全量：费用、颜色、类别、攻防、稀有度、**规则文本 `text`**、
风味 `flavor`、系列 `set`、画师 `artist`、赛制 `legal`、关键词 `kw` + 中文名 `kw_cn`。

```bash
curl 'localhost:PORT/api/card/_SHIVAN_DRAGON_CW_129730'
```

### `GET /api/art/{key}?size=thumb|full`

插画。`thumb` 走磁盘 webp 缓存（**推荐**，一张约 8KB），`full` 是原图 PNG。

---

## 2. 组卡

卡组是 `projects/<id>.json`。字段对齐 `dotp2014decks/decks_data.py` 的格式，
方便第二阶段直接导出成游戏 WAD。

```json
{
  "id": "oath-of-druids",
  "name_cn": "誓约德鲁伊", "name_en": "Oath of Druids",
  "colors": ["green", "blue"], "cover": "OATH_OF_DRUIDS",
  "main": {"Oath of Druids": 4},
  "min_lands": {"minIsland": 4, "minForest": 2},
  "side": {"Ancient Tomb": 2}
}
```

### 端点

| | |
|---|---|
| `GET /api/decks` | 列表（含每个的 stats） |
| `POST /api/decks` | 新建 `{"name_cn": "...", "name_en": "..."}`，见下面的**撞名规则** |
| `GET /api/decks/{id}` | 读（含 `stats` 和 `cards` 详情）—— **打包向导就靠 `cards` 列出可选的封面/立绘** |
| `PUT /api/decks/{id}` | 整体覆盖 |
| `DELETE /api/decks/{id}` | 删除 |
| `POST /api/decks/{id}/duplicate` | **复制**一份工程，返回 `{deck, stats}` |
| `POST /api/decks/{id}/cards` | **增删改牌（按卡名，不用查 key）** |
| `GET /api/decks/{id}/text` | 导出文本牌表 |
| `POST /api/decks/{id}/import` | 从文本牌表导入 |
| `POST /api/decks/{id}/stats` | 算草稿的统计（不必先保存） |

> [!IMPORTANT] `POST /api/decks` 撞名会**同时改 id、名字和 uid**
> 界面上的「新建」是一键完成（名字默认 `NEW`），连点很容易出好几副同名草稿。
> 撞车时 `new` → `new-2` → `new-3`，**名字和 uid 一起跟着加后缀**
> （`NEW 2` / `DATA_DLC_NEW2`）。只改 id 的话它们 uid 全是 `DATA_DLC_NEW`，
> 列表里几行都叫「NEW」分不出谁是谁，将来打包还会落到**同一个 WAD 文件名**
> `DATA_DLC_NEW.wad` 上互相覆盖。

### `POST /api/decks/{id}/cards` —— agent 最常用的一个

**卡名走归一化**（弯引号/破折号/大小写），**中英文都能认**。
认不出的卡进 `unknown` 返回，**不会静默丢弃**。

```bash
curl -X POST localhost:PORT/api/decks/myd -H 'Content-Type: application/json' -d '{
  "section": "main",
  "add":    {"Oath of Druids": 4, "禁忌果园": 4},
  "remove": {"Ponder": 1},
  "set":    {"Sol Ring": 3},
  "min_lands": {"minIsland": 4, "minForest": 2}
}'
```

返回 `{deck, stats, unknown, issues}`。**`issues` 一定要看** ——
它会指出「牌组用到 G 色却没有 FOREST 下限」这类会让**游戏对局中崩溃**的问题。

### `POST /api/decks/{id}/duplicate` —— 复制

```bash
curl -X POST localhost:PORT/api/decks/世界之树/duplicate -d '{}'
# { "deck": {...}, "stats": {...} }
```

- **不改原工程**，复制出一个新的 `.json`
- id 一直加后缀直到不撞（`x` → `x-2` → `x-3`），名字同理
  （`静默圣所 副本` → `静默圣所 副本 2`）—— 否则连点两次第二份会**覆盖第一份**
- `source` 会写成 `复制自 <原名>`

复制游戏牌组用 `POST /api/gamedecks/{id}/import`（见下）。

### 文本牌表格式

导入导出都用它，故意做得宽松：

```
// 誓约德鲁伊
4 Oath of Druids
2 Emrakul, the Aeons Torn

Sideboard
2 Ancient Tomb

4 Island
```

- 数量写在**前面或后面**都行（`4 X` / `X x4`）
- 注释：`//` `#` `;` `--`
- 分段标题：`Sideboard` / `解锁表` / `备牌` / `Deck` / `主牌`
- 基本地（`Island` / `海岛`）进 `min_lands`，不占主牌名额

---

## 2.5 游戏里已有的牌组

本程序之外还有 100 多副牌组（官方战役、社区包、DLC）。它们**每次从 WAD 实时扫**，
不是本程序的工程文件。

### `GET /api/gamedecks`

按来源分组返回：

```json
{ "total": 128, "groups": [
  { "group": "custom",    "name_cn": "自制",     "count": 6,  "decks": [...] },
  { "group": "system",    "name_cn": "系统自带", "count": 82, "decks": [...] },
  { "group": "community", "name_cn": "社区包",   "count": 40, "decks": [...] }
]}
```

`decks[]` 每项有 `id`（形如 `DATA_DLC_1M_DECK1.wad::D14_1M_OATHOFDRUIDS`）、
`name`、`name_tag`、`wad`、`colors`、`n_card`、`playable`（`never_available` 的
boss 牌组是 `false`）。

### `GET /api/gamedecks/{id}`

含 `cards`（FILENAME → 张数）、`land_config`、以及 `card_details`
（翻好的卡牌详情，界面直接用）。

### `POST /api/gamedecks/{id}/import`

**复制**成一份可编辑的工程（不改原牌组）。返回 `{deck, stats, unmapped, issues}`。

### `GET /api/gamedecks/{id}/writable`

能不能直接改，以及改它的后果 —— 前端据此决定弹不弹警告。

```json
{ "writable": false, "group": "system", "wad": "DATA_DECKS_D14.WAD",
  "reason": "这是「系统自带」里的牌组。直接改会永久覆盖原作者的内容……" }
```

### `POST /api/gamedecks/{id}/save` —— **直接写回游戏 WAD**

```bash
curl -X POST localhost:PORT/api/gamedecks/<id>/save -H 'Content-Type: application/json' -d '{
  "cards": ["Oath of Druids", "Oath of Druids", "Forbidden Orchard"],
  "land_config": {"minIsland": 4, "minForest": 2},
  "allow_system": false
}'
```

- `cards` 是**已展开的卡名列表**，顺序就是 `deckOrderId`。卡名走归一化，中英文都能认
- `allow_system` 默认 `false`。改官方/社区包必须传 `true`（界面会先弹警告）
- 返回 `{wad, backup, size_before, size_after, cards, unknown}`

> [!CAUTION] 这是覆盖，不是另存
> 写之前自动整包备份到 `data/backups/`。内部走 `Wad.rebuild()` 做外科手术
> —— **不能重新打包**，那会丢 `headerXml` 和空目录，游戏会变英文、汉字变方块。
> 写前还会自检 `rebuild({})` 能否字节级还原原文件，不能就中止。

### `GET /api/backups` · `POST /api/backups/restore`

列出／还原备份。`{"name": "DATA_DLC_1M_DECK1.wad.20260924-231722.bak"}`

---

## 2.8 维护与修复

### WAD 备份

| | |
|---|---|
| `GET /api/wads` | 列出游戏目录里的包（名称/大小/时间/是否已备份） |
| `POST /api/wads/backup` | 备份到 `data/wad_backup/`。`{"names": [...]}` 可选，不传就全部。已存在的按大小跳过 |
| `GET /api/wads/backups` | 备份列表 |
| `POST /api/wads/restore` | `{"name": "DATA_CORE.WAD"}` —— 覆盖游戏目录里那份 |

### 检测并修复 `.bsf`（**游戏随机崩溃时可以试试这个**）

引擎解析 `.bsf` 的循环只检查「条目起点」是否越界，不检查数据本身。
末尾多一个 `0x00` 就会让它读出缓冲区外 3 字节，读到的垃圾被当成长度 ——
表现是**约 40% 概率、启动几秒后随机崩溃**。`DATA_DECKS_D910.WAD` 中过这个招。

```bash
# 扫全部 WAD
curl localhost:PORT/api/bsf/scan
# {
#   "bad": [{ "wad": "...", "name": "....bsf", "entries": 1234,
#             "overflow": true, "tail": "00", "tail_len": 1 }],
#   "clean": 90, "wads_scanned": 2, "need_fix": false
# }

# 修一个包
curl -X POST localhost:PORT/api/bsf/fix -H 'Content-Type: application/json' \
     -d '{"wad": "DATA_DECKS_D910.WAD"}'
```

修复走 `Wad.rebuild()` 做外科手术（**不能重新打包** —— 那会丢 `headerXml`
和空目录，游戏变英文、汉字变方块），写前自检、自动备份、写后复验头部完好。

检测逻辑有自测：

```bash
python backend/wadtools.py --selftest
```

它用构造样本验证「干净的不误报 / 多一个 `0x00` 能检出 / `valLen` 写大能检出」——
否则「扫不到问题」到底是真干净还是检测失效，说不清。

### 牌盒封面索引

`data/deckbox_index.json`：牌盒名 → `[包名, 包内路径]`，72 条。

```bash
python tools/build_deckbox.py [--force]
```

> [!IMPORTANT] 为什么必须是全局索引
> 128 副牌里有 **30 副**的封面**不在自己包里** —— TFM、DB07 这些 DLC 的牌组包
> 只有牌表，封面统一放在 `DATA_DLC_TFM_ART.wad` 这类共享美术包里。
> 只查自己包的命中率是 98/128，全局索引才是 **71/71**（71 张封面，多副牌共用一张）。
>
> 另外只收 `TEXTURES/DECKS/` 下的 —— `PLANESWALKERS/` 下有同名 TDX
> （鹏洛客立绘），尺寸不一样，混进来会取错图。

### 其它

| | |
|---|---|
| `POST /api/reindex` | `{"force": false}` 重建索引（默认按 WAD 指纹跳过没变的） |
| `POST /api/settings` | `{"game_dir": "..."}` 改游戏目录 |
| `POST /api/browse-dir` | 弹原生目录选择框（只在 pywebview 窗口下可用） |
| `GET /api/logs` · `GET /api/logtail` | 最近的警告/错误、日志文件尾部 |

---

## 2.9 打包成游戏 WAD

把一份卡组工程做成游戏能加载的卡包。**这是唯一会往游戏目录写新文件的接口。**

### `GET /api/decks/{id}/pack-plan` —— 打包前体检（不写文件）

返回 `{ok, problems, info, cover, avatar, uid, uid_num, filename}`。

```bash
curl 'localhost:PORT/api/decks/誓约德鲁伊/pack-plan?cover=SHIVAN_DRAGON_CW_129730'
```

```json
{ "ok": true, "problems": [],
  "info": { "n_main": 54, "n_basic": 6, "total": 60,
            "colors": ["blue","green"], "basics": ["ISLAND","FOREST"],
            "n_unlock": 0, "target": 60 },
  "uid": "DATA_DLC_1M_DECK1", "uid_num": 6407, "filename": "DATA_DLC_1M_DECK1.wad" }
```

`problems[]` 每项 `{level: "error"|"warn", msg}`。**只有 error 会挡住打包**：

| 检查 | 级别 | 说明 |
|---|---|---|
| `<CARD>` 条数 + `sum(min_lands)` **> 60** | error | `min*` 是引擎补地的下限，超了塞不进 60 张 |
| 卡名解析不出 `<FILENAME>` | error | 卡池里没有这张卡 |
| 主牌为空 | error | 没什么可打的 |
| 牌表不足 60 **且**缺 `min*` | warn | 引擎自己按曲线配地；想控制配比才要写 |
| 解锁表 > 30 条 | warn | 多出来的会被游戏丢掉 |

**不满 60 张本身不提示也不拦** —— 引擎会把差的地补满，那是它的正常工作方式
（官方 `D14_SLIVERS` = 59 张 + 补 1 张）。

> [!NOTE] 这个接口**不会占用** `uid_num`
> 它用 `peek_uid_num()`，只看一眼。真正打包才 `next_uid_num()` 登记。
> 否则每开一次向导就烧掉一个编号。

### `POST /api/decks/{id}/pack` —— 打包 + 装入

```bash
curl -X POST localhost:PORT/api/decks/誓约德鲁伊/pack \
     -H 'Content-Type: application/json' \
     -d '{"cover": "Shivan Dragon", "avatar": "Oath of Druids", "install": true}'
```

| 字段 | 说明 |
|---|---|
| `cover` | **卡池 key 或中英文卡名**。必填 |
| `avatar` | 同上。不给就跟 `cover` |
| `install` | 默认 `true`：装进游戏目录。`false` 只出到 `out/` |
| `overwrite` | 默认 `false`：游戏目录已有同名包时**先备份**到 `data/backups/` |

成功后 `cover` / `avatar` 会**写回工程文件**，下次打开还是这两张。

**包名和文件名都由卡组名决定**：卡组名转拼音（`pypinyin`）→
`DATA_DLC_<拼音>`，WAD 文件名 = `<包名>.wad`，包内 XML / 贴图前缀也用同一段。
工程里存了 `uid` 就以它为准（可手改）。

```json
{ "uid": "DATA_DLC_1M_DECK1", "uid_num": 6407,
  "cover_name": "D14_1M_DECK1_BOX", "pw_name": "D14_1M_DECK1_PW",
  "filename": "DATA_DLC_1M_DECK1.wad", "entries": 11, "bytes": 920632,
  "installed": "E:\\game\\Magic 2014\\DATA_DLC_1M_DECK1.wad",
  "out_path": "...\\out\\DATA_DLC_1M_DECK1.wad",
  "backup": null }
```

打包不合格会抛 `PackError`，`error.msg` 直接给用户看。

> [!IMPORTANT] 装进去之后要手动保存一次
> 进游戏 → 牌组编辑器 → 找到这副牌 → **保存一次**才算可用。

### `GET /api/preview/cover?key=&zoom=` —— 封面预览

```bash
curl 'localhost:PORT/api/preview/cover?key=Shivan%20Dragon&zoom=1.2' -o cover.png
```

**和打包时走同一条 `artgen.make_cover()`**，所见即所得。`zoom` 0.2~3.0，默认 1.0。

### `GET /api/preview/portrait?key=&kind=` —— 立绘预览

`kind` 取 `avatar`（256×256 圆形）/ `locked`（自动压暗去饱和）/
`backplate`（256×512）/ `full`（1024×1024）。

```bash
curl 'localhost:PORT/api/preview/portrait?key=Shivan%20Dragon&kind=full' -o pw.png
```

### 命令行 / Python

```python
import cardset, packer
cs = cardset.get()
meta = packer.pack(deck_dict, cs, cover_key, avatar_key, install=True)
print(meta["installed"], meta["bytes"])

p = packer.plan(deck_dict, cs)      # 只体检
print(p["ok"], p["problems"])
```

自检（不碰游戏目录）：

```bash
python backend/artgen.py     # 封面/立绘尺寸 + TDX 头部
python backend/packer.py     # XML 生成 + 建包 + magic
```

---

## 3. 卡面生成

后端用 PIL 把卡面渲染成 PNG —— **不依赖浏览器**。
坐标和前端 DOM 合成用的是同一套（基准 356×512）。

### `GET /api/render/{name}`

`name` 可以是卡池 key，**也可以是中英文卡名**。

```bash
curl 'localhost:PORT/api/render/Shivan%20Dragon?width=744' -o shivan.png
curl 'localhost:PORT/api/render/西瓦巨龙?width=400&fmt=webp' -o shivan.webp
```

`width` 任意（80~1600），高度按比例。卡框会**按颜色和类别自动选**
（单色/双色/神器/无色/地），费用符号、系列稀有度符号、力防框一并画上。

### `POST /api/render-sheet`

多张拼一张联系表：

```bash
curl -X POST localhost:PORT/api/render-sheet -H 'Content-Type: application/json' \
     -d '{"cards": ["Sol Ring", "Black Lotus"], "cols": 4, "width": 240}'
```

返回 `{png_base64, width, height, count}`。

### Python

```python
import cardset, render
cs = cardset.get()
render.render_card(cs.key_of("Shivan Dragon"), width=744).save("out.png")
render.render_sheet(["Sol Ring", "Black Lotus"], cols=2).save("sheet.png")
```

---

## 4. 命令行

```bash
python cli.py doctor                    # 自检：路径 / 索引 / 最近错误
python cli.py meta                      # 可筛的取值域
python cli.py search "dragon" --color R --cmc-max 4 --limit 10
python cli.py search --rarity M --kw CHARACTERISTIC_FLYING
python cli.py card "Sol Ring"

python cli.py deck list
python cli.py deck new "我的卡组"
python cli.py deck add  <id> "Oath of Druids" 4 "禁忌果园" 4
python cli.py deck rm   <id> "Ponder" 1
python cli.py deck set  <id> "Sol Ring" 3
python cli.py deck show <id>
python cli.py deck import <id> --file list.txt --mode replace
python cli.py deck export <id> --out list.txt

python cli.py render "Shivan Dragon" -o shivan.png --width 744
python cli.py sheet "Sol Ring" "Black Lotus" --cols 4 -o sheet.png
python cli.py rebuild [--force]
```

**`--json` 放在子命令之前**，输出机器可解析的 JSON：

```bash
python cli.py --json search "dragon" --limit 5
python cli.py --json deck new "测试" | python -c "import sys,json; print(json.load(sys.stdin)['id'])"
```

---

## 5. 错误处理

HTTP 端点统一返回：

```json
{ "ok": false, "ms": 12,
  "error": { "type": "KeyError", "msg": "…", "where": "card",
             "hint": "取不到某个字段 —— 索引可能是旧版本建的，跑 tools/rebuild.py --force",
             "trace": "…" } }
```

只读端点**用 200 + `ok:false`** 返回（前端好统一处理）；渲染和资源端点用真实状态码
（404 / 500）。

所有异常都写进 `data/logs/app.log`（5MB × 3 轮转），并进内存环形缓冲：

```bash
GET /api/logs?limit=100     # 最近的警告/错误，带 trace
GET /api/logtail?lines=200  # 日志文件尾部
python cli.py doctor        # 顺带打印最近 10 条
```

界面右上角「诊断」按钮读的就是这个 —— 用户截图即可报障。

---

## 6. 需要注意的坑

| 坑 | 说明 |
|---|---|
| **索引会过期** | 装了新 WAD 后索引要重建。指纹会自动检测，`/api/health` 会报 |
| **`<CARD>` 条数决定地数** | 引擎的规矩是「`<CARD>` 条数 + 补的地 = 60」。`min_lands` 只是**下限**。所以牌表只放 10 条，引擎会补 50 张地 |
| **每个用到的颜色都要有基本地下限** | 否则**对局中崩溃**（社区 wiki 明列）。`validate()` / `issues` 会查这条 |
| **卡名不是 key** | 卡组的 `main` 存的是**英文卡名**，卡池 key 是 `<FILENAME>`。HTTP/CLI 会自动转换 |
| **双面牌** | 背面没有 `<CASTING_COST>`，渲染出来费用是空的，属正常 |
| **关键词只有 30 种** | `CHARACTERISTIC_*` 只覆盖特征性异能。其余异能要用 `q=` 搜规则文本 |
