# CHANGELOG

本文件记录糕糕校园网自动认证（GaoGaoNetAUTO-Python）的变更。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

---

## [1.0.2] — 2026-10-26

### 修复

- **「手机 IP」设置每 15 秒被清空**：`info_snapshot()` 不返回 `phone_ip`，
  前端回填的是个不存在的 `dataset.v`，输入框被清成空串；用户随后点「保存设置」
  就把这项配置**写成了空值**。现在快照带上 `phone_ip` / `scheme`，并真正落盘。
- **「抢占上线」在没有其他设备在线时静默失效**：`_takeover_bg_inner` 调
  `self.login()`，而忙碌锁已被 `takeover()` 持有，于是只打一行
  「已有认证在进行中」就结束。改为直接跑认证主体。
- **退出程序可能卡住**：`ThreadingHTTPServer` 默认在 `server_close()` 里 join
  所有处理线程，SSE 长连接和浏览器挂着的 keep-alive 会拖住退出数秒。
  改为 `block_on_close = False`，并让 handler 不再往 stderr 写访问/错误日志。
- **首屏令牌读不出来**：种的是 `HttpOnly` cookie，`document.cookie` 取不到，
  `X-Auth-Token` 始终是空串（请求全靠 cookie 兜住）。改为优先读地址栏 `?t=`。
- **SSE 重连后状态栏一直停在「连接中断」**：没有处理服务端建连时下发的
  `kind: "state"` 事件，也没有 `onopen` 复位。
- **断网判定会累积**：在线时没清零失败计数，抖动两次就被当成断网。
- **`generate_204` 探针永远失败**：实际返回 204，代码却按 200 判定。
- **重连循环停不下来**：`stop_reconnect()` 只改标志位，线程仍在跑并重复认证；
  线程自己退出时又不复位标志，导致之后的 `start_reconnect()` 永久失效。
- **账号密码登录的错误归类**：「该手机号不存在」先命中「手机号」，
  被误报成「手机号格式不正确」。
- `save_settings()` 校验了 `scheme` 却从不保存它。

### CI 与构建

- **CI 从来没成功过**：两次 run 都挂在
  `UnicodeEncodeError: 'charmap' codec can't encode` —— runner 的控制台是
  cp1252，`build_exe.py` 第一句 `print("=== 环境自检 ===")` 就崩了。
  `build_exe.py` / `make_release.py` / `make_docs_package.py` /
  `tools/audit_features.py` 统一把 stdout/stderr 切到 UTF-8，
  workflow 里也加 `PYTHONUTF8=1`。
- **onefile 打包会把 onedir 产物挪走且不还原**，紧随其后的
  `make_release.py --all` 找不到 onedir，`portable.zip` 被静默跳过
  （CI 的步骤顺序正好踩中）。现在打包成功后自动挪回。
- **`--sign` 之前会签错文件**：onefile 构建完成后 `dist/NAME/` 也在，
  签的却是 onedir 里那个 exe。改成显式传入本次构建产物路径。
- **交付包改用英文文件名**：`-portable.zip` / `-singlefile.zip` /
  `-source.zip` / `-docs.zip`。中文名上传 GitHub Release 会 HTTP 404，
  以前每次发版都得手工改名重传。
- **CI 不再默认自动发布**：正式包是本地用 `tools/sign_release.ps1` 签名后
  上传的（还带 `.cer` 与文档包），CI 产物没签名，自动发布会把已签名附件
  覆盖成未签名版本。需要时在仓库 Variables 里设 `PUBLISH_RELEASE=true`。

### 扫码

- **轮询不再压测网关**：`poll()` 每 2 秒都会发一次真实认证请求
  `auth?str=`，还会跑一轮 3 个地址的连通性探测，10 分钟能发出几百次请求。
  现在 `auth?str=` 最多每 15 秒一次（生成后先留 5 秒给用户扫），
  `probe_online()` 最多每 4 秒一次。

### 测试

- pytest 75 → **84 项**。
- **测试会清掉用户真实凭据**（高危）：`TestCoreController` 直接 `Config()`，
  读写的是 `%LOCALAPPDATA%` 里的**真实配置**，其中 `test_account_masked` 收尾调
  `clear_credentials()` —— 在开发机上跑一次测试，用户保存的 Token / 手机号 / 密码
  就没了。新增 `tests/conftest.py`，把所有测试的数据目录锁定到临时目录。
