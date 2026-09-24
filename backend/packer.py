# -*- coding: utf-8 -*-
"""把一份卡组工程打包成游戏能加载的 WAD。

搬运自 `dotp2014decks/build.py`，数据源从 `pool.json` 换成 builder 的 `cardset`。

一个包产出这些条目
------------------
    <UID>/DATA_ALL_PLATFORMS/DECKS/<名>.XML                牌组定义（<CARD> + LandConfig 补地）
    <UID>/DATA_ALL_PLATFORMS/DECKS/<名>_LAND_POOL.XML      可用基本地（引擎补地时从这里挑）
    <UID>/DATA_ALL_PLATFORMS/UNLOCKS/<名>_UNLOCKS.XML      解锁表（打赢对局解锁的 30 张）
    <UID>/DATA_ALL_PLATFORMS/TEXT_PERMANENT/<名>_TEXT.XML  牌组名/描述（多语言表）
    <UID>/DATA_ALL_PLATFORMS/AI_PERSONALITIES/<名>.XML     鹏洛客人格（指向立绘）
    <UID>/DATA_ALL_PLATFORMS/ART_ASSETS/TEXTURES/DECKS/<封面>.TDX        牌盒封面
    <UID>/DATA_ALL_PLATFORMS/ART_ASSETS/TEXTURES/PLANESWALKERS/<立绘>*.TDX  4 张立绘
    HEADER.XML                                             容器头

**包只装牌组定义，不装卡。** `<CARD name="...">` 写的是卡的 `<FILENAME>`，
运行时由引擎去当前载入的内容包里找 —— 所以包不硬绑定任何卡包。

引擎凑 60 的规矩（扫全盘 128 副牌组统计出来的，见 `dotp2014decks/readme.md`）
--------------------------------------------------------------------------
    `<CARD>` 条数 + `<LandConfig>` 里 `min<基本地>` 补的地 = 60

基本地由引擎按下限从 `_LAND_POOL.XML` 里挑（还会按法术力曲线微调），
所以 `<CARD>` 里**不写基本地** —— 这是官方玩家牌组的标准写法。
`plan()` 会在打包前把这条不满足的情况拦下来。
"""

import os
import re
import glob
import time
import shutil

import paths
import settings
import artgen
import fsx
import wadbuild
from log import get_logger

log = get_logger("packer")

CONTENT_PACK = 1000     # 蹭游戏自带、已验证可用的内容包（Data_DLC_1000_Content_Pack_Enabler.wad）
TOTAL_TARGET = 60       # <CARD> 条数 + 补的地，必须正好 60
UNLOCK_SLOTS = 30       # 游戏内每个牌组的解锁槽固定 30 个

COLORS = ["white", "blue", "black", "red", "green"]
LANDCONFIG_ORDER = ["minPlains", "minIsland", "minSwamp", "minMountain", "minForest"]

# 颜色单字母 -> (小写英文名, 基本地)
COLOR_INFO = {
    "W": ("white", "PLAINS"), "U": ("blue", "ISLAND"), "B": ("black", "SWAMP"),
    "R": ("red", "MOUNTAIN"), "G": ("green", "FOREST"),
}

# 可用基本地池 —— **必须给全每种颜色的全部美术编号**，引擎会按法术力曲线在里面挑。
# 少了任何一种用到的颜色 → 进对局时崩溃（社区 wiki「Frequent Modding Mistakes」）。
# 编号直接抄游戏自带的 LAND_POOL。
LAND_ARTS = {
    "ISLAND": ["357811", "357812", "357813", "357814", "357815", "357816",
               "357819", "357935", "357939", "369824", "369825"],
    "PLAINS": ["357846", "357849", "357850", "357851", "357853", "357855",
               "357856", "357981", "357982", "357983", "369832", "369833"],
    "SWAMP": ["357821", "357823", "357825", "357826", "357828", "357830",
              "357831", "357832", "357944", "357946", "357948", "369837", "369838"],
    "MOUNTAIN": ["357797", "357798", "357800", "357802", "357803", "357806",
                 "357947", "357954", "357956", "357960", "369827", "369830"],
    "FOREST": ["357833", "357834", "357835", "357836", "357837", "357838",
               "357839", "357841", "357844", "357964", "357968", "357973",
               "369819", "369820"],
}


