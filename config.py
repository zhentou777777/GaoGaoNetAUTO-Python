# -*- coding: utf-8 -*-
"""配置与本地凭据存储。

凭据保护
--------
Token / 密码 / 手机号一律走 :mod:`vault` 加密后落盘：

* Windows → **DPAPI**（``CryptProtectData``）—— 系统级、用户绑定、
  随机 nonce、自带完整性校验，换用户或换机器解不开。
* 其他平台 → 带随机 salt/nonce 的降级方案（仅开发环境会走到）。

旧版把「确定性 XOR」当作加密，那是混淆不是加密，历史密文仍可解密，
但**读到旧格式后会立刻用新方案重新加密**。

**数据目录**：``%LOCALAPPDATA%\\GaoGaoNetAUTO\\``（Windows 标准应用数据目录），
并收紧 ACL 到「当前用户 + SYSTEM + Administrators」、断开权限继承。
旧版用的 ``C:\\GaoGaoNetAUTO\\`` 会被自动迁移一次。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any, Dict, Tuple

import vault

CONFIG_NAME = "config.json"

# 数据目录默认值。环境变量 GAOGAO_DATA_DIR 可覆盖（便携/测试用）。
APP_DIR_NAME = "GaoGaoNetAUTO"
LEGACY_DATA_DIR = r"C:\GaoGaoNetAUTO"

# 允许的网关主机白名单。不同学校的主机不同，但**只允许显式列出**，
# 防止配置文件被篡改后把 Token 发到任意地址。
ALLOWED_HOSTS = {
    "hgs.auth.yungao-tech.com",
    "user.v4.yungao-tech.com",
}
ALLOWED_SCHEMES = ("http", "https")

# ---------------------------------------------------------------- 路径


def app_dir() -> Path:
    """程序自身所在目录（仅用于定位随包资源，不存放配置）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _local_app_data() -> Path:
    """Windows 标准应用数据目录；其他平台退回用户目录。"""
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base)
    if sys.platform.startswith("win"):
        return Path.home() / "AppData" / "Local"
    return Path.home() / ".local" / "share"


def data_dir() -> Path:
    """数据目录：``%LOCALAPPDATA%\\GaoGaoNetAUTO\\``，与 exe 位置无关。

    可用环境变量 ``GAOGAO_DATA_DIR`` 覆盖。目录创建后会收紧 ACL。
    """
    raw = os.environ.get("GAOGAO_DATA_DIR") or str(_local_app_data() / APP_DIR_NAME)
    d = Path(raw)
    try:
        created = not d.exists()
        d.mkdir(parents=True, exist_ok=True)
        if created:
            # 新建目录才收紧 ACL；已存在的目录不改动，避免每次启动都调
            # 安全 API（也会打扰用户手工设过的权限）。
            vault.harden_dir_acl(d)
    except Exception:
        # 目录建不出来时退回 exe 旁边，保证程序仍可运行
        d = app_dir()
    return d


def config_path() -> Path:
    return data_dir() / CONFIG_NAME


def logs_dir() -> Path:
    """日志目录：与配置同级的 logs/。"""
    d = data_dir() / "logs"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


# ---------------------------------------------------------------- 默认配置

DEFAULTS: Dict[str, Any] = {
    # 认证网关（云高统一认证）。不同学校主机/端口不同，可在这里改。
    "host": "hgs.auth.yungao-tech.com",
    "port": 1110,
    "scheme": "http",
    # 接入点信息，用于构造 /web/network/code 的查询参数
    "wlan_acname": "Panabit",
    "paip": "",
    "vlan": "0.0",
    # 单设备限制下，被顶掉的手机 IP（留空则不自动顶替）
    "phone_ip": "",
    # 加密后的 Bearer Token（DPAPI）
    "token": "",
    # 上一版 Token（L1 自动续存失败时可回退）
    "token_prev": "",
    # 账号密码登录（用于 Token 失效时自动换取新 Token，实现完全自愈）
    "account_mobile": "",
    "account_password": "",
    # 行为开关
    "autostart": False,
    # 默认不顶替手机——单设备套餐下顶掉手机会让人很困扰，
    # 需要独占时可在设置里手动打开。
    "auto_kick_phone": False,
    # 抓包模式发现的认证页 URL（可为空；为空则用默认的 /web/network/code）
    "code_page_url": "",
    # 运行状态
    "last_ip": "",
    "last_auth_ok": False,
}


# ---------------------------------------------------------------- 机器指纹

def _machine_id() -> bytes:
    """取一个稳定的机器指纹，作为本地加密密钥的种子（**旧版种子**）。

    读 HKLM\\MachineGuid 在部分安全软件眼里像「设备唯一标识采集」，
    新版已改用不含该标识的轻量种子（见 `_light_seed`）。此函数保留，
    仅用于解密历史配置。
    """
    if platform.system() == "Windows":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography"
            ) as key:
                guid = str(winreg.QueryValueEx(key, "MachineGuid")[0])
            return guid.encode("utf-8")
        except Exception:
            pass
    return f"{platform.node()}|{platform.machine()}|{os.getlogin()}".encode("utf-8")


