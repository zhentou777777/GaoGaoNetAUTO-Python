# CHANGELOG

本文件记录糕糕校园网自动认证（GaoGaoNetAUTO-Python）的变更。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

---

## [1.0.5] — 2026-10-31

### 修复

- **配置写盘改成原子替换**（`config.py`）：原先是直接覆盖 `config.json`，
  写到一半被杀进程 / 断电就留下半截 JSON —— 下次启动读不出来，用户看到的
  是「凭据凭空消失」，而且备份文件是同样的写法，一起坏。现在先写
  `config.json.tmp` 再 `os.replace`，写失败时旧配置完好无损。
- **配置读坏不再静默**（`config.py`）：半截 JSON / 空文件以前被 `continue`
  悄悄跳过，表现为「怎么又要重新登录」。现在记进 `read_error`，启动日志
  里能直接看到「config.json 内容损坏」。
- **只改网关地址会把开关一起关掉**（`core.py`）：`save_settings` 里
  `autostart` / `auto_kick_phone` 原先是无条件 `bool(data.get(...))`，
  请求里没带这两个字段就等于把它们设成 `False`。现在只在请求带了才写。
- **账号密码换到的 Token 会被丢掉**（`auth.py` / `core.py`）：自愈链路
  换到新 Token 后，如果紧接着那次认证失败，Token 就没了 —— 下次启动又要
  再换一次，白白多一次登录（服务端风控会记账，连续失败会熔断）。现在换到
  就立刻落盘，并把旧的留作 `token_prev`；同时把 `core` 的续存判断改成和
  「当前已存的 Token」比，避免把 `token_prev` 写成新 Token 自己、让回退
  兜底失效。
- **Web 版第二次双击没反应**（`main.py`）：单实例锁拦下第二次启动后，
  原代码去 `FindWindow` 找 Tk 窗口 —— Web 版根本没有窗口，什么都找不到，
  于是静默返回。现在改为打开已有实例的 `webui.url`（带令牌）。
- **单实例锁创建失败会静默退出**（`taskbar.py`）：`CreateMutexW` 返回
  NULL 时原逻辑返回 `False`（当作「已有实例」），程序直接退出、用户
  双击多少次都没反应。现在创建失败就放行。
- **Cookie 鉴权用子串匹配**（`webui.py`）：`f"gg_token={token}" in cookie`
  改成逐项解析 + `compare_digest`，避免伪造的、把真 token 当子串的
  cookie 混过去。
- **`index.html` 缺失时甩 traceback**（`webui.py`）：改成 500 + 人话提示。
- **连点「刷新」会跑出两个扫码轮询**（`core.py`）：取消标记改成通过参数
  传给线程，不再在线程里回头读 `self._qr_cancel`（那时可能已被下一次
  刷新换掉）。
- **历史配置迁移会污染临时目录**（`config.py`）：`Config(path)` 显式指定
  文件、或设了 `GAOGAO_DATA_DIR`（便携 / 测试）时不再从
  `C:\GaoGaoNetAUTO` 搬旧配置 —— 否则测试结果随「这台机器有没有装过
  旧版」而变。

### 测试

- 101 → 103 项，新增覆盖：配置损坏留痕、主备回退、原子写失败保护、
  部分更新语义、Token 轮换落盘、Cookie 精确比对。

## [1.0.4] — 2026-10-31

### 修复

- **自启写入再稳一层**（`autostart.py`）：v1.0.3 的兜底只有「直写 → 临时
  文件替换」两条路，仍可能被瞬时拦截打成失败。现在：
  - 内容已经一致就**不写** —— 白挨一次杀软扫描不说，还可能「明明已经生效
    却报失败」，然后 core 把配置里的开关拨回「关」，下次启动再白忙一遍；
  - 三条写法轮换（直写 / 临时文件替换 / 挪开再建），三种写法的权限要求
    不一样，一条被拦不代表全被拦；
  - 失败后隔 120/240ms 重试，共 3 轮（这类拦截基本是毫秒级抖动）；
  - 全部失败时**复查文件实际内容**，已经对了就当成功；
  - 报错里带上启动项的完整路径和「文件是否存在」，并给出
    `icacls <路径> /reset` 这条恢复命令（空 DACL 的情况）。
- **Tk 界面的自启实现删掉了**（`legacy/tkui.py`）：那是第二份代码，没有
  重试、没有兜底、也不清只读属性，同一个动作在两套界面上成功率不一样。
  现在只转发给 `autostart` 模块。
- **配置写不进去不再静默**（`config.py` / `core.py`）：`save()` 以前吞掉
  所有异常，界面照样提示「Token 已保存」，重启后又要重新登录，完全查不出
  原因。现在 `save()` 返回是否成功，`Config.storage_problem()` 给出
  「读不了 / 写不进去」的原因，启动时写进日志并打印配置文件路径。
