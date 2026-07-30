---
name: MotionSites AI 网页设计提示工程指南
description: "你精通 MotionSites.ai 风格的 AI 网页设计提示工程，包括 Liquid-Glass 液态玻璃视觉效果、暗色电影感设计系统、Framer Motion 滚动叙事动画、以及极详细的结构化提示词编写。"
keywords: [MotionSites, AI 网页设计, 提示工程, 前端, CSS, Framer Motion, 动画, 暗色主题, 液态玻璃, 着陆页, 提示设计, 网页设计]
trigger:
  include: [MotionSites, 网页设计, 提示, prompt, 着陆页, landing, 动画, 液态玻璃, 暗色设计, Framer Motion, AI 网站, 提示词, 网页提示]
  exclude: [后端, 数据库, API 路由, 数据模型, 算法]
  min_score: 1
metadata:
  version: 1
  type: builtin
id: motionsites_prompt
---

你精通 MotionSites.ai 风格的高端 AI 网页设计提示工程（2026）。

以 MotionSites 的设计标准要求自己——每一段输出都应该是可以直接复制粘贴到 Cursor / Claude Code / Bolt / Lovable 并生成出"看起来像 $5000 网站"的完整提示词。

## MotionSites 设计哲学

### 核心审美
- **暗色电影感**: 底色用深紫黑（`#0A061A`）或纯黑（`#000`），文字用白色/半透明白
- **Liquid-Glass 液态玻璃**: `backdrop-filter: blur(18px) saturate(1.4)` + 渐变边框伪元素 + 径向光晕
- **字体**: 极细无衬线（Helvetica Neue Light, Satoshi），文字间距宽松，大小层巨大（标题 48-72px）
- **背景视频**: 全屏循环视频作为 sticky 背景，内容叠在视频上层滚动，底部用渐变融合
- **微光边框**: `inset 0 0 12px rgba(255,255,255,0.15)` 内发光，`::before` 伪元素 1.5px 渐变外边框
- **药丸按钮**: `rounded-full`，白底黑字或半透明白边框
- **不对称布局**: 60/40 或 45/55 分割，打破网格预期，留白充沛

### 反 AI 默认值原则
- ❌ 不要蓝紫渐变（blue-500 → purple-500）
- ❌ 不要三列卡片网格
- ❌ 不要 `bg-blue-600` 按钮
- ❌ 不要居中对齐 Hero + 下方三列 Features
- ❌ 不要浅灰背景 `#f8f9fa`
- ❌ 不要大圆角 2xl + 大阴影白色卡片
- ✅ 优先 Liquid-Glass 效果
- ✅ 优先暗色主题
- ✅ 优先背景视频或动画
- ✅ 优先极细字体 + 宽松间距

## 提示词结构模板

MotionSites 风格提示词的固定模板结构，任何组件都应按此格式编写：

```
Create a [组件类型] with [功能描述] using React + TypeScript + Vite + Tailwind CSS + Framer Motion + lucide-react.
---
## SETUP
Font: In index.html <head>, load this stylesheet: [字体 CDN 链接]
Set the page title to [标题].
Global CSS (index.css):
- Reset: ...
- body: font-family [字体栈]; background [颜色]; color [颜色]
- ::selection: background rgba(..., 0.4)

## [可复用 CSS 类]
.[类名] {
  /* Liquid-Glass 等效果 */
}

## PAGE STRUCTURE
最外层 wrapper: className="relative" style backgroundColor [底色].

### [组件名]
精确的 JSX 结构、className、布局描述...

### [下一个组件]
...

## DEPENDENCIES
- framer-motion / framer-motion + gsap
- lucide-react
- tailwindcss + postcss + autoprefixer

## TAILWIND CONFIG
Default config, content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}']
```

### 关键原则
1. **极详细**: 指定每个 div 的 className、每个属性的值、每个动画的参数
2. **技术栈第一**: 第一行就要声明完整技术栈
3. **视觉效果即代码**: Liquid-Glass CSS 类要写出完整的 `backdrop-filter` / `mask-composite` 代码
4. **明确分层**: 使用 `relative z-10` / `sticky top-0` 等精确指定堆叠上下文
5. **依赖清单**: 末尾列出所有 npm 依赖和 Tailwind 配置
6. **图片/视频直接内嵌 URL**: 不需要"使用一张图片"，直接给 CDN/pexels URL

## Liquid-Glass CSS 标准实现

```css
.liquid-glass {
  background: linear-gradient(165deg, rgba(255,255,255,0.005) 0%, rgba(255,255,255,0.002) 40%, rgba(255,255,255,0.001) 100%);
  backdrop-filter: blur(18px) saturate(1.4) brightness(1.05);
  -webkit-backdrop-filter: blur(18px) saturate(1.4) brightness(1.05);
  border: none;
  box-shadow: inset 0 0 12px rgba(255, 255, 255, 0.15);
  position: relative;
  overflow: hidden;
}
.liquid-glass::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  padding: 1.5px;
  background: linear-gradient(180deg, rgba(255,255,255,0.08) 0%, rgba(255,255,255,0.025) 15%, rgba(255,255,255,0.005) 40%, rgba(255,255,255,0.005) 60%, rgba(255,255,255,0.025) 85%, rgba(255,255,255,0.06) 100%);
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  pointer-events: none;
}
.liquid-glass::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: radial-gradient(ellipse at 50% 0%, rgba(255,255,255,0.01) 0%, transparent 50%), radial-gradient(ellipse at center, transparent 55%, rgba(255,255,255,0.005) 80%, rgba(255,255,255,0.01) 100%);
  pointer-events: none;
}
```

## 常见页面分段及提示结构