class PackError(Exception):
    """打包前的检查没过。`msg` 直接给用户看。"""


# ---------------------------------------------------------------- 小工具

def xmlish(s):
    """统一 **CRLF + UTF-8 BOM**。

    原版和 Deck Builder 产出的 XML 都是 CRLF（`DATA_DLC_TFM_D*` 全 4 个都带 BOM）。
    例外是 `HEADER.XML`：原版那个是 CRLF 但**没有 BOM**，别一律加。
    """
    if isinstance(s, bytes):
        return s
    s = s.replace("\r\n", "\n").replace("\n", "\r\n")
    return b"\xef\xbb\xbf" + s.encode("utf-8")


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def safe_uid_part(s):
    """包名/贴图名只留大写字母数字 —— 游戏按名字查资源，带符号会找不到。"""
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _is_ascii(s):
    try:
        (s or "").encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _looks_internal(s):
    """看着像内部标识（`DATA_DLC_1M_DECK3` / `D14_SLIVERS`）而不是给人看的名字。"""
    u = (s or "").upper()
    return u.startswith(("DATA_", "DATA ", "D14_", "1M_")) or u.startswith("DATA_DLC")


def display_name(d):
    """**游戏里显示的牌组名 —— 必须是 ASCII。**

    游戏那一栏的字体**没有中文字形**，写中文会渲染成一排 `[····]`（真见过）。
    官方牌组名也全是英文（`Sliver Hive`）。

    优先级：`name_en` → `name_cn` → 拼音。前两个都要是 ASCII 且不像内部标识。
    """
    for c in (d.get("name_en"), d.get("name_cn")):
        c = (c or "").strip()
        if c and _is_ascii(c) and not _looks_internal(c):
            return c
    return _slug(d)


def _slug(d):
    """卡组 -> 纯 ASCII 大写标识（中文转拼音）。见 `deck.slug_ascii()`。

    这是**包名 / XML 文件名 / 贴图名前缀**共用的那一段
    （`DATA_DLC_<SLUG>` / `<SLUG>.XML` / `<SLUG>_BOX`）。
    """
    import deck as deckmod
    return deckmod.slug_ascii(d.get("name_cn") or d.get("name_en"))


def expand(cs, cards, limit=None):
    """`{卡名: 张数}` -> **一张一行**的 key 列表。

    照抄社区 Deck Builder 的 `Deck.cs CreateCardTags` —— 它**不用**官方牌组里
    那种 `SHOCK_348966@2` 合并写法。对齐它，少一个变量。
    """
    out = []
    for name, n in sorted(cards.items(), key=lambda kv: -kv[1]):
        key = cs.key_of(name)
        if not key:
            raise PackError("卡池里找不到这张卡：%s" % name)
        out.extend([key] * n)
    return out[:limit] if limit else out


def deck_colors(cs, main):
    """从**实际卡牌**推颜色，不信 `deck["colors"]` 那个字段。

    新建的卡组那个字段是空的，而且用户可能手改过。用真实卡牌推最稳。
    返回 `(单字母集合, 小写英文名列表)`。
    """
    need = set()
    for name in main:
        r = cs.cards.get(cs.key_of(name))
        if r:
            need.update(r.get("colors") or [])
    need &= set(COLOR_INFO)
    names = [c for c in COLORS if any(COLOR_INFO[k][0] == c for k in need)]
    return need, names


def basics_needed(cs, main):
    """牌组用到的颜色 -> 需要哪些基本地（按 LANDCONFIG_ORDER 的顺序）。"""
    need, _ = deck_colors(cs, main)
    want = {COLOR_INFO[k][1] for k in need}
    return [b for b in ("PLAINS", "ISLAND", "SWAMP", "MOUNTAIN", "FOREST")
            if b in want]


def is_land(cs, name):
    key = cs.key_of(name)
    r = cs.cards.get(key) if key else None
    return bool(r and "LAND" in (r.get("type") or "").upper())


# ---------------------------------------------------------------- XML 生成

