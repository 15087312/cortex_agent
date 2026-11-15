#!/usr/bin/env python3
"""
Cortex Agent Intro Video Generator
Renders 1080x1920 30fps frames and pipes directly into FFmpeg to mux with audio_mixed.m4a
"""

import os
import sys
import math
import subprocess
import time
from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
FPS = 30
TOTAL_DURATION = 45.83
TOTAL_FRAMES = int(TOTAL_DURATION * FPS) # 1374 frames

AUDIO_PATH = "/tmp/cortex_audio/audio_mixed.m4a"
OUTPUT_PATH = "/Users/abc/PycharmProjects/ai_backend/cortex_agent_intro.mp4"
FFMPEG_BIN = "/Users/abc/PycharmProjects/ai_backend/_mptlibs/imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v9.0"

FONT_BOLD = "/Users/abc/PycharmProjects/ai_backend/_vidres/MoneyPrinterTurbo/resource/fonts/MicrosoftYaHeiBold.ttc"
FONT_NORM = "/Users/abc/PycharmProjects/ai_backend/_vidres/MoneyPrinterTurbo/resource/fonts/MicrosoftYaHeiNormal.ttc"
FONT_ST = "/Users/abc/PycharmProjects/ai_backend/_vidres/MoneyPrinterTurbo/resource/fonts/STHeitiMedium.ttc"

f_brand = ImageFont.truetype(FONT_BOLD, 26)
f_brand_sub = ImageFont.truetype(FONT_NORM, 22)
f_title = ImageFont.truetype(FONT_BOLD, 44)
f_h2 = ImageFont.truetype(FONT_BOLD, 34)
f_card_title = ImageFont.truetype(FONT_BOLD, 30)
f_body = ImageFont.truetype(FONT_NORM, 25)
f_body_bold = ImageFont.truetype(FONT_BOLD, 25)
f_small = ImageFont.truetype(FONT_NORM, 21)
f_small_bold = ImageFont.truetype(FONT_BOLD, 21)
f_stat_num = ImageFont.truetype(FONT_BOLD, 64)
f_sub = ImageFont.truetype(FONT_BOLD, 36)
f_btn = ImageFont.truetype(FONT_BOLD, 34)

# Subtitle timeline: (start_s, end_s, text)
SUBTITLES = [
    # Scene 1
    (0.00, 2.80, "别再用单一模型单打独斗了！"),
    (2.80, 6.48, "来看看真正的下一代多模型协作系统：Cortex Agent！"),
    # Scene 2
    (6.48, 11.50, "核心三层编排：Large 决策中枢、主管分解任务、专家集群并行。"),
    (11.50, 17.07, "配合事件驱动认知黑板，彻底告别上下文污染与重复回复。"),
    # Scene 3
    (17.07, 21.80, "告别机械一问一答，连续思考引擎赋予模型自主探索预算；"),
    (21.80, 27.18, "结构化事件记忆与因果树，让思考具备真正的深度推理能力。"),
    # Scene 4
    (27.18, 32.20, "Web 端、桌面原生客户端、智能桌宠全端覆盖，"),
    (32.20, 38.10, "85 项工具热插拔与分级安全门控，6700+ 项测试铸就坚若磐石工程底座。"),
    # Scene 5
    (38.10, 42.00, "Cortex Agent，不仅是工具，更是你的全能智能中枢。"),
    (42.00, 45.83, "立刻探索项目，开启多模型协作未来！"),
]

def get_current_subtitle(t):
    for st, et, txt in SUBTITLES:
        if st <= t < et:
            return txt
    return ""

def draw_header_and_bg(draw, t):
    progress = min(1.0, t / TOTAL_DURATION)
    
    # Background subtle cyber grid
    grid_color = (20, 32, 54)
    for y in range(0, H, 90):
        draw.line([(0, y), (W, y)], fill=grid_color, width=1)
    for x in range(0, W, 90):
        draw.line([(x, 0), (x, H)], fill=grid_color, width=1)
        
    # Top brand header (y = 135 ~ 215)
    draw.rounded_rectangle([70, 135, 360, 185], radius=25, fill=(15, 23, 42), outline=(0, 240, 255), width=2)
    draw.text((88, 146), "✦ CORTEX AGENT ✦", font=f_brand, fill=(0, 240, 255))
    draw.text((385, 148), "NEXT-GEN AI ARCHITECTURE", font=f_brand_sub, fill=(148, 163, 184))
    
    # Progress line along top
    draw.line([(70, 210), (W - 70, 210)], fill=(30, 41, 59), width=4)
    prog_w = int(70 + (W - 140) * progress)
    if prog_w > 70:
        draw.line([(70, 210), (prog_w, 210)], fill=(0, 240, 255), width=4)

