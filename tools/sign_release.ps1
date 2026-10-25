<#
.SYNOPSIS
    给 糕糕校园网自动认证 的 exe 做代码签名（自签名）。

.DESCRIPTION
    自签名不能根治 SmartScreen，但能消除「未知发布者」的外观，
    并让 Defender 的信誉体系不再因为「无发布者」额外扣分。

    关键点：
    * EKU 必须是 Code Signing（1.3.6.1.5.5.7.3.3），
      少了这个 Windows 不认，签名等于没签。
    * 必须挂**可信时间戳**，否则证书到期后签名失效，
      装到别人机器上也照样报「签名无效」。
    * 导出 .cer 供目标机导入：LocalMachine\Root（信任根）
      + LocalMachine\TrustedPublisher（信任发布者）。

    优先用 Windows SDK 的 signtool.exe（支持 RFC3161 时间戳）；
    找不到时回退到 PowerShell 的 Set-AuthenticodeSignature。

.PARAMETER Path
    要签名的 exe 路径。

.PARAMETER CertPassword
    PFX 导出密码。留空则生成一个受保护 PFX（后续签名需要传密码）。

.PARAMETER OutCert
    导出的 .cer 路径（给目标机导入用）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\sign_release.ps1 `
        -Path dist\GaoGaoNetAUTO\GaoGaoNetAUTO.exe
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Path,

    [string]$CertPassword = "",

    [string]$OutCert = ""
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) {
    Write-Host "==> $msg" -ForegroundColor Cyan
}

if (-not (Test-Path $Path)) {
    Write-Error "找不到待签名文件：$Path"
    exit 1
}
$Path = (Resolve-Path $Path).Path
$workDir = Split-Path -Parent $Path
$pfxPath = Join-Path $workDir "GaoGaoNetAUTO-sign.pfx"
$cerPath = if ($OutCert) { $OutCert } else { Join-Path $workDir "GaoGaoNetAUTO-sign.cer" }

Write-Step "目标文件：$Path"

# ---------------------------------------------------------------- 1. 造自签证书
Write-Step "检查/创建代码签名证书"

$subject = "CN=Gaogao Campus Auth, O=Gaogao Network, L=Guangzhou, S=Guangdong, C=CN"

$cert = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert -ErrorAction SilentlyContinue |
        Where-Object { $_.Subject -like "CN=Gaogao Campus Auth*" } |
        Select-Object -First 1

if (-not $cert) {
    Write-Host "  未找到现有证书，生成新的自签名证书…"
    $cert = New-SelfSignedCertificate `
        -Subject $subject `
        -Type CodeSigningCert `
        -KeyUsage DigitalSignature `
        -KeyExportPolicy Exportable `
        -KeyLength 2048 `
        -HashAlgorithm SHA256 `
        -CertStoreLocation "Cert:\CurrentUser\My" `
        -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3") `
        -NotAfter (Get-Date).AddYears(3)
    Write-Host "  已创建：$($cert.Thumbprint)" -ForegroundColor Green
} else {
    Write-Host "  复用已有证书：$($cert.Thumbprint)"
}

# 本机信任自己的证书，方便校验时显示 Valid
# （用 Export/Import 而不是 New-Object X509Certificate2，
#   后者在部分 .NET 版本上对 PrivateKey 的处理会抛异常）
try {
    $tmpCer = Join-Path $env:TEMP "gaogao-trust.cer"
    Export-Certificate -Cert $cert -FilePath $tmpCer | Out-Null
    foreach ($store in @("Root", "TrustedPublisher")) {
        $dst = "Cert:\CurrentUser\$store"
        if (-not (Get-ChildItem $dst -ErrorAction SilentlyContinue |
                  Where-Object { $_.Thumbprint -eq $cert.Thumbprint })) {
            Import-Certificate -FilePath $tmpCer -CertStoreLocation $dst | Out-Null
            Write-Host "  已加入本机信任：$dst"
        }
    }
    Remove-Item $tmpCer -Force -ErrorAction SilentlyContinue
} catch {
    Write-Host "  （本机信任设置跳过：$($_.Exception.Message)）" -ForegroundColor Yellow
}