def deck_xml(d, cs, uid_num, cover_name, pw_name, min_lands):
    """牌组定义。`<CARD>` 只列具体卡牌，基本地交给 `<LandConfig>` 让引擎补。"""
    keys = expand(cs, d["main"])
    lines = ['  <CARD name="%s" deckOrderId="%d" />' % (k, i)
             for i, k in enumerate(keys)]

    _, color_names = deck_colors(cs, d["main"])
    cols = "".join(' is_%s="true"' % c for c in COLORS if c in color_names)
    lc = " ".join('%s="%d"' % (k, min_lands[k]) for k in LANDCONFIG_ORDER
                  if min_lands.get(k))
    # ⚠️ `name_tag` 必须等于**牌组 XML 的文件名**（不含扩展名），不是包名。
    #
    # 实测官方：`DECKS/D14_SLIVERS.XML` + `name_tag="D14_SLIVERS"`
    # + TEXT 表里 `Ident="D14_SLIVERS"` —— 三处同一个字符串，游戏才查得到中文名。
    # 早先这里用的是包名（`DATA_DLC_1M_DECK3`），和文件名对不上，
    # 游戏查不到就退回显示**文件名**——用户在游戏里看到 `D14_1M_THRONEOFTHEDEEP`
    # 就是这么来的。
    tag = deck_tag(d)
    return (
        # `deck_box_image_locked` 指向**游戏自带的** D14_LOCKED.TDX（在 DATA_DECKS_D14
        # 包里），不是我们生成的 —— 别写成 `<封面名>_locked`，那张图根本不存在。
        '<DECK personality="%s.XML" deck_box_image="%s" '
        'deck_box_image_locked="D14_locked" cheat_menu_filter_deck_type="Standard" '
        'cheat_menu_filter_datapool="D14" content_pack="%d" always_available="true" '
        'uid="%d" tus_save_data_id="%d" steam_id_1="213850" steam_id_2="213850" '
        'ios_id_1="D14_DECK_UNLOCK_1" ios_id_2="D14_DECK_FOIL_1" '
        'android_id_1="d14_deck_unlock_01" android_id_2="d14_deck_foil_01" '
        'name_tag="%s" description_tag="%s"%s>\n'
        '  <DECKSTATISTICS Size="8" Speed="6" Flex="4" Syn="9" />\n'
        '  <LandConfig ignoreCmcOver="0" %s />\n'
        '%s\n'
        '</DECK>\n'
    ) % (pw_name, cover_name, CONTENT_PACK, uid_num, uid_num,
         tag, tag + "_DESC", cols, lc, "\n".join(lines))


def landpool_xml(cs, main, uid_num):
    """可用基本地池。缺色会让引擎补不出地、进对局崩溃。"""
    lines = []
    oid = 0
    for basic in basics_needed(cs, main):
        for art in LAND_ARTS[basic]:
            lines.append('  <CARD name="%s_%s" deckOrderId="%d" />'
                         % (basic, art, oid))
            oid += 1
    return (
        '<DECK personality="" deck_box_image="" deck_box_image_locked="locked" '
        'content_pack="%d" never_available="true" cheat_menu_filter_deck_type="Utility" '
        'cheat_menu_filter_datapool="D14" uid="%d" tus_save_data_id="%d">\n'
        '%s\n</DECK>\n'
    ) % (CONTENT_PACK, uid_num + 100000, uid_num + 100000, "\n".join(lines))


def unlocks_xml(cs, side, uid_num, start_oid):
    """解锁表：**正好 30 条、每条 quantity 恒 1**。

    Deck Builder 源码原话：`quantity` 写 >1 会让游戏出问题，所以每种牌展开成独立一行。
    """
    keys = expand(cs, side, limit=UNLOCK_SLOTS)
    lines = ['  <CARD name="%s" deckOrderId="%d" unlockOrderId="%d" quantity="1" />'
             % (k, start_oid + i, i) for i, k in enumerate(keys)]
    return (
        '<UNLOCKS uid="%d" deck_uid="%d" content_pack="0" game_mode="0">\n'
        '%s\n</UNLOCKS>\n'
    ) % (uid_num + 200000, uid_num, "\n".join(lines))


