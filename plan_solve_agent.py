#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plan_solve_agent.py — 纯 Python 旅行助手智能体（Plan → Solve 两阶段范式，不引入任何 Agent 框架）

技术栈：openai（DeepSeek 兼容接口）+ requests（wttr.in）+ tavily-python（Tavily 搜索）

工作方式（Plan-Solve / Plan-and-Solve 两阶段）：
    Phase 1 · Plan（计划）：只把「系统提示词 + 用户问题」发给模型，要求它先输出一份编号执行计划，
                          这一阶段绝不调用任何工具；随后用正则解析出计划步骤。
    Phase 2 · Solve（执行）：把 Phase 1 生成的计划注入执行阶段的 System Prompt，主循环按
                          Thought / Action / Finish 格式逐轮让模型执行计划：命中 Action 就调用工具、
                          把结果作为 Observation 回喂；命中 Finish 就输出最终中文旅行答复并结束；
                          超过 MAX_SOLVE_ROUNDS 轮仍未 Finish 则以最后输出兜底。

日志与复盘：每一轮模型调用前打印完整输入 messages、调用后打印输出原文，并将二者 deepcopy 后
            结构化写入 plan_solve_trace.json（整文件覆盖写）；再由 make_plan_solve_html.py 渲染成
            plan_solve_trace.html 复盘网页（参考 agent_trace.html 的视觉风格）。

API Key 从同目录 .env 读取：deepseek-api-key 与 tailvy-api-key（兼容 tavily-api-key 拼写）。

用法：
    python3 plan_solve_agent.py "我想去北京玩"     # 命令行直接给需求
    python3 plan_solve_agent.py                     # 交互式输入
