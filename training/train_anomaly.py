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

from patrol.common import imio


def _iter_images(root: Path):
    import cv2
    exts = {".jpg", ".jpeg", ".png", ".bmp"}
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() in exts:
            img = imio.imread(p)
            if img is not None:
                yield p, img


def _iter_normal(root: Path):
    """训练用:只吃 gen_synthetic 的 normal/ 子目录(若存在)。

    **不能整目录 rglob**:gen_synthetic 的产物里 images/ 是完整帧——里面
    可能包含 FOREIGN_OBJECT 异常目标,check/ 还是带标注叠加的核对图。
    把这两样喂进非监督正常分布,等于教模型"异常也是正常"(实测 EfficientAD
    在脏数据上异常分倒挂、漏报 100 %)。没有 normal/ 子目录时才退回全目录
    (手工收集的纯正常图目录)。
    """
    d = root / "normal"
    if d.is_dir():
        yield from _iter_images(d)
    else:
        yield from _iter_images(root)


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
        img = imio.imread(roi)
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
    for _, img in _iter_normal(data):
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


def train_padim(data: Path, out: Path, *, device: str) -> dict:
    """PaDiM(对角协方差简化版)。任务书点名的第二种非监督方法。

    不用训学生:冻结的预训练主干提取 layer2+layer3 特征(384 通道),在正常
    样本上对每个空间位置拟合均值/方差,异常分 = 逐位置马氏距离的 top-k 均值。
    完整版 PaDiM 用随机子集上的全协方差,这里取对角——样本 259 张 < 特征
    维度,全协方差必然奇异,对角是该数据量下的诚实选择(见交付记录)。

    与 efficientad 通路的关系:哪个进系统由同一批评测集上的对比数据决定,
    不是预先拍板——两种方法都训,交付记录里给结论。
    """
    try:
        import torch                              # noqa: PLC0415
        import torch.nn as nn                     # noqa: PLC0415
        from torchvision import models, transforms  # noqa: PLC0415
    except ImportError:
        raise SystemExit("PaDiM 通路需要 torch/torchvision(见 requirements-yolo.txt)")

    tf = transforms.Compose([transforms.ToTensor(),
                             transforms.Resize((256, 256), antialias=True)])
    children = list(models.resnet18(weights="DEFAULT").children())
    net2 = nn.Sequential(*children[:6]).eval().to(device)   # layer2: 128ch @ 32×32
    net3 = nn.Sequential(*children[:7]).eval().to(device)   # layer3: 256ch @ 16×16

    def features(bgr: "np.ndarray"):
        x = tf(bgr[:, :, ::-1].copy()).unsqueeze(0).to(device)
        with torch.no_grad():
            f2 = net2(x)
            f3 = nn.functional.interpolate(
                net3(x), size=f2.shape[2:], mode="bilinear", align_corners=False)
        return torch.cat([f2, f3], dim=1)[0]                 # [384, 32, 32]

    n = 0
    sum1 = sum2 = None
    for _, img in _iter_normal(data):
        f = features(img)
        if sum1 is None:
            sum1 = torch.zeros_like(f)
            sum2 = torch.zeros_like(f)
        sum1 += f
        sum2 += f * f
        n += 1
    if n == 0:
        raise SystemExit("在 %s 下没有找到任何图片" % data)
    mu = sum1 / n
    var = (sum2 / n - mu * mu).clamp(min=1e-6)

    # 训练集上每个样本的 top-k 分数,拟合均值/标准差供推理归一化
    topk_frac = 0.1
    k = max(1, int(mu.shape[1] * mu.shape[2] * topk_frac))
    scores = []
    for _, img in _iter_normal(data):
        with torch.no_grad():
            d = ((features(img) - mu) ** 2 / var).mean(dim=0)
        scores.append(float(d.flatten().topk(k).values.mean()))
    import statistics as st
    d_mu = float(sum(scores) / len(scores))
    d_sigma = float(max(1e-6, st.pstdev(scores)))

    out.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"mu": mu, "var": var, "arch": "resnet18-l23-padim",
                "samples": n, "size": 256, "topk_frac": topk_frac,
                "d_mu": d_mu, "d_sigma": d_sigma}, out)
    return {"backend": "padim", "samples": n, "out": str(out),
            "d_mu": d_mu, "d_sigma": d_sigma}