def text_xml(d, pw_tag):
    """TEXT_PERMANENT —— Excel 风格多语言表。

    **中文写进所有语言列**：汉化版游戏读 en-US 那一列，只填 Master Text 不够。
    """
    # ⚠️ **必须 ASCII** —— 这一栏的字体没有中文字形，中文会变成 `[····]`。
    # 详见 `display_name()`。
    name = "[%s]" % display_name(d)
    guide = d.get("guide") or {}
    raw = (guide.get("特色") or guide.get("description") or "").replace("**", "")
    desc = "（%s）%s" % (d.get("name_en") or "", raw[:60]) if d.get("name_en") else raw[:60]
    cols = ["Ident", "Comment", "Master Text", "French", "Spanish", "German",
            "Italian", "", "Japanese", "Korean", "Russian", "Portuguese (Brazil)",
            "", "Chinese Simplified", "Chinese Traditional"]

    def row(vals):
        cells = []
        for i, v in enumerate(vals):
            if v == "":
                continue
            idx = ' ss:Index="%d"' % (i + 1) if i > 0 else ""
            cells.append('        <Cell%s>\n          <Data ss:Type="String">%s</Data>\n'
                         '        </Cell>' % (idx, v))
        return "      <Row>\n" + "\n".join(cells) + "\n      </Row>"

    # Ident 用**牌组 XML 的文件名**，和 `name_tag` / `description_tag` 对齐。
    # 用包名的话游戏查不到，会退回显示文件名（见 `deck_xml()` 里的说明）。
    uid = deck_tag(d)
    rows = [row(cols)]
    rows.append(row([_esc(uid)] + [""] + [_esc(name)] * 12))
    rows.append(row([_esc(uid + "_DESC"), ""] + [_esc(desc)] * 12))
    # 鹏洛客的名字 —— personality XML 里 PLANESWALKER_NAME_TAG 指的就是它，
    # 不定义的话大厅里会显示成一串内部标识
    rows.append(row([_esc(pw_tag), ""] + [_esc(name)] * 12))
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<?mso-application progid="Excel.Sheet"?>\n'
        '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" '
        'xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">\n'
        '  <DocumentProperties xmlns="urn:schemas-microsoft-com:office:office">\n'
        '    <Author>1m</Author>\n'
        '    <Created>2014-01-01T00:00:00Z</Created>\n'
        '    <Company></Company>\n'
        '  </DocumentProperties>\n'
        '  <Worksheet ss:Name="Sheet4">\n    <Table>\n%s\n    </Table>\n'
        '  </Worksheet>\n</Workbook>\n' % "\n".join(rows)
    )


def personality_xml(pw_name, pw_tag):
    """鹏洛客人格 —— 7 个字段，指向 4 张贴图。牌组 XML 的 `personality=` 就指它。"""
    return (
        '<CONFIG>\n'
        '  <PLANESWALKER_NAME_TAG string="%s" />\n'
        '  <LARGE_AVATAR_IMAGE string="%s_full" />\n'
        '  <MEDIUM_AVATAR_IMAGE string="%s" />\n'
        '  <SMALL_AVATAR_IMAGE string="%s" />\n'
        '  <SMALL_AVATAR_IMAGE_LOCKED string="%s_locked" />\n'
        '  <LOBBY_IMAGE string="%s_Backplate" />\n'
        '  <MUSIC string="" />\n'
        '</CONFIG>\n'
    ) % (pw_tag, pw_name, pw_name, pw_name, pw_name, pw_name)


def header_xml(uid):
    """容器头。**CRLF 但无 BOM** —— 原版的这个和 DATA_ALL_PLATFORMS 里那几个不一样。"""
    return ('<?xml version="1.0"?>\n<WAD_HEADER>\n'
            '  <ENTRY platform="ALL" source="%s/DATA_ALL_PLATFORMS/" '
            'alias="Content" order="3" />\n</WAD_HEADER>\n' % uid
            ).replace("\n", "\r\n").encode("utf-8")


# ---------------------------------------------------------------- 命名

