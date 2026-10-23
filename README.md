# 糕糕校园网自动认证（GaoGaoNetAUTO-Python）

云高（广州云糕科技）校园网的 Windows 桌面自动认证工具。
**开机自启、断网自动重连、日常全程免手机。**

> 起因：GitHub 上同名的 `JasonYANG170/GaoGaoNetAUTO` 是一个 C++/Qt 项目，
> 仓库里没有编译好的 exe，且把 `clientip / clientmac / wlanacname / paip`
> 全部硬编码成了作者自己学校的值，普通用户基本跑不起来。
> 本项目用 Python 重新实现，**接口语义保持一致，但把硬编码全部改成运行时探测**，
> 并额外做了 Token 自愈、二维码兜底、单设备限制适配。

---

## 📖 使用教程

**完整图文教程：[docs/使用教程.md](docs/使用教程.md)**

三十秒速览：

1. 下载 `GaoGaoNetAUTO.exe` 双击运行
2. **先连上校园网**（状态卡上能看到出口 IP）
3. 点「扫码登录」→ 用糕糕 APP 扫一下 → 立刻联网

之后想彻底不掏手机，再按教程抓一次 Token 填进去，勾上开机自启。

---

## 一、它是怎么做到"免手机"的

云高认证是一个 REST 接口，服务端只认请求头里的 **`Authorization: Bearer <Token>`**。

原生扫码流程（手机必须在场）：

```
PC 生成 code  →  手机扫码并用 Token 授权 code  →  云高标记 code 已授权  →  PC 绑定 IP 上线
```

但接口层其实有**两个等价入口**：

| 接口 | 语义 | 需要的凭据 |
|---|---|---|
| `GET /api/network/auth?str=<code>` | 授权「刚刚被手机同意的那次会话」 | Token + 一个已被手机授权的 code |
| `GET /api/network/auth?ip=<IP>` | **直接把指定 IP 挂进账号的在线设备** | **只要 Token** |

`code` 只是网页流程里传递"这次授权给哪台设备"的一次性载体。
走 `auth?ip=` 就把 code 整环跳过了——**Token 替代了手机**。
手机因此退化成一次性"Token 搬运工"，此后每次上线 PC 自己带 Token 调接口即可。

```
手机导出 Token(一次)  →  PC 本地加密保存  →  PC 调 auth?ip=  →  云高放行
                                          ↑______ 断网/开机时自动重放，手机不再参与
```

---

## 二、账号"只能登录一台设备"怎么办

云高账号同时只能有 1 台设备在线，所以：

- **PC 认证会顶掉手机**，手机上的校园网会掉线（这是单名额的必然结果，不是 bug）。
- 手机在 APP 里重新认证，又会把 PC 顶掉，**并可能导致 Token 轮换**。

程序对此做了三件事：

1. **四级认证链**，逐级回退，覆盖服务端"踢旧设备"和"拒绝新设备"两种行为：

   ```
   auth?ip=<PC_IP>                        直接认证
     ↓ 失败
   logout?ip=<PC_IP> → auth?ip=           清理本机陈旧会话后重试
     ↓ 仍失败（判定名额被占）
   logout?ip=<手机IP> → auth?ip=          自动顶替手机（需在设置里填一次手机 IP）
     ↓ 仍失败
   明确报错：区分「Token 已失效」与「账号被其他设备占用」
   ```

2. **「归还名额」按钮**：一键把在线名额还给手机（`logout?ip=<PC_IP>`）。
3. **只做 PC 独占**，不实现热点/ICS/路由器共享。手机需要上网时用「归还名额」，
   或自己在糕糕 APP 里下线 PC。

> 另：单名额下，MAC 地址一变就被当成新设备，更容易撞上限制。
> 建议对校园 Wi-Fi 关闭 Windows 的「随机硬件地址」
> （设置 → 网络和 Internet → Wi-Fi → 属性 → 随机硬件地址 → 关闭）。
> 程序本身按"保持随机"设计，每次运行都会重新探测当前 IP/MAC，绝不硬编码。

---

## 三、Token 失效了怎么办（三层自愈）

Token 可能因为「在 APP 内退出账号」或「发生设备登录」而失效/轮换。
程序不会让你卡在抓包上：

