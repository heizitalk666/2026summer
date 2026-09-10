# RK3576 RKNN 转换记录

**2026-09-09 组长在 WSL2 上完成。**任务书第 2 项点名要的 INT8 掉点数,现在有了。

`rknn_export.md` §3 原先写的是「rknn-toolkit2 仅支持 x86 Linux,本机 Windows
且无 WSL」——前半句对,后半句已不成立。

## 结果

| 模型 | 量化 | RKNN 大小 | 相对 ONNX | 余弦均值 | 余弦最小 | 相对 L1 |
|---|---|---|---|---|---|---|
| padim_net2 | **fp(对照)** | 1.49 MB | 1.84× | 0.999997 | 0.999997 | **0.22 %** |
| padim_net2 | **int8** | 0.82 MB | 3.33× | 0.998603 | 0.998226 | **5.40 %** |
| padim_net3 | **fp(对照)** | 5.72 MB | 1.95× | 0.999999 | 0.999999 | **0.15 %** |
| padim_net3 | **int8** | 2.97 MB | 3.74× | 0.998899 | 0.998621 | **4.96 %** |

**FP 对照组是这张表的关键。**没有它,INT8 那两行说明不了任何事——误差可能来自
转换、来自预处理、来自量化,分不开。对照组余弦 0.999999 证明转换路径与预处理
都正确,所以 INT8 那 5 % 可以干净地归给量化本身。

第一次跑出来余弦只有 0.72、相对误差 77 %,那不是量化掉点,是**输入喂错了**:
给 RKNN 传了已经 `/255` 的 float,而 config 里又写着 `std_values=[255,255,255]`,
等于除了两次 255。这类错误不会报错,只会得到一个看着"合理"的坏数字。

## 怎么复现

```bash
# WSL2 / 任意 x86_64 Linux。不需要 root,不需要板子。
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.12
mkdir -p ~/rknn && cd ~/rknn && uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python rknn-toolkit2==2.3.2 \
    "numpy==1.26.4" "opencv-python-headless==4.10.0.84" onnxruntime "setuptools==80.9.0"
# 把 training/runs/anomaly/padim_net{2,3}.onnx 放进 ~/rknn/onnx/
# 把 100 张正常裁片放进 ~/rknn/calib/ 并 ls > calib_list.txt
./.venv/bin/python convert_rknn.py
```

### 两个坑(都会让转换直接失败)

1. **`setuptools` 必须 <81。**81+ 移除了 `pkg_resources`,而 `rknn_base` 还在
   import 它,否则 `ModuleNotFoundError: No module named 'pkg_resources'`。

2. **`onnx.mapping` 要打垫片。**rknn-toolkit2 2.3.2 的 metadata 声明
   `onnx>=1.16.1`,但代码里调 `onnx.mapping.TENSOR_TYPE_TO_NP_TYPE`——而
   `onnx.mapping` 正是在 **onnx 1.16.0 被删除**的。**它声明的下界和它能跑的
   上界是矛盾的**,装任何满足声明的 onnx 都会
   `AttributeError: module 'onnx' has no attribute 'mapping'`。
   onnx 1.14/1.15 在 Python 3.12 上没有预编译 wheel(要 cmake 从源码编),
   所以这里选择用 `onnx_mapping_shim.py` 按新 API 把那个只读映射重建回去。

脚本 `convert_rknn.py` 与 `onnx_mapping_shim.py` 见本目录。

## 仍未验证

**板端速度。**本次跑的是 x86 模拟器(`init_runtime(target=None)`),不是 RK3576
真机 NPU。**掉点数有效,速度数没有** ——那一条仍然等硬件,`rknn_export.md` §5
写得对,不用改。