def uid_name(d):
    """包名（容器根名 + 字符串表偏移 0 + 头里的 source，三处必须一致）。

    这就是 **WAD 文件名**（`<uid>.wad`）。存在工程里的 `uid` 优先，
    没有才按卡组名的拼音现推 —— 保证「包名 = 文件名 = 游戏里看到的名字源头」。
    """
    # ⚠️ 这里**不能用 `safe_uid_part`** —— 它把下划线也当非法字符抹掉，
    # `DATA_DLC_1M_DECK3` 会被毁成 `DATADLC1MDECK3`（真踩过）。
    # 包名允许下划线，只挡中文和标点。
    u = re.sub(r"[^A-Za-z0-9_]", "", (d.get("uid") or "")).upper()
    return u or ("DATA_DLC_" + _slug(d))


def deck_tag(d):
    """牌组 XML / 贴图共用的短名 = 卡组名的拼音。

    这个字符串会出现在 `DECKS/<tag>.XML` 和各张贴图名里。
    早先是 `D14_1M_<name_en 去符号>`，中文名被抹光后剩下
    `D14_1M_THRONEOFTHEDEEP` 这种 —— 用户在游戏里看到的就是它。
    """
    return _slug(d)


# ---------------------------------------------------------------- uid 编号

_uid_cache = {}     # game_dir -> set(已占用编号)


def used_uids(game_dir=None):
    """扫游戏目录里所有 WAD 的 DECKS/UNLOCKS XML，收集已占用的编号。

    一个包占用 **三个** 编号（`uid_num` / `+100000` / `+200000`），
    所以必须把三种属性都收进来，否则会撞。

    `AI_PERSONALITIES` 里没有数字 uid，不用扫。
    """
    # 走 settings 而不是 paths.GAME —— 后者只在 `settings.load()` 跑过之后才有值，
    # 没读过配置时是空串，打包会去找「当前目录下的 WAD」。
    gd = game_dir or settings.game_dir()
    if gd in _uid_cache:
        return _uid_cache[gd]
    from wadlite import WadPool
    pool = WadPool(gd, limit=4)
    used = set()
    pat = re.compile(r'(?:uid|tus_save_data_id|deck_uid)="(\d+)"')
    names = sorted(n for n in os.listdir(gd) if n.lower().endswith(".wad")) \
        if os.path.isdir(gd) else []
    for n in names:
        w = pool.get(n)
        if w is None:
            continue
        for f in w.files:
            up = f.path.replace("\\", "/").upper()
            if not up.endswith(".XML"):
                continue
            if "/DECKS/" not in up and "/UNLOCKS/" not in up:
                continue
            try:
                raw = w.read(f)
            except Exception:
                continue
            for m in pat.finditer(raw.decode("cp1252", "replace")):
                used.add(int(m.group(1)))
    _uid_cache[gd] = used
    log.info("已占用牌组编号 %d 个", len(used))
    return used


def reserve_uid_num(uid_num, game_dir=None):
    """把一个编号标记成已占用。

    **必须调** —— `used_uids()` 是建一次就缓存的，不登记的话
    连着打包两副牌会分到同一个编号。
    """
    used_uids(game_dir).update((uid_num, uid_num + 100000, uid_num + 200000))


def forget_uids(game_dir=None):
    """游戏目录变了、或用户自己装了新包之后调它，下次重新扫。"""
    # 走 settings 而不是 paths.GAME —— 后者只在 `settings.load()` 跑过之后才有值，
    # 没读过配置时是空串，打包会去找「当前目录下的 WAD」。
    gd = game_dir or settings.game_dir()
    _uid_cache.pop(gd, None)


def _find_uid_num(game_dir=None, start=6401):
    used = used_uids(game_dir)
    base = start
    while base in used or (base + 100000) in used or (base + 200000) in used:
        base += 1
        if base > start + 5000:
            raise PackError("找不到可用的牌组编号（从头 6401 往后找了 5000 个都被占了）")
    return base


def peek_uid_num(game_dir=None, start=6401):
    """**只看一眼**下一个可用编号，不登记。

    界面预览用这个 —— 用 `next_uid_num` 的话，用户每打开一次向导就烧掉一个编号。
    """
    return _find_uid_num(game_dir, start)


