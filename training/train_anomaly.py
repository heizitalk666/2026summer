#!/usr/bin/env python3
"""L3 未知异常检测的训练入口。

    python -m training.train_anomaly --data training/datasets/normal_patches

**非监督，只用"看起来正常"的样本。**这正好绕开方案书 §6.2.1 那条卡死外观
缺陷的数据可得性约束：室内配电室的渗漏油、呼吸器变色、积水几乎没有公开的
标注数据，但"正常设备的图"要多少有多少——巡检跑一轮就攒下一批。

两条通路，按依赖是否装齐自动选：

``efficientad``
    有 torch 时训一个学生-教师蒸馏模型（EfficientAD 的简化版：教师是冻结的
    预训练特征提取器，学生学它在正常样本上的输出；异常处学生学不像，特征
    距离就大）。产物是 ``.pt``，由 ``perception/anomaly.py`` 的 EfficientAD
    通路加载。

``statistical``
    没有 torch 时退回统计法——在线估计正常样本特征的均值与标准差，按偏离
    几个 σ 打分。这条通路**不需要训练**，系统跑起来就在学，所以这里做的是
    "把已经跑出来的正常分布固化成一份基线"，供下次冷启动直接加载，省掉
    30 个样本的预热期。

无论哪一条，L3 的输出**只允许进人工复核队列，不得直接告警**（ICD §3.1）。
这条约束在 uploader/packer.py 的 decide_verdict 里落地：有二级读数时以读数
为准，L3 排在它后面。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def _iter_images(root: Path):
    import cv2
    exts = {".jpg", ".jpeg", ".png", ".bmp"}
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() in exts:
            img = cv2.imread(str(p))
            if img is not None:
                yield p, img


def collect_from_evidence(evidence_dir: Path, out_dir: Path,
                          verdicts=("READING_OK", "FALSE_ALARM")) -> int:
    """从跑过的证据包里挑"正常"样本。

    只收结论是 READING_OK 或 FALSE_ALARM 的那些——前者是读数正常的表计，
    后者是被复核否掉的误报，两类都属于"正常外观"。**绝不能收
    READING_ABNORMAL 与 CONFIRMED_DEFECT**：把缺陷样本喂进正常分布，异常
    检测就学会把缺陷当正常了，这是非监督方法最容易踩的坑。
    """
    import cv2
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for mf in sorted(evidence_dir.glob("*/*/manifest.json")):
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if m.get("verdict", {}).get("result") not in verdicts:
            continue
        roi = mf.parent / "verify_roi.jpg"
        if not roi.exists():
            continue
        img = cv2.imread(str(roi))
        if img is None:
            continue
        cv2.imwrite(str(out_dir / ("%s.jpg" % m["event_id"][:8])), img)
        n += 1
    return n


def train_statistical(data: Path, out: Path) -> dict:
    """固化一份正常分布基线，供 StatisticalAnomaly 冷启动加载。"""
    from patrol.perception.anomaly import StatisticalAnomaly

    det = StatisticalAnomaly(threshold=0.55, warmup=0)
    n = 0
    for _, img in _iter_images(data):
        h, w = img.shape[:2]
        det.observe_normal(img, (0.0, 0.0, float(w), float(h)))
        n += 1
    if n == 0:
        raise SystemExit("在 %s 下没有找到任何图片" % data)
    baseline = det.export_baseline()
    baseline["samples"] = n
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(baseline, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    return {"backend": "statistical", "samples": n, "out": str(out)}


def train_efficientad(data: Path, out: Path, *, epochs: int, device: str) -> dict:
    """学生-教师蒸馏。只在装了 torch 时可用。"""
    try:
        import torch                              # noqa: PLC0415
        import torch.nn as nn                     # noqa: PLC0415
        from torchvision import models, transforms  # noqa: PLC0415
    except ImportError:
        raise SystemExit(
            "EfficientAD 通路需要 torch/torchvision：\n"
            "    pip install -r requirements-yolo.txt\n"
            "没有 torch 时用 --backend statistical，全链路照常跑。")

    tf = transforms.Compose([transforms.ToTensor(),
                             transforms.Resize((256, 256), antialias=True)])
    xs = []
    for _, img in _iter_images(data):
        xs.append(tf(img[:, :, ::-1].copy()))
    if not xs:
        raise SystemExit("在 %s 下没有找到任何图片" % data)
    x = torch.stack(xs).to(device)

    # 教师：冻结的预训练主干，只取浅层特征（纹理层，异常最敏感）
    teacher = nn.Sequential(*list(models.resnet18(weights="DEFAULT").children())[:6])
    teacher.eval().to(device)
    for p in teacher.parameters():
        p.requires_grad_(False)
    # 学生：同构但随机初始化，只在正常样本上学教师的输出
    student = nn.Sequential(*list(models.resnet18(weights=None).children())[:6]).to(device)
    opt = torch.optim.Adam(student.parameters(), lr=1e-4)

    for ep in range(int(epochs)):
        opt.zero_grad()
        with torch.no_grad():
            t = teacher(x)
        loss = ((student(x) - t) ** 2).mean()
        loss.backward()
        opt.step()
        print("epoch %3d/%d  loss=%.6f" % (ep + 1, epochs, float(loss)))

    out.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"student": student.state_dict(), "arch": "resnet18-layer2",
                "samples": len(xs)}, out)
    return {"backend": "efficientad", "samples": len(xs), "out": str(out)}


def main() -> int:
    ap = argparse.ArgumentParser(description="L3 未知异常检测训练")
    ap.add_argument("--data", default="training/datasets/normal_patches",
                    help="正常样本目录")
    ap.add_argument("--from-evidence", default=None,
                    help="先从证据包目录里挑正常样本填充 --data")
    ap.add_argument("--backend", choices=["auto", "efficientad", "statistical"],
                    default="auto")
    ap.add_argument("--out", default=None)
    ap.add_argument("--epochs", type=int, default=50)
    ap.add_argument("--device", default="cpu")
    a = ap.parse_args()

    data = Path(a.data)
    if a.from_evidence:
        n = collect_from_evidence(Path(a.from_evidence), data)
        print("从证据包里挑出 %d 张正常样本 → %s" % (n, data))
    if not data.exists():
        raise SystemExit("样本目录不存在：%s（先跑 --from-evidence 或手工放图）" % data)

    backend = a.backend
    if backend == "auto":
        try:
            import torch  # noqa: F401,PLC0415
            backend = "efficientad"
        except ImportError:
            backend = "statistical"
            print("未检测到 torch，退回统计法基线（全链路照常可跑）")

    if backend == "efficientad":
        out = Path(a.out or "training/runs/anomaly/efficientad.pt")
        info = train_efficientad(data, out, epochs=a.epochs, device=a.device)
    else:
        out = Path(a.out or "training/runs/anomaly/baseline.json")
        info = train_statistical(data, out)

    print(json.dumps(info, ensure_ascii=False, indent=1))
    print("\n把它接进去：configs/system.yaml → perception.l3.weights: %s" % info["out"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
