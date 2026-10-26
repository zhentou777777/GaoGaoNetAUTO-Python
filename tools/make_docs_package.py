# -*- coding: utf-8 -*-
"""打包全部文档成 zip。

只依赖标准库。

用法：
    python make_docs_package.py

产物：
    dist/文档包/GaoGaoNetAUTO-文档包-v<VERSION>/     （中间目录）
    release/GaoGaoNetAUTO-v<VERSION>-docs.zip         （最终交付）

**不写桌面** —— 桌面是用户的私人空间，构建产物一律留在项目内。
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# ---------------------------------------------------------------- 控制台编码
# Windows 控制台 / CI runner 的默认编码可能是 cp1252（GitHub Actions 就是），
# 直接 print 中文会抛 UnicodeEncodeError 让构建在第一步就崩掉。
# 统一把 stdout/stderr 切到 UTF-8，替换不出来的字符而不是炸掉。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 脚本在 tools/ 下，项目根要向上一层
HERE = Path(__file__).resolve().parent.parent
VERSION = "1.0.2"
PKG_NAME = f"GaoGaoNetAUTO-文档包-v{VERSION}"
# 落盘的文件名用英文（中文名上传 GitHub Release 会 404）；
# zip 内的目录名保留中文，解压后好认。
PKG_ZIP = f"GaoGaoNetAUTO-v{VERSION}-docs.zip"
OUT_DIR = HERE / "dist" / "文档包" / PKG_NAME

DOCS = HERE / "docs"
DIST = HERE / "dist"

# (包内文件名, 源文件相对项目根的路径)
FILES = [
    ("00-需求说明书.md", DOCS / "需求说明书.md"),
    ("00-需求说明书.html", DOCS / "需求说明书.html"),
    ("01-生产交接文档.md", DOCS / "生产交接文档.md"),
    ("01-生产交接文档.html", DOCS / "生产交接文档.html"),
    ("02-使用教程.md", DOCS / "使用教程.md"),
    ("02-使用教程.html", DOCS / "使用教程.html"),
    ("03-技术路径.md", DOCS / "技术路径.md"),
    ("03-技术路径.html", DOCS / "技术路径.html"),
    ("04-项目说明.md", HERE / "README.md"),
    ("04-项目说明.html", DOCS / "README.html"),
    ("06-Defender误报排查.md", DOCS / "Defender-误报排查.md"),
    ("06-Defender误报排查.html", DOCS / "Defender-误报排查.html"),
    ("07-CHANGELOG.md", HERE / "CHANGELOG.md"),
    ("LICENSE", HERE / "LICENSE"),
]

INDEX_MD = """# 糕糕校园网自动认证 · 文档包

版本：v{version}
项目：https://github.com/zhentou777777/GaoGaoNetAUTO-Python

---

## 怎么读这些文档

**你是使用者（想把电脑装上这个工具）**

1. `01-生产交接文档.md` → 第 1 部分「使用者指南」（安装、配置、日常使用、FAQ）
2. `02-使用教程.md` → 逐步操作细节
3. `01-生产交接文档.md` → 1.9 节「杀软拦截处置」（Windows Defender 拦截必读）
4. `06-Defender误报排查.md` → 被拦了怎么一步步自查（下载拦截 / 运行被杀 / 证书信任）

**你是维护者（要接手这个项目）**

1. `00-需求说明书.md` → 需求边界与验收标准
2. `01-生产交接文档.md` → 第 2、3、4、5 部分（架构 / 构建发布 / 部署 / 监控故障）
3. `03-技术路径.md` → 协议逆向全过程与实现取舍
4. `05-构建与发布说明.md` → 打包命令、参数解释、签名流程、CI

**你是项目负责人**

1. `00-需求说明书.md` → 第 3 节需求台账 R1–R11 + 第 10 节遗留计划
2. `01-生产交接文档.md` → 第 0 部分交接总览 + 第 6 部分风险登记 + 第 7 部分迭代计划

---

## 文件清单