- 修复项回归测试集中放在 `tests/test_regressions.py`
  （含扫码轮询节流、交付包文件名必须是 ASCII、构建脚本必须切 UTF-8 三项）。

### 仓库

- 删除 `ci/` 目录：`ci/build_windows.yml` 与 `.github/workflows/build_windows.yml`
  内容**完全重复**，`ci/install_workflow.py` 只是一次性推送脚本。
- README 重写：补上缺失的标题与一句话简介，正文压缩到原来的一半，
  深度内容交给 `docs/`。
- `.gitignore` 整理：补 `.pytest_cache/`、`config.json.bak`、`logs/`，去掉重复条目。
- 版本号 1.0.1 → 1.0.2（`version_info.txt` / `make_release.py` / `make_docs_package.py` 三处同步）。
- `build_exe.py`：修掉两处 UTF-8 乱码，删掉与 `version_info.txt` 不同步的
  `VERSION` 常量（exe 属性里写的是旧版本号）。

---

## [1.0.1] — 2026-10-25

### 界面改版

**主界面从 tkinter 桌面窗口换成浏览器里的本地网页。**

- 新增 `webui.py`：本地 HTTP 服务（**只用标准库** `http.server`，不引入 Web 框架）
  - 只监听 `127.0.0.1`，不对外网开放
  - URL 带**一次性随机令牌**，所有 API 校验 `X-Auth-Token`；
    首屏通过 `?t=` 传令牌并种 HttpOnly Cookie
  - 实时日志与状态用 **SSE** 推送
  - 静态资源做**目录穿越**防护
- 新增 `core.py`：业务控制器（认证 / 设备 / 扫码 / 设置 / 账号），
  **与界面完全解耦** —— Web 版和 Tk 版共用同一套认证行为
- 新增 `webui/`：HTML / CSS / JS，无框架、无 CDN，**离线可用**
- `main.py` 瘦身成纯入口；旧 Tk 界面挪到 `tkui.py`，加 `--tk` 参数可切回
- 新增 `autostart.py`：开机自启逻辑从界面层抽出

### 修复

- **目录 ACL 收紧时把自己挡在门外**（高危）：`SetNamedSecurityInfoW` 要的是
  `pDacl`（ACL 指针），代码却传了整个 SECURITY_DESCRIPTOR，Windows 按 ACL
  结构读 SD 头部，得到一个**损坏的空 DACL** —— 目录变成「谁都没权限」，
  连当前用户自己都写不进去，且**程序自己修不回来**。
  改用 `icacls`（先 `/grant:r` 再 `/inheritance:r`），并补 3 条回归测试。
- 静态资源缓存：加 `Pragma: no-cache` + 版本号，避免改了 CSS 浏览器不刷新

### 测试

- pytest 套件 58 → **73 项**，全部通过
  - 新增：Web 服务鉴权 / 目录穿越防护 / 白名单闸门 / 目录 ACL 回归 / 控制器行为

---

## [1.0.0] — 2026-10-25

首个对外可交付版本。包含打包链路加固、任务栏占用控制，
以及一轮外部代码评审的 12 条问题修复。

### 安全（P1-6 / P1-7）

- 凭据存储改用 **Windows DPAPI**（`CryptProtectData`，用户绑定、系统级、随机 nonce、自带完整性校验）；
  DPAPI 不可用时降级为 PBKDF2-HMAC-SHA256(100k) + HMAC-SHA256 认证加密（`v3x:` 前缀）
