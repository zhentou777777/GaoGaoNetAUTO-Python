# Windows Defender 误报排查手册

> 适用版本：糕糕校园网自动认证 v1.0.0
> 场景：Windows  Defender 拦截下载 / 拦截运行 / 提示「已阻止」

---

## 0. 一句话结论

本程序是**纯校园网认证工具**，不联网下载、不改系统配置、不注入别的进程、不读写用户文件（除自己的配置目录）。

但它是 **PyInstaller 打包的 GUI 程序 + 自启动 + 采集网卡信息**，这三个特征叠加，正好落在 Defender 启发式规则的判定区间里。所以误报是可预期的，处置也有章可循。

**本版本已经做的降误报处理**（对照下表左列）：

| 曾经的特征 | Defender 规则命中 | 现在 |
|---|---|---|
| PyInstaller **onefile**（自解压到 `%TEMP%\_MEIxxxx`） | 自解压落盘 + 大量文件写入 | ✅ 改为 **onedir**，不再往临时目录解包 |
| **UPX 加壳** | 加壳可执行文件（启发式高权重） | ✅ 构建时 `--noupx` |
| 写注册表 `HKCU\...\CurrentVersion\Run` 自启动 | **T1547.001 注册表自启动/持久化** | ✅ 改为「启动」文件夹放 `.cmd` |
| 起 `ipconfig` 子进程采集网卡 | 进程创建 + 信息采集组合 | ✅ 改为 `iphlpapi.dll` ctypes 直调，**零子进程** |
| 读 `MachineGuid` 设备指纹 | 信息收集 | ✅ 改为轻量指纹（主机名 + MAC + 用户名 + 架构） |
| 无版本资源 | 「未知发布者」外观 | ✅ 写入 Windows 版本资源 |
| 无数字签名 | 信誉分不足 | ✅ 交付前自签名（见 §4） |

---

## 1. 先分清是"哪一种"拦截

| 现象 | 发生在哪一步 | 典型提示 |
|---|---|---|
| **A. 下载被拦** | 浏览器/微信/QQ 下载 zip 或 exe 时 | "已阻止下载"，"此文件已被删除" |
| **B. 运行被拦** | 双击 exe 时 | "Windows 已保护你的电脑"，"已阻止应用" |
| **C. 运行时被杀** | 程序已启动，被 Defender 终止 | 进程莫名消失，日志中断 |
| **D. 智能屏幕** | 别的电脑首次运行 | "Windows 保护了你的电脑"，蓝底白框 + "更多信息" |

**A/B/C/D 的处置完全不同**，先看清楚是哪一种再动手。

---

## 2. 情况 A：下载被拦

### 2.1 原因

Defender 的 **Download Protection（下载保护）** 会扫描压缩包内的可执行文件。zip 里的 `.exe` 被标记时，整个 zip 都会被删。

### 2.2 处置

**方案 1（推荐）：点「更多信息」→「仍要运行」**

这是**最安全**也最省事的做法：只放行这一个文件，不改变系统的整体防护。
SmartScreen/Defender 记住这个哈希后，同一个文件再运行就不会再拦。

**方案 2：只排除这一个文件（不排除整个目录）**

如果每次运行都被拦，可以用 Defender 的**文件级排除**。注意用
`Add-MpPreference -ExclusionPath` 排除的是**路径**，粒度最粗是目录；
想更精确就排除具体文件：

```powershell
# 管理员 PowerShell —— 只排除这一个 zip / exe，不要排除整个下载目录
Add-MpPreference -ExclusionPath "D:\Downloads\GaoGaoNetAUTO-v1.0.0-免安装.zip"

# 确认已生效
Get-MpPreference | Select-Object -ExpandProperty ExclusionPath
```

> ⚠️ **不要排除整个磁盘或整个下载目录** —— 那等于给这一整块地方开了免检区，
> 任何落进去的可执行文件都不再被检查。**排除范围越窄越好**，
> 理想情况是排除这一个具体文件。
>
> ❌ `Add-MpPreference -ExclusionPath "D:\Downloads"`   ← 范围过大，不推荐
> ✅ `Add-MpPreference -ExclusionPath "D:\Downloads\GaoGaoNetAUTO-v1.0.0-免安装.zip"`  ← 精确到文件

**方案 3：临时关闭实时保护（仅限你完全确认来源时）**

```powershell
Set-MpPreference -DisableRealtimeMonitoring $true
# 下载完成后务必改回来
Set-MpPreference -DisableRealtimeMonitoring $false
```

> ⚠️ 关闭期间不要用其他不认识的程序。这是临时手段，不是解决方案。

**方案 4（长期正解）：让下载方加签名**
用 Azure Trusted Signing 签的包，Defender 不会在下载阶段拦。长期方案见 §6。

### 2.3 顺手检查：MOTW（Mark of the Web）

从浏览器/微信下载的文件带 `Zone.Identifier` 标记，Windows 会额外弹「安全警告」。解除：