| 层 | 机制 | 你的成本 |
|---|---|---|
| **L1** | 认证响应里若下发新 Token，程序**自动解析并存下来**（服务端轮换 Token 时无缝续命） | 0 |
| **L2** | Token 失效时**自动降级到二维码模式**，这条路径完全不依赖 Token | 手机扫一下 |
| **L3** | 重新抓包 | 走一次抓包流程 |

另外面板会显示 **Token 健康状态**，提前预警，而不是等你断网才发现。

想用数据说话？可以跑一次实测脚本：

```bash
python tokenlab.py
```

它会：Token 认证一次 → 让你在手机上做一次设备登录 → 再用同一 Token 认证 →
直接给出「你这学校到底轮不换 Token」的结论。

---

## 四、快速开始

### 方式 A：直接用 exe（推荐）

1. 从 [Releases](https://github.com/zhentou777777/GaoGaoNetAUTO-Python/releases) 下载 `GaoGaoNetAUTO.exe`。
2. 双击运行（绿色的是主窗口，右上角 ✕ 隐藏到托盘，右键托盘图标可退出）。
3. **先连上校园网**，然后：
   - 想马上上网 → 点「扫码登录」→ 手机糕糕 APP 扫一下 → 完成；
   - 想以后完全免手机 → 按下面「获取 Token」取一次 Token，粘贴后点「校验并保存」。
4. 勾选「开机自动认证」→ 以后开机自动联网。

### 方式 B：从源码运行

```bash
git clone https://github.com/zhentou777777/GaoGaoNetAUTO-Python.git
cd GaoGaoNetAUTO-Python
pip install -r requirements.txt
python main.py
```

### 方式 C：自己打包 exe

```bash
pip install -r requirements.txt pyinstaller
python build_exe.py
# 产物：dist/GaoGaoNetAUTO.exe
```

### 方式 D：启用 GitHub Actions 自动构建（可选）

`ci/build_windows.yml` 是预置的 Windows 自动构建工作流（推 tag 自动出 exe）。

> GitHub 要求写入 `.github/workflows/` 的 OAuth token 必须带 `workflow` scope，
> 所以它默认**不随仓库一起启用**。启用方式（任选其一）：
>
> ```bash
> # 方案 1：交互式补授权（推荐）
> python ci/install_workflow.py
>
> # 方案 2：手动新建带 workflow scope 的 PAT 后替换 remote
> git remote set-url origin https://<PAT>@github.com/zhentou777777/GaoGaoNetAUTO-Python.git
> ```
>
> 不想折腾 CI 也完全没关系——本机跑 `python build_exe.py` 一样能出 exe。

---

## 五、扫码登录是怎么做到"不依赖 Token"的

这条兜底路径完全独立于 Token，实测逆向了云高网页的实现细节：

1. `GET /web/network/code` 会 **301 到自身**并下发 `Set-Cookie: str=<UUID>`，
   这个 UUID 就是本次授权会话的标识，10 分钟有效。
2. 页面把二维码内容用 **XOR 混淆** 内嵌在 JS 里：

   ```js
   var encoded = [253,172,255, ...];
   var key    = 158;
   var str = encoded.map(function (c) {
       return String.fromCharCode(c ^ key);
   }).join('');
   qrcode.makeCode(str);
   ```

   解码结果与 `str` cookie **完全一致**（UUID 形式）。
   所以本项目能**纯本地渲染二维码**，不必去扒服务端图片——
   `key` 每个会话随机生成，本地解码器按同一算法还原即可。
3. 页面里**没有任何状态轮询接口**。手机在 APP 里确认后，
   服务端直接把「该 session 对应的 IP」置为已上线。
   因此判定"授权是否完成"的正确信号不是某个轮询接口，而是
   **本机是否已经能连通外网**（`netguard.probe_online`）。

这套机制下，扫码路径的完整闭环是：

```
PC 拉页面 → 拿 str(UUID) → 本地渲染二维码 → 手机 APP 扫 → 服务端放行本机 IP → PC 检测到已联网 → 完成
```

程序还会在轮询时顺带调用 `auth?str=<UUID>` 做一次确认（若有 Token），
两者任一成立即判定授权成功。

---

## 六、获取 Token（安卓 SSL 抓包）

> 云高网页登录流程本身**不带** `Authorization` 头，所以**从浏览器 DevTools 复制这条路多半取不到**。
> Token 只存在于糕糕 APP 的网络流量里。

1. 安卓手机安装 **抓包精灵** 或 **HttpCanary**（任选其一）。
2. 打开抓包工具 → 允许安装 CA 证书：
   - 抓包精灵：设置 → 证书安装 → 安装到「系统证书」（部分机型需在系统设置里手动信任）；
   - HttpCanary：设置 → 证书 → 安装。
3. 开始抓包 → 打开 **糕糕 APP**，等几秒让它产生网络请求。
4. 在抓包列表里按域名筛选 **含 `auth.yungao`** 的那条请求。
5. 点开请求 → **请求头 / Request Headers** → 找到 `Authorization`：
   ```
   Authorization: Bearer eyJhbGciOi...
                       ^^^^^^^^^^^^^^^^^ 从这里开始复制，到行尾为止
   ```
6. 粘贴到主程序左下角的 Token 输入框 → 点「校验并保存」。

> 如果 HttpCanary 提示证书固定（Certificate pinning）导致看不到明文，
> 说明该 APP 启用了 SSL Pinning，普通抓包无法解密。
> 这时可以：① 换用「抓包精灵」等带 SSL Unpinning 的工具；
> ② 放弃 Token，直接用「扫码登录」模式（**完全不需要 Token**）。

**注意：不要在糕糕 APP 里退出账号**，退出后 Token 会立即失效。

---

## 七、界面说明

| 区域 | 说明 |
|---|---|
| 状态卡 | 大圆点显示当前状态（灰=未认证 / 黄=认证中 / 绿=在线 / 红=失败），右侧显示出口 IP |
| 立即认证 | 执行四级认证链 |
| 扫码登录 | 打开二维码窗口，手机扫一下即可（不依赖 Token） |
| 归还名额 | 把账号的在线名额还给手机 |
| 检查 Token | 轻量体检，验证 Token 是否还有效 |
| 设置 · Token | 粘贴 / 校验 / 保存 Token（本地加密存储，不落明文） |
| 设置 · 手机 IP | 单设备限制下用于自动顶替手机，填一次即可 |
| 设置 · 认证网关 | 不同学校的主机端口不同，默认 `hgs.auth.yungao-tech.com:1110` |
| 设置 · 开机自动认证 | 写注册表 `Run` 项实现开机自启 |
| 运行日志 | 所有认证尝试、失败原因、降级过程都记录在这里 |

配置文件 `config.json` 生成在 exe 同目录，Token 以机器指纹派生的密钥加密后存储，
换机器/换用户读出来的密文无法解密。

---

## 八、常见问题

**Q：提示"Token 已失效"？**
先看日志里的过程信息。若是账号名额被占，在设置里填上「手机 IP」；
若是 Token 真的轮换了，重新抓一次即可（期间可以用「扫码登录」顶一下）。

**Q：提示"名额被占用"？**
你的账号同时只能有一台设备在线。把手机 IP 填进设置，程序会自动顶替；
或者先在 APP 里下线手机。

**Q：能不能手机和电脑同时上网？**
云高单设备限制下服务端只允许一台。理论上可以让 PC 做 NAT 共享给手机，
但本项目按「PC 独占」设计，没有实现共享功能。

**Q：抓包打不开 / 看不到明文？**
多半是 APP 启用了 SSL Pinning，用「扫码登录」模式即可绕过，
Token 只是让你少掏手机，不是必需品。

**Q：抓取模式 capture.py 是干嘛的？**
用于分析网页扫码流程中"手机确认之后 PC 靠哪个调用完成上线"，
以便把零 Token 的扫码助手彻底做实。需要额外装 Playwright：

```bash
pip install playwright
python -m playwright install chromium
python capture.py
```

---

## 九、致谢与许可

- 接口语义参考并逆向自 **[JasonYANG170/GaoGaoNetAUTO](https://github.com/JasonYANG170/GaoGaoNetAUTO)**（GPL-2.0）。
  本项目为**独立实现**，未复制其源码，仅研究其公开的接口调用方式。
- 云高校园网认证接口归 **广州云糕科技有限公司** 所有，本项目仅供个人学习使用，请遵守学校与厂商的相关规定。
- 本项目代码以 **MIT** 协议开源，详见 [LICENSE](LICENSE)。