def next_uid_num(game_dir=None, start=6401):
    """找一个可用编号并**登记**，保证连着调两次拿到两个不同编号。"""
    base = _find_uid_num(game_dir, start)
    reserve_uid_num(base, game_dir)
    return base


# ---------------------------------------------------------------- 打包前检查

def plan(d, cs):
    """打包前把该检查的都检查了。返回一份给人看的清单。

    `{"ok": bool, "problems": [{level,msg}], "info": {...}}`

    最要命的一条是 **`<CARD>` 条数 + 补的地必须正好 60** ——
    引擎按这个数凑牌组，对不上轻则牌数不对，重则进对局崩溃。
    """
    problems = []
    main = d.get("main") or {}
    side = d.get("side") or {}
    min_lands = d.get("min_lands") or {}

    if not main:
        problems.append({"level": "error", "msg": "主牌是空的，没什么可打包的"})

    # 卡名能不能解析成 FILENAME
    missing = [n for n in list(main) + list(side) if not cs.key_of(n)]
    if missing:
        problems.append({"level": "error",
                         "msg": "卡池里找不到这 %d 张卡：%s"
                                % (len(missing), "、".join(missing[:6])),
                         "cards": missing})

    n_main = sum(main.values())
    n_basic = sum(min_lands.values())
    total = n_main + n_basic

    # 引擎的规矩是「`<CARD>` 条数 + 补的地 **凑满** 60」—— 不是「必须正好 60」。
    #
    # 所以 `n_main + min*` **小于** 60 是**正常**的：引擎会把差的那几张地补上。
    # 官方 `D14_SLIVERS` 就是 59 张 + 引擎补 1 张。早先按 `!= 60` 一刀切，
    # 把官方牌组的副本全拦死了。
    # 只有**超过** 60 才是真问题：`min*` 是下限，引擎塞不进 60 张里。
    # **不满 60 不提示、不拦截** —— 引擎会把差的地补满，这是它的正常行为
    # （官方 `D14_SLIVERS` 就是 59 张 + 补 1 张）。只有当牌表本身为空时才值得说一句。
    if total > TOTAL_TARGET:
        problems.append({
            "level": "error",
            "msg": "牌表 %d 条 + 基本地下限 %d 张 = %d 张，超过 %d 张了。"
                   "`min*` 是引擎补地的下限，塞不进 60 张里 —— 减掉 %d 张再打"
                   % (n_main, n_basic, total, TOTAL_TARGET, total - TOTAL_TARGET),
        })

    # 基本地下限（`min*`）—— 只是**提醒**，不是拦。
    #
    # 真正会崩的是「`_LAND_POOL` 里缺某个颜色的基本地」，而那个池子是
    # `landpool_xml()` 按牌组实际用色**自动生成**的，永远齐全，不用用户操心。
    #
    # `min*` 是「引擎补地时每种至少给几张」的下限。官方牌组**很多压根不写
    # `<LandConfig>`**（`D14_SLIVERS` 就是 40 条 `<CARD>` + 引擎补 20），
    # 引擎自己按法术力曲线配 —— 跑得好好的。
    # 早先抄 dotp2014decks 把这条当 error，会把官方牌组的副本全拦死。
    need, color_names = deck_colors(cs, main)
    want = {COLOR_INFO[k][1] for k in need}
    declared = {k.replace("min", "").upper() for k in min_lands}
    lack = want - declared
    if lack and n_main < TOTAL_TARGET:
        problems.append({
            "level": "warn",
            "msg": "牌组用到 %s 色，但没写基本地下限（%s）。引擎会自己按曲线配地 —— "
                   "想控制配比的话，给牌组加上 min* 更稳"
                   % ("".join(sorted(need)), "、".join(sorted(lack))),
        })

    if len(side) > 0 and sum(side.values()) > UNLOCK_SLOTS:
        problems.append({
            "level": "warn",
            "msg": "解锁表有 %d 张，游戏只有 %d 个槽，多出来的会被丢掉"
                   % (sum(side.values()), UNLOCK_SLOTS),
        })

    return {
        "ok": not any(p["level"] == "error" for p in problems),
        "problems": problems,
        "info": {
            "n_main": n_main, "n_basic": n_basic, "total": total,
            "n_unlock": sum(side.values()),
            "colors": color_names,
            "basics": basics_needed(cs, main),
            "target": TOTAL_TARGET,
        },
    }


