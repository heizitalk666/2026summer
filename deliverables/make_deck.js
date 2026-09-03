#!/usr/bin/env node
// 中期答辩 PPT 生成器
//
//   cd deliverables && node make_deck.js
//
// 明天甲/乙 补齐后：把新图放进 _deck/img/，改对应槽位，重跑本脚本。
// 槽位在 _deck/s2_models.js 里，搜 slot( 就能找到（第 13、16 页）。
const path = require("path");
const pptxgen = require("pptxgenjs");

const DIR = path.join(__dirname, "_deck");
const IMG = path.join(DIR, "img");

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";                  // 13.333 × 7.5，必须在 addSlide 之前
pres.author = "配电室巡检系统项目组";
pres.company = "测控系统综合实训";
pres.title = "基于 RK3576 边缘计算的无人车主动式 AI 巡检系统 · 中期答辩";

require(path.join(DIR, "sec0_cover.js"))(pres, IMG);    // 封面 · 目录
require(path.join(DIR, "sec1_bg.js"))(pres, IMG);       // 一、研究背景和现状
require(path.join(DIR, "sec2_flow.js"))(pres, IMG);     // 二、研究思路和结构
require(path.join(DIR, "sec3_method.js"))(pres, IMG);   // 三、方法和研究内容
require(path.join(DIR, "sec4_sum.js"))(pres, IMG);      // 四、总结与创新点
require(path.join(DIR, "sec5_ref.js"))(pres, IMG);      // 五、参考文献与存在的不足

const out = path.join(__dirname, "中期答辩.pptx");
pres.writeFile({ fileName: out }).then(() => console.log("已生成 " + out));
