# RK3576 部署

边缘端四个进程（网关、感知、任务、上传）装到 RK3576 上运行。部署包里不含 torch 和
ultralytics：检测器与 L3 的特征网络在 NPU 上跑（rknn-toolkit-lite2），打分与后处理用 numpy。

## 1. 包里有什么

| 路径 | 内容 |
|---|---|
| `patrol/` `cloud/` | 代码（云端可以不在车上起） |
| `configs/rk3576.yaml` | 上车配置：`driver_mode: real`、`detector: rknn`、L3 `padim_np`，检测器实际精度已写入 |
| `models/cruise_ft.rknn` `models/verify_ft.rknn` | 两级 YOLO11 检测器 |
| `models/padim_net2.rknn` `models/padim_net3.rknn` `models/padim_cov_stats.npz` | L3 特征网络与统计量 |
| `deploy/install.sh` `deploy/run_edge.sh` `deploy/systemd/` | 安装脚本、前台启动脚本、systemd 服务 |
| `SHA256SUMS` `VERSION` | 校验和、提交号与构建时间 |

精度怎么定的：

- **检测器 FP16。**依据 `deliverables/甲-检测/rknn/rknn_report.json`：在 RKNN 模拟器上与 ONNX(FP32)
  逐框对照，INT8 相对 FP32 召回 ≥ 0.99 且置信度平均漂移 ≤ 0.05 才用 INT8，否则用 FP16。
  置信度也要管，因为复核触发按 0.25–0.60 的置信度带判。实测 cruise_ft 的 INT8 召回 1.0 但漂移 0.073，
  verify_ft 的 INT8 召回 0.961，两个都用 FP16；FP16 在 40 帧上与 FP32 逐框一致（漂移 ≤ 0.0003）。
- **L3 特征网络 INT8。**依据 `deliverables/丙-异常/rknn/padim_bench_rknn.json`：整套 L3 评测集
  （106 正常 / 120 异常）上误报 4、漏报 4，与 ONNX 相同，没有一个判定翻转。

## 2. 在开发机上打包

需要先有这几样东西：

```bash
# a. 从部署权重导 ONNX（Windows，ultralytics）
python -m training.export_onnx --detector training/runs/cruise_ft/weights/best.pt --imgsz 1280 --out-dir artifacts/cruise_ft
python -m training.export_onnx --detector training/runs/verify_ft/weights/best.pt --imgsz 1280 --out-dir artifacts/verify_ft
#    （产物名为 detector.onnx，改名为 best.onnx）

# b. 检测器转 RKNN 并在模拟器上评测（WSL2 / x86 Linux，rknn-toolkit2 2.3.2）
~/rknn/.venv/bin/python training/export_rknn_yolo.py --repo /mnt/c/.../2026summer-main --work ~/rknn/yolo

# c. 导出 PaDiM 统计量（Windows，需要 torch）
python -m training.export_padim_stats

# d. L3 特征网络转 RKNN，并在 L3 评测集上量误报漏报（WSL2）
~/rknn/.venv/bin/python training/eval_padim_rknn.py --repo /mnt/c/.../2026summer-main \
    --calib-list ~/rknn/calib_list.txt --work ~/rknn/padim
```

然后：

```bash
python deploy/build_package.py \
    --yolo-rknn-dir  "\\wsl.localhost\Ubuntu\home\<用户>\rknn\yolo\out" \
    --padim-rknn-dir "\\wsl.localhost\Ubuntu\home\<用户>\rknn\padim\out" --padim-dtype int8
```

输出 `dist/patrol-rk3576-<日期>.tar.gz`。`models/` 与 `dist/` 都不进版本库。

## 3. 装到 RK3576

板子要求：aarch64 Linux，Python ≥ 3.10，已装 RKNPU 驱动（官方镜像自带）。

```bash
tar xzf patrol-rk3576-<日期>.tar.gz && cd patrol-rk3576-<日期>
sudo ./deploy/install.sh              # 装到 /opt/patrol，注册 systemd 服务，不自动启动
```

**启动前现场必须改的配置**（改 `/opt/patrol/configs/` 下的文件）：

1. `real.yaml`：底盘与云台串口（`real.serial.chassis.port`、`real.serial.ptz.port`）、相机设备号
2. `scene.yaml` 的 `targets`：现场每台设备的位置、朝向、量程先验。这就是读数用的标定表，
   真机上读数先验按「设备位置 + 车体位姿 + 云台指向」投影匹配（`perception/node.py::_priors_for`），
   标定表不对，读数就不产出
3. `waypoints.yaml`：现场巡检路线与巡检位
4. 按 ICD §3.2：实测镜头视场角与 60° 偏差超过 3° 时，按实测值改 `optics.hfov_at_1x_deg` 并重算 d_max

```bash
sudo systemctl start patrol.target
journalctl -u 'patrol-*' -f
tail -f /opt/patrol/logs/perception.jsonl
```

进程异常退出时，死因写在 `logs/<进程名>.jsonl` 的 `CRITICAL` 记录里（带完整 traceback）。

## 4. 验证

```bash
cd /opt/patrol
PYTHONPATH=. venv/bin/python -m patrol.tools.validate          # 接口一致性
PATROL_CONFIG=configs/system.yaml ./deploy/run_edge.sh --seconds 120   # 桩模式试跑整条链路
./deploy/run_edge.sh --seconds 120                             # 接真机
```

没接硬件时可以先用假小车调串口（`python -m patrol.tools.fakecar --pty`），见 `docs/底盘串口协议.md`。

## 5. 回退

- NPU 运行时装不上：`configs/rk3576.yaml` 里 `detector: onnx`、L3 `backend: onnx`，并把
  `perception.onnx` 与 `perception.l3.net2/net3` 指到 ONNX 文件（CPU 上 1280 输入跑不进 100 ms 节拍，只作应急）
- L3 加载失败会自动退回统计法，感知节点不会因此停机

## 6. 尚未在真机上验证的

以下只在 x86 上的 RKNN 模拟器与虚拟配电室里验证过，上板后要实测：

- NPU 单帧耗时（模拟器的耗时不代表板上速度）
- 真实相机、串口、云台时序
- 现场光照下的检测与读数效果