| 文件 | 说明 |
|---|---|
| `00-需求说明书.md` / `.html` | 需求权威文档：背景、需求台账、功能/非功能/数据/接口需求、验收标准、遗留项 |
| `01-生产交接文档.md` / `.html` | 使用 + 维护合一手册：使用者指南、架构、构建发布、部署、Runbook、风险登记 |
| `02-使用教程.md` / `.html` | 逐步操作手册 |
| `03-技术路径.md` / `.html` | 接口逆向全过程、自愈架构、实现取舍 |
| `04-项目说明.md` / `.html` | 项目总览、原理速览、快速开始、FAQ |
| `05-构建与发布说明.md` | 打包命令与参数、签名流程、CI 配置、发布清单 |
| `06-Defender误报排查.md` / `.html` | 被杀毒软件拦截的成因、处置步骤、证书信任、彻底根治方案 |
| `07-CHANGELOG.md` | 版本变更记录（安全 / 可靠性 / 工程化改动明细） |
| `LICENSE` | GPL-2.0 |
| `MANIFEST.txt` | 本包所有文件的路径、字节数、MD5 |

> `.html` 为单文件版本，离线双击即可阅读（内嵌样式，支持深浅色自适应）。

---

## 校验

```powershell
Get-Content .\\MANIFEST.txt
Get-FileHash .\\01-生产交接文档.md -Algorithm MD5
```
"""

BUILD_MD = """# 糕糕校园网自动认证 · 构建与发布说明

版本：v{version}

---

## 一、环境准备

```powershell
# 建虚拟环境（隔离，不污染系统 Python）
py -3 -m venv .venv
.\\.venv\\Scripts\\python.exe -m pip install --upgrade pip

# 装依赖
.\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt
.\\.venv\\Scripts\\python.exe -m pip install pyinstaller
```

**依赖清单**（`requirements.txt`）：

```
requests>=2.31.0      # HTTP 客户端
segno>=1.6.0          # 二维码生成（纯 Python）
pystray>=0.19.5       # 系统托盘（可选，缺失时程序降级）
Pillow>=10.0.0        # 托盘图标（可选，缺失时程序降级）
```

> `capture.py` 需要额外装 `playwright`（仅逆向工具用，不参与打包）：
> ```powershell
> pip install playwright && python -m playwright install chromium
> ```

---

## 二、打包

```powershell
# 目录版（主推：Defender 误报概率最低）
.\\.venv\\Scripts\\python.exe build_exe.py

# 单文件版
.\\.venv\\Scripts\\python.exe build_exe.py --onefile
```

产物：
- onedir：`dist\\GaoGaoNetAUTO\\GaoGaoNetAUTO.exe`（+ `_internal\\`）
- onefile：`dist\\GaoGaoNetAUTO.exe`

### 打包参数逐条解释

| 参数 | 作用 | 为什么 |
|---|---|---|
| `--noconfirm --clean` | 覆盖旧产物、清缓存 | 避免旧文件混入 |
| `--onedir`（默认） | 目录形态 | 不走 %TEMP% 自解压，Defender 误报最低 |
| `--onefile` | 单文件形态 | 便利，但自解压是 ML 分类器高权重特征 |
| `--windowed` | 无控制台窗口 | GUI 程序 |
| `--noupx` | 关闭 UPX 压缩 | UPX 壳是 Defender ML 头号命中点（代价：体积 +3MB） |
| `--version-file version_info.txt` | PE 版本资源 | 消除「未知发布者」外观 |
| `--icon assets/icon.ico` | 应用图标 | |
| `--hidden-import pystray._win32` | 托盘依赖 | 静态分析抓不到 |
| `--exclude-module playwright` | 排除逆向工具 | 体积巨大且运行时不需要 |
| `--add-data assets;assets` | 打包资源目录 | 图标与运行时资源 |

---

## 三、文档构建

```powershell
# Markdown → 单文件 HTML（纯标准库，不装第三方 markdown）
python docs/build_html.py

# 打包全部文档到项目内 release/ 目录
python make_docs_package.py
```

---

## 四、签名（自签名）

### 4.1 生成证书（Administrator PowerShell）

