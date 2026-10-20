# 校验规则

规则 JSON：`{"schema":1,"profiles":[...]}`。完整合成示例见 `data/demo/checksum_profiles.json`。

每个规则指定唯一 id、name、version、文件 size、固定 anchors、evidence 和 blocks。
校验必须匹配文件长度、版本（如已识别）及至少 8 字节非恒定固定特征。

- `blocks[].algorithm`：crc32、sum16 或 sum32。
- `regions`：文件偏移 `[start,end)` 数组，按声明顺序计算，end 不包含。
- `store_offset`：存储校验值的文件偏移。
- `byteorder`：存储字段的 little / big。
- `word_byteorder`：累加字的 little / big。
- `output`：direct / negate / ones_complement。
- JSON 整数是十进制，地址也可用 `"0x..."` 字符串。

CRC32 使用 zlib.crc32；SUM16 / SUM32 为无符号字累加并取模。
不支持校验字段包含在计算区、校验字段相互重叠、嵌套校验、OEM 专有算法或数字签名重签。
不能以空值、填充或通用字符串命中就宣称识别真实 ECU。

修正前必须有同规则且校验通过的原始 BIN。校验字段在副本中修改，全部复验通过后才提交；报告记录偏移、旧／新值及 SHA-256。
未匹配规则、歧义规则或无有效基准时不会猜测修正，但允许下载原样文件并保留未支持／未通过状态。

规则 evidence 是贡献者填写的证据说明，不是第三方认证。引擎单元测试通过不代表真实 ECU 算法通过验证。
参考：https://docs.python.org/3/library/zlib.html#zlib.crc32