def draw_subtitle_box(draw, text):
    if not text:
        return
    bbox = draw.textbbox((0, 0), text, font=f_sub)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    
    pad_x, pad_y = 36, 18
    box_w = min(W - 140, tw + pad_x * 2)
    box_h = th + pad_y * 2
    x0 = (W - box_w) // 2
    y0 = 1530 - box_h // 2
    
    # Subtitle pill box
    draw.rounded_rectangle([x0, y0, x0 + box_w, y0 + box_h], radius=18, fill=(11, 19, 43), outline=(0, 240, 255), width=2)
    draw.text((x0 + pad_x, y0 + pad_y - 4), text, font=f_sub, fill=(255, 255, 255))

# ==================== SCENE 1 ====================
def render_scene_1(draw, t, frame_in_scene):
    # Top title
    draw.text((70, 250), "传统单模型 VS 多智能体协作", font=f_title, fill=(255, 255, 255))
    
    # Card 1: 传统单一模型
    draw.rounded_rectangle([70, 330, 1010, 710], radius=24, fill=(35, 18, 26), outline=(239, 68, 68), width=3)
    draw.text((105, 360), "❌ 传统单一模型工作模式", font=f_card_title, fill=(248, 113, 113))
    draw.text((105, 430), "• 记忆断层：长对话快速遗忘，上下文急剧膨胀溢出", font=f_body, fill=(226, 232, 240))
    draw.text((105, 500), "• 角色过载：一人既当架构师又当码农，错误率陡增", font=f_body, fill=(226, 232, 240))
    draw.text((105, 570), "• 容易幻觉：缺乏交叉校验机制，陷入自我确认死循环", font=f_body, fill=(226, 232, 240))
    draw.text((105, 640), "⚠️ 难以胜任复杂的真实工业级全流程软件工程", font=f_small_bold, fill=(239, 68, 68))
    
    # Middle Evolution Badge (with pulsing neon border)
    pulse = int(127 + 127 * math.sin(frame_in_scene * 0.15))
    draw.rounded_rectangle([300, 740, 780, 800], radius=20, fill=(15, 23, 42), outline=(0, 240, 255), width=2)
    draw.text((325, 752), "⚡ ARCHITECTURAL BREAKTHROUGH ⚡", font=f_brand, fill=(0, 240, 255))
    
    # Card 2: Cortex Agent
    draw.rounded_rectangle([70, 830, 1010, 1420], radius=24, fill=(15, 28, 48), outline=(0, 240, 255), width=3)
    draw.text((105, 865), "✅ Cortex Agent 下一代多模型协同", font=f_card_title, fill=(0, 240, 255))
    draw.text((105, 940), "✦ 三层编排：Large 决策中枢 + 主管拆解 + 专家集群", font=f_body_bold, fill=(255, 255, 255))
    draw.text((105, 1010), "✦ 认知黑板：事件驱动解耦，多模型共享状态与上下文", font=f_body_bold, fill=(255, 255, 255))
    draw.text((105, 1080), "✦ 连续思考：自主探索预算 + 因果树深度推理与自愈", font=f_body_bold, fill=(255, 255, 255))
    draw.text((105, 1150), "✦ 全端覆盖：Web 控制台 · 原生客户端 · 智能桌宠一体化", font=f_body_bold, fill=(255, 255, 255))
    draw.text((105, 1220), "✦ 工业工程：85+ 插件工具热插拔 + 6700+ 自动化测试", font=f_body_bold, fill=(255, 255, 255))
    draw.text((105, 1315), "🚀 真正让大模型协同爆发超强生产力的工程体系", font=f_h2, fill=(52, 211, 153))

