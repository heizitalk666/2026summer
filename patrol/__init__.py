"""RK3576 无人车主动式 AI 巡检系统。

四个节点进程（perception / mission / gateway / uploader）之间只通过
ICD-RK3576-PATROL 定义的四条接口通信，硬件访问全部经由 drivers 层的
四个抽象基类，桩与真机由 drivers.factory 单点切换。
"""

__version__ = "0.1.0"
SCHEMA_VERSION = "1.0.0"
