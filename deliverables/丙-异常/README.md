# L3 未知异常检测（非监督） 交付

> 状态：**空模板，待丙填**。带 `⬜` 的是要填的格子。
> 模板出处：[`docs/交付物清单.md`](../../docs/交付物清单.md) §一页纸模板。
> 这一页要直接粘进 PPT，**保持一页**；长的东西放 [`避坑清单.md`](避坑清单.md)。
>
> 分工书里丙这一节：`docs/后续计划与分工.md:241-278`。
> **你是三个人里唯一第一天就能开工的**——正常样本本地生成，不用等任何下载。

## 一句话结论

⬜ 一句话。例："在同一批 xx 正常 / xx 异常样本上，EfficientAD 的误报 x %、
漏报 x %，与零权重统计法的 x % / x % 持平/更好/更差；权重 xx MB，
INT8 掉点 x 个百分点。"

**赢不了统计法也是结论**，写清楚即可——任务书要的是「调研并比选后确定方案」。

## 关键数字

| 方法 | 误报率 | 漏报率 | 权重大小 | 可解释 | 结论 |
|---|---|---|---|---|---|
| 统计法（现默认，零权重） | ⬜ | ⬜ | 0 | ✅ 说得清哪个通道 | 基线 |
| EfficientAD（你训的） | ⬜ | ⬜ | ⬜ | ❌ | ⬜ |

> ⚠ **两行必须在同一批样本上测**，且样本数要写出来。
> 统计法在现有样本上的成绩是 **72 正常 / 12 异常，误报漏报皆为 0**
> （`patrol/perception/anomaly.py:84` 的 docstring）。
> **在同一批样本上它已经满分，学习法最多打平**——所以这张表要有意义，
> 得先有一批更难的样本。详见 [`避坑清单.md`](避坑清单.md) 第 1 节。

**部署指标**（方案书要的那一组）：

| 项 | FP32 | INT8 | 掉点 |
|---|---|---|---|
| 异常分 AUC（或误报/漏报） | ⬜ | ⬜ | ⬜ |
| 单帧耗时 | ⬜ | ⬜ | — |
| 权重大小 | ⬜ | ⬜ | — |

## 图

| 文件 | 内容 | 进 PPT 哪一页 | 状态 |
|---|---|---|---|
| `figures/score_dist.png` | 正常 vs 异常的异常分分布（两个直方图叠一起） | 异常检测页 | ⬜ |
| `figures/compare.png` | 统计法 vs EfficientAD | 方案比选页 | ⬜ |

`score_dist.png` 是这一路最好讲的一张图：**两堆分数分得开不开，一眼就看出来**。
坐标轴记得标 σ 或阈值线。

## 怎么复现

```bash
# 正常样本：合成
python -m training.gen_synthetic --n 300 --out training/datasets/normal_patches
python -m training.train_anomaly --data training/datasets/normal_patches --device ⬜

# 更好的一条路：拿跑出来的证据包当正常样本，比合成的更接近真实分布
python -m patrol.tools.run_all --seconds 300
python -m training.train_anomaly --from-evidence evidence/ --device ⬜

# 导出（量化校准集默认就从 evidence/ 取，见 避坑清单 第 3 节）
python -m training.export_onnx --weights ⬜ --out artifacts/l3.onnx
python -m training.export_rknn --weights ⬜
```

`--backend` 可选 `auto` / `efficientad` / `statistical`，默认 `auto`：
依赖装齐走 EfficientAD，装不齐自动退回统计法——**装不上也不会卡住你**。

## 未做 / 未验证

⬜ 老实写，没有就写「无」。