```powershell
Unblock-File -Path "D:\Downloads\GaoGaoNetAUTO-文档包-v1.0.0.zip"
Get-Item "D:\Downloads\GaoGaoNetAUTO-文档包-v1.0.0.zip" -Stream *   # 确认已无 Zone.Identifier
```

---

## 3. 情况 B：运行被拦 / 情况 C：运行时被杀

### 3.1 情况 B —— 「Windows 已保护你的电脑」

这是 **SmartScreen**，不是 Defender 本体。SmartScreen 查的是**文件信誉**，新文件默认没有信誉。

**处置（按推荐顺序）：**

**① 点「更多信息」→「仍要运行」**（最简单，当场可用）

**② 本机永久放行：**

```powershell
# 管理员 PowerShell —— 精确到 exe 文件
Add-MpPreference -ExclusionPath "D:\Tools\GaoGaoNetAUTO\GaoGaoNetAUTO.exe"
```

> ❌ 不要写 `-ExclusionPath "D:\Tools\GaoGaoNetAUTO"`（整目录）——那等于给整个目录开免检，范围过大。

**③ 解除 MOTW 后再运行**（如果是复制/解压出来的文件）

```powershell
Get-ChildItem "D:\Tools\GaoGaoNetAUTO" -Recurse | Unblock-File
```

### 3.2 情况 C —— 运行中被终止

打开 Defender 的保护历史：

```powershell
Get-MpThreatDetection | Format-List *
Get-MpThreat | Format-List *
```

重点看：

| 字段 | 含义 |
|---|---|
| `ThreatName` | 命中的规则名（如 `Behavior:Win32/DefenseEvasion`） |
| `Resources` | 被判定的具体文件路径 |
| `ActionSuccess` | 是否真的拦下了 |

**如果 `Resources` 指向 `%TEMP%` 下的 `_MEI*` 目录** → 说明还在用 onefile，需要用本版本的 onedir 包。

**如果 `Resources` 指向 exe 本身** → 把该 exe 目录加排除项，或看 `ThreatName` 对应的规则。

### 3.3 用 Defender 自带命令扫描确认

```powershell
# 扫描某个文件（返回码 0=干净，2=有威胁）
& "$env:ProgramFiles\Windows Defender\MpCmdRun.exe" -Scan -ScanType 3 -File "D:\Tools\GaoGaoNetAUTO\GaoGaoNetAUTO.exe"

# 看 Defender 认为有威胁的目录
& "$env:ProgramFiles\Windows Defender\MpCmdRun.exe" -Scan -ScanType 3 -File "D:\Tools\GaoGaoNetAUTO"
```

---

## 4. 情况 D：别的电脑上首次运行

这是**部署环节**的问题：目标机的 Defender 不认识你的自签名证书。

### 4.1 本机签名（打包时做）

```powershell
# 在打包机上执行（管理员 PowerShell 不必需，当前用户证书库即可）
powershell -ExecutionPolicy Bypass -File tools\sign_release.ps1 `
    -Path "dist\GaoGaoNetAUTO\GaoGaoNetAUTO.exe"
```

脚本会：
1. 创建带 **Code Signing EKU** 的自签名证书（缺 EKU Windows 不认）
2. 导出 `GaoGaoNetAUTO-sign.pfx` 与 `GaoGaoNetAUTO-sign.cer`
3. 用 **SHA256 + 可信时间戳** 签名
4. 校验签名状态

> 时间戳很重要：没有它，证书 3 年到期后签名直接失效，装到别人机器上照样报「签名无效」。

### 4.2 目标机是否需要导入证书？

**默认不需要导入。**

自签名证书导入到**受信任的根证书存储**（`Cert:\LocalMachine\Root`）意味着
「这台机器从此信任这个签发者签的一切东西」—— 这是一个**权限很大的动作**，
而且会让签名者私钥的安全性直接等同于整台机器的安全性。
对于一个内部工具，**收益（少一次「未知发布者」提示）远小于风险**。

推荐做法：

1. **直接点「更多信息」→「仍要运行」**。自签名的意义只是让签名状态显示为
   `Valid`（而不是 `NotSigned`），本身不能替代受信任证书。
2. 如果目标机确实需要免除提示，**优先导入到「受信任的发布者」**
   （`TrustedPublisher`）—— 它只影响发布者身份判定，
   **不会让该签发者变成可以签发任何证书的根**：

   ```powershell
   # 管理员 PowerShell
   Import-Certificate -FilePath ".\GaoGaoNetAUTO-sign.cer" `
       -CertStoreLocation Cert:\LocalMachine\TrustedPublisher
   ```

   > ❌ **不建议**导入到 `Cert:\LocalMachine\Root`（受信任的根）。
   > 只有在你能完全控制该签发者私钥、且有明确的内部信任链时才这么做。

   导入后检查：

   ```powershell
   Get-AuthenticodeSignature ".\GaoGaoNetAUTO.exe" | Format-List Status,SignerCertificate
   # Status 应为 Valid
   ```

### 4.3 仍然提示「未知发布者」？

说明证书没进对位置。排查：

