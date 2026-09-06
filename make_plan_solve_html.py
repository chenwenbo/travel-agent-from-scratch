#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_plan_solve_html.py — 把 plan_solve_trace.json 渲染成单文件网页 plan_solve_trace.html

用法：
    python3 plan_solve_agent.py "我想去北京玩"   # 先运行，生成 plan_solve_trace.json
    python3 make_plan_solve_html.py             # 再生成复盘网页

页面为纯静态、样式/脚本内嵌，双击即可打开（也可直接托管）。
时间线按 Plan → Solve 两阶段组织：Phase 1 计划卡片 → Phase 2 逐轮执行卡片 → 最终答复。
视觉风格参考 agent_trace.html。
"""

import html
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
TRACE_FILE = BASE / "plan_solve_trace.json"
OUT_FILE = BASE / "plan_solve_trace.html"

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
  font: 14px/1.7 -apple-system, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif; }
.wrap { max-width: 1040px; margin: 0 auto; padding: 28px 18px 80px; }
.hero { background: linear-gradient(135deg, #1e2b4a 0%, #24356b 100%); color: #fff;
  border-radius: 16px; padding: 26px 30px; }
.hero h1 { margin: 0 0 6px; font-size: 22px; letter-spacing: .5px; }
.hero .sub { font-size: 13px; color: #aeb9d6; }
.hero .query { font-size: 18px; margin: 10px 0 14px; color: #ffe9a8; font-weight: 600; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip { background: rgba(255,255,255,.14); border: 1px solid rgba(255,255,255,.25);
  padding: 3px 12px; border-radius: 999px; font-size: 12px; }
.chip.ok { background: rgba(74,222,128,.2); border-color: rgba(74,222,128,.5); }
.chip.warn { background: rgba(251,191,36,.18); border-color: rgba(251,191,36,.5); }
.toc { display: flex; flex-wrap: wrap; gap: 8px; margin: 18px 0 4px; }
.toc a { text-decoration: none; font-size: 12px; padding: 4px 12px; border-radius: 999px;
  background: var(--card); border: 1px solid var(--line); color: var(--muted); }
.toc a:hover { border-color: var(--blue); color: var(--blue); }
.hint { color: var(--muted); font-size: 12.5px; margin-top: 10px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 14px;
  margin: 18px 0; overflow: hidden; transition: box-shadow .15s ease, border-color .15s ease; }
.card:hover { box-shadow: 0 4px 18px rgba(29,78,216,.08); border-color: #c9d4ea; }
.card.phase1 { border-left: 5px solid var(--violet); }
.card.solve { border-left: 5px solid var(--blue); }
.round-head { display: flex; align-items: center; gap: 12px; padding: 14px 20px;
  background: #f9fafc; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
.round-num { background: var(--blue); color: #fff; font-weight: 700; font-size: 13px;
  padding: 4px 12px; border-radius: 8px; letter-spacing: 1px; }
.round-num.p1 { background: var(--violet); }
.round-num.fin { background: var(--green); }
.round-meta { color: var(--muted); font-size: 12.5px; flex: 1; min-width: 200px; }
.section { padding: 6px 20px 18px; }
h3.step { font-size: 14px; margin: 18px 0 10px; padding-top: 14px; border-top: 1px dashed var(--line);
  color: #334155; display: flex; align-items: center; gap: 8px; }
h3.step::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: var(--blue); }
h3.step.plan::before { background: var(--violet); }
h3.step.tool::before { background: var(--amber); }
h3.step.out::before { background: var(--violet); }
h3.step.obs::before { background: var(--teal); }
.badge { display: inline-block; font-size: 11px; font-weight: 700; padding: 2px 8px;
  border-radius: 6px; letter-spacing: .5px; white-space: nowrap; }
.b-system { background: var(--amber-bg); color: var(--amber); }
.b-user { background: var(--teal-bg); color: var(--teal); }
.b-assistant { background: var(--violet-bg); color: var(--violet); }
.b-obs { background: var(--blue-bg); color: var(--blue); }
.b-tool { background: var(--red-bg); color: var(--red); }
.b-fin { background: var(--green-bg); color: var(--green); }
pre { margin: 0; padding: 12px 14px; background: #0f172a; color: #dbe4f0;
  border-radius: 10px; overflow-x: auto; font: 12.5px/1.65 "SF Mono", Menlo, Consolas, monospace;
  white-space: pre-wrap; word-break: break-word; }
pre.plain { background: #f8fafc; color: #1e293b; border: 1px solid var(--line); }
.msg { border: 1px solid var(--line); border-radius: 10px; margin: 8px 0; overflow: hidden; }
.msg-head { display: flex; align-items: center; gap: 10px; padding: 7px 12px; cursor: pointer;
  background: #fbfcfe; user-select: none; }
.msg-title { color: var(--muted); font-size: 12.5px; flex: 1; }
.caret { font-size: 11px; color: #94a3b8; }
.msg pre { border-radius: 0; border-top: 1px solid var(--line); }
.msg.system:not(.open) pre { display: none; }
.msg:not(.system) pre { display: block; }
.parsed-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
@media (max-width: 720px) { .parsed-grid { grid-template-columns: 1fr; } }
.pbox { border: 1px solid var(--line); border-radius: 10px; padding: 10px 14px; background: #f8fafc; }
.pbox .label { font-size: 11px; color: var(--muted); font-weight: 700; letter-spacing: 1px; margin-bottom: 4px; }
.action-line { font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 13px; }
.step-list { margin: 6px 0 0; padding: 0; list-style: none; }
.step-list li { display: flex; gap: 10px; padding: 9px 12px; margin: 6px 0;
  background: #f8fafc; border: 1px solid var(--line); border-radius: 10px; align-items: flex-start; }
.step-num { flex: 0 0 auto; min-width: 26px; text-align: center; font-weight: 700; font-size: 12px;
  color: var(--violet); background: var(--violet-bg); border-radius: 8px; padding: 2px 6px; }
.step-txt { flex: 1; color: var(--ink); }
.kv { display: flex; flex-wrap: wrap; gap: 6px 18px; color: var(--muted); font-size: 12.5px; }
.kv b { color: var(--ink); font-weight: 600; }
.final-card { background: linear-gradient(135deg, #0b3d2e, #14532d); color: #ecfdf5;
  border-radius: 14px; padding: 22px 26px; margin-top: 24px; }
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
        css, badge, title = "system", "b-user", "USER · 用户提问"
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


def render_msg_list(msgs: list | None, sys_note: str = "SYSTEM · 系统提示词") -> str:
    """渲染一组完整输入消息；首条（通常为 system）默认折叠。"""
    msgs = msgs or []
    return "".join(
        render_msg(m["role"], m["content"], i, len(msgs), open_default=(i != 1),
                   sys_note=sys_note)
        for i, m in enumerate(msgs, 1)
    )


def render_phase1(p1: dict) -> str:
    """Phase 1 · 计划生成卡片。"""
    in_msgs = p1.get("input_messages") or []
    out = p1.get("model_output") or ""
    plan_text = p1.get("plan_text") or out
    steps = p1.get("plan_steps") or []

    msgs_html = render_msg_list(
        in_msgs,
        sys_note="SYSTEM · 规划 System Prompt（角色 + 工具清单 + “只规划、禁止调用工具”约束）",
    )

    if steps:
        steps_html = "".join(
            f'<li><span class="step-num">{i}</span><span class="step-txt">{esc(s)}</span></li>'
            for i, s in enumerate(steps, 1)
        )
        steps_html = f'<ul class="step-list">{steps_html}</ul>'
    else:
        steps_html = '<p style="color:var(--muted);font-size:12.5px">未解析出编号步骤，计划全文已注入 Phase 2 执行。</p>'

    return f"""