```powershell
$pw = ConvertTo-SecureString -String "<你的口令>" -Force -AsPlainText
New-SelfSignedCertificate -Type CodeSigningCert -Subject "CN=GaoGaoNetAUTO" `
  -FriendlyName "GaoGaoNetAUTO" -CertStoreLocation Cert:\\CurrentUser\\My `
  -NotAfter (Get-Date).AddYears(10) `
  -TextExtension @("2.5.29.37={{text}}1.3.6.1.5.5.7.3.3") `
  -Password $pw

$cert = Get-ChildItem Cert:\\CurrentUser\\My | Where-Object {{ $_.Subject -like "*GaoGaoNetAUTO*" }}
New-Item -ItemType Directory -Force .\\sign | Out-Null
Export-PfxCertificate -Cert $cert -FilePath .\\sign\\GaoGaoNetAUTO.pfx -Password $pw
Export-Certificate  -Cert $cert -FilePath .\\sign\\GaoGaoNetAUTO.cer
```

> `2.5.29.37={{text}}1.3.6.1.5.5.7.3.3` 是 **Extended Key Usage = Code Signing**，缺它 Windows 不认这是代码签名证书。
> `.pfx` 含私钥，**不要提交到仓库**（`.gitignore` 已忽略 `sign/*.pfx`）。

### 4.2 签名 exe

```powershell
$signtool = "C:\\Program Files (x86)\\Windows Kits\\10\\bin\\x64\\signtool.exe"
& $signtool sign /fd SHA256 /td SHA256 `
  -f .\\sign\\GaoGaoNetAUTO.pfx -p "<口令>" `
  -tr http://timestamp.digicert.com -ts http://timestamp.digicert.com `
  .\\dist\\GaoGaoNetAUTO.exe
```

> `/tr` + `/ts` 是**可信时间戳**，保证证书过期后签名依然有效 —— 这是「签名有效性」的关键，务必带上。

### 4.3 验证

```powershell
Get-AuthenticodeSignature .\\dist\\GaoGaoNetAUTO.exe |
  Format-List Status, StatusMessage, SignerCertificate, TimeStamperCertificate
```

本机已信任该自签证书时 `Status = Valid`；未导入的机器上是 `UnknownError` / `NotTrusted`（正常，需先导入 `.cer`）。

### 4.4 目标机导入（让「发布者」不再显示「未知」）

**管理员** PowerShell：

```powershell
Import-Certificate -FilePath .\\GaoGaoNetAUTO.cer -CertStoreLocation Cert:\\LocalMachine\\Root
Import-Certificate -FilePath .\\GaoGaoNetAUTO.cer -CertStoreLocation Cert:\\LocalMachine\\TrustedPublisher
```

> 自签名**不能**消除 SmartScreen 拦截，只能消除「未知发布者」外观。
> 根治需正规 OV 代码签名证书或 Azure Trusted Signing。

---

## 五、CI 自动构建

配置源（唯一一份）：`.github/workflows/build_windows.yml`。

```
触发：workflow_dispatch（手动） | push tag v*
环境：windows-latest, Python 3.11
步骤：checkout → setup-python → pip install -r requirements.txt
     → python -m pytest tests/
     → python build_exe.py（含产物特征审计）→ python build_exe.py --onefile
     → python make_release.py --all --out dist/packages
     → 生成 SHA256SUMS.txt → upload-artifact
     → （仅 tag）softprops/action-gh-release 发布 Release
