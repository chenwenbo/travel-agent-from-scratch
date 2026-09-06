#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_reflection_html.py — 把 reflection_trace.json 渲染成单文件网页 reflection_trace.html

用法：
    python3 reflection_agent.py "我想去北京玩"   # 先运行，生成 reflection_trace.json
    python3 make_reflection_html.py              # 再生成复盘网页

页面为纯静态、样式/脚本内嵌，双击即可打开（也可直接托管）。
时间线按 Reflection 三阶段组织：Draft 草稿生成 → Reflect 自我评审 → Revise 修订改进 → 最终答复。
视觉风格参考 agent_trace.html / plan_solve_trace.html。
"""

import html
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
TRACE_FILE = BASE / "reflection_trace.json"
OUT_FILE = BASE / "reflection_trace.html"

# ---------------- 样式 ----------------

CSS = """
:root {
  --bg: #f4f6fb;
  --card: #ffffff;
  --line: #e3e8f0;
  --ink: #1c2434;
  --muted: #68718a;
  --amber: #b45309; --amber-bg: #fef3c7;
  --teal: #0f766e; --teal-bg: #ccfbf1;
  --violet: #6d28d9; --violet-bg: #ede9fe;
  --blue: #1d4ed8; --blue-bg: #dbeafe;
  --red: #b91c1c; --red-bg: #fee2e2;
  --green: #15803d; --green-bg: #dcfce7;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 14px/1.7 "PingFang SC", -apple-system, "Microsoft YaHei", "Segoe UI", sans-serif; }
.wrap { max-width: 1080px; margin: 0 auto; padding: 28px 18px 80px; }

/* ---------- Hero ---------- */
.hero { position: relative; overflow: hidden; border-radius: 18px; padding: 28px 30px; color: #fff;
  background: linear-gradient(135deg, #16265c 0%, #24356b 45%, #4c1d95 100%);
  box-shadow: 0 18px 40px -18px rgba(36,53,107,.65); }
.hero::after { content: ""; position: absolute; right: -70px; top: -90px; width: 280px; height: 280px;
  background: radial-gradient(circle at 30% 30%, rgba(255,255,255,.20), transparent 62%); }
.hero h1 { margin: 0 0 6px; font-size: 22px; font-weight: 600; letter-spacing: .5px; }
.hero .sub { font-size: 13px; color: #b9c4e4; }
.hero .flow { margin: 14px 0 6px; font-size: 13px; color: #dbe3ff; }
.hero .flow b { color: #ffe9a8; font-weight: 600; }
.hero .query { font-size: 18px; margin: 10px 0 14px; color: #ffe9a8; font-weight: 600; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; position: relative; z-index: 1; }
.chip { background: rgba(255,255,255,.14); border: 1px solid rgba(255,255,255,.25);
  padding: 3px 12px; border-radius: 999px; font-size: 12px; }
.chip.ok { background: rgba(74,222,128,.2); border-color: rgba(74,222,128,.5); }
.chip.warn { background: rgba(251,191,36,.18); border-color: rgba(251,191,36,.5); }

/* ---------- 导航 ---------- */
.toc { display: flex; flex-wrap: wrap; gap: 8px; margin: 18px 0 4px; }
.toc a { text-decoration: none; font-size: 12px; padding: 4px 12px; border-radius: 999px;
  background: var(--card); border: 1px solid var(--line); color: var(--muted);
  transition: all .18s ease; }
.toc a:hover { border-color: var(--blue); color: var(--blue); transform: translateY(-1px); }
.hint { color: var(--muted); font-size: 12.5px; margin-top: 10px; }

/* ---------- 反思轨迹 ---------- */
.track-card { background: var(--card); border: 1px solid var(--line); border-radius: 14px;
  margin: 18px 0; padding: 16px 20px; }
.track-card h2 { margin: 0 0 12px; font-size: 15px; font-weight: 600; }
.track { display: flex; flex-wrap: wrap; align-items: stretch; gap: 8px; }
.track-step { flex: 1 1 180px; border: 1px solid var(--line); border-radius: 12px; padding: 10px 14px;
  background: #f8fafc; }
.track-step .tv { font-size: 12px; color: var(--muted); font-weight: 700; letter-spacing: .5px; }
.track-step .ts { font-size: 18px; font-weight: 700; color: var(--ink); margin-top: 2px; }
.track-step .tm { font-size: 12px; color: var(--muted); margin-top: 4px; }
.track-arrow { display: flex; align-items: center; color: #94a3b8; font-size: 18px; }

/* ---------- 阶段标题 ---------- */
.stage { display: flex; align-items: center; gap: 10px; margin: 26px 0 2px; }
.stage .line { flex: 1; height: 1px; background: var(--line); }
.stage .desc { color: var(--muted); font-size: 12.5px; }

/* ---------- 卡片 ---------- */
.card { background: var(--card); border: 1px solid var(--line); border-radius: 14px;
  margin: 14px 0; overflow: hidden; animation: riseIn .35s ease both;
  transition: box-shadow .18s ease, border-color .18s ease, transform .18s ease; }
.card:hover { box-shadow: 0 8px 24px -12px rgba(29,78,216,.28); border-color: #c9d4ea; transform: translateY(-1px); }
.card.draft { border-left: 5px solid var(--blue); }
.card.evaluate { border-left: 5px solid var(--violet); }
.card.revise { border-left: 5px solid var(--amber); }
@keyframes riseIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
.round-head { display: flex; align-items: center; gap: 12px; padding: 14px 20px;
  background: linear-gradient(180deg, #fbfcfe, #f5f7fc); border-bottom: 1px solid var(--line); flex-wrap: wrap; }
.round-num { background: var(--blue); color: #fff; font-weight: 700; font-size: 12px;
  padding: 4px 12px; border-radius: 8px; letter-spacing: 1px; }
.round-num.eval { background: var(--violet); }
.round-num.rev { background: var(--amber); }
.round-num.fin { background: var(--green); }
.round-title { font-weight: 600; font-size: 13.5px; }
.round-meta { color: var(--muted); font-size: 12.5px; flex: 1; min-width: 200px; }
.section { padding: 6px 20px 18px; }
h3.step { font-size: 14px; margin: 18px 0 10px; padding-top: 14px; border-top: 1px dashed var(--line);
  color: #334155; display: flex; align-items: center; gap: 8px; }
h3.step::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: var(--blue); }
h3.step.out::before { background: var(--violet); }
h3.step.tool::before { background: var(--amber); }
h3.step.obs::before { background: var(--teal); }
h3.step.rev::before { background: var(--green); }

/* ---------- 徽章 ---------- */
.badge { display: inline-block; font-size: 11px; font-weight: 700; padding: 2px 8px;
  border-radius: 6px; letter-spacing: .5px; white-space: nowrap; }
.b-system { background: var(--amber-bg); color: var(--amber); }
.b-user { background: var(--teal-bg); color: var(--teal); }
.b-assistant { background: var(--violet-bg); color: var(--violet); }
.b-obs { background: var(--blue-bg); color: var(--blue); }
.b-tool { background: var(--red-bg); color: var(--red); }
.b-fin { background: var(--green-bg); color: var(--green); }
.b-pass { background: var(--green-bg); color: var(--green); }
.b-need { background: var(--red-bg); color: var(--red); }

/* ---------- 代码块 ---------- */
pre { margin: 0; padding: 12px 14px; background: #0f172a; color: #dbe4f0;
  border-radius: 10px; overflow-x: auto; font: 12.5px/1.65 "SF Mono", Menlo, Consolas, monospace;
  white-space: pre-wrap; word-break: break-word; }
pre.plain { background: #f8fafc; color: #1e293b; border: 1px solid var(--line); }
.msg { border: 1px solid var(--line); border-radius: 10px; margin: 8px 0; overflow: hidden; }
.msg-head { display: flex; align-items: center; gap: 10px; padding: 7px 12px; cursor: pointer;
  background: #fbfcfe; user-select: none; transition: background .15s ease; }
.msg-head:hover { background: #f1f5fb; }
.msg-title { color: var(--muted); font-size: 12.5px; flex: 1; }
.caret { font-size: 11px; color: #94a3b8; }
.msg pre { border-radius: 0; border-top: 1px solid var(--line); }
.msg.system:not(.open) pre { display: none; }
.msg:not(.system) pre { display: block; }

/* ---------- 解析区 ---------- */
.parsed-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
@media (max-width: 780px) { .parsed-grid { grid-template-columns: 1fr; } }
.pbox { border: 1px solid var(--line); border-radius: 10px; padding: 10px 14px; background: #f8fafc; }
.pbox .label { font-size: 11px; color: var(--muted); font-weight: 700; letter-spacing: 1px; margin-bottom: 6px; }
.action-line { font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 13px; }
.score-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
.score-total { font-size: 26px; font-weight: 700; color: var(--violet); line-height: 1; }
.score-max { color: var(--muted); font-size: 12.5px; }
.score-row { display: flex; align-items: center; gap: 10px; margin: 6px 0; }
.score-dim { flex: 0 0 84px; font-size: 12.5px; color: var(--ink); }
.bar { flex: 1; height: 8px; background: #eef2f8; border-radius: 999px; overflow: hidden; }
.bar i { display: block; height: 100%; border-radius: 999px;
  background: linear-gradient(90deg, #1d4ed8, #6d28d9); transition: width .6s ease; }
.score-val { flex: 0 0 auto; font-size: 12px; color: var(--muted);
  font-family: "SF Mono", Menlo, Consolas, monospace; }
.list-box { border: 1px solid var(--line); border-radius: 10px; padding: 10px 14px; background: #f8fafc; }
.list-box.warn { background: #fff7ed; border-color: #fed7aa; }
.list-box.tip { background: #f0fdf4; border-color: #bbf7d0; }
.list-box ol { margin: 0; padding-left: 18px; }
.list-box li { margin: 4px 0; }
.diff { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
@media (max-width: 780px) { .diff { grid-template-columns: 1fr; } }
.kv { display: flex; flex-wrap: wrap; gap: 6px 18px; color: var(--muted); font-size: 12.5px; }
.kv b { color: var(--ink); font-weight: 600; }

/* ---------- 最终答复 ---------- */
.final-card { background: linear-gradient(135deg, #0b3d2e, #14532d); color: #ecfdf5;
  border-radius: 14px; padding: 22px 26px; margin-top: 24px;
  box-shadow: 0 18px 36px -20px rgba(6,78,59,.8); }
.final-card h2 { margin: 0 0 10px; font-size: 16px; color: #a7f3d0; }
.final-card pre { background: transparent; color: #f0fdf4; padding: 0; font: inherit;
  white-space: pre-wrap; word-break: break-word; font-size: 14px; }
footer { color: var(--muted); font-size: 12px; text-align: center; margin-top: 26px; }
"""

# ---------------- 渲染辅助 ----------------

def esc(text: str) -> str:
    """转义 HTML，避免提示词中的 <> & 破坏页面。"""
    return html.escape(text or "", quote=False)


def render_msg(role: str, content: str, idx: int, total: int,
               open_default: bool, sys_note: str = "SYSTEM · 系统提示词") -> str:
    """渲染一条消息（SYSTEM 默认折叠，点标题展开；其余常开）。"""
    if role == "system":
        css, badge, title = "system", "b-system", sys_note
    elif role == "user" and content.startswith("Observation"):
        css, badge, title = "system", "b-obs", "USER · 工具结果回喂（Observation，主循环自动追加）"
    elif role == "user":
        css, badge, title = "system", "b-user", "USER · 用户提问 / 事实依据 / 待评审内容"
    else:
        css, badge, title = "system", "b-assistant", "ASSISTANT · 模型历史回复"
    state = "open" if open_default else ""
    return (
        f'<div class="msg {css} {state}">'
        f'<div class="msg-head" onclick="this.parentElement.classList.toggle(\'open\')">'
        f'<span class="badge {badge}">{esc(role.upper())}</span>'
        f'<span class="msg-title">{esc(title)}（{idx}/{total}）</span>'
        f'<span class="caret">点击展开 / 收起</span>'
        f'</div><pre>{esc(content)}</pre></div>'
    )


def render_msg_list(msgs: list | None, sys_note: str) -> str:
    """渲染一组完整输入消息；首条（system）默认折叠。"""
    msgs = msgs or []
    return "".join(
        render_msg(m["role"], m["content"], i, len(msgs), open_default=(i != 1), sys_note=sys_note)
        for i, m in enumerate(msgs, 1)
    )


def render_list_box(title: str, items: list, css_class: str) -> str:
    """把「主要问题 / 改进建议」渲染成编号列表。"""
    if not items:
        return (
            f'<div class="list-box {css_class}"><div class="label" style="font-size:11px;'
            f'color:var(--muted);font-weight:700;letter-spacing:1px">{esc(title)}</div>'
            f'<span style="color:var(--muted);font-size:12.5px">（评审未给出条目）</span></div>'
        )
    lis = "".join(f"<li>{esc(t)}</li>" for t in items)
    return (
        f'<div class="list-box {css_class}"><div class="label" style="font-size:11px;'
        f'color:var(--muted);font-weight:700;letter-spacing:1px">{esc(title)}</div><ol>{lis}</ol></div>'
    )


def render_scores(step: dict) -> str:
    """评审卡片的解析结果：总分 + 结论徽章 + 五维评分条 + 问题 / 建议。"""
    parsed = step.get("parsed") or {}
    scores = parsed.get("scores") or []
    total = parsed.get("total")
    max_total = parsed.get("max_total") or 25
    verdict = parsed.get("verdict") or "-"
    verdict_cls = "b-pass" if verdict == "PASS" else "b-need"

    total_txt = f"{total}" if total is not None else "—"
    tail = ""
    if not parsed.get("verdict_parsed", True):
        tail = '<span style="color:var(--muted);font-size:12px">（未解析到「结论：」行，按分数阈值兜底）</span>'

    rows = []
    for s in scores:
        pct = int(round(s["score"] / s["max"] * 100)) if s["max"] else 0
        rows.append(
            f'<div class="score-row"><span class="score-dim">{esc(s["dim"])}</span>'
            f'<span class="bar"><i style="width:{pct}%"></i></span>'
            f'<span class="score-val">{s["score"]}/{s["max"]}</span></div>'
        )
    rows_html = "".join(rows) or '<span style="color:var(--muted);font-size:12.5px">（未解析出逐项分数，见下方评审原文）</span>'

    return f"""
    <div class="score-head">
      <span class="score-total">{esc(total_txt)}</span>
      <span class="score-max">/ {esc(str(max_total))} 分</span>
      <span class="badge {verdict_cls}">{esc(verdict)}</span>
      {tail}
    </div>
    {rows_html}
    <div class="parsed-grid" style="margin-top:12px">
      {render_list_box("主要问题", parsed.get("issues") or [], "warn")}
      {render_list_box("改进建议", parsed.get("suggestions") or [], "tip")}
    </div>
    """


# ---------------- 三种 step 卡片 ----------------

def render_draft_step(step: dict, draft_no: int, draft_total: int) -> str:
    """Draft 卡片：四步层级（输入 / 输出 / 解析 / Observation）。"""
    msgs = step.get("input_messages") or []
    out = step.get("model_output") or ""
    thought = step.get("thought") or ""
    action = step.get("action")
    observation = step.get("observation")
    is_fin = bool(step.get("finish"))

    msgs_html = render_msg_list(
        msgs, "SYSTEM · 草稿 System Prompt（角色 + 工具清单 + Thought/Action/Finish 约束）"
    )

    if is_fin:
        parse_html = (
            '<p class="action-line"><span class="badge b-fin">FINISH</span>&nbsp; '
            '模型判定信息足够，输出第一版草稿答复（不再调用工具）</p>'
        )
    elif action:
        parse_html = (
            f'<div class="parsed-grid">'
            f'<div class="pbox"><div class="label">THOUGHT · 模型本轮推理</div>'
            f'<pre class="plain">{esc(thought)}</pre></div>'
            f'<div class="pbox"><div class="label">ACTION · 主循环解析</div>'
            f'<span class="badge b-tool">ACTION</span>'
            f'<span class="action-line"> {esc(action["name"])}[{esc(action["args"])}]</span></div>'
            f'</div>'
        )
    else:
        parse_html = (
            f'<div class="pbox"><div class="label">未解析到 Action / Finish</div>'
            f'<pre class="plain">{esc(thought or out)}</pre></div>'
        )

    if observation is not None:
        obs_html = (
            f'<h3 class="step obs">④ 工具返回 Observation —— 作为事实依据（Evidence）回喂模型</h3>'
            f'<pre>{esc(observation)}</pre>'
        )
    elif is_fin:
        obs_html = (
            f'<h3 class="step rev">④ 草稿答复 v0 —— 交给评审环节打分</h3>'
            f'<pre class="plain">{esc(step.get("finish") or "")}</pre>'
        )
    else:
        obs_html = '<h3 class="step obs">④ 本轮无 Observation</h3>'

    tail = "产出草稿 → 进入评审" if is_fin else "工具执行后进入下一轮"
    return f"""
<article class="card draft" id="step-{step['index']}">
  <header class="round-head">
    <span class="round-num">DRAFT {draft_no} / {draft_total}</span>
    <span class="round-title">{esc(step.get('title') or '')}</span>
    <span class="round-meta">输入 {len(msgs)} 条消息 → 调用 DeepSeek → {tail}</span>
  </header>
  <section class="section">
    <h3 class="step">① 本轮模型输入 —— 发送给 DeepSeek 的完整 messages（Prompt）</h3>
    {msgs_html}
    <h3 class="step out">② 模型输出 —— Response 原文（可见其「思考过程」）</h3>
    <pre>{esc(out)}</pre>
    <h3 class="step tool">③ 主循环解析结果</h3>
    {parse_html}
    {obs_html}
  </section>
</article>
"""


def render_evaluate_step(step: dict, reviewed_answer: str) -> str:
    """Reflect 卡片：输入（Evidence + 当前版本）→ 评审原文 → 解析（评分条 / 结论 / 问题 / 建议）。"""
    msgs = step.get("input_messages") or []
    out = step.get("model_output") or ""
    msgs_html = render_msg_list(
        msgs, "SYSTEM · 评审 System Prompt（评审专家角色 + 五维评分 + 输出格式约束）"
    )
    return f"""
<article class="card evaluate" id="step-{step['index']}">
  <header class="round-head">
    <span class="round-num eval">REFLECT {step.get('round')}</span>
    <span class="round-title">{esc(step.get('title') or '')}</span>
    <span class="round-meta">输入 {len(msgs)} 条消息 → 调用 DeepSeek → 打分并给出改进意见（不调用工具）</span>
  </header>
  <section class="section">
    <h3 class="step">① 本轮模型输入 —— 原始提问 + 工具事实依据 + 待评审的当前版本</h3>
    {msgs_html}
    <h3 class="step out">② 模型输出 —— 评审意见原文（Response）</h3>
    <pre>{esc(out)}</pre>
    <h3 class="step tool">③ 主循环解析结果 —— 五维评分与结论</h3>
    {render_scores(step)}
    <h3 class="step rev">④ 本轮被评审的版本</h3>
    <pre class="plain">{esc(reviewed_answer)}</pre>
  </section>
</article>
"""


def render_revise_step(step: dict, prev_answer: str) -> str:
    """Revise 卡片：输入（注入评审意见）→ 修订后原文 → 修订前后对照。"""
    msgs = step.get("input_messages") or []
    out = step.get("model_output") or ""
    new_answer = (step.get("parsed") or {}).get("answer") or out
    msgs_html = render_msg_list(
        msgs, "SYSTEM · 修订 System Prompt（依据评审意见重写、禁止编造事实、直接输出正文）"
    )
    return f"""
<article class="card revise" id="step-{step['index']}">
  <header class="round-head">
    <span class="round-num rev">REVISE {step.get('round')}</span>
    <span class="round-title">{esc(step.get('title') or '')}</span>
    <span class="round-meta">输入 {len(msgs)} 条消息 → 调用 DeepSeek → 生成改进后的新版本</span>
  </header>
  <section class="section">
    <h3 class="step">① 本轮模型输入 —— 评审意见 + 事实依据 + 上一版答复</h3>
    {msgs_html}
    <h3 class="step out">② 模型输出 —— 修订后的答复原文（Response）</h3>
    <pre>{esc(out)}</pre>
    <h3 class="step rev">③ 修订前后对照</h3>
    <div class="diff">
      <div class="pbox"><div class="label">修订前 · v{step.get('round', 1) - 1}</div>
        <pre class="plain">{esc(prev_answer)}</pre></div>
      <div class="pbox"><div class="label">修订后 · v{step.get('round', 1)}</div>
        <pre class="plain">{esc(new_answer)}</pre></div>
    </div>
  </section>
</article>
"""


def render_track(draft_answer: str | None, reflections: list) -> str:
    """顶部「反思轨迹」：草稿 → 每轮评审分数/结论 → 最终版本。"""
    items = [
        f'<div class="track-step"><div class="tv">V0 · DRAFT</div>'
        f'<div class="ts">草稿</div>'
        f'<div class="tm">工具收集事实后生成的第一版答复</div></div>'
    ]
    for r in reflections:
        total = r.get("total")
        total_txt = f"{total} / {r.get('max_total') or 25}" if total is not None else "—"
        verdict = r.get("verdict") or "-"
        cls = "b-pass" if verdict == "PASS" else "b-need"
        if r.get("after"):
            nxt = f'未通过 → 依据 {len(r.get("issues") or [])} 条问题修订为 v{r.get("round")}'
        else:
            nxt = "通过评审 → 停止修订，作为最终答复" if verdict == "PASS" else "达到轮数上限 → 取评分最高版本"
        items.append(
            f'<div class="track-step"><div class="tv">第 {r.get("round")} 轮评审</div>'
            f'<div class="ts">{esc(total_txt)} <span class="badge {cls}">{esc(verdict)}</span></div>'
            f'<div class="tm">{esc(nxt)}</div></div>'
        )
    arrows = '<div class="track-arrow">→</div>'
    body = arrows.join(items)
    return f"""
<div class="track-card" id="track">
  <h2>反思轨迹总览</h2>
  <div class="track">{body}</div>
</div>
"""


def render_trace(data: dict) -> str:
    steps = data.get("steps") or []
    query = data.get("query", "")
    model = data.get("model", "")
    timestamp = data.get("timestamp", "")
    paradigm = data.get("paradigm", "reflection")
    reflections = data.get("reflections") or []
    final_answer = data.get("final_answer")
    status = data.get("status", "")
    max_rounds = data.get("max_reflect_rounds", "-")

    draft_steps = [s for s in steps if s.get("kind") == "draft"]
    eval_steps = [s for s in steps if s.get("kind") == "evaluate"]
    rev_steps = [s for s in steps if s.get("kind") == "revise"]

    if status == "passed":
        status_chip = f'<span class="chip ok">反思收敛 · 评审 PASS</span>'
    elif status == "max_rounds":
        status_chip = '<span class="chip warn">达到反思轮数上限 · 取评分最高版本</span>'
    elif status == "api_error":
        status_chip = '<span class="chip warn">API 调用失败中断</span>'
    elif status == "draft_failed":
        status_chip = '<span class="chip warn">草稿阶段未产出 Finish</span>'
    else:
        status_chip = '<span class="chip warn">未正常完成</span>'

    # 每轮反思的「被评审版本」：用于展示评审批注与修订前后对照
    reviewed_by_round = {r.get("round"): r.get("before") or "" for r in reflections}
    prev_by_round = {r.get("round"): r.get("before") or "" for r in reflections}

    # ---- Draft 区 ----
    draft_html = "".join(
        render_draft_step(s, i, len(draft_steps)) for i, s in enumerate(draft_steps, 1)
    ) or '<div class="card draft"><section class="section"><p style="color:var(--muted)">没有可展示的草稿轮次。</p></section></div>'

    # ---- 反思区：按时间顺序交替渲染 evaluate / revise 卡片 ----
    reflect_steps = [s for s in steps if s.get("kind") in ("evaluate", "revise")]
    reflect_html = ""
    for s in reflect_steps:
        rd = s.get("round", 1)
        if s["kind"] == "evaluate":
            reflect_html += render_evaluate_step(s, reviewed_by_round.get(rd, ""))
        else:
            reflect_html += render_revise_step(s, prev_by_round.get(rd, ""))
    if not reflect_html:
        reflect_html = (
            '<div class="card evaluate"><section class="section">'
            '<p style="color:var(--muted)">没有可展示的反思轮次（草稿阶段未产出答复或已中断）。</p>'
            '</section></div>'
        )

    # ---- TOC ----
    toc = ['<a href="#track">反思轨迹</a>']
    for i, s in enumerate(draft_steps, 1):
        toc.append(f'<a href="#step-{s["index"]}">Draft {i}</a>')
    for s in reflect_steps:
        rd = s.get("round", 1)
        label = f'Reflect {rd}' if s["kind"] == "evaluate" else f'Revise {rd}'
        toc.append(f'<a href="#step-{s["index"]}">{label}</a>')
    toc.append('<a href="#final">最终答复</a>')
    toc_html = "".join(toc)

    final_html = f"""
<section class="final-card" id="final">
  <h2>最终答复（Reflection 收敛版）</h2>
  <pre>{esc(final_answer)}</pre>
</section>
""" if final_answer else (
    '<section class="final-card" id="final" style="background:linear-gradient(135deg,#3b2f0b,#78350f)">'
    '<h2>本次运行未产生最终答复</h2>'
    '<pre>请检查 reflection_run_log.txt / 终端日志中的报错信息。</pre></section>'
)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI 旅行助手 · Reflection 反思过程复盘</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <header class="hero">
    <h1>AI 旅行助手 · Reflection 反思过程复盘</h1>
    <div class="sub">范式：Reflection（生成 → 自我评审 → 修订 → 再评审，直到通过）</div>
    <div class="flow"><b>Draft</b> 工具收集事实并生成草稿 → <b>Reflect</b> 评审专家五维打分 → <b>Revise</b> 按意见重写 → 再次评审</div>
    <div class="query">用户提问：{esc(query)}</div>
    <div class="chips">
      <span class="chip">模型：{esc(model)}</span>
      <span class="chip">时间：{esc(timestamp)}</span>
      <span class="chip">Paradigm：{esc(paradigm)}</span>
      <span class="chip">模型调用：{len(steps)} 次（草稿 {len(draft_steps)} / 评审 {len(eval_steps)} / 修订 {len(rev_steps)}）</span>
      <span class="chip">反思轮数：{len(reflections)} / 上限 {esc(str(max_rounds))}</span>
      {status_chip}
      <span class="chip">终端原始日志：reflection_run_log.txt</span>
      <span class="chip">结构化过程：reflection_trace.json</span>
    </div>
  </header>
  <nav class="toc">{toc_html}</nav>
  <div class="hint">
    每张卡片按时间线展开一次大模型交互：<b>① 完整输入 Prompt（system / 事实依据 / 待评审内容）→ ② 模型原始输出 → ③ 主循环解析（Thought / Action / 评分 / 修订对照）→ ④ Observation 或当前版本</b>。
    灰色 SYSTEM 消息默认折叠，点标题即可展开。
  </div>
  {render_track(data.get('draft_answer'), reflections)}
  <div class="stage"><span class="badge b-tool">阶段一 · DRAFT</span>
    <span class="desc">Actor 通过 Thought / Action 调用 get_weather、get_attraction 收集事实，产出第一版草稿</span><span class="line"></span></div>
  {draft_html}
  <div class="stage"><span class="badge b-assistant">阶段二 · REFLECT & REVISE</span>
    <span class="desc">评审专家打分并列出问题与建议，未通过则交给 Revisor 重写，再评审</span><span class="line"></span></div>
  {reflect_html}
  {final_html}
  <footer>由 reflection_agent.py 生成 reflection_trace.json，再经 make_reflection_html.py 渲染为网页</footer>
</div>
</body>
</html>
"""


def main() -> None:
    if not TRACE_FILE.exists():
        print(f"未找到 {TRACE_FILE}，请先运行：python3 reflection_agent.py \"你的需求\"")
        raise SystemExit(1)
    data = json.loads(TRACE_FILE.read_text(encoding="utf-8"))
    html_text = render_trace(data)
    OUT_FILE.write_text(html_text, encoding="utf-8")
    print(
        f"OK：已生成 {OUT_FILE.name}（{len(data.get('steps') or [])} 次模型调用、"
        f"{len((data.get('reflections') or []))} 轮反思，{len(html_text) / 1024:.0f} KB）"
    )


if __name__ == "__main__":
    main()
