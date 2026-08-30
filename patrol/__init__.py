"""RK3576 无人车主动式 AI 巡检系统。

四个节点进程（perception / mission / gateway / uploader）之间只通过
ICD-RK3576-PATROL 定义的四条接口通信，硬件访问全部经由 drivers 层的
四个抽象基类，桩与真机由 drivers.factory 单点切换。
"""

__version__ = "0.1.0"
#: 报文版本。1.0.0 是 ICD 冻结版；1.1.0 加入了差异清单 A4 的 detections[].quality
#: 可选字段与 QUALITY_LOW 触发规则——按 ICD 的冻结规则，新增可选字段与新增
#: 枚举值只需次版本号 +1、通知即可，不用全组重评审、不用改桩。
#: 接收方只比主版本号，所以 1.1.0 与 1.0.0 的报文可以互通。
SCHEMA_VERSION = "1.1.0"