```

> **CI 产物的坑**：GitHub Actions 生成的 exe 无签名、下载量低 → SmartScreen 信誉仍差。Release 说明里必须写明「首次运行需『更多信息 → 仍要运行』」。

---

## 六、发布清单

每次发布需同步更新：

- [ ] `version_info.txt` 版本号（FileVersion / ProductVersion）
- [ ] exe（onefile + onedir zip）
- [ ] 源码绿色包 zip
- [ ] `.cer` 公钥证书
- [ ] 文档四件套 HTML + 文档包 zip
- [ ] GitHub Release 说明（含首次运行提示）
- [ ] `CHANGELOG.md`（计划新增）
- [ ] git tag `vX.Y.Z`

### Release 附件

附件名**一律英文**（中文名上传会 404），中文说明写在 Release 正文里。

| 附件 | 必给 | 说明 |
|---|---|---|
| `GaoGaoNetAUTO.exe` | ✅ | 单文件版，双击即用 |
| `GaoGaoNetAUTO-vX.Y.Z-portable.zip` | ✅ | 目录版（**推荐**，误报最低） |
| `GaoGaoNetAUTO-vX.Y.Z-source.zip` | ✅ | 源码绿色包（需自装 Python） |
| `GaoGaoNetAUTO-vX.Y.Z-docs.zip` | ✅ | 文档包（HTML + Markdown） |
| `GaoGaoNetAUTO-sign.cer` | ✅ | 自签名公钥证书（可选导入） |

> 目录版 zip 里已经含 `GaoGaoNetAUTO.exe`，所以**不再单独发** `singlefile.zip`
> —— 单文件版直接用上面那个裸 exe 就是。

---

## 七、产物校验

```powershell
Get-FileHash .\\dist\\GaoGaoNetAUTO.exe -Algorithm MD5
Get-ChildItem -Recurse -Include *.exe,*.dll,*.pyd | Unblock-File   # 解除下载锁定
```

Defender 命令行扫描（返回 0 = 未检出）：

```powershell
& "$env:ProgramFiles\\Windows Defender\\MpCmdRun.exe" -Scan -ScanType 3 -File .\\dist\\GaoGaoNetAUTO.exe
```
"""


def md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ensure_html() -> None:
    """先把 Markdown 文档渲染成 HTML。

    ``docs/*.html`` **不进版本库**（是生成物，入库只会和 md 打架），
    所以每次出文档包之前都在本地重新生成一遍，保证 HTML 永远跟得上 md。
    """
    script = DOCS / "build_html.py"
    if not script.exists():
        print("[warn] 找不到 docs/build_html.py，跳过 HTML 生成")
        return
    print("· 生成 HTML 版文档…")
    proc = subprocess.run([sys.executable, str(script)],
                          cwd=str(HERE), check=False)
    if proc.returncode != 0:
        print("[warn] HTML 生成失败，文档包里将只有 Markdown")


def main() -> int:
    _ensure_html()
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    missing = []
    for name, src in FILES:
        if not src.exists():
            missing.append(str(src))
            continue
        shutil.copy2(src, OUT_DIR / name)

    # 新增的两份说明文档
    (OUT_DIR / "README.md").write_text(
        INDEX_MD.format(version=VERSION), encoding="utf-8")
    (OUT_DIR / "05-构建与发布说明.md").write_text(
        BUILD_MD.format(version=VERSION), encoding="utf-8")

    if missing:
        print("✗ 缺少源文件：")
        for m in missing:
            print("   ", m)

    # MANIFEST
    lines = [
        f"GaoGaoNetAUTO 文档包 v{VERSION}",
        f"生成时间：{__import__('time').strftime('%Y-%m-%d %H:%M:%S')}",
        "仓库：https://github.com/zhentou777777/GaoGaoNetAUTO-Python",
        "",
        f"{'文件':<34}{'字节':>10}  MD5",
        "-" * 78,
    ]
    total = 0
    for f in sorted(OUT_DIR.iterdir(), key=lambda p: p.name):
        if f.name == "MANIFEST.txt" or not f.is_file():
            continue
        size = f.stat().st_size
        total += size
        lines.append(f"{f.name:<34}{size:>10}  {md5(f)}")
    lines.append("-" * 78)
    lines.append(f"{'合计（不含 MANIFEST）':<32}{total:>10}")
    (OUT_DIR / "MANIFEST.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # zip → 项目内 release/（**不写桌面**，桌面是用户的私人空间）
    release_dir = HERE / "release"
    release_dir.mkdir(parents=True, exist_ok=True)
    out_zip = release_dir / PKG_ZIP
    if out_zip.exists():
        out_zip.unlink()
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(OUT_DIR.rglob("*")):
            if f.is_file():
                z.write(f, f"{PKG_NAME}/{f.relative_to(OUT_DIR).as_posix()}")

    count = len([f for f in OUT_DIR.rglob("*") if f.is_file()])
    print(f"✓ 文档包已生成：{out_zip}")
    print(f"  文件数：{count}")
    print(f"  压缩包大小：{out_zip.stat().st_size / 1024:.1f} KB")
    print(f"  MD5：{md5(out_zip)}")
    print(f"  中间目录：{OUT_DIR}")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
