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

require(path.join(DIR, "s1_intro.js"))(pres, IMG);
require(path.join(DIR, "s2_models.js"))(pres, IMG);
require(path.join(DIR, "s3_system.js"))(pres, IMG);

const out = path.join(__dirname, "中期答辩.pptx");
pres.writeFile({ fileName: out }).then(() => console.log("已生成 " + out));
