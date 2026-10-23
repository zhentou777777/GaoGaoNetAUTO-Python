# -*- coding: utf-8 -*-
"""把 CI workflow 部署到 GitHub 仓库。

为什么需要这个脚本
------------------
GitHub 对 ``.github/workflows/`` 下的文件写入有额外授权要求：
OAuth token 必须带 ``workflow`` scope，否则 contents API 与 git push
都会拒绝（表现为 404 / "refusing to allow an OAuth App to create or
update workflow"）。

本脚本按顺序尝试三条路径，成功即止：

1. ``gh auth refresh -s workflow`` —— 交互式浏览器授权（最正规，需要你点一下）；
2. git push —— 授权完成后直接推；
3. 提示手动操作路径。

用法：
    python ci/install_workflow.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = HERE / "build_windows.yml"
REPO = "zhentou777777/GaoGaoNetAUTO-Python"
DEST = Path(".github") / "workflows" / "build_windows.yml"


def _run(cmd, **kw):
    return subprocess.run(cmd, shell=isinstance(cmd, str), **kw)


def _has_workflow_scope() -> bool:
    try:
        out = subprocess.run(
            ["gh", "api", "user", "-i"],
            capture_output=True, text=True, timeout=60,
        ).stdout
        for line in out.splitlines():
            if line.lower().startswith("x-oauth-scopes:"):
                return "workflow" in line.lower()
    except Exception:
        pass
    return False


def main() -> int:
    if not SRC.exists():
        print(f"✗ 找不到 {SRC}")
        return 1
    if not shutil.which("gh"):
        print("✗ 需要 GitHub CLI：https://cli.github.com")
        return 1

    if _has_workflow_scope():
        print("✓ token 已具备 workflow scope")
    else:
        print("! token 缺少 workflow scope，尝试交互式授权…")
        print("  浏览器会弹出授权页，请勾选 workflow 并确认。\n")
        res = _run(["gh", "auth", "refresh", "-h", "github.com", "-s", "workflow"])
        if res.returncode != 0:
            print("\n✗ 授权未完成。")
            print("  替代方案：在 https://github.com/settings/tokens 新建一个")
            print("  带 workflow scope 的 Personal Access Token，然后执行：")
            print("      git remote set-url origin https://<TOKEN>@github.com/"
                  f"{REPO}.git")
            print("      git push origin main")
            return 2

    # 授权已就位 → 正常推送
    res = _run(["git", "add", str(SRC), str(DEST.parent)])
    res = _run(["git", "commit", "-m", "ci: 添加 Windows exe 自动构建 workflow"])
    res = _run(["git", "push", "origin", "main"])
    if res.returncode == 0:
        print("✓ workflow 已部署，可在 Actions 页查看构建")
        return 0
    print("✗ 推送失败，请检查上面的错误信息")
    return res.returncode


if __name__ == "__main__":
    sys.exit(main())
