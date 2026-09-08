"""RK3576 无人车主动式 AI 巡检系统。

四个节点进程（perception / mission / gateway / uploader）之间只通过
ICD-RK3576-PATROL 定义的四条接口通信，硬件访问全部经由 drivers 层的
四个抽象基类，桩与真机由 drivers.factory 单点切换。
"""

__version__ = "0.1.0"
#: 报文版本。沿革：
#:
#: - ``1.0.0``  ICD v1.0 冻结版
#: - ``1.1.0``  差异清单 A4 的 ``detections[].quality`` 与 ``QUALITY_LOW``。
#:              新增可选字段与新增枚举值按 ICD §0 只需次版本号 +1，桩不用改
#: - ``2.0.0``  **D3 评审决议落地（ICD v2.0）**。其中 D1 改了 ``brake_latency_ms``
#:              的取值范围、D3 改了 ``evidence_package.l2_reading`` 的类型，
#:              两条都是「修改字段语义/类型/范围」，按规则主版本号 +1
#:
#: **接收方只比主版本号**，所以 1.x 与 2.0.0 的报文不再互通，混跑会被判
#: ``SCHEMA_VERSION_MISMATCH`` 并丢弃。这正是要的行为：D1 之后
#: ``brake_latency_ms = 150`` 是合法报文，1.x 的接收方会把它当非法丢掉，
#: 而那恰好是最该留证的一条。
SCHEMA_VERSION = "2.0.0"
