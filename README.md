# 糕糕校园网自动认证

**Windows 上的云高校园网自动认证工具**：开机自动联网、断网自动重连、全程不用掏手机。

![platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078D6?logo=windows&logoColor=white)
![python](https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white)
![license](https://img.shields.io/badge/license-MIT-green)
[![release](https://img.shields.io/github/v/release/zhentou777777/GaoGaoNetAUTO-Python)](https://github.com/zhentou777777/GaoGaoNetAUTO-Python/releases)

- 只依赖标准库风格的本地网页界面（`127.0.0.1` + 一次性令牌），无框架、无 CDN、离线可用
- Token / 手机号 / 密码用 **Windows DPAPI** 加密落盘，配置文件里只有密文
- 账号密码可自动续 Token，断网、换网络、Token 轮换都能自愈，不需要重新抓包

---

## 三十秒上手

1. 从 [Releases](https://github.com/zhentou777777/GaoGaoNetAUTO-Python/releases) 下载 `GaoGaoNetAUTO.exe`，双击运行
2. **先连上校园网**（状态卡能看到出口 IP），点「扫码登录」→ 手机糕糕 APP 扫一下 → 立刻联网
3. 想以后彻底不掏手机：设置区填云高手机号 + 密码 → 「登录并保存」→ 勾选「开机自动认证」

找不到界面？打包版没有控制台，**右键托盘图标 → 打开界面**（地址也写在
`%LOCALAPPDATA%\GaoGaoNetAUTO\webui.url`）。

---

## 它是怎么做到「免手机」的

云高认证是一个 REST 接口，服务端只认请求头里的 `Authorization: Bearer <Token>`。

| 接口 | 语义 | 需要的凭据 |
|---|---|---|
| `GET /api/network/auth?str=<code>` | 授权「刚刚被手机同意的那次会话」 | Token + 一个已被手机授权的 code |
| `GET /api/network/auth?ip=<IP>` | **直接把指定 IP 挂进账号的在线设备** | **只要 Token** |

`code` 只是网页流程里「这次授权给哪台设备」的一次性载体。走 `auth?ip=` 就绕过了整条扫码链，
手机于是退化成一次性的「Token 搬运工」。之后每次上线，PC 自己带 Token 调接口即可。

```
手机导出 Token（一次）→ PC 本地加密保存 → PC 调 auth?ip= → 云高放行
                                     ↑____ 断网 / 开机时自动重放，手机不再参与
```

接口逆向的完整过程（含抓包结论、XOR 混淆的二维码载荷、为什么页面没有轮询接口）
都在 **[技术路径](docs/技术路径.md)**。

---

## 单设备限制怎么办

云高账号同时只能有 1 台设备在线，所以 PC 认证可能顶掉手机 —— 这是套餐的必然结果，不是 bug。
程序对此的处理：

- **四级认证链**：`auth?ip=PC` → `logout?ip=PC` 后重试 → 顶替手机 → 明确归因报错
- **「归还名额」**：一键把名额还给手机
- **默认不抢**：「立即认证」走非抢占路径；要抢占必须显式点「抢占上线」并二次确认被下线的 IP
- **手机热点共存**：电脑连手机热点时两者出口 IP 相同，程序查到「本机出口 IP 已在线」就直接判定成功，
  **不会重复认证、也就不会把手机挤掉**

> 单名额下 MAC 一变就算新设备，建议对校园 Wi-Fi 关闭 Windows 的「随机硬件地址」。
> 程序每次都重新探测 IP / MAC，绝不硬编码。

---

## Token 失效了怎么办：三层自愈

| 层 | 机制 | 你的成本 |
|---|---|---|
| **L0** | **账号密码自动换 Token**（推荐）：填一次手机号 + 密码，Token 失效时程序自己去云高换 | 0 |
| **L1** | 服务端下发新 Token 时自动解析续存；当前 Token 失效还会回退试上一版 | 0 |
| **L2** | Token 失效且没有账号密码 → 主动弹二维码 | 手机扫一下 |
| **L3** | 重新抓包 | 走一次抓包流程 |

L0 走的是 `POST https://user.v4.yungao-tech.com/api/login`，返回码语义（`400 参数不能为空` /
`408 手机号不合法` / `401 账号不存在` / `401 密码错误`）已在 `acclogin.py` 里区分成人类可读的提示。

想在命令行上先验证账号能不能登录（不保存任何东西）：

```bash
python login_check.py        # 交互输入手机号与密码，密码不回显、不进 shell 历史
```

想知道自己学校到底轮不轮换 Token：

```bash
python tokenlab.py
```

不填账号密码、坚持手动抓 Token 也可以：步骤见
[使用教程 · 备用方案：抓包取 Token](docs/使用教程.md)（浏览器 DevTools 这条路取不到，
Token 只在糕糕 APP 的流量里）。

---

## 运行与构建

**从源码运行**

```bash
git clone https://github.com/zhentou777777/GaoGaoNetAUTO-Python.git
cd GaoGaoNetAUTO-Python
pip install -r requirements.txt
python main.py          # 默认 Web 界面；加 --tk 走旧版 Tk 窗口
```

**自己打包**

```bash
python build_exe.py                     # onedir，产物 dist/GaoGaoNetAUTO/
python build_exe.py --onefile           # 单文件
python make_release.py --all            # 三种形态的 zip，输出到 release/
python -m pytest tests/ -q              # 测试（80 项）
```

产物一律写到项目内的 `release/`，不会污染桌面。

**CI**：`.github/workflows/build_windows.yml` —— 推 `v*` tag 时自动跑测试 → 打包 → 产物特征审计
→ 生成 SHA-256 → 发 Release。

---

## 配置与数据

| 项 | 位置 / 说明 |
|---|---|
| 配置文件 | `%LOCALAPPDATA%\GaoGaoNetAUTO\config.json`（与 exe 放哪无关，目录 ACL 收紧到当前用户 + SYSTEM + Administrators） |
| 凭据 | Token / 手机号 / 密码均为 DPAPI 密文，换机器或换用户都解不开；可在「清除已保存凭据」一键抹掉（含备份与二维码残留） |
| 日志 | `%LOCALAPPDATA%\GaoGaoNetAUTO\logs\app.log`，自动脱敏 + 轮转 |
| 开机自启 | 「启动」文件夹里的一个 `.cmd`（不用注册表 Run 项，避免杀软高危特征） |
| 界面 | 本地 HTTP 服务，只监听 `127.0.0.1`，URL 带一次性令牌 |

---

## 常见问题

**提示「Token 已失效」** 看日志里的过程信息。名额被占就填「手机 IP」；Token 真轮换了就重新抓一次，
期间先用「扫码登录」顶一下。

**抓包看不到明文** 多半是 APP 启用了 SSL Pinning，直接用「扫码登录」，Token 只是让你少掏手机。

**被杀毒软件拦了** 见 [Defender 误报排查](docs/Defender-误报排查.md)：首选「更多信息 → 仍要运行」，
默认无需导入证书。

**手机和电脑能同时上网吗** 服务端只允许一台。电脑连手机热点时共用出口 IP，程序会跳过重复认证；
除此之外本项目不做 ICS / 路由器共享。

---

## 文档

| 文档 | 内容 |
|---|---|
| [使用教程](docs/使用教程.md) | 下载、配置、开机自启、故障排查 |
| [技术路径](docs/技术路径.md) | 接口逆向全过程、协议分析、自愈架构 |
| [需求说明书](docs/需求说明书.md) | 需求台账、功能 / 接口规格、验收标准 |
| [生产交接文档](docs/生产交接文档.md) | 构建发布、部署、Runbook、风险登记 |
| [Defender 误报排查](docs/Defender-误报排查.md) | 被杀软拦了怎么一步步自查 |
| [CHANGELOG](CHANGELOG.md) | 版本变更记录 |

---

## 致谢与许可

- 接口语义参考并逆向自 [JasonYANG170/GaoGaoNetAUTO](https://github.com/JasonYANG170/GaoGaoNetAUTO)（GPL-2.0）。
  本项目为**独立实现**，未复制其源码，仅研究其公开的接口调用方式。
- 云高校园网认证接口归广州云糕科技有限公司所有，本项目仅供个人学习使用，请遵守学校与厂商的相关规定。
- 本项目代码以 **MIT** 协议开源，详见 [LICENSE](LICENSE)。