```powershell
# 证书是否在「受信任的发布者」里
Get-ChildItem Cert:\LocalMachine\TrustedPublisher | Where-Object { $_.Subject -like "*Gaogao*" }
```

> **重要提醒：自签名 ≠ SmartScreen 放行。**
> 导入证书只解决「谁发布的」，SmartScreen 的**信誉**是基于文件哈希和全球下载量的，新文件必然没有信誉。
> 想彻底消除蓝框，见 §6。

---

## 5. 公司/学校环境的特殊约束

如果目标机有**域策略**（Group Policy）强制管理 Defender，本地排除项可能不生效：

| 策略 | 现象 | 处置 |
|---|---|---|
| 域统一下发排除项 | 你加的排除被冲掉 | 找 IT 加域策略 |
| 「阻止所有未签名代码」 | 直接拒绝运行 | 找 IT 用受信任证书重签 |
| 禁用了 SmartScreen | 不弹蓝框 | 无需处理 |
| Application Control（WDAC/AppLocker） | 拒绝运行任何非白名单程序 | 必须由 IT 加入白名单 |

**判断是否是策略导致的：**

```powershell
Get-MpPreference | Select-Object DisableRealtimeMonitoring,PUAProtection
# 查看本地策略与域策略
rsop.msc
```

---

## 6. 根治方案（面向批量部署）

自签名解决不了 SmartScreen 信誉问题。要真正做到「双击即用、零弹窗」，需要：

| 方案 | 成本 | 说明 |
|---|---|---|
| **Azure Trusted Signing** | 按次计费，最便宜 | 微软云签名服务，OV 级别信任，SmartScreen 认 |
| **OV 代码签名证书** | 约 ¥1500–4000/年 | 传统 OV 证书，需企业主体；国内 CA（ DigiCert / GlobalSign / WoSign ）均可 |
| **EV 代码签名证书** | 约 ¥3000–8000/年 | 走线下企业核验，信誉建立最快，硬白名单命中率最高 |
| 内部 CA 签发 | 需自建 PKI | 仅企业内网场景适用 |

**批量部署时的最佳实践：**
1. 用受信任证书签名
2. 把证书链的 `.cer` 随安装包分发
3. 用 `msiexec` / 组策略 / Intune 做静默安装
4. 走 IT 的软件分发渠道，而不是让用户自己下载

> 排除项（`Add-MpPreference -ExclusionPath`）只应在**明确知道目录内容**、
> 且由 IT 统一管理时使用。给终端用户下发「排除整个安装目录」虽然方便，
> 但等于在该目录里关掉了杀软检查 —— 内部工具可控，
> 对外分发则不建议。

---

## 7. 自查工具

本项目自带产物特征审计，打包后可自查：

```powershell
python tools\audit_features.py dist\GaoGaoNetAUTO
```

输出会列出：

- PE 结构与签名状态
- 是否命中 UPX / onefile / 注册表自启动 / 子进程 / 设备指纹等特征
- 源码侧的模式扫描

**退出码 0 = 没有高危项，1 = 有高危项需要处理。**

---

## 8. 上报（如果确认是误报）

如果按上面全部处理后仍然被拦，可以向微软上报误报：

1. 打开 <https://www.microsoft.com/wdsi/filesubmission> 或
   <https://support.microsoft.com/microsoftfeedback>
2. 提交文件哈希：
   ```powershell
   Get-FileHash "D:\Tools\GaoGaoNetAUTO\GaoGaoNetAUTO.exe" -Algorithm SHA256
   ```
3. 附上 Defender 的检测详情：
   ```powershell
   Get-MpThreatDetection | Format-List * | Out-File detection.txt
   ```
4. 说明：纯校园网认证工具、PyInstaller 打包、已自签名、无注入无子进程

**同时要在意一点**：如果你的程序分发渠道本身有问题（比如从个人网盘下载、
文件名带「破解/免激活」字样），误报报告通常不会被受理——
分发渠道的信誉才是根源。

---

## 9. 一页速查

```powershell
# —— 快速放行（管理员 PowerShell）—— 精确到 exe，不要排除整个目录
Add-MpPreference -ExclusionPath "D:\Tools\GaoGaoNetAUTO\GaoGaoNetAUTO.exe"

# —— 解除下载标记 ——
Unblock-File -Path "D:\Downloads\xxx.zip"

# —— 查看到底命中了什么 ——
Get-MpThreatDetection | Format-List *

# —— 扫描确认 ——
& "$env:ProgramFiles\Windows Defender\MpCmdRun.exe" -Scan -ScanType 3 -File "D:\Tools\GaoGaoNetAUTO"

# —— 导入签名证书（管理员，可选）—— 只导入 TrustedPublisher
Import-Certificate -FilePath ".\GaoGaoNetAUTO-sign.cer" -CertStoreLocation Cert:\LocalMachine\TrustedPublisher

# —— 自查产物特征 ——
python tools\audit_features.py dist\GaoGaoNetAUTO
```

---

*文档版本 1.0.0 · 对应程序版本 1.0.0*