# ---------------------------------------------------------------- 2. 导出 PFX / CER
Write-Step "导出证书（供目标机导入）"
if (Test-Path $pfxPath) { Remove-Item $pfxPath -Force }
if (Test-Path $cerPath) { Remove-Item $cerPath -Force }

# Export-PfxCertificate 要求必须提供密码或 ProtectTo，不能两个都不给。
# 没显式传密码时随机生成一个，并写到同目录的 pfx-password.txt，
# 免得用户拿到一个打不开的 PFX。
$passwordToUse = $CertPassword
if (-not $passwordToUse) {
    $bytes = New-Object byte[] 16
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $passwordToUse = [Convert]::ToBase64String($bytes)
    $pwFile = Join-Path $workDir "pfx-password.txt"
    Set-Content -Path $pwFile -Value $passwordToUse -Encoding UTF8
    Write-Host "  已生成随机 PFX 密码，存于：$pwFile" -ForegroundColor Yellow
}

$securePwd = ConvertTo-SecureString -String $passwordToUse -Force -AsPlainText
Export-PfxCertificate -Cert $cert -FilePath $pfxPath -Password $securePwd | Out-Null
Export-Certificate -Cert $cert -FilePath $cerPath | Out-Null
Write-Host "  PFX: $pfxPath" -ForegroundColor Green
Write-Host "  CER: $cerPath" -ForegroundColor Green

# ---------------------------------------------------------------- 3. 签名
$sigTool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" `
    -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match "x64" } |
    Sort-Object FullName -Descending |
    Select-Object -First 1

if ($sigTool) {
    Write-Step "签名（signtool + SHA256 + RFC3161 时间戳）"
    Write-Host "  使用：$($sigTool.FullName)"
    $tsUrl = "http://timestamp.digicert.com"
    $sargs = @("sign", "/fd", "SHA256", "/td", "SHA256", "/tr", $tsUrl, "/ts", $tsUrl, "/f", $pfxPath)
    $sargs += @("/p", $passwordToUse)
    $sargs += $Path
    & $sigTool.FullName @sargs
    if ($LASTEXITCODE -ne 0) {
        Write-Error "签名失败（exit $LASTEXITCODE）"
        exit 3
    }
} else {
    Write-Step "签名（Set-AuthenticodeSignature + 可信时间戳）"
    Write-Host "  未安装 Windows SDK，改用 PowerShell 内置签名器"
    $signed = Set-AuthenticodeSignature `
        -FilePath $Path `
        -Certificate $cert `
        -HashAlgorithm SHA256 `
        -TimestampServer "http://timestamp.digicert.com"
    if ($signed.Status -ne "Valid") {
        Write-Host "! 签名结果：$($signed.StatusMessage)" -ForegroundColor Yellow
    }
}

# ---------------------------------------------------------------- 4. 校验
Write-Step "校验签名"
$final = Get-AuthenticodeSignature $Path
Write-Host ""
if ($final.Status -eq "Valid") {
    Write-Host "✓ 签名有效" -ForegroundColor Green
    Write-Host "  主题：$($final.SignerCertificate.Subject)"
    Write-Host "  颁发者：$($final.SignerCertificate.Issuer)"
    Write-Host "  有效期：$($final.SignerCertificate.NotBefore) ~ $($final.SignerCertificate.NotAfter)"
} else {
    Write-Host "! 签名状态：$($final.Status) —— $($final.StatusMessage)" -ForegroundColor Yellow
    Write-Host "  （本机未信任自己的证书时可能显示 UnknownError；导入 CER 后即正常）"
}

Write-Host ""
Write-Host "目标机还需要导入 $cerPath 才能显示为「已信任」：" -ForegroundColor Yellow
Write-Host "  1) 双击 .cer → 安装到「本地计算机」→ 受信任的根证书颁发机构"
Write-Host "  2) 再导入一次到「受信任的发布者」"
Write-Host "  管理员 PowerShell 一键导入："
Write-Host "  Import-Certificate -FilePath '$cerPath' -CertStoreLocation Cert:\LocalMachine\Root"
Write-Host "  Import-Certificate -FilePath '$cerPath' -CertStoreLocation Cert:\LocalMachine\TrustedPublisher"
exit 0