<article class="card phase1" id="phase1">
  <header class="round-head">
    <span class="round-num p1">PHASE 1 · PLAN</span>
    <span class="round-meta">1 次模型调用 · 只生成计划，不调用工具 → 计划步骤注入 Phase 2</span>
  </header>
  <section class="section">
    <h3 class="step plan">① 本轮模型输入 —— 发送给 DeepSeek 的完整 Prompt</h3>
    {msgs_html}
    <h3 class="step out">② 模型输出 —— 计划原文（Response）</h3>
    <pre>{esc(plan_text)}</pre>
    <h3 class="step tool">③ 正则解析出的计划步骤</h3>
    {steps_html}
  </section>
</article>
"""


def render_solve_round(r: dict, turn: int, total_rounds: int) -> str:
    """Phase 2 · 单轮执行卡片（复用 agent_trace.html 的四步信息层级）。"""
    msgs = r.get("input_messages") or []
    out = r.get("model_output") or ""
    thought = r.get("thought") or ""
    action = r.get("action")
    observation = r.get("observation")
    is_fin = bool(r.get("finish"))

    msgs_html = render_msg_list(
        msgs,
        sys_note="SYSTEM · 执行 System Prompt（注入 Phase 1 计划 + Thought/Action/Finish 约束）",
    )

    if is_fin:
        parse_html = (
            f'<p class="action-line"><span class="badge b-fin">FINISH</span>&nbsp; '
            f'模型判定计划信息已收集完毕，直接给出最终答复（不再调用工具）</p>'
        )
    else:
        if action:
            act_html = (
                f'<span class="badge b-tool">ACTION</span>'
                f'<span class="action-line"> {esc(action["name"])}[{esc(action["args"])}]</span>'
            )
        else:
            act_html = '<span class="badge b-tool">未解析到 Action</span>'
        parse_html = (
            f'<div class="parsed-grid">'
            f'<div class="pbox"><div class="label">THOUGHT · 模型本轮推理</div>'
            f'<pre class="plain">{esc(thought)}</pre></div>'
            f'<div class="pbox"><div class="label">ACTION · 主循环解析</div>{act_html}</div>'
            f'</div>'
        )

    if observation is not None:
        obs_html = (
            f'<h3 class="step obs">④ 工具返回 Observation —— 作为下一条消息回喂给模型</h3>'
            f'<pre>{esc(observation)}</pre>'
        )
    else:
        obs_html = (
            f'<h3 class="step obs">④ 无工具调用 —— 本轮结束对话（Finish）</h3>'
            f'<p style="color:var(--muted);font-size:12.5px">最终答复见页面底部。</p>'
        )

    tail = "Finish → 对话终止" if is_fin else "工具执行后进入下一轮"
    return f"""
