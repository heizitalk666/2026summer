# RKNN 导出记录(PaDiM 主干)

对应任务书第 2 项「模型部署」的导出链路。**如实记录:导出与冒烟在本机完成,
RKNN 转换与 INT8 掉点未验证——缺 Linux 环境与板子。**

## 1. ONNX 导出 —— ✅ 成功

```bash
python out/l3_eval/export_onnx.py
```

| 产物 | 输入 | 输出 | 大小 |
|---|---|---|---|
| `training/runs/anomaly/padim_net2.onnx` | [1,3,256,256] float32(0-1,RGB) | [1,128,32,32] | ~2.9 MB |
| `training/runs/anomaly/padim_net3.onnx` | 同上 | [1,256,16,16] | ~5.8 MB |

opset 12(rknn-toolkit2 对更高版本算子支持有缺口,沿用仓库 export_rknn.py
的约定)。部署侧打分 = 两个 ONNX 前向 + 权重文件里的 mu/var 做逐位置马氏
距离(纯张量运算,无需再训练)。教师/学生的 resnet18 预训练主干由
torchvision 提供,权重文件里只存 student/μ/σ 增量部分。

## 2. ONNX 冒烟推理 —— ✅ 通过

- onnxruntime 与 torch 前向**逐元素比对:net2 最大差 3.4e-06、net3 最大差
  2.7e-06**(float32 舍入量级,一致)
- 按部署语义完整打分(ONNX 前向 + numpy 马氏距离):正常裁片 top-k 均值
  **1.13**,异常裁片 **243.49**——分离 215 倍,与 torch 版打分行为一致
- 记录文件:`training/runs/anomaly/onnx_smoke.json`

## 3. INT8 掉点 —— ❌ 未验证(缺环境)

rknn-toolkit2 仅支持 x86 Linux,本机 Windows 且无 WSL。转换脚本与流程已备好
(`training/export_rknn.py` 的 `to_rknn`,目标平台 rk3576、非对称 INT8),
拿到 Linux 环境后两条命令即可跑通;掉点数字届时补记,**现在不编**。

## 4. 量化校准集 —— ✅ 已备好,来自本场景

`training/runs/calib.txt`(由 `build_calibration_set` 从 `evidence/` 的证据包
提取 **cruise_raw.jpg** 无标注原图,共 N 张)。**不是 COCO**——配电室低照度、
高对比表盘的定标分布必须用本场景图,这条是仓库 export_rknn.py 的既有纪律。

## 5. 上板测速 —— ❌ 未验证,缺硬件

RK3576 板子未到货。板到后:rknpu2 runtime 加载 .rknn → 单帧测速 → 与
PC CPU 的 22 ms/检出 对比。现在不编数。