- **启动检查根本没被调用**（`main.py`）：`Controller.startup_checks()`
  定义了却没人调，于是「自启开关开着却不会自动认证」「配置里的自启状态和
  启动文件夹里的文件长期对不上」，启动过程日志也一行不写。现在 Web 界面
  起来后在后台跑它（它会发网络请求，不能挡住界面）。
- **开机自启不再弹浏览器**（`main.py`）：`--silent` 启动时跳过
  `open_browser()`，入口走托盘「打开界面」。
- **测试里 Tk 变量的回收顺序**（`tests/test_gaogao.py`）：`Variable.__del__`
  会在 Tcl 解释器销毁后才被 GC 调到，抛 unraisable 异常，还会让后面的
  Web HTTP 测试莫名超时。改为先回收变量再 `destroy()`。

## [1.0.3] — 2026-10-28

### 修复

- **开机自启写入失败时报一句 Errno 13 就没了**（用户实际踩到：
  `PermissionError: [Errno 13] ... Startup\GaoGaoNetAUTO.cmd`）。
  「启动」文件夹被拒多半不是 Windows 权限，而是安全软件（360 / 火绒的
  「启动项保护」）或文件被占用。现在：
  - 先清掉文件的只读/隐藏属性再写；
  - 直接写被拒时兜底走「同目录临时文件 + `os.replace`」，替换只要求对目录
    有删除权限，很多只拦「打开文件写入」的方案放的是后者；
  - 仍然失败时给出人能照做的提示（`Win+R` → `shell:startup` 手动放快捷方式）；
  - 开失败就把配置里的开关拨回去 —— 否则每次启动都要再失败一次，
    用户还以为自启已经生效。
- **自启条目过期识别不出来**：只看「文件存在」，程序被移走后会开机静默启动
  一个不存在的路径。新增 `autostart_stale()`，只把「指向的程序不在了」判过期
  （仅仅是源码/打包版路径不同不算，否则启动项会来回翻烧饼）。
- **自启开关的失败原因不回给界面**：只在日志里，用户看到的就是勾选又被取消。
  现在 `保存设置` 会把失败原因一起返回。

### 测试

pytest 84 → **89 项**（开机自启的兜底写入、过期识别、失败回滚配置开关）。

### 文档与版本

- 文档里的交付包文件名同步到 v1.0.3（`生产交接文档` / `Defender-误报排查`），
  HTML 由 `tools/make_docs_package.py` 重新生成。
- 版本号 1.0.2 → 1.0.3（`version_info.txt` / `tools/make_release.py` /
  `tools/make_docs_package.py` 三处同步）。

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
- 版本号 1.0.1 → 1.0.2（`version_info.txt` / `tools/make_release.py` / `tools/make_docs_package.py` 三处同步）。

### 仓库整理

- **构建 / 调试脚本全部收进 `tools/`**：`build_exe.py` / `make_release.py` /
  `make_docs_package.py` / `make_launchers.py` / `capture.py` / `tokenlab.py` /
  `login_check.py`（原来散在根目录，根目录一度有 24 个 `.py`）。
  根目录现在只剩**运行时模块**，`tools/README.md` 里有一张「这脚本是干嘛的」表。
- 脚本移走后 `HERE` 改为指向项目根（`parent.parent`）；`build_exe.py` 额外把
  项目根插进 `sys.path`，否则 `import icons` 会失败（图标静默不生成）。
- **`tkui.py` → `legacy/tkui.py`**：旧版 Tk 界面只是「没浏览器时的兜底」，
  2147 行不该占着根目录。`--tk` 改为**仅源码运行可用**（打包版只带 Web 界面，
  Tk 要拖进整套 tcl/tk），并在打包版里给出明确提示而不是
  ModuleNotFoundError —— 顺带修掉一个隐藏 bug：惰性 import 从来没被打包器
  静态分析抓到，打包版的 `--tk` 其实是坏的。
- **`docs/*.html` 不再入库**：那是 `docs/build_html.py` 从 Markdown 生成的副本，
  入库只会和 md 打架（改一次文档要记得重建，忘了就是旧内容）。
  改成 `tools/make_docs_package.py` 打包前自动生成。
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

[1.0.5]: https://github.com/zhentou777777/GaoGaoNetAUTO-Python/releases/tag/v1.0.5
[1.0.4]: https://github.com/zhentou777777/GaoGaoNetAUTO-Python/releases/tag/v1.0.4
[1.0.3]: https://github.com/zhentou777777/GaoGaoNetAUTO-Python/releases/tag/v1.0.3
[1.0.2]: https://github.com/zhentou777777/GaoGaoNetAUTO-Python/releases/tag/v1.0.2
[1.0.1]: https://github.com/zhentou777777/GaoGaoNetAUTO-Python/releases/tag/v1.0.1
[1.0.0]: https://github.com/zhentou777777/GaoGaoNetAUTO-Python/releases/tag/v1.0.0
