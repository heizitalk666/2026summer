"""最终答辩 PPT 用的两张重画图。

1. repeat_final.png   43 次独立标定的三项指标分布（数据：deliverables/组长-系统/artifacts/repeatability_seeds.csv）
                      原图的「另有 N 轮出图外」注释压在柱子和图例上，这里挪到每个分图的标题下
2. l1_samples.png     巡航级 cruise_ft 模型的检测样例。标签自己画，不再用 ultralytics 拼图（标签互相重叠）
"""
import csv, glob, io, os, statistics as st, sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
IMG = os.path.join(HERE, "img")
FONT = r"C:\Windows\Fonts\msyh.ttc"
fm.fontManager.addfont(FONT)
plt.rcParams["font.family"] = fm.FontProperties(fname=FONT).get_name()
plt.rcParams["axes.unicode_minus"] = False
NAVY, RED, GREEN, MUTED, GRID = "#0B3C6B", "#C0202B", "#1E7A46", "#5D6E7E", "#D5E1EC"


def repeat_figure():
    rows = list(csv.DictReader(open(os.path.join(REPO, "deliverables/组长-系统/artifacts/repeatability_seeds.csv"),
                                    encoding="utf-8")))
    spec = [("线性度", 0.40), ("重复性", 0.40), ("基本误差", 0.50)]
    fig, axes = plt.subplots(1, 3, figsize=(13.0, 4.4), dpi=170)
    for ax, (name, lim) in zip(axes, spec):
        vs = [float(r[name]) for r in rows]
        med = st.median(vs)
        cut = max(lim * 1.6, med * 2.0)
        inside = [v for v in vs if v <= cut]
        off = sorted(v for v in vs if v > cut)
        ok = sum(1 for v in vs if v <= lim)
        ax.hist(inside, bins=14, range=(min(inside), cut), color=NAVY, edgecolor="white", zorder=2)
        ax.axvline(lim, color=RED, lw=2.2, zorder=3, label="限值 %.1f %% FS" % lim)
        ax.axvline(med, color=GREEN, lw=2.2, ls="--", zorder=3, label="中位 %.3f %% FS" % med)
        ax.set_title("%s　%d/%d 轮合格" % (name, ok, len(vs)), fontsize=14, color=NAVY, fontweight="bold",
                     loc="left", pad=22)
        note = ("另有 %d 轮大于 %.2f 未画出" % (len(off), cut)) if off else "全部轮次均在图内"
        ax.text(0.0, 1.015, note, transform=ax.transAxes, fontsize=10.5, color=MUTED, va="bottom")
        ax.set_xlabel("% FS", fontsize=11, color=MUTED)
        ax.grid(axis="y", color=GRID, lw=0.7, zorder=0)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.tick_params(labelsize=10, colors=MUTED)
        ax.legend(fontsize=10, frameon=False, loc="upper right")
    axes[0].set_ylabel("轮次数", fontsize=11, color=MUTED)
    fig.tight_layout(w_pad=2.5)
    out = os.path.join(IMG, "repeat_final.png")
    fig.savefig(out, facecolor="white")
    plt.close(fig)
    im = Image.open(out)
    print(out, im.size, round(im.width / im.height, 3))


def l1_samples():
    from ultralytics import YOLO
    model = YOLO(os.path.join(REPO, "training/runs/cruise_ft/weights/best.pt"))
    real = sorted(glob.glob(os.path.join(REPO, "training/datasets/seg_combined/images/val/paddlex_*.jpg")))
    ev = sorted(glob.glob(os.path.join(REPO, "evidence/20260910-113122-0186/*/cruise_raw.jpg")))
    colors = {"PRESSURE_GAUGE": (11, 60, 107), "INDICATOR_LIGHT": (30, 122, 70), "SWITCH_HANDLE": (192, 32, 43)}
    zh = {"PRESSURE_GAUGE": "压力表", "INDICATOR_LIGHT": "指示灯", "SWITCH_HANDLE": "开关"}
    font = ImageFont.truetype(FONT, 26)

    def run(path, crop=None, need=None):
        im = Image.open(path).convert("RGB")
        if crop:
            im = im.crop(crop)
        r = model.predict(im, imgsz=1280, conf=0.25, verbose=False)[0]
        dets = [(r.names[int(b.cls[0])], float(b.conf[0]), [float(v) for v in b.xyxy[0]]) for b in r.boxes]
        if need and not any(d[0] in need for d in dets):
            return None, dets
        return im, dets

    def tile(im, dets, size=(640, 480)):
        sx, sy = size[0] / im.width, size[1] / im.height
        s = min(sx, sy)
        canvas = Image.new("RGB", size, (236, 241, 246))
        w, h = int(im.width * s), int(im.height * s)
        ox, oy = (size[0] - w) // 2, (size[1] - h) // 2
        canvas.paste(im.resize((w, h)), (ox, oy))
        d = ImageDraw.Draw(canvas)
        for name, conf, (x1, y1, x2, y2) in sorted(dets, key=lambda t: t[2][1]):
            X1, Y1, X2, Y2 = ox + x1 * s, oy + y1 * s, ox + x2 * s, oy + y2 * s
            c = colors.get(name, (80, 80, 80))
            d.rectangle([X1, Y1, X2, Y2], outline=c, width=4)
            label = "%s %.2f" % (zh.get(name, name), conf)
            tw = d.textlength(label, font=font)
            ty = Y1 - 34 if Y1 - 34 > oy else Y2 + 2
            d.rectangle([X1, ty, X1 + tw + 12, ty + 32], fill=c)
            d.text((X1 + 6, ty), label, font=font, fill="white")
        return canvas

    tiles = []
    for p in real:
        im, dets = run(p, need={"PRESSURE_GAUGE"})
        if im is not None and len(dets) == 1:
            tiles.append(tile(im, dets))
        if len(tiles) == 2:
            break
    for p in ev:
        full = Image.open(p)
        im, dets = run(p, crop=(0, 150, full.width, 900), need={"INDICATOR_LIGHT", "SWITCH_HANDLE"})
        if im is not None:
            xs = [b[2][0] for b in dets] + [b[2][2] for b in dets]
            cx = (min(xs) + max(xs)) / 2
            left = int(max(0, min(im.width - 1000, cx - 500)))
            sub = im.crop((left, 0, left + 1000, im.height))
            sd = [(n, c, [x1 - left, y1, x2 - left, y2]) for n, c, (x1, y1, x2, y2) in dets if x1 >= left and x2 <= left + 1000]
            if sd:
                tiles.append(tile(sub, sd))
        if len(tiles) == 4:
            break
    print("tiles:", len(tiles))
    grid = Image.new("RGB", (1280 + 12, 960 + 12), "white")
    for i, t in enumerate(tiles[:4]):
        r_, c_ = divmod(i, 2)
        grid.paste(t, (c_ * 652, r_ * 492))
    out = os.path.join(IMG, "l1_samples.png")
    grid.save(out)
    print(out, grid.size)


if __name__ == "__main__":
    repeat_figure()
    l1_samples()
