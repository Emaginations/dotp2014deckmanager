# -*- coding: utf-8 -*-
"""一键打包成单文件 exe。

    python build_exe.py            # 先建前端，再打包
    python build_exe.py --skip-web # 前端没改过，跳过 npm（省 2 秒，别省出事）
    python build_exe.py --clean    # 打包前删掉 build/ 和 dist/

**必须先建前端**：`backend/main.py` 是把 `frontend/dist` 当静态目录托管的，
不重建的话打进去的还是上次的界面 —— 改了前端却看不到效果，能查半天。

产物：`dist/dotp2014deckmanager.exe`（单文件，双击即用）。
"""

import os
import sys
import shutil
import subprocess

# Windows 控制台默认 GBK，下面那些 `»` `✓` 会直接抛 UnicodeEncodeError
# 把构建打断（readme「踩过的坑」里记的就是这个）。这里不 import log ——
# 那是应用内部的模块，构建脚本不该依赖它。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.abspath(__file__))
FRONTEND = os.path.join(ROOT, "frontend")
DIST = os.path.join(FRONTEND, "dist")
EXE = os.path.join(ROOT, "dist", "dotp2014deckmanager.exe")


def run(cmd, cwd, shell=False):
    print("» %s" % (cmd if isinstance(cmd, str) else " ".join(cmd)))
    r = subprocess.run(cmd, cwd=cwd, shell=shell)
    if r.returncode != 0:
        raise SystemExit("命令失败（退出码 %d）" % r.returncode)


def build_web():
    if not os.path.isdir(os.path.join(FRONTEND, "node_modules")):
        run("npm install", FRONTEND, shell=True)
    # Windows 上 npm 是 .cmd，shell=True 才找得到
    run("npm run build", FRONTEND, shell=True)
    idx = os.path.join(DIST, "index.html")
    if not os.path.exists(idx):
        raise SystemExit("前端没构建出来：%s 不存在" % idx)
    print("  前端产物 %s" % DIST)


def main():
    args = sys.argv[1:]

    if "--clean" in args:
        for d in (os.path.join(ROOT, "build"), os.path.join(ROOT, "dist")):
            if os.path.isdir(d):
                shutil.rmtree(d)
                print("  已删 %s" % d)

    if "--skip-web" not in args:
        build_web()
    else:
        print("!! 跳过前端构建 —— 确认 dist 是最新的")

    try:
        import PyInstaller.__main__  # noqa: F401
    except ImportError:
        raise SystemExit("没装 PyInstaller：pip install pyinstaller")

    run([sys.executable, "-m", "PyInstaller", "--noconfirm",
         os.path.join(ROOT, "dotp2014deckmanager.spec")], ROOT)

    if not os.path.exists(EXE):
        raise SystemExit("打包完了但没找到 %s" % EXE)
    mb = os.path.getsize(EXE) / 1024.0 / 1024.0
    print("\n✓ %s（%.1f MB）" % (EXE, mb))
    print("  直接把 exe 拷到任意目录双击即可 —— 索引和卡组会放在**它旁边**。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
