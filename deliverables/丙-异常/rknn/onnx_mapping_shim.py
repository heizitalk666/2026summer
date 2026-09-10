"""补回 onnx.mapping —— rknn-toolkit2 2.3.2 的兼容垫片。

**为什么需要它。** rknn-toolkit2 2.3.2 的 metadata 声明 `onnx>=1.16.1`，
但 `rknn/api/base_utils.py:to_np_type` 里调的是 `onnx.mapping.TENSOR_TYPE_TO_NP_TYPE`
—— 而 `onnx.mapping` 恰恰是在 **onnx 1.16.0 被删掉**的。所以它声明的下界
和它实际能跑的上界是矛盾的：装任何满足它声明的 onnx，load_onnx 都会
`AttributeError: module 'onnx' has no attribute 'mapping'`。

onnx 1.14/1.15 在 Python 3.12 上没有预编译 wheel（要 cmake 从源码编），
所以这里选择把这个只读映射按新 API 重建回去，而不是降 Python。

映射内容与被删掉的那份等价：TensorProto 的枚举值 → numpy dtype，
数据来源是 onnx 官方现在推荐的 `helper.tensor_dtype_to_np_dtype`。
"""
import types

import numpy as np
import onnx
from onnx import TensorProto, helper


def install() -> bool:
    """已经有 mapping 就什么都不做；返回是否打了补丁。"""
    if hasattr(onnx, "mapping"):
        return False

    t2np = {}
    for name, val in TensorProto.DataType.items():
        if name == "UNDEFINED":
            continue
        try:
            t2np[int(val)] = helper.tensor_dtype_to_np_dtype(val)
        except Exception:                                  # noqa: BLE001
            # BFLOAT16 / FLOAT8* 这些新类型在旧 mapping 里本来也没有
            continue

    np2t = {}
    for k, v in t2np.items():
        np2t.setdefault(np.dtype(v), k)

    m = types.ModuleType("onnx.mapping")
    m.TENSOR_TYPE_TO_NP_TYPE = t2np
    m.NP_TYPE_TO_TENSOR_TYPE = np2t
    m.TENSOR_TYPE_MAP = t2np
    m.STORAGE_TENSOR_TYPE_TO_FIELD = {
        TensorProto.FLOAT: "float_data",
        TensorProto.INT32: "int32_data",
        TensorProto.INT64: "int64_data",
        TensorProto.STRING: "string_data",
        TensorProto.DOUBLE: "double_data",
        TensorProto.UINT64: "uint64_data",
    }
    onnx.mapping = m
    import sys
    sys.modules["onnx.mapping"] = m
    return True