"""

from __future__ import annotations

import copy
import datetime
import json
import re
import sys
import textwrap
from pathlib import Path

import requests
from openai import OpenAI
from tavily import TavilyClient

# ---------------- 常量与配置 ----------------

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"          # 如使用其它模型可在此修改
MAX_SOLVE_ROUNDS = 5                      # Phase 2（Solve 执行阶段）最大轮数
OBS_TRUNCATE = 1500                       # Observation 展示/回喂的最大字符数
ENV_PATH = Path(__file__).resolve().parent / ".env"
TRACE_PATH = Path(__file__).resolve().parent / "plan_solve_trace.json"  # 两阶段每轮调用的结构化记录

# ---------------- .env 简易解析 ----------------

def load_env(path: Path = ENV_PATH) -> dict[str, str]:
    """逐行解析 .env，键统一转为大写并把 '-' 换成 '_'，值去掉引号。"""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().strip("\"'").upper().replace("-", "_")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        env[key] = value
    return env


def _pick(env: dict[str, str], *names: str) -> str:
    for name in names:
        if env.get(name):
            return env[name]
    return ""


_ENV = load_env()
DEEPSEEK_API_KEY = _pick(_ENV, "DEEPSEEK_API_KEY", "DEEPSEEK_KEY")
TAVILY_API_KEY = _pick(_ENV, "TAVILY_API_KEY", "TAILVY_API_KEY")  # 兼容 .env 里的 tailvy 拼写

# ---------------- 工具清单与实现 ----------------

TOOLS = [
    {"name": "get_weather", "args": "city: 城市名", "desc": "查询城市当前天气（温度/体感/湿度/风速）"},
    {"name": "get_attraction", "args": "city: 城市名, weather: 天气摘要", "desc": "根据城市与当天天气检索景点推荐"},
]

# wttr.in 返回的英文天气现象 -> 中文（未覆盖的保留原文）
_WEATHER_ZH = {
    "Sunny": "晴", "Clear": "晴朗", "Partly cloudy": "多云", "Cloudy": "阴天",
    "Overcast": "阴", "Mist": "薄雾", "Fog": "雾", "Light rain": "小雨",
    "Moderate rain": "中雨", "Heavy rain": "大雨", "Patchy rain possible": "可能有阵雨",
    "Light snow": "小雪", "Moderate snow": "中雪", "Heavy snow": "大雪", "Thundery outbreaks possible": "可能有雷雨",
    "Smoky haze": "霾", "Light rain shower": "小阵雨", "Patchy light rain": "零星小雨",
}


def get_weather(city: str) -> str:
    """工具 1：调用 wttr.in 查天气。成功返回中文摘要；任何失败都返回「工具错误」字符串，不抛异常。"""
    try:
        url = f"https://wttr.in/{requests.utils.quote(city)}?format=j1"
        resp = requests.get(url, timeout=15, headers={"User-Agent": "curl/8.5.0"})
        resp.raise_for_status()
        data = resp.json()
        cur = data["current_condition"][0]
        desc_en = cur["weatherDesc"][0]["value"]
        desc_zh = _WEATHER_ZH.get(desc_en, desc_en)
        try:
            area = data["nearest_area"][0]["areaName"][0]["value"]
        except (KeyError, IndexError):
            area = city
        return (
            f"[{area}当前天气] {desc_zh}({desc_en})；气温 {cur.get('temp_C', '?')}°C，"
            f"体感 {cur.get('FeelsLikeC', '?')}°C；湿度 {cur.get('humidity', '?')}%；"
            f"{cur.get('winddir16Point', '')}风 {cur.get('windspeedKmph', '?')}km/h"
        )
    except Exception as exc:  # 网络超时/城市无效/JSON 解析失败等，一律交给模型决定重试策略
        return f"工具错误: 查询 {city} 天气失败（{type(exc).__name__}: {exc}）。可换英文或更常见的城市名重试"


def get_attraction(city: str, weather: str) -> str:
    """工具 2：用 Tavily 检索「城市 + 当天天气」下的景点推荐。失败同样返回错误字符串。"""
    try:
        if not TAVILY_API_KEY:
            return "工具错误: .env 中未找到 Tavily API Key（tavily-api-key / tailvy-api-key），无法检索景点"
        query = f"{city} {weather} 旅游景点推荐 游玩攻略"
        resp = TavilyClient(api_key=TAVILY_API_KEY).search(
            query=query, search_depth="basic", max_results=5
        )
        results = resp.get("results", []) if isinstance(resp, dict) else []
        if not results:
            return f"工具错误: Tavily 未返回 {city} 的景点结果，请换关键词重试"
        lines = [f"以下是根据「{city} · {weather}」检索到的景点推荐："]
        for idx, item in enumerate(results, 1):
            title = item.get("title", "").strip()
            url = item.get("url", "").strip()
            snippet = re.sub(r"\s+", " ", item.get("content", "")).strip()[:180]
            lines.append(f"{idx}. {title}\n   链接: {url}\n   简介: {snippet}")
        return "\n".join(lines)
    except Exception as exc:
        return f"工具错误: Tavily 检索失败（{type(exc).__name__}: {exc}）。可稍后重试或换种问法"


def run_tool(name: str, args_text: str) -> str:
    """根据 Action 解析出的工具名与参数文本执行工具（含参数数量校验），统一返回字符串。"""
    parts = [p.strip() for p in args_text.split(",")]
    if name == "get_weather":
        city = parts[0] if parts and parts[0] else ""
        if not city:
            return "工具错误: get_weather 缺少参数，格式应为 get_weather[城市名]"
        return get_weather(city)
    if name == "get_attraction":
        city = parts[0] if parts and parts[0] else ""
        if not city:
            return "工具错误: get_attraction 缺少参数，格式应为 get_attraction[城市名, 天气摘要]"
        weather = parts[1] if len(parts) > 1 and parts[1] else "当前天气"
        return get_attraction(city, weather)
    names = "、".join(t["name"] for t in TOOLS)
    return f"工具错误: 未知工具 {name!r}，可用工具: {names}"


# ---------------- 输出格式解析 ----------------

ACTION_RE = re.compile(r"Action\s*[:：]\s*(\w+)\s*\[([^\]]*)\]")
FINISH_RE = re.compile(r"Finish\s*[:：]?\s*\[\s*(.+?)\s*\]")
# Phase 1 计划步骤行：允许 "1. xxx"、"1、xxx"、"Step 1: xxx"、"步骤1：xxx" 等写法
_PLAN_STEP_RE = re.compile(r"^(?:(?:Step|步骤)\s*)?\d{1,3}\s*[.、)．:：\-]\s*(.+)$")
_PLAN_HEADER_RE = re.compile(r"^\s*(?:执行计划|计划|Plan)\s*[:：]?\s*$")


def parse_action(text: str) -> tuple[str, str] | None:
    """抽取 Action: 工具名[参数]；未匹配返回 None。"""
    m = ACTION_RE.search(text)
    if not m:
        return None
    return m.group(1).strip(), m.group(2).strip()


def parse_finish(text: str) -> str | None:
    """抽取 Finish[最终回答]；未匹配返回 None。"""
    m = FINISH_RE.search(text)
    return m.group(1).strip() if m else None


def parse_thought(text: str) -> str:
    """抽取 Thought: ... 的正文（到下一行 Action / Finish 之前）。"""
    m = re.search(
        r"Thought\s*[:：]\s*(.*?)(?=\n\s*(?:Action|Finish)\b)", text, re.S
    )
    return m.group(1).strip() if m else text.strip()


def parse_plan_steps(text: str) -> list[str]:
    """
    解析 Phase 1 生成的计划原文为步骤列表。

    规则：只认「编号开头」的行作为新步骤；编号行之间的非空行视为上一步骤的续行；
    「计划：」等标题行跳过。一个都匹配不到时，把全文兜底成单步，保证 Phase 2 有可用上下文。
    """
    steps: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _PLAN_HEADER_RE.match(line):
            continue
        m = _PLAN_STEP_RE.match(line)
        if m:
            steps.append(m.group(1).strip())
        elif steps:
            # 续行内容拼到当前最后一步后面
            steps[-1] = f"{steps[-1]} {line}".strip()
    if not steps:
        cleaned = re.sub(r"\s+", " ", text).strip()
        steps = [cleaned] if cleaned else []
    # 规整连续空白
    return [re.sub(r"\s+", " ", s).strip() for s in steps if s.strip()]


# ---------------- System Prompt（两套：Plan 阶段 & Solve 阶段） ----------------

_TOOLS_TEXT = """
可用工具清单：
1. get_weather[城市名]
   作用：查询该城市当前天气（温度/体感/湿度/风速/天气现象）。
   示例：get_weather[北京]
