# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

别直接跑 `pyinstaller dotp2014builder.spec` —— 用 `python build_exe.py`，
它会先把前端 `npm run build` 出来，否则打进去的是**旧的 dist**（踩过）。

打包形态和隔壁 `reassemblymodmanager` 一致：**onefile + windowed + upx**。

几个必须这么写的地方
--------------------
- `pathex` 要带 `backend/tools` 和 `backend/vendor` —— 那两个目录是靠
  `sys.path.insert` 挂进来的（见 `paths.py`），PyInstaller 静态分析看不到。
- `datas` 把 `frontend/dist` 和 `backend/assets` 放进包里的**同名子目录**，
  这样 `paths.py` 里 `BUNDLE/frontend/dist`、`HERE/assets` 在冻结后依然成立。
- `hiddenimports` 里那几个 `webview.platforms.*` 是**运行时按需 import** 的，
  静态分析扫不到；少了它打包能过、一启动就报「no suitable GUI library」。
- **别加 `uvloop`** —— Windows 上没有，加了直接崩。
"""

import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(SPECPATH)
BACKEND = os.path.join(ROOT, "backend")

datas = [
    # 渲染素材（牌盒 Mask/Overlay/Alpha、鹏洛客立绘模板）—— 随程序走
    (os.path.join(BACKEND, "assets"), os.path.join("backend", "assets")),
    # 前端构建产物 —— 必须**先 npm run build**
    (os.path.join(ROOT, "frontend", "dist"), os.path.join("frontend", "dist")),
]

hiddenimports = [
    # pywebview 的 GUI 后端是运行时挑的。Windows 上走 EdgeChromium（经 pythonnet），
    # 挑不到会退回 mshtml。三个都带上，反正它们互相不冲突。
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
    "webview.platforms.mshtml",
    # uvicorn 的 loop / protocol 也是运行时按需 import
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

# 后端自己的模块：`backend/tools` 和 `backend/vendor` 不在默认搜索路径上，
# 靠 collect_submodules 会把它们当包扫（它们不是包），所以逐个点名。
for _m in ("build_pool", "build_details", "build_artidx", "build_deckbox",
           "export_frames", "rebuild", "dxt", "wadbuild", "wadtool"):
    hiddenimports.append(_m)

a = Analysis(
    [os.path.join(BACKEND, "main.py")],
    pathex=[BACKEND,
            os.path.join(BACKEND, "tools"),
            os.path.join(BACKEND, "vendor")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 用不到，且会拖进来一大坨（tkinter 还带 tcl/tk 的 DLL）
        "tkinter", "matplotlib", "pandas", "scipy", "IPython",
        "pytest", "setuptools", "pip",
        # Linux / macOS 的 GUI 后端，Windows 上纯属浪费。
        #
        # ⚠️ 别把 `webview.platforms.win32` 也排除掉 —— 它名字像「另一个后端」，
        # 其实是 `winforms.py:22` 里 `from webview.platforms import win32` 的
        # **工具模块**（屏幕尺寸、DPI 那些）。排掉之后 winforms 直接
        # `ImportError: cannot import name 'win32'`，pywebview 报
        # 「pythonnet cannot be loaded」，看着像 pythonnet 没打进去，其实是自己删的。
        "webview.platforms.gtk", "webview.platforms.cocoa",
        "webview.platforms.qt", "webview.platforms.android",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="dotp2014deckmanager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # 见 log._utf8_console()：没有控制台时它会给
                            # sys.stdout/stderr 塞空写入器，print 不会炸
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
