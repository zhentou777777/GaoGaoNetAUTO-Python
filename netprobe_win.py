# -*- coding: utf-8 -*-
"""网卡信息探测（纯 ctypes，不起子进程）。

为什么不用 ``ipconfig /all``：
``ipconfig`` 是外部进程。Windows Defender 的行为规则与机器学习分类器
对「创建进程 + 采集网卡信息」这类组合敏感，PyInstaller 打包后更容易被
判定为可疑。这里改用 ``iphlpapi.dll`` 的 API 直接问系统要数据，
**不创建任何子进程、不调用 shell**。

提供三个能力：
    - :func:`adapters`          网卡清单（IPv4 / MAC / 默认网关 / 名称）
    - :func:`mac_for_ip`        按 IPv4 找对应网卡的 MAC
    - :func:`default_gateway`   取默认网关 IPv4
    - :func:`gateway_for_ip`    取某张网卡自己的网关（判断 NAT 更准）

结构体布局严格对照 Windows SDK ``iptypes.h``，不要凭记忆改字段顺序：

.. code-block:: c

    typedef struct _IP_ADDR_STRING {
        struct _IP_ADDR_STRING *Next;
        IP_ADDRESS_STRING IpAddress;   // char String[16]
        IP_MASK_STRING    IpMask;      // char String[16]
        DWORD             Context;
    } IP_ADDR_STRING;

    typedef struct _IP_ADAPTER_INFO {
        struct _IP_ADAPTER_INFO *Next;
        DWORD ComboIndex;
        char  AdapterName[MAX_ADAPTER_NAME_LENGTH + 4];
        char  Description[MAX_ADAPTER_DESCRIPTION_LENGTH + 4];
        UINT  AddressLength;
        BYTE  Address[MAX_ADAPTER_ADDRESS_LENGTH];
        DWORD Index;
        UINT  Type;
        UINT  DhcpEnabled;
        PIP_ADDR_STRING CurrentIpAddress;
        IP_ADDR_STRING IpAddressList;
        IP_ADDR_STRING GatewayList;
        IP_ADDR_STRING DhcpServer;
        WINBOOL HaveWins;
        IP_ADDR_STRING PrimaryWinsServer;
        IP_ADDR_STRING SecondaryWinsServer;
        __time64_t LeaseObtained;
        __time64_t LeaseExpires;
    } IP_ADAPTER_INFO;

注意 ``Description`` 是**定长 ANSI 数组**（不是指针，也不是 Unicode），
这是最容易踩坑的地方。

全部基于标准库 ctypes，不需要额外依赖。非 Windows 平台返回空。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
from typing import Dict, List, Optional

MAX_ADAPTER_DESCRIPTION_LENGTH = 128
MAX_ADAPTER_NAME_LENGTH = 256
MAX_ADAPTER_ADDRESS_LENGTH = 8

# GetAdaptersInfo 的错误码
_ERROR_BUFFER_OVERFLOW = 111


# ------------------------------------------------------------ 结构定义

class IP_ADDRESS_STRING(ctypes.Structure):
    _fields_ = [
        ("String", ctypes.c_char * 16),
    ]


class IP_MASK_STRING(ctypes.Structure):
    _fields_ = [
        ("String", ctypes.c_char * 16),
    ]


class IP_ADDR_STRING(ctypes.Structure):
    pass


IP_ADDR_STRING._fields_ = [
    ("Next", ctypes.POINTER(IP_ADDR_STRING)),
    ("IpAddress", IP_ADDRESS_STRING),
    ("IpMask", IP_MASK_STRING),
    ("Context", wt.DWORD),
]


class IP_ADAPTER_INFO(ctypes.Structure):
    pass


IP_ADAPTER_INFO._fields_ = [
    ("Next", ctypes.POINTER(IP_ADAPTER_INFO)),
    ("ComboIndex", wt.DWORD),
    ("AdapterName", ctypes.c_char * (MAX_ADAPTER_NAME_LENGTH + 4)),
    ("Description", ctypes.c_char * (MAX_ADAPTER_DESCRIPTION_LENGTH + 4)),
    ("AddressLength", wt.UINT),
    ("Address", ctypes.c_ubyte * MAX_ADAPTER_ADDRESS_LENGTH),
    ("Index", wt.DWORD),
    ("Type", wt.UINT),
    ("DhcpEnabled", wt.UINT),
    ("CurrentIpAddress", ctypes.POINTER(IP_ADDR_STRING)),
    ("IpAddressList", IP_ADDR_STRING),
    ("GatewayList", IP_ADDR_STRING),
    ("DhcpServer", IP_ADDR_STRING),
    ("HaveWins", wt.BOOL),
    ("PrimaryWinsServer", IP_ADDR_STRING),
    ("SecondaryWinsServer", IP_ADDR_STRING),
    ("LeaseObtained", ctypes.c_longlong),
    ("LeaseExpires", ctypes.c_longlong),
]


# ------------------------------------------------------------ DLL 句柄

_DLL = None
_DLL_READY = False


def _iphlpapi():
    """拿到 iphlpapi.dll 并声明好签名。"""
    global _DLL, _DLL_READY
    if _DLL_READY:
        return _DLL
    _DLL_READY = True
    try:
        dll = ctypes.WinDLL("iphlpapi.dll")
        dll.GetAdaptersInfo.argtypes = [
            ctypes.POINTER(IP_ADAPTER_INFO),
            ctypes.POINTER(wt.ULONG),
        ]
        dll.GetAdaptersInfo.restype = wt.ULONG
        _DLL = dll
    except Exception:
        _DLL = None
    return _DLL


def _is_windows() -> bool:
    return sys.platform == "win32"


# ------------------------------------------------------------ 内部工具

def _decode(raw: bytes) -> str:
    """ANSI 字节串 → 文本（中文系统按 GBK 解）。"""
    if not raw:
        return ""
    for enc in ("mbcs", "gbk", "latin-1"):
        try:
            return raw.decode(enc).strip()
        except Exception:
            continue
    return ""


def _trim(raw: bytes) -> str:
    """截断 C 字符串。"""
    if not raw:
        return ""
    return raw.split(b"\x00", 1)[0]


def _chain_texts(head) -> List[str]:
    """遍历 IP_ADDR_STRING 链表，取出所有非空文本。

    ``head`` 可以是结构体成员（IP_ADDR_STRING 实例）或指针。
    """
    out: List[str] = []
    try:
        cur = head if isinstance(head, ctypes.POINTER(IP_ADDR_STRING)) else \
            ctypes.pointer(head)
        guard = 0
        seen = set()
        while cur and guard < 32:
            guard += 1
            node = cur.contents
            text = _trim(bytes(node.IpAddress.String)).decode("ascii", "ignore").strip()
            if text and text not in seen:
                seen.add(text)
                out.append(text)
            nxt = node.Next
            if not nxt:
                break
            cur = nxt
    except Exception:
        pass
    return out


def _mac_bytes(info: IP_ADAPTER_INFO) -> str:
    """格式化物理地址（大写冒号分隔）。"""
    try:
        n = int(info.AddressLength)
        if n <= 0:
            return ""
        n = min(n, MAX_ADAPTER_ADDRESS_LENGTH)
        raw = bytes(bytearray(int(info.Address[i]) for i in range(n)))
        if not any(raw):
            return ""
        return ":".join(f"{b:02X}" for b in raw)
    except Exception:
        return ""


# ------------------------------------------------------------ 对外接口

def adapters() -> List[Dict[str, object]]:
    """返回网卡清单。

    每项形如::

        {"ip": "10.135.130.202", "mac": "08:BF:B8:83:FB:21",
         "gateway": "10.135.255.254", "name": "以太网", "index": 11}

    一张网卡有多个 IPv4 时只返回第一个非回环地址，其余进 ``extra_ips``。
    """
    if not _is_windows():
        return []
    dll = _iphlpapi()
    if dll is None:
        return []
    try:
        size = wt.ULONG(0)
        # 第一次调用只为拿缓冲区大小（返回 111 也算正常）
        dll.GetAdaptersInfo(None, ctypes.byref(size))
        if not size.value:
            return []
        buf = ctypes.create_string_buffer(size.value)
        rc = dll.GetAdaptersInfo(
            ctypes.cast(buf, ctypes.POINTER(IP_ADAPTER_INFO)),
            ctypes.byref(size),
        )
        if rc != 0:
            return []

        out: List[Dict[str, object]] = []
        node = ctypes.cast(buf, ctypes.POINTER(IP_ADAPTER_INFO))
        guard = 0
        while node and guard < 64:
            guard += 1
            try:
                info = node.contents
                ips = [x for x in _chain_texts(info.IpAddressList) if x != "0.0.0.0"]
                # CurrentIpAddress 可能不在 IpAddressList 首位，补一次
                if info.CurrentIpAddress:
                    for x in _chain_texts(info.CurrentIpAddress):
                        if x and x != "0.0.0.0" and x not in ips:
                            ips.append(x)
                gws = [g for g in _chain_texts(info.GatewayList) if g != "0.0.0.0"]
                mac = _mac_bytes(info)
                if ips or mac:
                    out.append({
                        "ip": ips[0] if ips else "",
                        "extra_ips": ips[1:],
                        "mac": mac,
                        "gateway": gws[0] if gws else "",
                        "gateways": gws,
                        "name": _decode(_trim(bytes(info.Description))),
                        "adapter_name": _trim(bytes(info.AdapterName)).decode(
                            "ascii", "ignore"),
                        "index": int(info.Index),
                    })
            except Exception:
                pass
            try:
                nxt = node.contents.Next
            except Exception:
                break
            if not nxt:
                break
            node = nxt
        return out
    except Exception:
        return []


def mac_for_ip(ip: str) -> str:
    """按 IPv4 找对应网卡的 MAC；找不到返回空串。"""
    if not ip:
        return ""
    for a in adapters():
        if a.get("ip") == ip or ip in (a.get("extra_ips") or []):
            mac = str(a.get("mac") or "")
            if mac:
                return mac
    return ""


def gateway_for_ip(ip: str) -> str:
    """取指定 IPv4 所属网卡的网关；找不到返回空串。"""
    if not ip:
        return ""
    for a in adapters():
        if a.get("ip") == ip or ip in (a.get("extra_ips") or []):
            gw = str(a.get("gateway") or "")
            if gw:
                return gw
    return ""


def default_gateway() -> str:
    """取默认网关 IPv4（取第一个非零的网关地址）。"""
    for a in adapters():
        gw = str(a.get("gateway") or "")
        if gw and gw != "0.0.0.0":
            return gw
    return ""


if __name__ == "__main__":  # 手动自检
    print("默认网关:", default_gateway())
    print("网卡列表:")
    for a in adapters():
        print(f"  {a['ip']:<16} {a['mac']:<18} gw={a['gateway']:<16} "
              f"{a['name']} [{a['index']}]")