2. get_attraction[城市名, 天气摘要]
   作用：根据城市和当天天气，检索该城市适合游玩的景点/攻略。
   示例：get_attraction[北京, 晴 26°C 微风]
"""

PLAN_SYSTEM_PROMPT = f"""你是一个「旅行小助手」智能体。回答用户的旅行需求前，你要先制定一份清晰的执行计划。

任务：请仔细阅读用户问题，规划出「先查什么、再查什么、最后怎么汇总答复」的步骤序列。

{_TOOLS_TEXT}

输出格式（必须严格遵守）：只输出一行「计划：」标题，后面每一行是一条编号步骤，例如：

计划：
1. 调用 get_weather 查询目标城市的当前天气
2. 把天气摘要作为参数，调用 get_attraction 检索适合当天游玩的景点/攻略
3. 综合天气与景点检索结果，用中文给出完整的旅行建议（Finish）

规则：
- 每一步用一行中文描述，可注明要用的工具（get_weather / get_attraction），最后一步注明 Finish 即可。
- 本阶段只做规划：禁止真正执行或书写 Action: 调用、禁止输出 Thought:，也禁止使用 Finish[方括号] 形式。
- 计划要具体、可直接执行，步骤数建议 2~5 条。
- 全程使用中文。"""


def build_solve_prompt(plan_text: str) -> str:
    """构造 Phase 2（Solve）的 System Prompt：把 Phase 1 生成的计划注入，并要求逐步执行。"""
    return f"""你是一个「旅行小助手」智能体。以下是针对本次用户问题制定的执行计划，请逐步执行该计划，通过多轮调用工具收集信息，直到计划中的信息收集完毕，再给出最终中文旅行建议。

