# -*- coding: utf-8 -*-
"""本地凭据保险库：Windows DPAPI 加密 + 目录 ACL 收紧。

为什么换掉原来的方案
--------------------
旧实现（``config.py`` 里的 ``encrypt_token``）是**自拼密码学**：

* 密钥 = ``sha256("gaogao-token-enc2|" + hostname|网卡MAC|用户名|机型)``；
  种子全是**确定性**的，任何知道目标机这几项的人都能算出同一把钥匙；
* 加密是 ``明文 XOR keystream`` —— 没有 IV/nonce，同一明文永远得到同一密文；
* 完整性用 HMAC，但密钥同样派生自公开信息。

结论：它只能算「**混淆**」，挡不住任何有心人，也挡不住同机的低权限账号。

本模块改成标准方案：

* **Windows DPAPI**（``CryptProtectData`` / ``CryptUnprotectData``）——
  系统级、用户绑定、随机 nonce（由 LSA 生成）、带完整性校验，
  换用户或换机器解不开。**这是主路径。**
* 非 Windows / DPAPI 不可用时，退回**带随机 salt + 随机 nonce 的
  派生密钥流加密 + HMAC**（版本前缀 ``v3x:``）。
  注意它仍不是标准 AEAD，只是把「确定性 XOR」这个最致命的缺陷补上，
  并且**只在本项目目标平台之外的开发环境**才会走到。
* 密文前缀自描述版本：``dpapi:`` / ``v3x:`` / ``v1:`` / ``v2:``（旧版）/ 无前缀（历史明文）。

另外提供 :func:`harden_dir_acl`，用 SDDL 把数据目录权限收紧到
「当前用户 + SYSTEM + Administrators」，并断开权限继承。
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import os
import secrets
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Optional, Tuple

PREFIX_DPAPI = "dpapi:"
PREFIX_V3X = "v3x:"
PREFIX_V1 = "v1:"
PREFIX_V2 = "v2:"

# DPAPI 的附加熵（可选但推荐）：即便别的程序调用 CryptProtectData，
# 没有这段熵也解不开我们的密文。
_ENTROPY = b"GaogaoNetAUTO.CampusAuth.v1"

_is_windows = sys.platform.startswith("win")

# ---------------------------------------------------------------- DPAPI


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _blob(data: bytes) -> Tuple[_DATA_BLOB, ctypes.Array]:
    buf = ctypes.create_string_buffer(data, len(data))
    blob = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    return blob, buf


def _dpapi_available() -> bool:
    if not _is_windows:
        return False
    try:
        ctypes.windll.crypt32  # noqa: B018
        return True
    except Exception:
        return False


def _dpapi_protect(plain: bytes) -> Optional[bytes]:
    """调用 CryptProtectData。失败返回 None。"""
    if not _dpapi_available():
        return None
    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        src, src_buf = _blob(plain)
        ent, ent_buf = _blob(_ENTROPY)
        out = _DATA_BLOB()
        # CRYPTPROTECT_UI_FORBIDDEN = 1
        ok = crypt32.CryptProtectData(
            ctypes.byref(src), "GaoGaoNetAUTO", ctypes.byref(ent),
            None, None, 1, ctypes.byref(out),
        )
        if not ok:
            return None
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            kernel32.LocalFree(out.pbData)
    except Exception:
        return None


def _dpapi_unprotect(blob: bytes) -> Optional[bytes]:
    """调用 CryptUnprotectData。失败返回 None。"""
    if not _dpapi_available():
        return None
    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        src, src_buf = _blob(blob)
        ent, ent_buf = _blob(_ENTROPY)
        out = _DATA_BLOB()
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(src), None, ctypes.byref(ent), None, None,
            1, ctypes.byref(out),
        )
        if not ok:
            return None
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            kernel32.LocalFree(out.pbData)
    except Exception:
        return None


# ------------------------------------------------- v3x 降级方案（随机 salt/nonce）


def _v3x_key_file() -> Path:
    """降级模式下保存随机主密钥的 sidecar 文件。"""
    base = os.environ.get("GAOGAO_DATA_DIR") or ""
    d = Path(base) if base else Path.home() / ".gaogao_netauto"
    return d / "vault.key"


def _v3x_master() -> bytes:
    """取（或首次生成）降级模式的主密钥。

    随机 32 字节，落在 sidecar 文件里，权限收紧到仅当前用户。
    它提供的是「换机器/换用户解不开」，但**不防同机低权限账号**——
    真正的防护在 Windows 上由 DPAPI 提供。
    """
    p = _v3x_key_file()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            data = p.read_bytes()
            if len(data) >= 32:
                return data[:32]
    except Exception:
        pass
    key = secrets.token_bytes(32)
    try:
        p.write_bytes(key)
        try:
            os.chmod(p, 0o600)
        except Exception:
            pass
    except Exception:
        pass
    return key


def _derive_v3x(salt: bytes) -> bytes:
    master = _v3x_master()
    return hashlib.pbkdf2_hmac("sha256", master, salt, 100_000, dklen=32)


def _v3x_encrypt(plain: bytes) -> str:
    """v3x：随机 salt + 随机 nonce + HMAC-SHA256。

    密钥 = PBKDF2(主密钥, salt)。salt/nonce 每次都不同，
    因此同一明文不会产生同一密文（修掉旧版「确定性 XOR」的核心缺陷）。
    """
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(16)
    key = _derive_v3x(salt)
    stream = _keystream(key, nonce, len(plain))
    cipher = bytes(a ^ b for a, b in zip(plain, stream))
    tag = hmac.new(key, nonce + cipher, hashlib.sha256).digest()[:16]
    payload = base64.b64encode(salt + nonce + tag + cipher).decode("ascii")
    return PREFIX_V3X + payload


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest()
        counter += 1
    return bytes(out[:length])


# ---------------------------------------------------------------- 对外 API


def protect(plain: str) -> str:
    """加密字符串，返回带版本前缀的密文。

    * Windows → ``dpapi:``（DPAPI，用户绑定）
    * 其他平台 → ``v3x:``（随机 salt/nonce，跨进程失效的降级方案）
    """
    if not plain:
        return ""
    raw = plain.encode("utf-8")
    blob = _dpapi_protect(raw)
    if blob is not None:
        return PREFIX_DPAPI + base64.b64encode(blob).decode("ascii")
    return _v3x_encrypt(raw)


def unprotect(blob: str) -> str:
    """解密。失败返回空字符串。

    兼容：``dpapi:`` / ``v3x:`` / ``v2:``（旧 XOR）/ ``v1:``（旧 XOR）/ 明文。
    """
    if not blob:
        return ""
    try:
        if blob.startswith(PREFIX_DPAPI):
            raw = base64.b64decode(blob[len(PREFIX_DPAPI):])
            out = _dpapi_unprotect(raw)
            return out.decode("utf-8") if out else ""
        if blob.startswith(PREFIX_V3X):
            data = base64.b64decode(blob[len(PREFIX_V3X):])
            salt, nonce, tag, cipher = data[:16], data[16:32], data[32:48], data[48:]
            key = _derive_v3x(salt)
            expect = hmac.new(key, nonce + cipher, hashlib.sha256).digest()[:16]
            if not hmac.compare_digest(tag, expect):
                return ""
            raw = bytes(a ^ b for a, b in zip(cipher, _keystream(key, nonce, len(cipher))))
            return raw.decode("utf-8")
        if blob.startswith(PREFIX_V1) or blob.startswith(PREFIX_V2):
            # 旧版自拼密码学 —— 只用于解开历史配置，解开后应立刻重新加密。
            # 注意：旧 encrypt_token 写的是 "v1:" 前缀但用的是 **v2 密钥**，
            # 所以这里两种密钥都要试一遍。
            from config import _decrypt_with, _derive_keys_v1, _derive_keys_v2  # noqa

            for keys in (_derive_keys_v2(), _derive_keys_v1()):
                out = _decrypt_with(blob, keys)
                if out:
                    return out
            return ""
        # 历史明文
        return blob
    except Exception:
        return ""


def is_protected(blob: str) -> bool:
    """判断是否已经是（新版）受保护密文。"""
    return bool(blob) and blob.startswith((PREFIX_DPAPI, PREFIX_V3X))


def is_legacy(blob: str) -> bool:
    """判断是否是旧版混淆密文（需要迁移重加密）。"""
    return bool(blob) and blob.startswith((PREFIX_V1, PREFIX_V2))


# ---------------------------------------------------------------- ACL 收紧


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Sid", ctypes.c_void_p),
        ("Attributes", wintypes.DWORD),
    ]


class _TOKEN_USER(ctypes.Structure):
    _fields_ = [("User", _SID_AND_ATTRIBUTES)]


def _current_user_sid() -> str:
    """取当前登录用户的 SID 字符串（不起子进程）。"""
    advapi32 = ctypes.windll.advapi32
    kernel32 = ctypes.windll.kernel32

    # ⚠️ 必须显式声明 argtypes/restype。
    # ctypes 默认把返回值当 32 位 int，HANDLE 在 64 位 Windows 上是 64 位，
    # 不声明就会把句柄截断 —— 结果是 OpenProcessToken 直接失败。
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)
    ):
        return ""
    try:
        size = wintypes.DWORD(0)
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if not size.value:
            return ""
        buf = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(
            token, 1, buf, size, ctypes.byref(size)
        ):
            return ""
        tu = ctypes.cast(buf, ctypes.POINTER(_TOKEN_USER)).contents
        cws = ctypes.c_wchar_p()
        if not advapi32.ConvertSidToStringSidW(
            ctypes.c_void_p(tu.User.Sid), ctypes.byref(cws)
        ):
            return ""
        try:
            return cws.value or ""
        finally:
            kernel32.LocalFree(cws)
    finally:
        kernel32.CloseHandle(token)


def harden_dir_acl(path: Path) -> bool:
    """把目录权限收紧到「当前用户 + SYSTEM + Administrators」，断开继承。

    实现走 ``ConvertStringSecurityDescriptorToSecurityDescriptor`` +
    ``SetNamedSecurityInfo``（纯 ctypes，不起子进程）。
    """
    if not _is_windows:
        return False
    try:
        advapi32 = ctypes.windll.advapi32
        kernel32 = ctypes.windll.kernel32

        advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            ctypes.c_wchar_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
        advapi32.SetNamedSecurityInfoW.argtypes = [
            ctypes.c_wchar_p, ctypes.c_int, wintypes.DWORD,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ]
        advapi32.SetNamedSecurityInfoW.restype = ctypes.c_long

        user_sid = _current_user_sid()
        if not user_sid:
            return False

        # D:P = 保护 DACL，不继承
        # (A;;FA;;;{user})  当前用户完全控制
        # (A;;FA;;;SY)       SYSTEM
        # (A;;FA;;;BA)       Administrators
        sddl = f"D:P(A;;FA;;;{user_sid})(A;;FA;;;SY)(A;;FA;;;BA)"
        sd = ctypes.c_void_p()
        if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            ctypes.c_wchar_p(sddl), 1, ctypes.byref(sd), None
        ):
            return False
        try:
            # DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION
            info = 0x00000004 | 0x80000000
            # SE_FILE_OBJECT = 1
            rc = advapi32.SetNamedSecurityInfoW(
                ctypes.c_wchar_p(str(path)), 1, info,
                None, None, sd,
            )
            return rc == 0
        finally:
            kernel32.LocalFree(sd)
    except Exception:
        return False


def wipe(path: Path) -> bool:
    """删除文件（用于「清除已保存凭据」）。

    覆写一遍内容再删除，尽量减少残留痕迹。
    """
    try:
        if not path.exists():
            return False
        try:
            size = path.stat().st_size
            if size:
                with path.open("r+b") as f:
                    f.write(b"\x00" * min(size, 64 * 1024))
                    if size > 64 * 1024:
                        f.write(b"\x00" * (size - 64 * 1024))
                    f.flush()
                    os.fsync(f.fileno())
        except Exception:
            pass
        path.unlink()
        return True
    except Exception:
        return False