<article class="card solve" id="round-{turn}">
  <header class="round-head">
    <span class="round-num {('fin' if is_fin else '')}">ROUND {turn} / {total_rounds}</span>
    <span class="round-meta">执行计划步骤 · 输入 {len(msgs)} 条消息 → 调用 DeepSeek → {tail}</span>
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


def render_trace(data: dict) -> str:
    p1 = data.get("phase1") or {}
    p2 = data.get("phase2") or {}
    rounds = p2.get("rounds") or []
    n = len(rounds)
    query = data.get("query", "")
    timestamp = data.get("timestamp", "")
    model = data.get("model", "")
    paradigm = data.get("paradigm", "plan_solve")
    final = p2.get("final_answer")
    status = p2.get("status", "")

    if final:
        status_chip = '<span class="chip ok">Phase 2 · Finish 正常收敛</span>'
        status_txt = "已收敛"
    elif status == "max_rounds":
        status_chip = '<span class="chip warn">Phase 2 · 达到轮数上限未收敛</span>'
        status_txt = "达到轮数上限"
    elif status == "api_error":
        status_chip = '<span class="chip warn">API 调用失败中断</span>'
        status_txt = "API 调用失败"
    else:
        status_chip = '<span class="chip warn">未正常完成</span>'
        status_txt = "未正常完成"

    toc_items = ''.join(
        ['<a href="#phase1">Phase 1 · 计划</a>']
        + [f'<a href="#round-{i}">Round {i}</a>' for i in range(1, n + 1)]
        + ['<a href="#final">最终答复</a>']
    )

    phase1_html = render_phase1(p1)
    round_html = "".join(render_solve_round(r, r.get("turn", i), n) for i, r in enumerate(rounds, 1))
    if not rounds:
        round_html = (
            '<div class="card solve" id="phase2"><section class="section">'
            '<p style="color:var(--muted)">Phase 2 没有可展示的执行轮次（本轮运行未进入 Solve 或已中断）。</p>'
            '</section></div>'
        )

    final_html = f"""
<section class="final-card" id="final">
  <h2>最终答复（Finish · {esc(status_txt)}）</h2>
  <pre>{esc(final)}</pre>
</section>
""" if final else (
    f'<section class="final-card" id="final" style="background:linear-gradient(135deg,#3b2f0b,#78350f)">'
    f'<h2>本次运行未产生最终答复（{esc(status_txt)}）</h2>'
    f'<pre>请检查 plan_solve_run_log.txt / 终端日志中的报错信息。</pre></section>'
)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI 旅行助手 · Plan-Solve 两阶段复盘</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <header class="hero">
    <h1>AI 旅行助手 · Plan-Solve 两阶段处理过程复盘</h1>
    <div class="sub">范式：Plan（先规划）→ Solve（逐步执行工具收集信息）→ 最终答复</div>
    <div class="query">用户提问：{esc(query)}</div>
    <div class="chips">
      <span class="chip">模型：{esc(model)}</span>
      <span class="chip">时间：{esc(timestamp)}</span>
      <span class="chip">Paradigm：{esc(paradigm)}</span>
      <span class="chip">Phase 1：计划生成 ×1</span>
      <span class="chip">Phase 2：{n} 轮执行</span>
      {status_chip}
      <span class="chip">结构化过程：plan_solve_trace.json</span>
    </div>
  </header>
  <nav class="toc">{toc_items}</nav>
  <div class="hint">
    时间线分两阶段：<b>Phase 1 · Plan</b>：模型只输出编号执行计划（无工具调用）；
    <b>Phase 2 · Solve</b>：把计划注入 System Prompt 后逐轮执行——每轮展示 ① 完整输入 Prompt → ② 模型输出 → ③ 解析结果 → ④ 工具 Observation 回喂 / Finish。
    灰色 SYSTEM 历史消息默认折叠，点标题即可展开。
  </div>
  {phase1_html}
  <div class="section" style="margin:26px 0 0"><span class="badge b-tool">PHASE 2 · SOLVE</span>
    <span style="color:var(--muted);font-size:12.5px;margin-left:8px">逐轮执行计划（上限 {esc(str(p2.get('max_rounds', '-'))) } 轮）</span></div>
  {round_html}
  {final_html}
  <footer>由 plan_solve_agent.py 生成 plan_solve_trace.json，再经 make_plan_solve_html.py 渲染为网页</footer>
</div>
</body>
</html>
"""


def main() -> None:
    if not TRACE_FILE.exists():
        print(f"未找到 {TRACE_FILE}，请先运行：python3 plan_solve_agent.py \"你的需求\"")
        raise SystemExit(1)
    data = json.loads(TRACE_FILE.read_text(encoding="utf-8"))
    html_text = render_trace(data)
    OUT_FILE.write_text(html_text, encoding="utf-8")
    rounds = len((data.get("phase2") or {}).get("rounds") or [])
    print(f"OK：已生成 {OUT_FILE.name}（Phase 2 共 {rounds} 轮，{len(html_text) / 1024:.0f} KB）")


if __name__ == "__main__":
    main()
