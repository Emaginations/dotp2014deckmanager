# -*- coding: utf-8 -*-
"""全局路径与常量。所有模块都从这里取路径，不要在别处硬编码。"""

import os
import sys

# ---------------------------------------------------------------- 两个「根」

FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    # PyInstaller 把程序资源解到 `sys._MEIPASS`（**只读**，进程退出就删）。
    # 注意别拿 `__file__` 去推 —— 冻结后它指向哪儿取决于打包方式，不可靠。
    BUNDLE = getattr(sys, "_MEIPASS",
                     os.path.dirname(os.path.abspath(sys.executable)))
    HERE = os.path.join(BUNDLE, "backend")
    # 可写数据放 **exe 旁边**（便携式，和本项目其它工具一个路子）：
    # 索引、缩略图缓存、卡组工程、打包产物都在这儿。
    # 放 `_MEIPASS` 里的话重启一次全没了，而且每次启动都要重解 157MB 缩略图。
    ROOT = os.path.dirname(os.path.abspath(sys.executable))
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
    BUNDLE = os.path.dirname(HERE)           # dotp2014builder/（源码根）
    ROOT = BUNDLE

# `vendor/` 放的是从 dotp2014decks 原样拷来的 wadtool / dxt / wadbuild。
# 这里统一挂进 import 路径，省得每个模块各写一遍 —— 任何 `import paths`
# 之后的代码都能直接 `import dxt` / `from wadtool import Wad`。
# 冻结后这些模块已经打进包里了，插不插都行，但插着不碍事。
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "vendor"))

# ---- 只读：跟着程序走 ----
FRONTEND_DIST = os.path.join(BUNDLE, "frontend", "dist")

# ---- 可写：跟着 exe / 源码根走 ----
DATA = os.path.join(ROOT, "data")            # 索引与缓存
PROJECTS = os.path.join(ROOT, "projects")    # 用户卡组工程

# 游戏本体。真正的取值在 settings.json 里（见 settings.py），这里只是**兜底默认值**。
# 早先写的是开发机的 `E:\game\Magic 2014` —— 开源出去别人跑不了，
# 而且把本机目录结构泄了个干净。现在空着，首次启动由用户在设置里选。
GAME = ""
WAD_BACKUP_DIRNAME = "WAD备份"       # 汉化前的 `.orig` 备份，用来取英文原名

# 本项目自己的索引（都由 backend/tools/ 下的脚本生成，带 WAD 指纹）
SETTINGS_JSON = os.path.join(DATA, "settings.json")
POOL_JSON = os.path.join(DATA, "pool.json")              # 卡池：卡名/费用/颜色/类型/稀有度
DETAILS_JSON = os.path.join(DATA, "card_details.json")   # 详情：关键词/规则文本/系列
ARTIDX_JSON = os.path.join(DATA, "art_index.json")       # 卡 -> ARTID -> 插画位置
FRAMEIDX_JSON = os.path.join(DATA, "frame_index.json")   # 卡面素材的导出清单
GAMEDECKS_JSON = os.path.join(DATA, "game_decks.json")   # 游戏里已有的牌组（按来源分组）
DECKBOX_JSON = os.path.join(DATA, "deckbox_index.json")  # 牌盒封面名 -> 贴图位置

# 缓存目录
THUMBS = os.path.join(DATA, "thumbs")        # 缩略图 webp
FRAMES = os.path.join(DATA, "frames")        # 卡框/PT框/符号 PNG

# 渲染素材 —— **随程序发布**（不像 data/ 是缓存，删了要重建）
# 从 dotp2014decks/data/ 拷来的官方素材，Deck Builder 合成封面/立绘就用这几张。
ASSETS = os.path.join(HERE, "assets")
DECKBOX_ASSETS = os.path.join(ASSETS, "deckbox")        # D14_DeckBox{Mask,Overlay,Alpha}.png
PW_ASSETS = os.path.join(ASSETS, "personality")         # D14_Personality{...}.png
OUTDIR = os.path.join(ROOT, "out")           # 打包产物

# 卡面排版基准（照抄 Deck Builder 的 CardInfo.cs:79-107，单位是像素 @ 356×512）
FACE_W, FACE_H = 356, 512
RC_ILLUSTRATION = (16, 47, 324, 238)
RC_NAME = (12, 14, 330, 22)
RC_TYPE_LINE = (14, 296, 328, 18)
RC_TEXT = (15, 326, 324, 149)
RC_PT_BOX = (245, 453, 130, 65)
RC_EXPANSION = (302, 292, 50, 25)
RC_ARTIST = (10, 488, 34, 9)
MANA_ICON = 32

# 牌盒封面（`ART_ASSETS/TEXTURES/DECKS/<名字>.TDX`）
# 整张是 512×512 的 **3D 盒子渲染** —— 四周大量透明留白，左边还有一条带
# 鹏洛客符号的盒脊。整个铺到列表条上会是个飘着的盒子，所以只取**正面**。
# 实测 512 基准：盒脊到 x≈203，正面右缘 x≈365，盒顶 y≈92，正面底 y≈348。
DECKBOX_BASE = 512
RC_DECKBOX_FRONT = (203, 92, 162, 256)   # 正面整块：宽 162 / 高 256

# 只取正面**上面这一截**。整块是竖的（0.63），铺到列表条上只有 26px 宽，太窄；
# 截到 40% 后宽高比变成 1.59，行高 42px 时有 66px 宽，醒目得多。
# **改这个数字会让旧的缩略图缓存失效**，文件名里带了份额（见 artcache.box_thumb_path）。
DECKBOX_CROP_TOP = 0.40

# 卡框素材所在的包。**只存文件名**，不拼 `GAME` ——
# 游戏目录是运行时可改的（`settings.save()` 会改 `GAME`），而模块级常量在
# import 那一刻就定死了，拼出来的会是个过期路径。要用就 `os.path.join(game_dir, CORE_WAD_NAME)`。
CORE_WAD_NAME = "DATA_CORE.WAD"

for _d in (DATA, PROJECTS, THUMBS, FRAMES):
    os.makedirs(_d, exist_ok=True)
