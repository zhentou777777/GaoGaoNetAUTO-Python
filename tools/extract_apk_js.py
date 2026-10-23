# -*- coding: utf-8 -*-
"""从云高官方 APK 里提取业务逻辑（明文 JS）。

云高 APP 是 uni-app(Vue) 打包的，核心逻辑在
``assets/apps/__UNI__A703B0A/www/`` 下，是**未混淆的明文 JavaScript**，
可以直接读出接口路由表、错误码处理、界面文案。

用法：
    python tools/extract_apk_js.py <xxx.apk> [输出目录]

会导出：
    pageCampusNetwork/home.js    校园网络页面逻辑（含全部接口调用）
    app-service.js               业务逻辑 + 接口路由表
    app-view.js                  视图层模板（含界面文案）
    app-config-service.js        配置
然后可以用 tools/grep_ctx.py 在这些文件里检索关键字上下文。

用途：
    · 接口变更时快速定位新的接口与参数
    · 查服务端错误码在 APP 里的处理方式
    · 查界面文案（往往直接说明服务端语义）
"""
from __future__ import annotations

import os
import sys
import zipfile

WANT = [
    "assets/apps/__UNI__A703B0A/www/pageCampusNetwork/home.js",
    "assets/apps/__UNI__A703B0A/www/app-service.js",
    "assets/apps/__UNI__A703B0A/www/app-view.js",
    "assets/apps/__UNI__A703B0A/www/app-config-service.js",
    "assets/apps/__UNI__A703B0A/www/app-config.js",
    "assets/apps/__UNI__A703B0A/www/manifest.json",
]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    apk = sys.argv[1]
    outdir = sys.argv[2] if len(sys.argv) > 2 else "apk_js"

    if not os.path.exists(apk):
        print(f"✗ 找不到 {apk}")
        return 1

    os.makedirs(outdir, exist_ok=True)
    try:
        z = zipfile.ZipFile(apk)
    except Exception as exc:
        print(f"✗ 打开 APK 失败: {exc}")
        return 1

    n = 0
    for name in WANT:
        try:
            data = z.read(name)
        except KeyError:
            continue
        out = os.path.join(outdir, os.path.basename(name))
        with open(out, "wb") as fh:
            fh.write(data)
        print(f"✓ {out}  ({len(data) / 1024:.1f} KB)")
        n += 1

    if not n:
        print("✗ 没找到目标文件——这个 APK 可能不是 uni-app 打包的")
        return 2
    print(f"\n共导出 {n} 个文件。检索示例：")
    print("    python tools/grep_ctx.py apk_js/home.js '/api/network/' 300 10")
    return 0


if __name__ == "__main__":
    sys.exit(main())