本次执行计划：
{plan_text}

{_TOOLS_TEXT}

执行阶段的回复格式（先 Thought 后 Action，Action 只能出现一行）：
Thought: 用中文简短说明你在想什么、这一步计划要做什么、为什么调用该工具。
Action: get_weather[北京]

如果信息已经足够、可以给最终答复，就不再写 Action，直接输出：
Thought: 北京今天有轻度霾、气温 26°C，检索结果里故宫和室内场馆都很合适……
Finish[今天北京是霾天、26°C 较热，建议优先安排室内与博物馆类行程：上午参观故宫博物院（提前预约、以室内展馆为主），中午在王府井用午餐，下午去中国国家博物馆避暑避霾；户外如颐和园等可放到傍晚风起之后。穿衣以轻薄夏装为主，建议佩戴口罩、随身带水。]

规则：
- 必须按上面给出的执行计划逐步推进；计划里没有的信息不要自己脑补，先调用工具确认。
- Action 中的工具名和参数必须与清单一致，参数写在半角方括号内；get_attraction 有两个参数，用英文逗号分隔。
- 每次只调用一个工具，等待 Observation 返回后再决定下一轮动作。
- Finish 不是工具：它用于给出最终答复，前面绝不能写 “Action:”，一行里也不能既有 Action 又有 Finish。
- Finish 的方括号里必须写出真正完整可用的中文旅行回答（结合刚查到的实际天气和景点信息），不要写格式提示或占位文字。
- 如果 Observation 以「工具错误」开头，说明刚才的调用失败了：不要慌，换一种表达（如英文城市名、更常见的说法）重试一次；若仍失败再如实告知用户。
- 推荐先 get_weather 拿到天气，再 get_attraction 按天气推荐景点，最后综合成完整建议。
- 全程使用中文回复用户。"""


# ---------------- DeepSeek 客户端 ----------------

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=DEEPSEEK_API_KEY or "not-configured", base_url=DEEPSEEK_BASE_URL)
    return _client


def chat_once(messages: list[dict[str, str]]) -> str:
    resp = get_client().chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=messages,
        temperature=0.3,
        max_tokens=2000,
    )
    return resp.choices[0].message.content or ""


# ---------------- 过程日志（Trace）：记录每轮模型的完整输入 / 输出 ----------------

def _role_note(role: str, content: str) -> str:
    """给 message 打一个简短的中文说明，方便终端阅读。"""
    if role == "system":
        return "系统提示词（角色 + 工具清单 + 格式约束）"
    if role == "user":
        return "工具结果回喂" if content.startswith("Observation") else "用户提问"
    return "模型历史回复"


def _print_messages(messages: list[dict[str, str]], header: str) -> None:
    """把一次模型调用的完整输入（messages 全部内容）打印到终端。"""
    print("-" * 72)
    print(f"[{header}] 共 {len(messages)} 条消息")
    print("-" * 72)
    for idx, msg in enumerate(messages, 1):
        print(f"── message {idx}/{len(messages)} · role={msg['role']} ({_role_note(msg['role'], msg['content'])}) ──")
        print(msg["content"])
        if idx < len(messages):
            print()


def _save_trace(user_query: str, phase1: dict, phase2: dict) -> None:
    """把两阶段整轮调用过程写成 plan_solve_trace.json，供网页复盘使用。"""
    data = {
        "query": user_query,
        "model": DEEPSEEK_MODEL,
        "paradigm": "plan_solve",
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "phase1": phase1,
        "phase2": phase2,
    }
    try:
        TRACE_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n[Trace] 过程记录已写入 {TRACE_PATH.name}（Phase1 计划 1 次 + Phase2 {len(phase2.get('rounds') or [])} 轮）")
    except OSError as exc:
        print(f"\n[警告] 无法写入过程记录 {TRACE_PATH}: {exc}")


# ---------------- 打印与通用小工具 ----------------

def _trunc(text: str, limit: int = OBS_TRUNCATE) -> str:
    return text if len(text) <= limit else text[:limit] + "\n……(内容过长已截断)"


def _indent(text: str, prefix: str = "  ") -> str:
    return "\n".join(prefix + line if line else line for line in text.splitlines())


def _wrap(text: str, width: int = 88) -> str:
    """长行自动换行（保留原有分段），避免超长单行刷屏/被截断。"""
    lines = []
    for line in text.splitlines():
        if not line:
            lines.append("")
            continue
        lines.extend(textwrap.fill(line, width=width, break_long_words=False).splitlines())
    return "\n".join(lines)


def _strip_prefix(text: str) -> str:
    text = text.strip()
    for prefix in ("Final Answer:", "最终回答:", "答案:", "答复:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    return text


# ---------------- 主流程：Plan → Solve 两阶段 ----------------

def run_agent(user_query: str) -> None:
    # ============ Phase 1 · Plan（1 次 LLM 调用，不调用任何工具） ============
    print("\n" + "=" * 72)
    print("[PHASE 1 · PLAN] 生成执行计划（本阶段只规划、不调用工具）")
    print("=" * 72)

    p1_messages: list[dict[str, str]] = [
        {"role": "system", "content": PLAN_SYSTEM_PROMPT},
        {"role": "user", "content": user_query},
    ]
    _print_messages(p1_messages, "Phase 1 · 计划生成 · 模型输入(完整 Prompt)")

    plan_text = ""
    for attempt in range(3):
        try:
            plan_text = chat_once(p1_messages)
            break
        except Exception as exc:
            print(f"  [API 调用失败，重试 {attempt + 1}/3] {type(exc).__name__}: {exc}")
    if not plan_text:
        print("  [Phase 1 模型连续 3 次调用失败，本次对话终止，请检查 .env 中的 DeepSeek Key 与网络]")
        return

    print("-" * 72)
    print("[Phase 1 · 模型输出 · 计划原文]")
    print(plan_text)
    plan_steps = parse_plan_steps(plan_text)
    print("-" * 72)
    print("[Phase 1 · 解析出的计划步骤]")
    if plan_steps:
        for idx, step in enumerate(plan_steps, 1):
            print(_indent(step, prefix=f"  {idx}. "))
    else:
        print("  （未解析出编号步骤，将原文作为整段计划注入 Phase 2）")

    phase1: dict = {
        "input_messages": copy.deepcopy(p1_messages),
        "model_output": plan_text,
        "plan_text": plan_text,
        "plan_steps": plan_steps,
    }

    # ============ Phase 2 · Solve（注入计划，逐轮执行直到 Finish） ============
    print("\n" + "=" * 72)
    print("[PHASE 2 · SOLVE] 注入计划并逐步执行（Thought / Action / Finish，上限 " + str(MAX_SOLVE_ROUNDS) + " 轮）")
    print("=" * 72)

    messages: list[dict[str, str]] = [
        {"role": "system", "content": build_solve_prompt(plan_text)},
        {"role": "user", "content": user_query},
    ]
    last_output = ""
    rounds: list[dict] = []
    final_answer: str | None = None
    status = "max_rounds"

    for turn in range(1, MAX_SOLVE_ROUNDS + 1):
        print("\n" + "-" * 72)
        print(f"▶ Phase 2 · 第 {turn}/{MAX_SOLVE_ROUNDS} 轮 · 调用 DeepSeek（{DEEPSEEK_MODEL}）")

        _print_messages(messages, f"Phase 2 · 第 {turn} 轮 · 模型输入")

        # 调模型（最多尝试 3 次）
        text = ""
        for attempt in range(3):
            try:
                text = chat_once(messages)
                break
            except Exception as exc:
                print(f"  [API 调用失败，重试 {attempt + 1}/3] {type(exc).__name__}: {exc}")
        if not text:
            print("  [模型连续 3 次调用失败，本次对话终止，请检查 .env 中的 DeepSeek Key 与网络]")
            _save_trace(user_query, phase1, {
                "max_rounds": MAX_SOLVE_ROUNDS,
                "rounds": rounds,
                "final_answer": None,
                "status": "api_error",
            })
            return
        last_output = text

        print("-" * 72)
        print("[模型输出 · 原文]")
        print(text)
        print("-" * 72)

        # 解析 Thought / Action / Finish
        thought = parse_thought(text)
        finish_answer = parse_finish(text)
        action = parse_action(text)
        # 兜底：模型偶尔会把终结动作误写成 Action: Finish[...]，这里归一化为 Finish
        if action is not None and action[0].lower() == "finish":
            if finish_answer is None:
                finish_answer = action[1]
            action = None

        entry: dict = {
            "turn": turn,
            "input_messages": copy.deepcopy(messages),
            "model_output": text,
            "thought": thought,
            "action": None,
            "finish": finish_answer,
            "observation": None,
            "history_after": None,
        }

        print("Thought:")
        print(_indent(thought if thought else "（模型本轮未按格式输出 Thought，原文如下）"))
        if action:
            entry["action"] = {"name": action[0], "args": action[1]}
            print(f"\nAction: {action[0]}[{action[1]}]")
        elif finish_answer:
            print("\n（模型输出中检测到 Finish，即将给出最终答复）")
        else:
            print("\n（本轮未解析到 Action，原文：）")
            print(_indent(text))

        # Finish 命中 -> 打印最终答复并退出
        if finish_answer:
            final = _strip_prefix(finish_answer)
            entry["finish"] = final
            entry["history_after"] = copy.deepcopy(
                messages + [{"role": "assistant", "content": text}]
            )
            rounds.append(entry)
            final_answer = final
            status = "finished"
            print("\n" + "-" * 72)
            print(f">> 最终答复（Phase 2 · 第 {turn} 轮 Finish）:")
            print(_indent(_wrap(final)))
            print("=" * 72)
            _save_trace(user_query, phase1, {
                "max_rounds": MAX_SOLVE_ROUNDS,
                "rounds": rounds,
                "final_answer": final_answer,
                "status": status,
            })
            return

        # 执行工具并打印 Observation
        if action is None:
            observation = "未识别到 Action，也没有 Finish。请严格按格式重新输出：先 Thought，再 Action: 工具名[参数]"
        else:
            observation = run_tool(action[0], action[1])
        observation = _trunc(observation)
        entry["observation"] = observation
        print("\n" + "-" * 72)
        print("Observation:")
        print(_indent(observation))

        # 把本轮输出与观察结果追加进消息历史，进入下一轮
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content": f"Observation: {observation}"})
        entry["history_after"] = copy.deepcopy(messages)
        rounds.append(entry)

    # 达到最大轮数仍未 Finish
    print("\n" + "=" * 72)
    print(f"[已达 Phase 2 最大轮数 {MAX_SOLVE_ROUNDS}] 未收到 Finish，对话结束。")
    print("模型最后一轮输出供参考：")
    print(_indent(_wrap(_strip_prefix(last_output))))
    print("=" * 72)
    _save_trace(user_query, phase1, {
        "max_rounds": MAX_SOLVE_ROUNDS,
        "rounds": rounds,
        "final_answer": None,
        "status": status,
    })


# ---------------- 入口 ----------------

def main() -> None:
    if not DEEPSEEK_API_KEY:
        print("错误: .env 中未找到 DeepSeek API Key（deepseek-api-key）。请在 plan_solve_agent.py 同目录的 .env 里补上后重试。")
        sys.exit(1)
    if not TAVILY_API_KEY:
        print("警告: .env 中未找到 Tavily API Key，景点推荐（get_attraction）将不可用，天气查询不受影响。")

    if len(sys.argv) > 1:
        run_agent(" ".join(sys.argv[1:]))
        return
    print("旅行小助手（Plan-Solve）已就绪（输入 q 退出）")
    while True:
        try:
            query = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not query:
            continue
        if query.lower() in ("q", "quit", "exit"):
            break
        run_agent(query)


if __name__ == "__main__":
    main()