# ==================== SCENE 2 ====================
def render_scene_2(draw, t, frame_in_scene):
    draw.text((70, 250), "核心三层编排与认知黑板", font=f_title, fill=(255, 255, 255))
    
    # Level 1: Large Decision Maker (Violet)
    draw.rounded_rectangle([70, 330, 1010, 470], radius=20, fill=(28, 20, 50), outline=(139, 92, 246), width=3)
    draw.text((105, 355), "👑 决策中枢 (Large Grand Planner)", font=f_card_title, fill=(196, 181, 253))
    draw.text((105, 415), "全局意图识别 · 架构规划分解 · 最终质量检验评估", font=f_body, fill=(226, 232, 240))
    
    # Flow arrow 1 with dynamic energy packet
    flow_y1 = 475 + int((frame_in_scene * 4) % 45)
    draw.line([(540, 472), (540, 520)], fill=(139, 92, 246), width=3)
    draw.ellipse([533, flow_y1 - 4, 547, flow_y1 + 4], fill=(0, 240, 255))
    
    # Level 2: Task Supervisor (Cyan)
    draw.rounded_rectangle([70, 525, 1010, 665], radius=20, fill=(14, 30, 52), outline=(0, 240, 255), width=3)
    draw.text((105, 550), "🎯 调度主管 (Task Supervisor)", font=f_card_title, fill=(0, 240, 255))
    draw.text((105, 610), "依赖拓扑排序 · 子任务动态分发 · 执行状态监督与熔断", font=f_body, fill=(226, 232, 240))
    
    # Flow arrow 2 branching to 3 workers
    flow_y2 = 670 + int((frame_in_scene * 4) % 45)
    draw.line([(540, 668), (540, 715)], fill=(0, 240, 255), width=3)
    draw.line([(220, 715), (860, 715)], fill=(0, 240, 255), width=2)
    draw.line([(220, 715), (220, 725)], fill=(0, 240, 255), width=2)
    draw.line([(540, 715), (540, 725)], fill=(0, 240, 255), width=2)
    draw.line([(860, 715), (860, 725)], fill=(0, 240, 255), width=2)
    
    # Level 3: 3 Expert Worker Cards (Green/Teal)
    # Worker A
    draw.rounded_rectangle([70, 730, 360, 890], radius=16, fill=(12, 36, 36), outline=(16, 185, 129), width=2)
    draw.text((95, 750), "💻 编码专家", font=f_card_title, fill=(52, 211, 153))
    draw.text((95, 800), "• 代码生成与重构\n• 执行单测验证", font=f_small, fill=(226, 232, 240))
    
    # Worker B
    draw.rounded_rectangle([380, 730, 700, 890], radius=16, fill=(12, 36, 36), outline=(16, 185, 129), width=2)
    draw.text((405, 750), "🔍 知识检索", font=f_card_title, fill=(52, 211, 153))
    draw.text((405, 800), "• 联网实时搜索\n• 向量知识库召回", font=f_small, fill=(226, 232, 240))
    
    # Worker C
    draw.rounded_rectangle([720, 730, 1010, 890], radius=16, fill=(12, 36, 36), outline=(16, 185, 129), width=2)
    draw.text((745, 750), "🛠️ 工具集群", font=f_card_title, fill=(52, 211, 153))
    draw.text((745, 800), "• 85+ 工具热插拔\n• 系统命令行沙箱", font=f_small, fill=(226, 232, 240))
    
    # Level 4: Cognitive Blackboard (Amber/Cyan Hub)
    draw.rounded_rectangle([70, 940, 1010, 1420], radius=24, fill=(20, 28, 44), outline=(245, 158, 11), width=3)
    draw.text((105, 975), "🧠 事件驱动认知黑板 (Cognitive Blackboard Hub)", font=f_card_title, fill=(251, 191, 36))
    draw.text((105, 1035), "✦ 状态集中式同步：各智能体无需相互传递庞大上下文", font=f_body_bold, fill=(255, 255, 255))
    draw.text((105, 1105), "✦ 事件驱动解耦：工具调用与感知结果即时广播订阅", font=f_body_bold, fill=(255, 255, 255))
    draw.text((105, 1175), "✦ 彻底隔离污染：杜绝死循环、上下文截断与幻觉重复", font=f_body_bold, fill=(255, 255, 255))
    draw.text((105, 1245), "✦ 协同确定性保证：清晰的数据流转与责任边界控制", font=f_body_bold, fill=(255, 255, 255))
    draw.text((105, 1335), "⚡ 架构级解耦，支撑千步级复杂自动化任务稳健运转", font=f_body_bold, fill=(0, 240, 255))

