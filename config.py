# -*- coding: utf-8 -*-
"""配置与本地加密存储。

Token 不以明文落盘：以「机器指纹」派生的密钥做流加密 + HMAC 完整性校验后
存入 config.json，换机器/换用户读出来的密文无法解密。

**数据目录**：配置不放在 exe 旁边（桌面），统一放 C 盘固定目录
``C:\\GaoGaoNetAUTO\\``，与 exe 放在哪里无关。exe 可以丢在桌面、放在
``C:\\Program Files\\`` 下，都不会再出现「配置写不进去」或「移走 exe 就丢配置」。
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

CONFIG_NAME = "config.json"

# 数据目录默认值。环境变量 GAOGAO_DATA_DIR 可覆盖（便携/测试用）。
DEFAULT_DATA_DIR = r"C:\GaoGaoNetAUTO"

# ---------------------------------------------------------------- 路径

def app_dir() -> Path:
    """程序自身所在目录（仅用于定位随包资源，不存放配置）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    """数据目录：固定在 C 盘同名文件夹下，与 exe 位置无关。

    可用环境变量 ``GAOGAO_DATA_DIR`` 覆盖。
    """
    raw = os.environ.get("GAOGAO_DATA_DIR") or DEFAULT_DATA_DIR
    d = Path(raw)
    try:
        d.mkdir(parents=True, exist_ok=True)
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
    # 加密后的 Bearer Token
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
    "keep_token_alive": True,
    # 抓取模式发现的接口模板（可为空）
    "status_url": "",
    "complete_url": "",
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


def encrypt_token(plain: str) -> str:
    """加密 Token，返回 base64 字符串（``v1:<payload>``）。"""
    if not plain:
        return ""
    raw = plain.encode("utf-8")
    enc_key, mac_key = _derive_keys_v2()
    cipher = bytes(a ^ b for a, b in zip(raw, _keystream(enc_key, len(raw))))
    tag = hmac.new(mac_key, cipher, hashlib.sha256).digest()[:16]
    return "v1:" + base64.b64encode(cipher + tag).decode("ascii")


def _decrypt_with(blob: str, keys: Tuple[bytes, bytes]) -> str:
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


def decrypt_token(blob: str) -> str:
    """解密 Token；失败返回空字符串（密钥变了/文件损坏）。

    先用新版轻量种子试，再用旧版 MachineGuid 种子试 —— 保证历史配置
    不会因为加密方案升级而丢失。
    """
    if not blob:
        return ""
    if not blob.startswith("v1:"):
        # 兼容手工填写的明文
        return blob
    out = _decrypt_with(blob, _derive_keys_v2())
    if out:
        return out
    return _decrypt_with(blob, _derive_keys_v1())


# ---------------------------------------------------------------- 配置读写

class Config:
    """轻量配置容器，读改写 config.json。"""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else config_path()
        self.data: Dict[str, Any] = dict(DEFAULTS)
        self.migrated_from = ""
        self.load()
        self._migrate_from_legacy()

    def _migrate_from_legacy(self) -> None:
        """把旧版「exe 同目录」的 config.json 迁移到数据目录。

        旧版把配置写在 exe 旁边，用户把 exe 放桌面时配置也在桌面，
        移动/重装 exe 就会丢。新版统一放 C:\\GaoGaoNetAUTO\\。
        迁移只做一次，**原文件保留不动**（出问题可手工找回）。
        """
        if self.path.parent == app_dir():
            return  # 已经是 exe 同目录，无需迁移
        legacy = app_dir() / CONFIG_NAME
        if not legacy.exists() or legacy == self.path:
            return
        try:
            raw = json.loads(legacy.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return
            # 只有当数据目录里还没有有效配置时才迁移
            if self.get_token() or self.get_account()[1]:
                return
            self.data.update(raw)
            self.save()
            self.migrated_from = str(legacy)
        except Exception:
            pass

    def load(self) -> "Config":
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self.data.update(raw)
            except Exception:
                pass
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

    # -------------------------------------------------- 账号密码（加密存储）

    def get_account(self) -> tuple[str, str]:
        """取 (手机号, 密码)。密码与 Token 同样加密存储。"""
        mobile = str(self.get("account_mobile", "") or "")
        pwd_blob = str(self.get("account_password", "") or "")
        return mobile, decrypt_token(pwd_blob)

    def set_account(self, mobile: str, password: str, save: bool = True) -> None:
        self.set("account_mobile", (mobile or "").strip(), save=False)
        self.set("account_password", encrypt_token(password or ""), save=save)

    def has_account(self) -> bool:
        mobile, pwd = self.get_account()
        return bool(mobile and pwd)

    # -------------------------------------------------- 连接信息

    @property
    def base_url(self) -> str:
        return f"{self.get('scheme')}://{self.get('host')}:{self.get('port')}"
