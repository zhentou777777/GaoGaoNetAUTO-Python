# -*- coding: utf-8 -*-
"""测试环境隔离。

**所有测试都必须跑在临时数据目录里。**

历史问题：测试里直接 ``Config()`` / ``Controller()``，会读写
``%LOCALAPPDATA%\\GaoGaoNetAUTO\\config.json`` —— 也就是**用户真实的配置**。
其中 ``test_account_masked`` 的收尾里调了 ``clear_credentials()``，
在开发机上跑一次测试，用户保存的 Token / 手机号 / 密码就被清空了。
另外删除真实目录里的文件还会触发各种文件保护钩子，把测试拖到超时。

这里用 session 级的 autouse fixture 统一改写 ``GAOGAO_DATA_DIR``，
任何测试都不再有机会碰到真实目录。
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolated_data_dir(tmp_path_factory):
    """把数据目录指到临时目录（对所有测试自动生效）。"""
    d = tmp_path_factory.mktemp("gaogao-data")
    os.environ["GAOGAO_DATA_DIR"] = str(d)
    yield d
    os.environ.pop("GAOGAO_DATA_DIR", None)