# ==================== SCENE 3 ====================
def render_scene_3(draw, t, frame_in_scene):
    draw.text((70, 250), "连续思考引擎与因果记忆树", font=f_title, fill=(255, 255, 255))
    
    # Box 1: The Loop (Thought-Action-Observe-Reflect)
    draw.rounded_rectangle([70, 330, 1010, 770], radius=24, fill=(15, 25, 45), outline=(0, 240, 255), width=3)
    draw.text((105, 360), "🔄 认知循环机制 (Cognitive Loop)", font=f_card_title, fill=(0, 240, 255))
    
    # 4 Steps grid (2x2)
    # Step 1: Thought
    draw.rounded_rectangle([105, 420, 520, 520], radius=14, fill=(25, 35, 60), outline=(139, 92, 246), width=2)
    draw.text((125, 435), "💡 思考 (Thought)", font=f_card_title, fill=(196, 181, 253))
    draw.text((125, 475), "意图识别与假设生成", font=f_small, fill=(226, 232, 240))
    
    # Step 2: Action
    draw.rounded_rectangle([560, 420, 975, 520], radius=14, fill=(25, 35, 60), outline=(16, 185, 129), width=2)
    draw.text((580, 435), "⚡ 行动 (Action)", font=f_card_title, fill=(52, 211, 153))
    draw.text((580, 475), "并发调度与工具执行", font=f_small, fill=(226, 232, 240))
    
    # Step 3: Observe
    draw.rounded_rectangle([105, 540, 520, 640], radius=14, fill=(25, 35, 60), outline=(245, 158, 11), width=2)
    draw.text((125, 555), "👁️ 观察 (Observe)", font=f_card_title, fill=(251, 191, 36))
    draw.text((125, 595), "执行反馈与环境校验", font=f_small, fill=(226, 232, 240))
    
    # Step 4: Reflect
    draw.rounded_rectangle([560, 540, 975, 640], radius=14, fill=(25, 35, 60), outline=(0, 240, 255), width=2)
    draw.text((580, 555), "🔄 反思 (Reflect)", font=f_card_title, fill=(0, 240, 255))
    draw.text((580, 595), "偏差度量与自愈修正", font=f_small, fill=(226, 232, 240))
    
    # Budget bar inside Box 1
    budget_pct = min(1.0, 0.4 + 0.55 * (0.5 + 0.5 * math.sin(frame_in_scene * 0.1)))
    draw.text((105, 670), f"🎯 自主探索预算 (Exploration Budget): {int(budget_pct*100)}%", font=f_body_bold, fill=(255, 255, 255))
    draw.rounded_rectangle([105, 715, 975, 740], radius=12, fill=(30, 41, 59))
    draw.rounded_rectangle([105, 715, int(105 + 870 * budget_pct), 740], radius=12, fill=(0, 240, 255))
    
    # Box 2: Causal Tree Memory
    draw.rounded_rectangle([70, 810, 1010, 1420], radius=24, fill=(18, 28, 48), outline=(16, 185, 129), width=3)
    draw.text((105, 845), "🌳 结构化事件记忆 + 因果关系树 (Causal Graph)", font=f_card_title, fill=(52, 211, 153))
    draw.text((105, 915), "• 用户目标输入 ── 生成多路解题探索路径", font=f_body, fill=(226, 232, 240))
    draw.text((105, 985), "   ├─ 假设 A (测试通过) ➔ 沉淀为长期确定性领域知识", font=f_body_bold, fill=(52, 211, 153))
    draw.text((105, 1055), "   └─ 假设 B (执行异常) ➔ 触发自愈分支 ➔ 纠偏成功", font=f_body_bold, fill=(251, 191, 36))
    draw.text((105, 1145), "✦ 思考过程全程透明可审计、可追溯、可回放", font=f_body_bold, fill=(255, 255, 255))
    draw.text((105, 1215), "✦ 跨会话长期记忆持久化，智能体伴随使用不断进化", font=f_body_bold, fill=(255, 255, 255))
    draw.text((105, 1315), "🚀 具备真正严谨逻辑推理与自主纠错的下一代大脑", font=f_h2, fill=(0, 240, 255))