# ---------------------------------------------------------------- 打包

def build(d, cs, cover_key, avatar_key, uid_num=None, game_dir=None):
    """生成整包字节。返回 `(data, meta)`；`meta` 给界面显示用。"""
    if not cover_key:
        raise PackError("还没选封面")
    avatar_key = avatar_key or cover_key

    ac = _artcache()
    cover_art = ac.image(cover_key, size="full")
    if cover_art is None:
        raise PackError("封面这张卡没有插画：%s" % cover_key)
    avatar_art = ac.image(avatar_key, size="full")
    if avatar_art is None:
        raise PackError("立绘这张卡没有插画：%s" % avatar_key)

    uid = uid_name(d)
    tag = deck_tag(d)
    cover_name = tag + "_BOX"
    pw_name = tag + "_PW"
    pw_tag = uid + "_PW_NAME"
    uid_num = uid_num if uid_num is not None else next_uid_num(game_dir)
    min_lands = d.get("min_lands") or {}

    entries = [
        ("DATA_ALL_PLATFORMS/DECKS/%s.XML" % tag,
         xmlish(deck_xml(d, cs, uid_num, cover_name, pw_name, min_lands))),
        # 引擎靠它补基本地，缺了会在对局中崩溃
        ("DATA_ALL_PLATFORMS/DECKS/%s_LAND_POOL.XML" % tag,
         xmlish(landpool_xml(cs, d.get("main") or {}, uid_num))),
        ("DATA_ALL_PLATFORMS/UNLOCKS/%s_UNLOCKS.XML" % tag,
         xmlish(unlocks_xml(cs, d.get("side") or {}, uid_num,
                            sum((d.get("main") or {}).values())))),
        ("DATA_ALL_PLATFORMS/TEXT_PERMANENT/%s_TEXT.XML" % tag,
         xmlish(text_xml(d, pw_tag))),
        ("DATA_ALL_PLATFORMS/AI_PERSONALITIES/%s.XML" % pw_name,
         xmlish(personality_xml(pw_name, pw_tag))),
        ("DATA_ALL_PLATFORMS/ART_ASSETS/TEXTURES/DECKS/%s.TDX" % cover_name,
         artgen.encode_cover(artgen.make_cover(cover_art))),
        ("HEADER.XML", header_xml(uid)),
    ]
    for fname, data in artgen.encode_portraits(avatar_art, pw_name).items():
        entries.append(
            ("DATA_ALL_PLATFORMS/ART_ASSETS/TEXTURES/PLANESWALKERS/%s" % fname, data))

    # ⚠️ **必须传 `header_xml`** —— 容器头里那段 `<WAD_HEADER>` 是内容包声明，
    # 少了它游戏**直接忽略整个包**（不出错、不给提示，就是"没读到"）。
    # 真踩过：调 `build()` 时只给了 root_name，打出来的包 headerXml 是 0 字节。
    # `wadbuild.write()` 内部就是 `build(entries, make_header_xml(uid), root_name=uid)`，
    # 这里要保持一致。
    data = wadbuild.build(entries, header_xml=wadbuild.make_header_xml(uid),
                          root_name=uid, compress=True)
    _verify(data, uid, len(entries))
    return data, {
        "uid": uid, "uid_num": uid_num, "tag": tag,
        "cover_name": cover_name, "pw_name": pw_name,
        "filename": uid + ".wad",
        "entries": len(entries),
        "bytes": len(data),
    }