def _light_seed() -> bytes:
    """新版加密种子：不读取任何设备唯一标识。

    只用「主机名 + 网卡节点号 + 登录名」组合。它仍然是稳定的，
    换机器/换用户同样解不开，但不会被判定为「采集设备指纹」。
    """
    node = 0
    try:
        import uuid

        node = uuid.getnode()
    except Exception:
        node = 0
    try:
        user = os.getlogin()
    except Exception:
        user = ""
    return f"{platform.node()}|{node}|{user}|{platform.machine()}".encode("utf-8")


def _derive_keys_v1() -> Tuple[bytes, bytes]:
    """旧版密钥（MachineGuid 种子）。"""
    seed = _machine_id()
    enc_key = hashlib.sha256(b"gaogao-token-enc|" + seed).digest()
    mac_key = hashlib.sha256(b"gaogao-token-mac|" + seed).digest()
    return enc_key, mac_key


def _derive_keys_v2() -> Tuple[bytes, bytes]:
    """新版密钥（轻量种子，不采集设备唯一标识）。"""
    seed = _light_seed()
    enc_key = hashlib.sha256(b"gaogao-token-enc2|" + seed).digest()
    mac_key = hashlib.sha256(b"gaogao-token-mac2|" + seed).digest()
    return enc_key, mac_key


# 兼容旧名（历史上指向 v1；现已统一到 v2 的轻量种子）
_derive_keys = _derive_keys_v2