# ==================== SCENE 4 ====================
def render_scene_4(draw, t, frame_in_scene):
    draw.text((70, 250), "全端覆盖与工业级工程底座", font=f_title, fill=(255, 255, 255))
    
    # 3 Client Cards side-by-side (y = 330 ~ 620)
    # Client 1: Web
    draw.rounded_rectangle([70, 330, 360, 620], radius=18, fill=(15, 25, 45), outline=(0, 240, 255), width=2)
    draw.text((95, 355), "🌐 Web 控制台", font=f_card_title, fill=(0, 240, 255))
    draw.text((95, 415), "• 响应式轻量面板\n• 多会话流式交互\n• 技能实时热装载\n• 极速云端触达", font=f_small, fill=(226, 232, 240))
    
    # Client 2: Desktop
    draw.rounded_rectangle([380, 330, 700, 620], radius=18, fill=(22, 24, 48), outline=(139, 92, 246), width=2)
    draw.text((405, 355), "💻 原生客户端", font=f_card_title, fill=(196, 181, 253))
    draw.text((405, 415), "• Electron 原生架构\n• 全局快捷呼出\n• 系统级权限沙箱\n• 本地文件直接管控", font=f_small, fill=(226, 232, 240))
    
    # Client 3: Pet
    draw.rounded_rectangle([720, 330, 1010, 620], radius=18, fill=(28, 22, 42), outline=(245, 158, 11), width=2)
    draw.text((745, 355), "🐱 智能桌宠", font=f_card_title, fill=(251, 191, 36))
    draw.text((745, 415), "• 桌面悬浮伴侣\n• 实时双向流式语音\n• 窗口主动感知交互\n• 情感陪伴与代办", font=f_small, fill=(226, 232, 240))
    
    # Stat Card 1: 85+ Tools (y = 660 ~ 1010)
    draw.rounded_rectangle([70, 660, 1010, 1010], radius=24, fill=(14, 28, 48), outline=(0, 240, 255), width=3)
    draw.text((105, 690), "85+", font=f_stat_num, fill=(0, 240, 255))
    draw.text((250, 715), "项工具插件热插拔 · 多级安全门控", font=f_card_title, fill=(255, 255, 255))
    draw.text((105, 800), "✦ 涵盖代码调试、系统运维、文件管理、音视频制作全能工具栈", font=f_body, fill=(226, 232, 240))
    draw.text((105, 860), "✦ 独创安全门控机制 (Security Gate)：敏感操作人工二次确认", font=f_body, fill=(226, 232, 240))
    draw.text((105, 920), "✦ 独立进程沙箱隔离，从架构根源杜绝越权与系统破坏", font=f_body_bold, fill=(52, 211, 153))
    
    # Stat Card 2: 6700+ Tests (y = 1045 ~ 1420)
    draw.rounded_rectangle([70, 1045, 1010, 1420], radius=24, fill=(12, 34, 32), outline=(16, 185, 129), width=3)
    draw.text((105, 1075), "6700+", font=f_stat_num, fill=(52, 211, 153))
    draw.text((330, 1100), "自动化测试守护 · 坚如磐石", font=f_card_title, fill=(255, 255, 255))
    draw.text((105, 1185), "✦ 单元测试 + 契约接口测试 + 全链路端到端集成覆盖", font=f_body, fill=(226, 232, 240))
    draw.text((105, 1245), "✦ 保证百次迭代与核心重构零回归，树立工业级高可靠标准", font=f_body, fill=(226, 232, 240))
    draw.text((105, 1320), "💎 经得起真实生产环境考验的硬核 AI 基础设施", font=f_h2, fill=(0, 240, 255))

# ==================== SCENE 5 ====================
def render_scene_5(draw, t, frame_in_scene):
    # Brand Outro
    # Glowing Logo Box (y = 300 ~ 680)
    pulse = int(127 + 127 * math.sin(frame_in_scene * 0.2))
    draw.rounded_rectangle([70, 300, 1010, 680], radius=28, fill=(16, 24, 46), outline=(0, 240, 255), width=3)
    draw.text((105, 340), "✦ CORTEX AGENT ✦", font=f_title, fill=(0, 240, 255))
    draw.text((105, 410), "NEXT-GENERATION MULTI-AGENT PLATFORM", font=f_card_title, fill=(148, 163, 184))
    
    draw.text((105, 490), "不仅是一套智能工具，", font=f_h2, fill=(255, 255, 255))
    draw.text((105, 560), "更是你的全能智能中枢。", font=f_title, fill=(52, 211, 153))
    
    # 4 Feature Pills (y = 710 ~ 970)
    pills = [
        ("三层编排体系", (139, 92, 246)),
        ("认知黑板解耦", (0, 240, 255)),
        ("连续思考自愈", (16, 185, 129)),
        ("因果记忆持久化", (245, 158, 11)),
        ("全端覆盖生态", (59, 130, 246)),
        ("85+ 工具安全门控", (236, 72, 153)),
    ]
    for i, (label, col) in enumerate(pills):
        col_idx = i % 2
        row_idx = i // 2
        px0 = 70 + col_idx * 480
        py0 = 710 + row_idx * 85
        draw.rounded_rectangle([px0, py0, px0 + 460, py0 + 65], radius=16, fill=(20, 30, 52), outline=col, width=2)
        draw.text((px0 + 35, py0 + 16), f"✦ {label}", font=f_body_bold, fill=(255, 255, 255))
        
    # Big Call To Action Button (y = 1040 ~ 1240)
    # Pulsing gradient effect
    draw.rounded_rectangle([70, 1040, 1010, 1240], radius=24, fill=(12, 40, 50), outline=(0, 240, 255), width=4)
    draw.text((160, 1085), "🚀 立刻探索项目 ➔ 开启多模型未来", font=f_title, fill=(0, 240, 255))
    draw.text((220, 1165), "GitHub: PycharmProjects/ai_backend", font=f_body_bold, fill=(148, 163, 184))
    
    # Footer Note (y = 1300 ~ 1400)
    draw.text((120, 1320), "✦ 严谨工程架构 · 6700+ 测试守护 · 极客自研开源 ✦", font=f_card_title, fill=(52, 211, 153))