def _verify(data, uid, n_entries):
    """把刚打出来的字节**读回来**核一遍。不过就抛，别把废包装进游戏。

    为什么非查不可：容器头里那段 `<WAD_HEADER>` 是内容包声明，
    **少了它游戏会静默忽略整个包** —— 不报错、不提示，就是"游戏里没读到"。
    光看生成代码很难发现，读回来比一次就露馅了。
    """
    import io as _io
    from wadtool import Wad
    tmp = os.path.join(paths.OUTDIR, "_verify.tmp.wad")
    os.makedirs(paths.OUTDIR, exist_ok=True)
    with open(tmp, "wb") as f:
        f.write(data)
    try:
        w = Wad(tmp)
        if not w.header_xml:
            raise PackError(
                "打出来的包**没有容器头**（headerXml 是空的）—— 游戏会直接忽略它。"
                "多半是 `wadbuild.build()` 漏了 header_xml 参数")
        if uid.encode() not in w.header_xml:
            raise PackError("容器头里没有包名 %s" % uid)
        if len(w.files) != n_entries:
            raise PackError("条目数对不上：写了 %d 个，读回来 %d 个"
                            % (n_entries, len(w.files)))
        root = w.files[0].path.split("/")[0] if w.files else ""
        if root != uid:
            raise PackError("根目录名是 %s，应该是 %s" % (root, uid))
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


_AC = None


def _artcache():
    global _AC
    if _AC is None:
        import artcache
        _AC = artcache.get()
    return _AC


def pack(d, cs, cover_key, avatar_key, install=True, game_dir=None,
         overwrite=False):
    """打包 + （可选）装入游戏目录。返回一份结果给界面显示。

    写入一律走 **临时文件 + `os.replace` 原子替换**；覆盖同名包前先备份。
    """
    # 走 settings 而不是 paths.GAME —— 后者只在 `settings.load()` 跑过之后才有值，
    # 没读过配置时是空串，打包会去找「当前目录下的 WAD」。
    gd = game_dir or settings.game_dir()
    p = plan(d, cs)
    if not p["ok"]:
        raise PackError("；".join(x["msg"] for x in p["problems"]
                                  if x["level"] == "error"))

    data, meta = build(d, cs, cover_key, avatar_key, game_dir=gd)
    os.makedirs(paths.OUTDIR, exist_ok=True)
    out_path = os.path.join(paths.OUTDIR, meta["filename"])

    dest = os.path.join(gd, meta["filename"])
    backup = None
    if install and os.path.exists(dest) and not overwrite:
        # 同名包已经在游戏目录里 —— 备份一份再覆盖，出事了能退回去
        bdir = os.path.join(paths.DATA, "backups")
        os.makedirs(bdir, exist_ok=True)
        backup = os.path.join(
            bdir, "%s.%s.bak" % (meta["filename"], time.strftime("%Y%m%d-%H%M%S")))
        shutil.copy2(dest, backup)
        log.info("打包覆盖前备份 -> %s", backup)

    targets = [out_path]
    if install:
        targets.append(dest)
    for t in targets:
        fsx.write_bytes(t, data)

    meta["out_path"] = out_path
    meta["installed"] = dest if install else None
    meta["backup"] = backup
    meta["plan"] = p
    log.info("打包完成 %s（%d 字节，%d 个条目）-> %s",
             meta["uid"], meta["bytes"], meta["entries"],
             dest if install else out_path)
    return meta


def selftest():
    """自检：不碰游戏目录，只验证 XML 生成和建包能不能跑通。"""
    import cardset
    cs = cardset.get()
    d = {
        "id": "selftest", "name_cn": "自检卡组", "name_en": "Self Test",
        "uid": "DATA_DLC_1M_SELFTEST",
        "main": {"Sol Ring": 4, "Shivan Dragon": 2},
        "min_lands": {"minMountain": 54},
        "side": {"Sol Ring": 2},
        "guide": {"特色": "自检用"},
    }
    p = plan(d, cs)
    print("plan ok=%s" % p["ok"], p["info"])
    for x in p["problems"]:
        print("  [%s] %s" % (x["level"], x["msg"]))
    assert p["ok"], "自检卡组没过检查"

    keys = expand(cs, d["main"])
    print("展开 %d 条 <CARD>：%s" % (len(keys), keys[:3]))
    assert cs.key_of("Sol Ring") in keys

    data, meta = build(d, cs, cs.key_of("Sol Ring"), cs.key_of("Shivan Dragon"),
                       uid_num=9901)
    print("包 %d 字节 / %d 个条目 / uid_num %d" % (meta["bytes"], meta["entries"],
                                                 meta["uid_num"]))
    assert data[:2] == b"\x34\x12", "WAD magic 不对"
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())