- 新增 `vault.py`：加密、目录 ACL 收紧（SDDL `D:P`，仅当前用户 / SYSTEM / Administrators）、凭据覆写删除
- 配置数据目录从 `C:\GaoGaoNetAUTO\` 迁到 `%LOCALAPPDATA%\GaoGaoNetAUTO\`；
  旧目录（及 exe 同目录）配置自动迁移一次，并用 DPAPI **重新加密**
- 手机号由明文改为加密存储；日志中一律显示为 `138****8000`
- 新增**网关地址白名单**（`config.ALLOWED_HOSTS` / `ALLOWED_SCHEMES` + 端口范围）：
  保存设置时校验，发请求前**再拦一道**，防止 Token 被误发到非白名单主机
- 网关为明文 HTTP 时，程序**明确提示** Token 会明文过内网
- `capture.py` 只写 Authorization **指纹**（存在性 + 长度 + 末 4 位），响应体中的 Token 串自动打码
- `login_check.py` 默认**交互式**输入口令（不回显、不进 shell 历史）；命令行传密码会显式告警
- 文档停止推荐「导入根证书」与「整目录 Defender 排除项」，改为「更多信息 → 仍要运行」+ 精确到文件的排除

### 可靠性（P0-2 / P0-3 / P1-8 / P1-9）

- 「检查 Token」改为只读体检（调 `onestatus`），**不再改变在线状态**
- 「立即认证」默认 `takeover=False`；抢占需二次确认并列出将被踢掉的设备；空 IP 提前退出
- 扫码状态机重做：结构化提取、去裸 `"success"` 匹配、缓存初始 HTML 不重拉、Event 取消、TTL 对齐、临时文件清理
- 认证结果解析：`_classify` 先判失败再判成功；`_extract_new_token` 收严，只在成功时提取新 Token
- 连通性探测从「TCP 握手」改为**带预期内容的 HTTPS 探测**，302/301 等门户劫持重定向一律不算联网
- 守护线程改盯**出口 IP**（而非网卡 IP），走热点时也能感知地址变化
- 新增权威在线判据 `is_already_online()`（查服务端在线列表）；
  原 `is_nat_mode()` 启发式改为保守判据，只作提示、不再作为跳过认证的依据
- Tk 主线程不再做网络 I/O：`_update_info` / `_refresh_devices` 拆为后台线程 + 队列回主线程渲染
- 修复帮助窗口 `inner.pack` 崩溃
- Token 失效时**回退重试**一次上一版 Token（`token_prev` 从死代码变为真实功能）
- 后台认证 / 抢占 / 扫码流程统一加 `try/finally`：异常时按钮自动恢复、状态不卡死、临时二维码必定清理
- 账号密码自动登录加**熔断**：连续失败 3 次后暂停 5 分钟，避免错误密码反复重试触发服务端风控

### 工程化（P2-10 / P2-11 / P2-12）

- 新增 **58 个 pytest 测试**全部通过（含 DPAPI 往返、白名单闸门、日志脱敏、轮转、账号熔断等）
- 依赖版本全部固定；exe 移出版本控制；新增 `CHANGELOG.md`；文档全面对齐实现并重建 6 份 HTML
- `.github/workflows/build_windows.yml` 真正落地；修正「先出包后打包单文件版」的顺序错误
- 依赖版本全部固定（`requirements.txt`），构建可复现
- `release/` 与 `dist/` 移出版本控制（`.gitignore`），二进制改由 Release 附件分发
- 新增 `logs.py`：统一落盘日志 + 自动轮转（512KB × 3）+ 脱敏
- 清理死代码：`_same_subnet` / `one_status` / `_selfheal_brief` / `_token_brief`，
  以及未用配置键 `keep_token_alive` / `status_url` / `complete_url`
- 新增「清除已保存凭据」按钮（二次确认），一键抹掉 Token / 手机号 / 密码及备份文件
- 文档全面对齐实现：需求说明书 / 生产交接文档 / 使用教程 / Defender 排查，并重建 6 份 HTML

### 打包与分发

- 目录版（onedir）为主推形态，单文件版（onefile）备用
- 去除 UPX、加入版本资源、自签名（SHA256 + 可信时间戳）
- 产物特征自检（`tools/audit_features.py`）接入构建流程，不通过直接失败

### 已知遗留

- `main.py` / `auth.py` 仍是 God Object，待拆分
- 70+ 处 `except Exception: pass` 待统一异常类型
- 校园网网关为明文 HTTP（协议决定，厂商侧支持才能改）
- 自签名不等于 SmartScreen 放行，根治需采购正规证书

[1.0.1]: https://github.com/zhentou777777/GaoGaoNetAUTO-Python/releases/tag/v1.0.1
[1.0.0]: https://github.com/zhentou777777/GaoGaoNetAUTO-Python/releases/tag/v1.0.0