def main():
    print(f"Starting video rendering: {TOTAL_FRAMES} frames ({TOTAL_DURATION:.2f}s) at {FPS} FPS...")
    start_time = time.time()
    
    # Launch FFmpeg process with rawvideo pipe
    ffmpeg_cmd = [
        FFMPEG_BIN,
        "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-s", f"{W}x{H}",
        "-pix_fmt", "rgb24",
        "-r", str(FPS),
        "-i", "-",
        "-i", AUDIO_PATH,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-pix_fmt", "yuv420p",
        OUTPUT_PATH
    ]
    
    proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    
    frame_count = 0
    img = Image.new("RGB", (W, H), (10, 14, 26))
    
    # Render loop
    for frame_idx in range(TOTAL_FRAMES):
        t = frame_idx / FPS
        draw = ImageDraw.Draw(img)
        
        # Clear frame with dark base background
        draw.rectangle([(0, 0), (W, H)], fill=(10, 14, 26))
        
        # Draw shared header and cyber grid
        draw_header_and_bg(draw, t)
        
        # Select scene based on time
        if t < 6.48:
            frame_in_scene = frame_idx
            render_scene_1(draw, t, frame_in_scene)
        elif t < 17.07:
            frame_in_scene = frame_idx - int(6.48 * FPS)
            render_scene_2(draw, t, frame_in_scene)
        elif t < 27.18:
            frame_in_scene = frame_idx - int(17.07 * FPS)
            render_scene_3(draw, t, frame_in_scene)
        elif t < 38.10:
            frame_in_scene = frame_idx - int(27.18 * FPS)
            render_scene_4(draw, t, frame_in_scene)
        else:
            frame_in_scene = frame_idx - int(38.10 * FPS)
            render_scene_5(draw, t, frame_in_scene)
            
        # Draw subtitles in safe zone
        sub_text = get_current_subtitle(t)
        draw_subtitle_box(draw, sub_text)
        
        # Write raw bytes to ffmpeg pipe
        try:
            proc.stdin.write(img.tobytes())
        except BrokenPipeError:
            print("FFmpeg pipe broken unexpectedly!")
            break
            
        frame_count += 1
        if frame_count % 150 == 0 or frame_count == TOTAL_FRAMES:
            elapsed = time.time() - start_time
            fps_speed = frame_count / elapsed if elapsed > 0 else 0
            print(f"Rendered {frame_count}/{TOTAL_FRAMES} frames ({frame_count/TOTAL_FRAMES*100:.1f}%) - {fps_speed:.1f} fps")

    proc.stdin.close()
    stderr_out = proc.stderr.read().decode('utf-8', errors='ignore')
    proc.wait()
    
    total_time = time.time() - start_time
    if proc.returncode == 0:
        file_size = os.path.getsize(OUTPUT_PATH)
        print(f"\n✅ Video successfully rendered in {total_time:.1f}s!")
        print(f"File: {OUTPUT_PATH}")
        print(f"Size: {file_size / (1024*1024):.2f} MB")
    else:
        print(f"\n❌ FFmpeg failed with code {proc.returncode}")
        print(stderr_out[-1000:])

if __name__ == "__main__":
    main()
