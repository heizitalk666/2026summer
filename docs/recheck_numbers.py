#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复算 ICD v1.0 与设计方案书里的关键数值，供《一致性差异清单》第 6 节核对。

之后可并入 ICD §10.1 要求的 validate.py 的第 3 项（像素密度算例）与第 4 项
（时序预算与复核预算）。运行：python3 docs/recheck_numbers.py
"""
import math

W = 1920.0          # 图像宽度 px，context.image_w
D = 0.15            # 指针表先验直径 m，detections[].target_size_m
P_MIN = 120.0       # 可靠读数下限 px，由 0.5 % FS 精度指标反推（方案书 §5.3.2）
D_CRUISE = 5.0      # 巡航时到表计的典型距离 m


def pixel_density(theta_deg, z, d):
    """方案书 §5.3.1 / ICD §3.2：p = W·D·z / (2·d·tan(θ/2))"""
    return W * D * z / (2.0 * d * math.tan(math.radians(theta_deg) / 2.0))


def max_distance(theta_deg, z, need_px):
    """给定变焦倍率与所需像素密度，反解距离上限"""
    return W * D * z / (2.0 * need_px * math.tan(math.radians(theta_deg) / 2.0))


def stub_k(z):
    """ptz_stub 的有效感光像素比 k = min(1, 2/z)（ICD §9.2，4K 裁剪仿真）"""
    return min(1.0, 2.0 / z)


def budget(states):
    return sum(states.values())


def n_max(T_max, L, v, T_r):
    """ICD §7.4：N_max = floor((T_max - L/v) / T_r)"""
    return math.floor((T_max - L / v) / T_r)


def main():
    print('=' * 68)
    print('一、像素密度与光学约束（C5：θ 取 60° 还是 67°）')
    print('=' * 68)
    hdr = '%-34s %10s %10s %10s' % ('', 'θ=60°', 'θ=67°', '差异')
    print(hdr)
    rows = []
    v60, v67 = {}, {}
    for th, box in ((60.0, v60), (67.0, v67)):
        box['p_cruise'] = pixel_density(th, 1.0, D_CRUISE)
        box['p_verify'] = pixel_density(th, 3.0, D_CRUISE)
        box['z_req'] = P_MIN / box['p_cruise']
        box['d_max'] = max_distance(th, 3.0, P_MIN)
        # 桩上 p_stub = p·k，z=3 时 k=2/3，故需真机等效 p ≥ 120/k = 180 px
        box['d_max_stub'] = max_distance(th, 3.0, P_MIN / stub_k(3.0))
    rows = [
        ('巡航像素密度 p (z=1, d=5 m)  [px]', 'p_cruise', '%10.2f'),
        ('复核像素密度 p (z=3, d=5 m)  [px]', 'p_verify', '%10.2f'),
        ('所需变焦倍率 z_req',              'z_req',   '%10.3f'),
        ('真机距离上限 d_max (z=3)   [m]',  'd_max',   '%10.2f'),
        ('桩距离上限 d_max (z=3,k=2/3) [m]', 'd_max_stub', '%10.2f'),
    ]
    for label, key, fmt in rows:
        print('%-34s' % label + fmt % v60[key] + fmt % v67[key]
              + '%10.2f' % (v67[key] - v60[key]))
    print()
    print('  ICD 现用 60°：巡航 %.1f px 低于 %.0f px 下限 → 必须复核；'
          % (v60['p_cruise'], P_MIN))
    print('  路线约束取整：真机 %.2f → 6 m，桩 %.2f → 4 m（ICD §9.2 写 4.16 m）'
          % (v60['d_max'], v60['d_max_stub']))
    print('  若按方案书 §4.1.3 自算的 67°：真机 %.2f → 5 m，桩 %.2f → 3.6 m'
          % (v67['d_max'], v67['d_max_stub']))
    print('  变焦余量从 %.2f→3.0 压缩到 %.2f→3.0' % (v60['z_req'], v67['z_req']))

    print()
    print('=' * 68)
    print('二、单次复核耗时 T_r 与复核预算 N_max（A3 / C4 的连锁影响）')
    print('=' * 68)
    # D3 前的 ICD v1.0 原值，保留下来是为了让「决议改了多少」看得见。
    icd_v1 = {'SUSPECT': 0.2, 'HALT_REQ': 2.0, 'AIM': 1.5, 'ZOOM': 1.2,
              'CAPTURE': 0.6, 'VERIFY': 2.5, 'PACK': 0.5, 'RESUME': 0.3}
    # D3 决议后（C10 SUSPECT 0.2→0.3，C4 ZOOM 1.2→1.5）的 ICD v2.0 值。
    icd = dict(icd_v1, SUSPECT=0.3, ZOOM=1.5)
    L, v, T_max = 200.0, 0.5, 600.0

    variants = [
        ('ICD v1.0（D3 之前）', dict(icd_v1)),
        ('仅 C4 按需变焦 (ZOOM 1.2→1.5)', dict(icd_v1, ZOOM=1.5)),
        ('仅 C10 三帧确认 (SUSPECT 0.2→0.3)', dict(icd_v1, SUSPECT=0.3)),
        ('A3 无条件三视角 (CAPTURE 0.6→2.1)', dict(icd_v1, CAPTURE=2.1)),
        ('A3 无条件三视角 + C4 + C10', dict(icd, CAPTURE=2.1)),
        ('** ICD v2.0：A3 条件式 + C4 + C10（均值）', dict(icd)),
    ]
    print('%-38s %8s %8s' % ('情形', 'T_r [s]', 'N_max'))
    for name, st in variants:
        T_r = budget(st)
        print('%-38s %8.1f %8d' % (name, T_r, n_max(T_max, L, v, T_r)))
    print()
    print('  算例参数：L=%.0f m, v=%.1f m/s, T_max=%.0f s（巡航占 %.0f s）'
          % (L, v, T_max, L / v))

    print()
    print('=' * 68)
    print('三、ICD §7.2 自洽性：每个状态的超时是否都大于其预算')
    print('=' * 68)
    # CAPTURE 1.5→4.0：A3 条件式三视角的连锁，见差异清单 §4 C1。
    timeout = {'SUSPECT': 0.5, 'HALT_REQ': 4.0, 'AIM': 3.0, 'ZOOM': 2.5,
               'CAPTURE': 4.0, 'VERIFY': 5.0, 'PACK': 2.0, 'RESUME': 1.0}
    bad = 0
    for s in icd:
        okmark = 'OK' if timeout[s] > icd[s] else '违反'
        if timeout[s] <= icd[s]:
            bad += 1
        print('  %-10s 预算 %.1f s   超时 %.1f s   %s' % (s, icd[s], timeout[s], okmark))
    print('  -> %s' % ('全部自洽' if bad == 0 else '%d 个状态超时不大于预算' % bad))

    print()
    print('=' * 68)
    print('四、证据包单次体积（B3：ICD 无视频角色）')
    print('=' * 68)
    icd_files = {'cruise.jpg': 284419, 'cruise_raw.jpg': 300000,
                 'verify_01.jpg': 311902, 'verify_02.jpg': 311902,
                 'verify_03.jpg': 311902, 'verify_roi.jpg': 61233,
                 'meta.jsonl': 198744}
    tot = sum(icd_files.values()) / 1024.0 / 1024.0
    print('  按 ICD §6.1/§6.7 的文件清单合计   : %.2f MB/次' % tot)
    print('  方案书 §7.3.3 的核算（含视频片段）: 6.70 MB/次')
    print('  差额约 %.2f MB 即视频片段，ICD files[].role 枚举里没有对应角色' % (6.70 - tot))
    print('  按方案书口径 20 次/轮、6 轮/日：')
    for label, per in (('含视频 ', 6.70), ('ICD 现状', tot)):
        day = per * 20 * 6
        print('    %s: 单轮 %5.0f MB，每日 %5.0f MB，64 GB 可缓存 %.0f 天'
              % (label, per * 20, day, 64 * 1024 / day))


if __name__ == '__main__':
    main()
