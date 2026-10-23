# -*- coding: utf-8 -*-
"""配置与本地加密存储。

Token 不以明文落盘：以「机器指纹」派生的密钥做流加密 + HMAC 完整性校验后
存入 config.json，换机器/换用户读出来的密文无法解密。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any, Dict

CONFIG_NAME = "config.json"

# ---------------------------------------------------------------- 路径

def app_dir() -> Path:
    """程序所在目录。

    PyInstaller onefile 打包后 ``__file__`` 指向临时解压目录，配置必须放在
    exe 旁边，所以这里统一走 ``sys.executable``。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def config_path() -> Path:
    return app_dir() / CONFIG_NAME


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
    # 行为开关
    "autostart": False,
    "auto_kick_phone": True,
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
    """取一个稳定的机器指纹，作为本地加密密钥的种子。"""
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


def _derive_keys() -> tuple[bytes, bytes]:
    seed = _machine_id()
    enc_key = hashlib.sha256(b"gaogao-token-enc|" + seed).digest()
    mac_key = hashlib.sha256(b"gaogao-token-mac|" + seed).digest()
    return enc_key, mac_key


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
    enc_key, mac_key = _derive_keys()
    cipher = bytes(a ^ b for a, b in zip(raw, _keystream(enc_key, len(raw))))
    tag = hmac.new(mac_key, cipher, hashlib.sha256).digest()[:16]
    return "v1:" + base64.b64encode(cipher + tag).decode("ascii")


def decrypt_token(blob: str) -> str:
    """解密 Token；失败返回空字符串（密钥变了/文件损坏）。"""
    if not blob:
        return ""
    if not blob.startswith("v1:"):
        # 兼容手工填写的明文
        return blob
    try:
        data = base64.b64decode(blob[3:])
        cipher, tag = data[:-16], data[-16:]
        enc_key, mac_key = _derive_keys()
        expect = hmac.new(mac_key, cipher, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(tag, expect):
            return ""
        raw = bytes(a ^ b for a, b in zip(cipher, _keystream(enc_key, len(cipher))))
        return raw.decode("utf-8")
    except Exception:
        return ""


# ---------------------------------------------------------------- 配置读写

class Config:
    """轻量配置容器，读改写 config.json。"""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else config_path()
        self.data: Dict[str, Any] = dict(DEFAULTS)
        self.load()

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

    # -------------------------------------------------- 连接信息

    @property
    def base_url(self) -> str:
        return f"{self.get('scheme')}://{self.get('host')}:{self.get('port')}"
