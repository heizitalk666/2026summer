#!/usr/bin/env node
// 中期答辩 PPT 生成器
//
//   NODE_PATH=<装有 pptxgenjs 的 node_modules> node make_deck.js
//
// 版式取自学院模板 C424_PPT_2024，页码与章节号由 _deck/theme.js 自动递增。
// 明天甲 / 乙 补齐后：把新图放进 _deck/img/，改对应槽位（搜 T.slot(），重跑本脚本。
const path = require("path");
const pptxgen = require("pptxgenjs");

const DIR = path.join(__dirname, "_deck");
const IMG = path.join(DIR, "img");
require(path.join(DIR, "theme.js")).setImgDir(IMG);

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";                  // 13.333 × 7.5，必须在 addSlide 之前
pres.author = "配电室巡检系统项目组";
pres.company = "测控系统综合实训";
pres.title = "基于 RK3576 边缘计算的无人车主动式 AI 巡检系统 · 中期答辩";

require(path.join(DIR, "deck.js"))(pres, IMG);    // 封面 · 一、二、三（前半）
require(path.join(DIR, "deck2.js"))(pres, IMG);   // 三（后半）· 四 · 五 · 结束

const out = path.join(__dirname, "中期答辩.pptx");
pres.writeFile({ fileName: out }).then(() => console.log("已生成 " + out));
