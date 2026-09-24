# -*- coding: utf-8 -*-
"""程序入口：起本地服务 + 开桌面窗口。

为什么是 pywebview 而不是 Tauri / Electron
-------------------------------------------
WAD 读写、DXT 解码、卡面合成**全是 Python**。pywebview 下后端和界面在**同一个进程**里，
直接 `import` 调用；Tauri（Rust 壳）和 Electron 都得把 Python 跑成 sidecar 子进程，
2.2 万张图的解码结果要在进程间搬，很不划算。

pywebview 借系统自带的 **WebView2**（Edge 内核），所以界面观感和 Tauri/Electron 一样，
打包体积却只有 20~40MB。

用法::

    python main.py                # 开窗口
    python main.py --browser      # 只起服务，自己开浏览器（调试前端方便）
    python main.py --port 8765    # 指定端口
    python main.py --reindex      # 起来之前先强制重建索引
"""

import os
import sys
import time
import socket
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))

import uvicorn

import paths
import settings
from log import get_logger, logfile

log = get_logger("main")

TITLE = "万智牌 2014 卡组管理器"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_ready(port, timeout=30.0):
    """等 uvicorn 真的开始监听 —— 不等的话窗口会先弹一个「无法访问」。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.3):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def ensure_indexes(force=False):
    """启动时按需建索引。返回「能不能正常用」。

    首次运行会慢（要解码几万张贴图），给个进度。

    ⚠️ **游戏目录没配好不再直接退出**。打成 exe 之后用户第一次双击就是这个状态，
    `return 2` 等于「双击没反应」，而且 `--windowed` 下连那句提示都看不见。
    现在照常开窗口，用户在「设置」里选完目录再重建。
    """
    import rebuild
    gd = settings.game_dir()
    if not os.path.isdir(gd):
        log.warning("游戏目录没配好：%r —— 先开界面，在右上角「设置」里选", gd)
        print("\n!! 游戏目录还没设置。界面打开后点右上角「设置」，选 Magic 2014 的安装目录。\n")
        return False
    try:
        st = rebuild.status(gd)
        stale = [n for n, (f, _m, _w) in st.items() if not f]
        if not stale and not force:
            log.info("索引都是新的，跳过重建")
            return True
        print("需要重建的索引：%s" % ("、".join(stale) if stale else "全部（--force）"))
        rebuild.ensure_all(force=force, verbose=True)
        return True
    # ⚠️ **必须显式带上 `SystemExit`**。`tools/*.py` 里报错用的是
    # `raise SystemExit("打不开 xxx —— 游戏目录对吗？")`，而 SystemExit 继承自
    # **BaseException**，`except Exception` 兜不住 —— 它会一路穿透 main()，
    # 把整个进程带走。表现是「双击 exe 没反应」，日志里才有堆栈。
    # （真踩过：目录里有个坏掉的 DATA_CORE.WAD，程序静默退出。）
    except (Exception, SystemExit) as e:
        # 建索引失败也不拦着开窗口 —— 用户还能进「设置」换目录 / 看日志。
        log.error("建索引失败：%s: %s", type(e).__name__, e, exc_info=True)
        print("\n!! 建索引失败：%s\n   界面照常打开，可在「设置」里换游戏目录或看日志。\n" % e)
        return False


def run_server(port):
    import api
    api.mount_frontend()
    cfg = uvicorn.Config(api.app, host="127.0.0.1", port=port,
                         log_level="warning", access_log=False)
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    t.start()
    return server, t


def main():
    force = "--reindex" in sys.argv
    browser_only = "--browser" in sys.argv
    port = 0
    if "--port" in sys.argv:
        try:
            port = int(sys.argv[sys.argv.index("--port") + 1])
        except (IndexError, ValueError):
            pass
    port = port or free_port()

    log.info("%s 启动", TITLE)
    log.info("Python %s", sys.version.split()[0])
    log.info("项目根 %s", paths.ROOT)
    log.info("日志 %s", logfile())

    server, _t = run_server(port)
    if not wait_ready(port):
        log.error("本地服务起不来（端口 %d）", port)
        return 3

    url = "http://127.0.0.1:%d/" % port
    log.info("服务就绪：%s", url)

    if browser_only:
        # 命令行/浏览器模式就在终端里同步建——有控制台，能看到进度
        ensure_indexes(force=force)
        import webbrowser
        webbrowser.open(url)
        print("\n服务在 %s —— Ctrl+C 退出\n" % url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return 0

    try:
        import webview
    except ImportError:
        print("\n!! 没装 pywebview。装一下：\n     pip install pywebview\n")
        print("   或者先只在浏览器里看：python main.py --browser")
        print("   服务已经在 %s 跑着，Ctrl+C 退出\n" % url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return 0

    # 建索引可能几十秒（首次），这里**必须同步建完再开窗**。
    #
    # ⚠️ 试过「先弹个小窗、在后台线程建索引、建完再 create_window 换主窗」——
    # Windows 上直接崩：
    #     winforms.py:814  signal.signal(signal.SIGINT, _sigint_handler)
    #     ValueError: signal only works in main thread of the main interpreter
    # pywebview 的 `start(func)` 把 func 放在**子线程**里跑，而 pywebview 的
    # Windows 后端在 `create_window` 内部要调 `signal.signal()`，那个只能在主线程调。
    # 而且异常抛在子线程里，`webview.start()` 外层的 try 根本兜不住。
    # 所以「窗口的创建/销毁」全程只能待在主线程 —— 别再往那条路上改了。
    ensure_indexes(force=force)

    window = webview.create_window(TITLE, url, width=1440, height=920,
                                   min_size=(1080, 680), text_select=True)
    # 调试用：开 devtools 的开关
    debug = "--debug" in sys.argv
    webview.start(debug=debug)
    server.should_exit = True
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