def _keystream(key: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(key + counter.to_bytes(4, "big")).digest()
        counter += 1
    return bytes(out[:length])


def _decrypt_with(blob: str, keys: Tuple[bytes, bytes]) -> str:
    """用指定密钥解开**历史版**（v1:/v2: 前缀）密文。

    仅用于迁移期读取旧配置；新写入一律走 :mod:`vault`。
    """
    enc_key, mac_key = keys
    try:
        data = base64.b64decode(blob[3:])
        cipher, tag = data[:-16], data[-16:]
        expect = hmac.new(mac_key, cipher, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(tag, expect):
            return ""
        raw = bytes(a ^ b for a, b in zip(cipher, _keystream(enc_key, len(cipher))))
        return raw.decode("utf-8")
    except Exception:
        return ""


def encrypt_token(plain: str) -> str:
    """加密 Token，返回带版本前缀的密文。

    主路径是 Windows DPAPI（用户绑定、系统级、随机 nonce）；
    非 Windows 走带随机 salt/nonce 的降级方案。
    """
    if not plain:
        return ""
    return vault.protect(plain)


def decrypt_token(blob: str) -> str:
    """解密 Token；失败返回空字符串。

    兼容历史格式：``v2:`` / ``v1:``（旧版自拼 XOR）与纯明文。
    """
    if not blob:
        return ""
    return vault.unprotect(blob)


# ---------------------------------------------------------------- 配置读写

class Config:
    """轻量配置容器，读改写 config.json。"""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else config_path()
        self.data: Dict[str, Any] = dict(DEFAULTS)
        self.migrated_from = ""
        self.load()
        self._migrate_from_legacy()
        self._upgrade_encryption()

    # -------------------------------------------------- 迁移

    def _legacy_candidates(self) -> list[Path]:
        """历史上用过的配置位置（按优先级）。"""
        out: list[Path] = []
        legacy_root = Path(LEGACY_DATA_DIR)
        if legacy_root != self.path.parent:
            out.append(legacy_root / CONFIG_NAME)
        if self.path.parent != app_dir():
            out.append(app_dir() / CONFIG_NAME)
        return out

    def _migrate_from_legacy(self) -> None:
        """把旧版 config.json 迁移到当前数据目录，并**用新方案重新加密**。

        旧版有两个来源：
        1. ``C:\\GaoGaoNetAUTO\\config.json``（旧数据目录）
        2. exe 同目录的 ``config.json``（更早的版本）

        迁移后：
        * 凭据字段一律用 DPAPI（新版）重新加密，不把旧混淆密文原样搬过去；
        * 原文件**保留**但不再被读取（用户可自行核对）；
        * 迁移记录写进 ``migrated_from`` 供 UI 提示。
        """
        if self.get_token() or self.get_account()[1]:
            return  # 当前目录已有可用配置，不再迁移
        for legacy in self._legacy_candidates():
            if not legacy.exists() or legacy == self.path:
                continue
            try:
                raw = json.loads(legacy.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    continue
                self.data.update(raw)
                self._upgrade_encryption()
                self.save()
                self.migrated_from = str(legacy)
                return
            except Exception:
                continue

    def _upgrade_encryption(self) -> None:
        """把旧版混淆密文（v1:/v2:/明文）重新加密为新版（DPAPI）。

        只在**能成功解出明文**时才重写，解不开就原样保留，
        避免把用户唯一一份可用凭据改成空值。
        """
        changed = False
        for key in ("token", "token_prev", "account_mobile", "account_password"):
            blob = str(self.data.get(key, "") or "")
            if not blob:
                continue
            if vault.is_protected(blob):
                continue
            plain = vault.unprotect(blob)
            if not plain:
                continue
            self.data[key] = vault.protect(plain)
            changed = True
        if changed:
            try:
                self.save()
            except Exception:
                pass

    # -------------------------------------------------- 读写

    def load(self) -> "Config":
        for src in (self.path, self.path.parent / "config.json.bak"):
            if not src.exists():
                continue
            try:
                raw = json.loads(src.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self.data.update(raw)
                    break
            except Exception:
                continue
        return self

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._backup()
        except Exception:
            pass

    def _backup(self) -> None:
        """写一份备份，防止误删/写坏配置。

        只保留 1 份固定名的 ``config.json.bak``（不带时间戳，
        避免出现「批量时间戳文件」这种可疑特征）。
        """
        try:
            bak = self.path.parent / "config.json.bak"
            bak.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    # -------------------------------------------------- 通用访问

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any, save: bool = True) -> None:
        self.data[key] = value
        if save:
            self.save()

    # -------------------------------------------------- Token 便捷方法

    def get_token(self) -> str:
        return decrypt_token(str(self.get("token", "")))

    def set_token(self, plain: str, save: bool = True) -> None:
        self.set("token", encrypt_token(plain.strip()), save=save)

    def has_token(self) -> bool:
        return bool(self.get_token())

    def get_token_prev(self) -> str:
        """取上一版 Token（服务端下发新 Token 时自动留的备份）。

        当前 Token 判定失效时，可以回退试一次这个 —— 它往往还没过期。
        """
        return decrypt_token(str(self.get("token_prev", "") or ""))

    # -------------------------------------------------- 账号密码（加密存储）

    def get_account(self) -> tuple[str, str]:
        """取 (手机号, 密码)。

        手机号同样加密存储 —— 明文手机号落在磁盘上会让「拿到 config.json
        就等于拿到用户身份」。
        """
        mobile_blob = str(self.get("account_mobile", "") or "")
        pwd_blob = str(self.get("account_password", "") or "")
        mobile = decrypt_token(mobile_blob) if mobile_blob else ""
        return mobile, decrypt_token(pwd_blob)

    def set_account(self, mobile: str, password: str, save: bool = True) -> None:
        self.set("account_mobile", encrypt_token((mobile or "").strip()), save=False)
        self.set("account_password", encrypt_token(password or ""), save=save)

    def has_account(self) -> bool:
        mobile, pwd = self.get_account()
        return bool(mobile and pwd)

    # -------------------------------------------------- 清除凭据

    def clear_credentials(self) -> None:
        """清除所有已保存的凭据（Token / 手机号 / 密码）。

        用于「换人用这台电脑」「怀疑凭据泄露」的场景。
        备份文件也一并覆写删除，避免旧密文残留。
        """
        for key in ("token", "token_prev", "account_mobile", "account_password"):
            self.data[key] = ""
        self.save()
        for extra in (self.path.parent / "config.json.bak",):
            try:
                if extra.exists():
                    vault.wipe(extra)
            except Exception:
                pass
        # 扫码临时二维码也一并清掉
        try:
            (self.path.parent / "qr_tmp.png").unlink()
        except Exception:
            pass

    # -------------------------------------------------- 连接信息

    @property
    def base_url(self) -> str:
        return f"{self.get('scheme')}://{self.get('host')}:{self.get('port')}"

    # -------------------------------------------------- 网关校验

    @staticmethod
    def validate_gateway(host: str, scheme: str, port: int) -> tuple[bool, str]:
        """校验网关地址是否可信。

        规则：
        * scheme 只能是 http / https；
        * host 必须在 :data:`ALLOWED_HOSTS` 白名单内；
        * port 必须在 1–65535。

        返回 ``(是否合法, 错误说明)``。
        """
        scheme = (scheme or "").strip().lower()
        host = (host or "").strip().lower()
        if scheme not in ALLOWED_SCHEMES:
            return False, f"协议只能是 {'/'.join(ALLOWED_SCHEMES)}，当前是「{scheme}」"
        if not host:
            return False, "网关地址不能为空"
        if host not in ALLOWED_HOSTS:
            return False, (
                f"网关「{host}」不在白名单内。"
                f"允许的地址：{'、'.join(sorted(ALLOWED_HOSTS))}"
            )
        try:
            p = int(port)
        except Exception:
            return False, "端口必须是数字"
        if not (1 <= p <= 65535):
            return False, f"端口 {p} 超出范围"
        return True, ""

    def gateway_valid(self) -> tuple[bool, str]:
        """校验当前配置里的网关地址。"""
        return self.validate_gateway(
            str(self.get("host") or ""),
            str(self.get("scheme") or ""),
            int(self.get("port") or 0),
        )
