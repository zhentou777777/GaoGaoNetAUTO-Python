# tools/ —— 构建、发布与调试工具

这里放的都是**不参与运行时**的脚本：程序本体（`main.py` 起的那些模块）一个都不在这儿。
根目录因此只保留运行必需的文件。

```
项目根
├── main.py / core.py / webui.py / auth.py / config.py / vault.py …   ← 运行时模块
├── webui/          ← 前端静态资源（HTML/CSS/JS）
├── assets/         ← 图标
├── docs/           ← 文档（Markdown，HTML 由 build_html.py 生成）
├── tests/          ← pytest
└── tools/          ← 本目录：构建 / 发布 / 调试
```

## 构建与发布

| 脚本 | 用途 |
|---|---|
| `build_exe.py` | PyInstaller 打包。`--onefile` 单文件、`--sign` 签名、`--skip-audit` 跳过高危特征审计 |
| `make_release.py` | 生成交付包 zip（portable / singlefile / source），输出到 `release/` |
| `make_docs_package.py` | 把全部文档打成 `GaoGaoNetAUTO-vX.Y.Z-docs.zip` |
| `make_launchers.py` | 生成源码绿色包里的 `setup.bat` / `run.bat` / `stop.bat` |
| `sign_release.ps1` | 给 exe 做自签名（复用 `Cert:\CurrentUser\My` 里的代码签名证书，导出 `.cer`） |
| `audit_features.py` | 产物高危特征审计：UPX / 自解压 / 注册表自启动 / 注入类 API。**审计不过 = 构建失败** |
| `clean_dist.py` | 清理 `dist/` 里的旧产物 |

## 调试与逆向（仅开发时需要，不影响用户）

| 脚本 | 用途 |
|---|---|
| `login_check.py` | 命令行验证账号密码能不能登录（密码走交互输入，不回显、不进 shell 历史） |
| `tokenlab.py` | 实测「你这所学校到底轮不轮换 Token」 |
| `capture.py` | Playwright 抓取网页扫码流程，需要额外装 `playwright`（见 `requirements.txt` 注释） |
| `extract_apk_js.py` | 从 APK 里提取明文 JS（逆向调试） |
| `grep_ctx.py` | 带上下文的代码检索 |
| `fix_ps1_bom.py` | 去掉 ps1 文件的 BOM（PowerShell 中文乱码时用） |

> `capture.py` / `tokenlab.py` / `login_check.py` 都被 `build_exe.py` 排除在生产包外，
> 装到用户机器上的只有运行时模块。
