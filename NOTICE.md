# 代码来源与边界

本仓库提供独立编写的 Python / HTML / CSS / JavaScript 实现，采用 MIT 许可证。
其中 `checksum.py` 及其算法测试来自本项目整理前已独立编写的校验引擎，现调整为可移植模块。
没有将原软件的加密 JavaScript、Go 可执行文件、Electron 运行时或解密密钥作为开源代码包含。

默认演示数据库、地图 JSON 和 BIN 由 `make_demo.py` 合成，可随代码再分发。
第三方数据库与地图附件不适用本代码的 MIT 许可证。其导入与再分发边界见 DATA_POLICY.md。

CRC32 使用 Python 标准库 zlib.crc32；不捆绑 Python 或第三方依赖。