def train_padim_cov(data: Path, out: Path, *, device: str) -> dict:
    """PaDiM 标准版:每位置随机通道子集上的全协方差 + 收缩正则。

    与对角版(padim)的差别只有打分里的协方差矩阵:每位置随机抽 K=100 个
    通道,在训练样本上估 100×100 协方差,加 trace 收缩(样本 259 对 100 维
    够用,但留余量);马氏距离用该子集。这就是论文的做法(子集维数按
    "样本数的一半以内"选)。
    """
    try:
        import torch                              # noqa: PLC0415
        import torch.nn as nn                     # noqa: PLC0415
        from torchvision import models, transforms  # noqa: PLC0415
    except ImportError:
        raise SystemExit("PaDiM 通路需要 torch/torchvision(见 requirements-yolo.txt)")

    tf = transforms.Compose([transforms.ToTensor(),
                             transforms.Resize((256, 256), antialias=True)])
    children = list(models.resnet18(weights="DEFAULT").children())
    net2 = nn.Sequential(*children[:6]).eval().to(device)
    net3 = nn.Sequential(*children[:7]).eval().to(device)

    def features(bgr: "np.ndarray"):
        x = tf(bgr[:, :, ::-1].copy()).unsqueeze(0).to(device)
        with torch.no_grad():
            f2 = net2(x)
            f3 = nn.functional.interpolate(
                net3(x), size=f2.shape[2:], mode="bilinear", align_corners=False)
        return torch.cat([f2, f3], dim=1)[0].numpy()      # [384, 32, 32]

    fs = [features(img) for _, img in _iter_normal(data)]
    if not fs:
        raise SystemExit("在 %s 下没有找到任何图片" % data)
    F = np.stack(fs)                                        # [N,384,32,32]
    n, C, Hp, Wp = F.shape
    P = Hp * Wp
    K = 100
    rng = np.random.default_rng(0)
    # 每位置固定的随机通道子集与收缩系数
    idx = np.stack([rng.choice(C, K, replace=False) for _ in range(P)])  # [P,K]
    shrink = np.empty(P, np.float64)
    mus = np.empty((P, K), np.float32)
    covs = np.empty((P, K, K), np.float32)
    for p in range(P):
        h, w = p // Wp, p % Wp
        X = F[:, idx[p], h, w].astype(np.float64)            # [N,K]
        mu = X.mean(axis=0)
        S = (X - mu).T @ (X - mu) / (n - 1)
        tr = np.trace(S) / K
        S += (0.01 * tr) * np.eye(K)                        # 收缩正则
        mus[p] = mu.astype(np.float32)
        covs[p] = S.astype(np.float32)
        shrink[p] = tr
    covs = torch.from_numpy(covs)
    mus_t = torch.from_numpy(mus)
    idx_t = torch.from_numpy(idx)

    # 训练集上的 top-k 分数分布,供推理 σ 归一化
    topk_frac = 0.1
    k = max(1, int(P * topk_frac))
    inv = torch.linalg.inv(covs)                            # [P,K,K]
    scores = []
    for f in fs:
        Xt = torch.from_numpy(f)                            # [C,32,32]
        Xp = torch.gather(Xt.permute(1, 2, 0).reshape(P, C), 1,
                          idx_t)                            # [P,K] 按行取子集
        d = torch.einsum("pk,pkj,pj->p", Xp - mus_t, inv, Xp - mus_t)
        scores.append(float(d.flatten().topk(k).values.mean()))
    d_mu = float(sum(scores) / len(scores))
    d_sigma = float(max(1e-6, np.std(scores)))

    out.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"mu": mus_t, "cov": covs, "inv": inv, "idx": idx_t,
                "arch": "resnet18-l23-padim-cov", "samples": n, "size": 256,
                "topk_frac": topk_frac, "d_mu": d_mu, "d_sigma": d_sigma}, out)
    return {"backend": "padim_cov", "samples": n, "out": str(out),
            "d_mu": d_mu, "d_sigma": d_sigma, "K": K, "positions": P}


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
    for _, img in _iter_normal(data):
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

    # 小批量:整批一次前向+反向的激活/梯度峰值会把低内存机器压到段错误
    # （本机 259 张整批实测 exit=139）。batch 32 的峰值内存约 1/8。
    batch = 32
    for ep in range(int(epochs)):
        total, n = 0.0, 0
        for i in range(0, x.shape[0], batch):
            xb = x[i:i + batch]
            opt.zero_grad()
            with torch.no_grad():
                t = teacher(xb)
            loss = ((student(xb) - t) ** 2).mean()
            loss.backward()
            opt.step()
            total += float(loss) * xb.shape[0]
            n += xb.shape[0]
        print("epoch %3d/%d  loss=%.6f" % (ep + 1, epochs, total / max(1, n)))

    # 在训练批上量正常样本的特征距离分布，推理时按它归一化成 0-1 异常分
    # （与统计法的 σ 归一化同一套语义：threshold 0.55 对应 3.3σ）
    ds = []
    with torch.no_grad():
        for i in range(0, x.shape[0], batch):
            xb = x[i:i + batch]
            ds.append(((student(xb) - teacher(xb)) ** 2).mean(dim=(1, 2, 3)))
    d = torch.cat(ds)
    d_mu = float(d.mean())
    d_sigma = float(max(1e-6, d.std()))

    out.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"student": student.state_dict(), "arch": "resnet18-layer2",
                "samples": len(xs), "d_mu": d_mu, "d_sigma": d_sigma,
                "input": "bgr-uint8", "size": 256, "epochs": int(epochs)}, out)
    return {"backend": "efficientad", "samples": len(xs), "out": str(out),
            "d_mu": d_mu, "d_sigma": d_sigma}


def main() -> int:
    ap = argparse.ArgumentParser(description="L3 未知异常检测训练")
    ap.add_argument("--data", default="training/datasets/normal_patches",
                    help="正常样本目录")
    ap.add_argument("--from-evidence", default=None,
                    help="先从证据包目录里挑正常样本填充 --data")
    ap.add_argument("--backend", choices=["auto", "efficientad", "padim",
                                          "padim_cov", "statistical"],
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
    elif backend == "padim":
        out = Path(a.out or "training/runs/anomaly/padim.pt")
        info = train_padim(data, out, device=a.device)
    elif backend == "padim_cov":
        out = Path(a.out or "training/runs/anomaly/padim_cov.pt")
        info = train_padim_cov(data, out, device=a.device)
    else:
        out = Path(a.out or "training/runs/anomaly/baseline.json")
        info = train_statistical(data, out)

    print(json.dumps(info, ensure_ascii=False, indent=1))
    print("\n把它接进去：configs/system.yaml → perception.l3.weights: %s" % info["out"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