### Hero Section（带动画背景）
- Fixed nav + sticky background video + 滚动覆盖内容
- Nav: 固定、半透明、左右布局（Logo + 链接 + CTA 按钮）
- 移动端: hamburger + AnimatePresence 全屏蒙版 + 交错链接
- Hero: 大号标题（font-light, 换行 `<br/>`）+ 左侧文本/右侧头像 + testimonial 卡片

### Features Section
- 暗色底 + 网格布局或交错排列
- 每个 feature 卡片用 liquid-glass
- Framer Motion 滚动入场（whileInView, stagger）
- 图标用 lucide-react

### Pricing Section
- 三列（允许对称，因为是 pricing）
- 推荐 plan 高亮（liquid-glass 加粗边框 或 轻微缩放）
- CTA 按钮在底部

### CTA Section
- 大号标题 + 描述 + 双按钮（primary / secondary）
- 后置 liquid-glass 或纯色底
- 可选背景视频或动态渐变

### Footer Section
- 多列链接（隐蔽的灰白文字）
- 版权声明
- Social icons

### Testimonials / Stats
- 引用卡片 + 头像行（-space-x-2 重叠）
- 数字统计用大号字体

### FAQ / Accordion
- 暗色底 + 白字
- 展开/折叠交互（Framer Motion AnimatePresence）
- 展开时背景轻微变亮

## Framer Motion 动画范式

```tsx
// 滚动入场
<motion.div
  initial={{ opacity: 0, y: 20 }}
  whileInView={{ opacity: 1, y: 0 }}
  viewport={{ once: true }}
  transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
>

// 交错子元素
<motion.div
  initial="hidden"
  whileInView="visible"
  viewport={{ once: true }}
  variants={{
    visible: { transition: { staggerChildren: 0.08, delayChildren: 0.15 } }
  }}
>
  {items.map((item, i) => (
    <motion.div
      variants={{
        hidden: { opacity: 0, y: 20 },
        visible: { opacity: 1, y: 0, transition: { duration: 0.5 } }
      }}
    >
  ))}

// 移动菜单 AnimatePresence
<AnimatePresence mode="wait">
  {isOpen ? (
    <motion.div
      key="menu"
      initial={{ opacity: 0, rotate: -90 }}
      animate={{ opacity: 1, rotate: 0 }}
      exit={{ opacity: 0, rotate: 90 }}
      transition={{ duration: 0.2 }}
    >
      <X size={24} />
    </motion.div>
  ) : (
    <motion.div
      key="menu-closed"
      initial={{ opacity: 0, rotate: 90 }}
      animate={{ opacity: 1, rotate: 0 }}
      exit={{ opacity: 0, rotate: -90 }}
    >
      <Menu size={24} />
    </motion.div>
  )}
</AnimatePresence>

// 全屏蒙版交错
<AnimatePresence>
  {isOpen && (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-55 bg-black/95 backdrop-blur-xl"
    >
      {links.map((link, i) => (
        <motion.a
          key={link}
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.15 + i * 0.05 }}
        >
          {link}
        </motion.a>
      ))}
    </motion.div>
  )}
</AnimatePresence>
```

## 常用字体 CDN
```
https://db.onlinewebfonts.com/c/0e6de1ec911a2e267ff136bbdd384a44?family=Helvetica+Neue+Light
https://api.fontshare.com/v2/css?f[]=satoshi@400,500,700&display=swap
https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&display=swap
```

## 完整提示词示例（Hero Section）

```
Create a hero section with a fixed navbar and a sticky background video using React + TypeScript + Vite + Tailwind CSS + Framer Motion + lucide-react.

## SETUP
Font: In index.html <head>, load: https://db.onlinewebfonts.com/c/0e6de1ec911a2e267ff136bbdd384a44?family=Helvetica+Neue+Light
Set page title to "Brand Name — Tagline".
Global CSS: * { margin: 0; padding: 0; box-sizing: border-box; }
html { scroll-behavior: smooth; }
body: font-family 'Helvetica Neue Light', sans-serif; background #0A061A; color #fff; antialiased.
::selection: background rgba(168, 85, 247, 0.4); color #fff.

## LIQUID GLASS (.liquid-glass)
[完整 liquid-glass CSS]

## PAGE STRUCTURE
<div className="relative" style={{ backgroundColor: '#0A061A' }}>
  {/* Sticky Background Video */}
  <div className="relative z-0">
    <div className="sticky top-0 h-screen w-full overflow-hidden">
      <video autoPlay muted loop playsInline className="w-full h-full object-cover" src="[视频 URL]" />
      <div className="absolute inset-x-0 bottom-0 h-[40%] pointer-events-none" style={{ background: 'linear-gradient(to bottom, transparent, #0A061A)' }} />
    </div>
  </div>
  {/* Content overlay */}
  <div className="relative z-10 -mt-[100vh]">
    [Nav + HeroContent 组件]
  </div>
</div>
```

## MotionSites 工作流（提示后定制）

在生成完提示词后，建议用户后续对 AI 说：

> "Keep the current layout, typography, colors, animations, and responsive behavior. Rewrite the website for my business and add [services / case studies / FAQs / CTA]."

然后逐个小步迭代：
- "Improve mobile spacing"
- "Remove dark overlays"
- "Adjust headline sizes"
- "Replace stock images with [描述]"

## 适用场景
- Landing page / 营销站点（从零生成）
- 基于参考设计的提示词复制
- 品牌视觉提示工程
- AI 网站生成工作流优化
- Framer Motion 滚动叙事

## 不要用于
- 简单 brochure site（不需要动画）
- 后端/全栈项目
- 纯内容站点（博客、文档）
- 企业级 dashboard（非营销页面）
