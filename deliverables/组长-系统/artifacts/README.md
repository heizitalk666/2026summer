# artifacts/ —— 供复现的产物

| 目录 / 文件 | 内容 | 出自 |
|---|---|---|
| `calib/calibration.md` | 标定记录：曲线系数、线性度、重复性、基本误差、逐点极差 | `calibrate` |
| `calib/calibration.csv` | 五点各 10 次的**逐次原始读数**，含置信度与轴比 | 同上 |
| `calib/pixel_density.csv` | 像素密度标定：实测 px vs 公式 px、反解视场角 | 同上 |
| `pid/pid_report.json` | 整定扫描表 + 三组阶跃响应的超调/调节时间/稳态误差 | `tune_pid --tune` |
| `pid/step_response.csv` | 阶跃响应逐采样点数据 | 同上 |
| `repeatability_seeds.csv` | N 次独立标定的线性度/重复性/基本误差 | `make_figures.py` |
| `stability.txt` | 三轮 300 s 连续运行的小结与耗时 | `run_all` |
| `offline_cache.txt` | 断网 75 s 的证据包与 `upload_state` 原始输出 | `run_all --no-cloud` |

## 两份原始数据别丢

**`calibration.csv` 的价值在逐次数据，不在汇总。** 汇总的三个数（线性度、
重复性、基本误差）只能说"达没达标"，说不清**为什么不达标**。逐次数据里能直接
看出来：五个标定点里第三点（指针指向正上方）的极差比其余四点大一个量级，
而且极差最大的那次读数，算法自己给的 `confidence` 就是低的。
这一条是一页纸「未做 / 未验证」栏里那条待办的全部依据。

**`pid_report.json` 的 `sweep` 段要留着。** 临界比例度法在本对象上没扫出等幅
振荡（阻尼较大，Kp 加到 2.6 才出现 1 次过零），所以最终参数是沿用配置值而不是
Ziegler-Nichols 算出来的——**这件事必须能查到证据**，否则"整定过"这句话没法自证。

本目录不含权重（组长这一路没有模型），故没有 `where.txt`。
