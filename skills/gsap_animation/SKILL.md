---
name: GSAP 动画引擎指南
description: "GSAP v3.15+ 动画引擎，包括 Core、Timeline、ScrollTrigger、Stagger、缓动系统及所有免费插件。"
keywords: [GSAP, GreenSock, 动画, 交互动效, 滚动动画, ScrollTrigger, Timeline, JavaScript 动画, 前端动画, 品牌动画, 入场动画, 页面动画, 动效]
trigger:
  include: [GSAP, GreenSock, 动画, 动效, 交互, 滚动动画, ScrollTrigger, 品牌动画, 入场动画, timeline, 缓动, ease, 过场动画]
  exclude: [后端, 数据库, 服务器配置, API 路由, 数据模型]
  min_score: 1
metadata:
  version: 1
  type: builtin
id: gsap_animation
---

你精通 GSAP (GreenSock Animation Platform) v3.15+，包括 Core、Timeline、ScrollTrigger、Stagger、缓动系统及所有免费插件。

## 核心原则
- 优先用 transform 别名：x / y / rotation / scale / skewX / skewY，避免 left/top/margin
- autoAlpha 替代 opacity（自动管理 display:none，提升性能）
- 能用 Timeline 就不要用 delay 链式回调
- 动画完成后用 clearProps 清理内联样式（避免冲突）
- 性能：只动画 transform 和 opacity，不动画 layout 属性

## 基础 API
gsap.to(target, vars)        # 从当前 → 目标
gsap.from(target, vars)      # 从指定 → 当前
gsap.fromTo(target, fromVars, toVars)
gsap.set(target, vars)       # 立即设置（duration=0）

## Timeline 序列编排
const tl = gsap.timeline({ defaults: { duration: 0.5, ease: "power2" } });
tl.to(".a", { x: 100 })      # 依次追加
  .to(".b", { y: 50 }, "+=0.2")   # 间隔 0.2s
  .to(".c", { opacity: 0 }, "-=0.1")  # 重叠 0.1s
  .to(".d", { rotation: 360 }, "<")   # 与上一个同时开始
  .to(".e", { scale: 1.5 }, ">")     # 上一个结束后开始
  .to(".f", { x: 200 }, "label+=0.5")  # 基于标签
  .addLabel("mid", 2);          # 在第 2 秒添加标签

## ScrollTrigger（免费插件）
gsap.registerPlugin(ScrollTrigger);

# 链式写法（推荐）
gsap.to(".box", {
  x: 500,
  scrollTrigger: { trigger: ".box", start: "top center", end: "bottom center", scrub: true }
});

# Timeline + ScrollTrigger
const tl = gsap.timeline({
  scrollTrigger: { trigger: ".section", pin: true, start: "top top", end: "+=500", scrub: 1 }
});
tl.to(".panel", { x: 100 }).to(".panel", { rotation: 5 });

# 关键参数
# trigger: 触发元素
# start: "top center" / "top bottom-=100" / "clamp(top bottom)"
# end: "bottom top" / "+=500" / "clamp(bottom top)"
# scrub: true / 0.5（秒，平滑追赶）
# pin: true（钉住触发器）
# markers: true（调试标记）
# toggleActions: "play none none none"（默认）

## Stagger 交错
gsap.from(".items", {
  y: 60, opacity: 0, duration: 0.6,
  stagger: { each: 0.08, from: "center" }
});
# each: 间隔秒数；from: "start"/"center"/"end"/"edges"/索引

## 缓动系统
# power1 / power2 / power3 / power4 (.in / .out / .inOut)
# back.out(1.7) — 过冲回弹；elastic.out(1,0.3) — 橡皮筋
# bounce.out — 弹球；circ.out — 圆形缓动
# none — 线性
# CustomEase 可创建任意贝塞尔曲线（免费）

## 综合最佳实践范式
const tl = gsap.timeline({
  defaults: { duration: 0.6, ease: "power3.out" },
  scrollTrigger: { trigger: ".container", start: "top 80%", toggleActions: "play none none none" }
});
tl.from("h1", { y: 40, opacity: 0 })
  .from("p", { y: 20, opacity: 0 }, "-=0.3")
  .from(".cards", { scale: 0.8, opacity: 0, stagger: 0.08 }, "-=0.2");

## React 集成（useGSAP hook）
import { useGSAP } from "@gsap/react";
import { gsap } from "gsap";
gsap.registerPlugin(useGSAP);

function Component({ containerRef }) {
  useGSAP(() => { gsap.to(".box", { x: 100 }); }, { scope: containerRef });
  // scope 自动限制选择器范围，无需手动 ref
}

## 性能要点
- 动画完成后用 .revert() 清理（vs .kill() 保留最终值）
- ScrollTrigger.refresh() 在 DOM/布局变化后调用
- 避免在 pinned 元素上动画（用内部子元素代替）
- 大量元素用 gsap.quickTo() / gsap.quickSetter() 批量更新
- React 中 useGSAP 的 scope 参数自动清理

## 适用场景
- 品牌 landing page 滚动叙事
- 产品展示动画（卡片入场、列表交互动画）
- 页面过渡和微交互动效
- 时间线驱动的复杂序列动画

## 不要用于
- 简单的 hover 效果（CSS transition 即可）
- 页面级路由切换（用框架内置动画）
- 数据可视化高帧率图表（用 canvas/d3）
