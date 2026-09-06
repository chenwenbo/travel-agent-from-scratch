#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reflection_agent.py — 纯 Python 旅行助手智能体（Reflection 反思范式，不引入任何 Agent 框架）

技术栈：openai（DeepSeek 兼容接口）+ requests（wttr.in）+ tavily-python（Tavily 搜索）

工作方式（Reflection / Reflexion 三角色闭环）：
    Draft（生成 · Actor）：以 Thought / Action / Finish 的方式调用工具收集事实（天气、景点），
                          产出第一版草稿答复；工具返回统一记为 Evidence（事实依据）。
    Evaluate（反思 · Critic）：换一个「评审专家」角色，按 5 个维度给草稿打分，输出
                          总分、PASS / NEED_REVISION 结论、主要问题与改进建议；本阶段不调工具，
                          只基于 Evidence 判断答复是否有事实错误或遗漏。
    Revise（修订 · Revisor）：未通过评审时，把「问题 + 建议 + Evidence + 上一版答复」一起注入，
                          让模型重写一版答复；随后再次评审，直到 PASS 或达到 MAX_REFLECT_ROUNDS。

收敛与兜底：PASS 立即收敛；达到反思轮数上限仍未 PASS，则取评分最高（并列取最新）的版本作为最终答复，
            保证任何情况下都有可用输出。

日志与复盘：每次大模型调用前打印完整输入 messages、调用后打印输出原文，并按调用顺序写入
            reflection_trace.json（整文件覆盖写）；再由 make_reflection_html.py 渲染成
            reflection_trace.html 复盘网页（参考 agent_trace.html 的视觉风格）。

API Key 从同目录 .env 读取：deepseek-api-key 与 tailvy-api-key（兼容 tavily-api-key 拼写）。

用法：
    python3 reflection_agent.py "我想去北京玩"     # 命令行直接给需求
    python3 reflection_agent.py                     # 交互式输入
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
MAX_DRAFT_ROUNDS = 4                      # Draft 阶段（Actor 工具循环）最大轮数
MAX_REFLECT_ROUNDS = 2                    # 反思循环最大轮数（每轮 = 1 次评估 + 可能的 1 次修订）
OBS_TRUNCATE = 1500                       # Observation / Evidence 展示与回喂的最大字符数
ENV_PATH = Path(__file__).resolve().parent / ".env"
TRACE_PATH = Path(__file__).resolve().parent / "reflection_trace.json"  # 每次调用的结构化记录（供网页复盘）

# 评分维度：评审器按这些维度逐项打分（单项满分 5 分）
EVAL_DIMENSIONS: list[tuple[str, str]] = [
    ("天气结合", "是否正确使用了查到的天气信息（温度/天气现象/风力）并据此给出建议"),
    ("景点推荐", "推荐的景点是否具体、与城市和天气匹配，且能追溯到检索结果"),
    ("实用可执行", "是否给出可执行的行程/时间/交通安排，而不是空泛套话"),
    ("结构完整", "是否覆盖行程、穿衣、注意事项等，条理清晰易读"),
    ("安全提醒", "是否针对天气、人流、预约等给出风险提示与应对"),
]
MAX_TOTAL_SCORE = 5 * len(EVAL_DIMENSIONS)   # 满分 25

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
# Finish 内容常跨多行（分点行程），因此必须开启 DOTALL
FINISH_RE = re.compile(r"Finish\s*[:：]?\s*\[\s*(.+?)\s*\]", re.S)
FINISH_FALLBACK_RE = re.compile(r"Finish\s*[:：]?\s*\[(.*)\]", re.S)

# 评估器输出解析：单项分数「维度名：4/5」、总分「总分：21/25」、结论「结论：NEED_REVISION」
_score_line_re = re.compile(r"^\s*(?:\d{1,2}\s*[.、)．:：\-]\s*)?([^:：\n]{2,12}?)\s*[:：]\s*(\d{1,2})\s*/\s*(\d{1,2})")
_total_re = re.compile(r"总分\s*[:：]\s*(\d{1,3})\s*(?:/\s*(\d{1,3}))?")
_verdict_re = re.compile(r"结论\s*[:：]\s*(PASS|NEED[\s_\-]?REVISION|通过|不通过)", re.I)
_bullet_re = re.compile(r"^(?:\d{1,2}\s*[.、)．:：\-]|[-*•·])\s*(.+)$")


def parse_action(text: str) -> tuple[str, str] | None:
    """抽取 Action: 工具名[参数]；未匹配返回 None。"""
    m = ACTION_RE.search(text)
    if not m:
        return None
    return m.group(1).strip(), m.group(2).strip()


def parse_finish(text: str) -> str | None:
    """抽取 Finish[最终回答]；未匹配返回 None。支持跨多行的 Finish 内容。"""
    m = FINISH_RE.search(text)
    if not m:
        m = FINISH_FALLBACK_RE.search(text)  # 兜底：一直取到最后一个 ]
    return m.group(1).strip() if m else None


def parse_thought(text: str) -> str:
    """抽取 Thought: ... 的正文（到下一行 Action / Finish 之前）。"""
    m = re.search(
        r"Thought\s*[:：]\s*(.*?)(?=\n\s*(?:Action|Finish)\b)", text, re.S
    )
    return m.group(1).strip() if m else text.strip()


def parse_scores(text: str) -> list[dict]:
    """解析评估器输出里的逐项打分，返回 [{"dim": 维度, "score": 4, "max": 5}, ...]。"""
    scores: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("总分") or line.startswith("结论"):
            continue
        m = _score_line_re.match(line)
        if not m:
            continue
        dim, score, max_score = m.group(1).strip(), int(m.group(2)), int(m.group(3))
        if max_score <= 0 or score > max_score:
            continue
        scores.append({"dim": dim, "score": score, "max": max_score})
    return scores


def parse_total(text: str) -> tuple[int | None, int]:
    """解析「总分：21/25」，拿不到时退回逐项打分求和。"""
    m = _total_re.search(text)
    if m:
        return int(m.group(1)), int(m.group(2)) if m.group(2) else MAX_TOTAL_SCORE
    scores = parse_scores(text)
    if scores:
        return sum(s["score"] for s in scores), sum(s["max"] for s in scores)
    return None, MAX_TOTAL_SCORE


def parse_verdict(text: str, total: int | None, max_total: int) -> tuple[str, bool]:
    """
    解析评估结论，返回 (PASS / NEED_REVISION, 是否由显式「结论：」行解析得到)。
    显式结论优先；解析不到时按总分阈值（>= 80% 满分）兜底。
    """
    m = _verdict_re.search(text)
    if m:
        raw = m.group(1).upper().replace(" ", "").replace("_", "").replace("-", "")
        if raw in ("PASS", "通过"):
            return "PASS", True
        return "NEED_REVISION", True
    if total is not None and max_total > 0 and total >= max_total * 0.8:
        return "PASS", False
    return "NEED_REVISION", False


def parse_section(text: str, header: str, stop_headers: tuple[str, ...]) -> list[str]:
    """
    抓取「主要问题：」「改进建议：」等小节下的条目列表。
    识别编号行 / 符号行；无编号的续行拼到上一条；遇到下一个小节标题即停止。
    """
    items: list[str] = []
    collecting = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(header):
            collecting = True
            rest = line[len(header):].lstrip(" ：:\t").strip()
            if rest:
                m = _bullet_re.match(rest)
                items.append(m.group(1).strip() if m else rest)
            continue
        if not collecting:
            continue
        if any(line.startswith(h) for h in stop_headers):
            break
        m = _bullet_re.match(line)
        if m:
            items.append(m.group(1).strip())
        elif items:
            items[-1] = f"{items[-1]} {line}".strip()
        else:
            items.append(line)
    return [re.sub(r"\s+", " ", i).strip() for i in items if i.strip()]


def parse_evaluation(text: str) -> dict:
    """把评估器输出解析成结构化结果（分数 / 总分 / 结论 / 问题 / 建议）。"""
    scores = parse_scores(text)
    total, max_total = parse_total(text)
    if max_total <= 0:
        max_total = MAX_TOTAL_SCORE
    verdict, verdict_parsed = parse_verdict(text, total, max_total)
    issues = parse_section(text, "主要问题", ("改进建议", "结论", "总分"))
    suggestions = parse_section(text, "改进建议", ("主要问题", "结论", "总分"))
    return {
        "scores": scores,
        "total": total,
        "max_total": max_total,
        "verdict": verdict,
        "verdict_parsed": verdict_parsed,
        "issues": issues,
        "suggestions": suggestions,
    }


# ---------------- System Prompt：Draft / Evaluate / Revise 三套 ----------------

_TOOLS_TEXT = """
可用工具清单：
1. get_weather[城市名]
   作用：查询该城市当前天气（温度/体感/湿度/风速/天气现象）。
   示例：get_weather[北京]
2. get_attraction[城市名, 天气摘要]
   作用：根据城市和当天天气，检索该城市适合游玩的景点/攻略。
   示例：get_attraction[北京, 晴 26°C 微风]
"""

DRAFT_SYSTEM_PROMPT = f"""你是一个「旅行小助手」智能体。为了回答用户的旅行需求，你可以在多轮中反复调用工具获取信息，直到信息足够再给出答复。

{_TOOLS_TEXT}

每一轮你的回复必须严格按下面格式输出（先 Thought 后 Action，且 Action 只能出现一行）：
Thought: 用中文简短说明你在想什么、下一步为什么调这个工具。
Action: get_weather[北京]

如果信息已经足够、要给出答复，就不要再写 Action，直接单独输出一行：
Thought: 北京今天有轻度霾、气温 26°C，检索结果里故宫和室内场馆都很合适……
Finish[今天北京是霾天、26°C 较热，建议优先安排室内与博物馆类行程：上午参观故宫博物院（提前预约、以室内展馆为主），中午在王府井用午餐，下午去中国国家博物馆避暑避霾；户外如颐和园等可放到傍晚风起之后。穿衣以轻薄夏装为主，建议佩戴口罩、随身带水。]

规则：
- 这只是一版草稿，后面还有评审环节会指出问题，所以请如实、完整地写出你基于工具结果能给出的最佳建议。
- Action 中的工具名和参数必须与清单一致，参数写在半角方括号内；get_attraction 有两个参数，用英文逗号分隔。
- 每次只调用一个工具，等待 Observation 返回后再决定下一轮动作。
- Finish 不是工具：它用于给出答复，前面绝不能写 “Action:”，一行里也不能既有 Action 又有 Finish。
- Finish 的方括号里必须写出真正完整可用的中文旅行回答（结合刚查到的实际天气和景点信息），不要写格式提示或占位文字。
- 严禁编造工具没有返回的数据（如具体票价、开放时间、车次），只能用工具 Observation 里的信息。
- 如果 Observation 以「工具错误」开头，说明刚才的调用失败了：不要慌，换一种表达（如英文城市名、更常见的说法）重试一次；若仍失败再如实告知用户。
- 推荐先 get_weather 拿到天气，再 get_attraction 按天气推荐景点，最后综合成完整建议。
- 全程使用中文回复用户。"""

# 评审维度说明（供 Prompt 与日志复用）
_EVAL_DIM_TEXT = "\n".join(
    f"{i}. {name}：{desc}" for i, (name, desc) in enumerate(EVAL_DIMENSIONS, 1)
)

EVALUATE_SYSTEM_PROMPT = f"""你是一位严格的「旅行方案评审专家」。你会拿到用户的原始提问、工具收集到的事实依据，以及助手给出的一版答复。
你的任务是给这版答复做质量评审，找出事实错误、遗漏与表达问题，供助手下一轮改进。

评分维度（每项 1~5 分，5 分最好）：
{_EVAL_DIM_TEXT}

输出格式（必须严格遵守，逐项都要写）：
评估：
1. 天气结合：4/5 —— 一句话说明理由
2. 景点推荐：3/5 —— 一句话说明理由
3. 实用可执行：4/5 —— 一句话说明理由
4. 结构完整：4/5 —— 一句话说明理由
5. 安全提醒：3/5 —— 一句话说明理由
总分：18/25
结论：NEED_REVISION
主要问题：
1. 问题一
2. 问题二
改进建议：
1. 建议一
2. 建议二

规则：
- 「结论：」这一行只能写 PASS 或 NEED_REVISION 二者之一：答复基本可用、没有事实硬伤时写 PASS；存在事实错误、遗漏关键信息、空泛不可执行时写 NEED_REVISION。
- 关键：答复里出现事实依据中不存在的票价、开放时间、具体数据，一律判 NEED_REVISION。
- 「主要问题」与「改进建议」即使判定 PASS 也要写（写「无」或写可优化项），每条单独一行、用编号开头。
- 只评审，不要重写整篇答复，也不要调用任何工具。
- 全程使用中文。"""


def build_evaluate_user(user_query: str, evidence_text: str, answer: str) -> str:
    """评审阶段的 user 消息：原始提问 + 事实依据 + 待评估答复。"""
    return (
        f"用户提问：{user_query}\n\n"
        f"已收集的事实依据（工具真实返回，答复中的任何数据都必须来自这里）：\n{evidence_text}\n\n"
        f"待评估的答复：\n{answer}"
    )


REVISE_SYSTEM_PROMPT = """你是一个「旅行小助手」智能体，正在根据评审专家的意见修订自己上一版的答复。

输出要求：直接输出修订后的完整中文旅行答复正文，不要写任何前缀、标题、解释或「以下是修订版」之类的话。

规则：
- 逐条解决评审意见中列出的「主要问题」与「改进建议」，同时保留上一版中已经正确的部分。
- 严禁编造事实依据中不存在的数据（票价、开放时间、车次、具体距离等）；需要但不掌握的信息，改为提示用户出行前自行核实。
- 内容要具体可执行：给出时间安排、室内外取舍、穿衣与防护、注意事项；若天气不佳要明确说明如何调整行程。
- 使用中文，语气自然友好，篇幅控制在 300~500 字左右。"""


def build_revise_user(user_query: str, evidence_text: str, prev_answer: str, review: dict) -> str:
    """修订阶段的 user 消息：注入评审意见、事实依据与上一版答复。"""
    issues_text = "\n".join(f"{i}. {t}" for i, t in enumerate(review["issues"], 1)) or "（评审未列出具体问题）"
    sugg_text = "\n".join(f"{i}. {t}" for i, t in enumerate(review["suggestions"], 1)) or "（评审未给出具体建议）"
    return (
        f"用户提问：{user_query}\n\n"
        f"已收集的事实依据（只能依据这些信息，不得编造）：\n{evidence_text}\n\n"
        f"上一版答复：\n{prev_answer}\n\n"
        f"评审结论：{review['verdict']}（总分 {review['total']} / {review['max_total']}）\n\n"
        f"主要问题：\n{issues_text}\n\n"
        f"改进建议：\n{sugg_text}"
    )


# ---------------- DeepSeek 客户端 ----------------

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=DEEPSEEK_API_KEY or "not-configured", base_url=DEEPSEEK_BASE_URL)
    return _client


def chat_once(messages: list[dict[str, str]], temperature: float = 0.3) -> str:
    resp = get_client().chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=2000,
    )
    return resp.choices[0].message.content or ""


# ---------------- 过程日志（Trace）：记录每轮模型的完整输入 / 输出 ----------------

_STEPS: list[dict] = []   # 本次运行按调用顺序累积的每一次大模型交互


def _role_note(role: str, content: str) -> str:
    """给 message 打一个简短的中文说明，方便终端阅读。"""
    if role == "system":
        return "系统提示词（角色 + 规则）"
    if role == "user":
        if content.startswith("Observation"):
            return "工具结果回喂"
        if content.startswith("用户提问"):
            return "提问 + 事实依据 + 待处理内容"
        return "用户提问"
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


def call_llm(
    messages: list[dict[str, str]],
    *,
    kind: str,
    title: str,
    round_no: int,
    temperature: float = 0.3,
) -> tuple[str, dict]:
    """
    统一的模型调用入口：打印完整输入 → 调用（最多 3 次）→ 打印输出原文 → 登记一条 step 记录。
    返回 (模型输出, step 字典)；连续失败时输出为空字符串，由调用方决定兜底。
    """
    _print_messages(messages, f"{title} · 模型输入")

    text = ""
    for attempt in range(3):
        try:
            text = chat_once(messages, temperature=temperature)
            break
        except Exception as exc:
            print(f"  [API 调用失败，重试 {attempt + 1}/3] {type(exc).__name__}: {exc}")
    if not text:
        print("  [模型连续 3 次调用失败，请检查 .env 中的 DeepSeek Key 与网络]")

    print("-" * 72)
    print(f"[{title} · 模型输出 · 原文]")
    print(text)
    print("-" * 72)

    step: dict = {
        "index": len(_STEPS) + 1,
        "kind": kind,                      # draft / evaluate / revise
        "title": title,
        "round": round_no,                 # 所属反思轮次（Draft 阶段为 0）
        "temperature": temperature,
        "input_messages": copy.deepcopy(messages),
        "model_output": text,
        "thought": None,
        "action": None,
        "observation": None,
        "finish": None,
        "parsed": {},
    }
    _STEPS.append(step)
    return text, step


def _save_trace(
    user_query: str,
    draft_answer: str | None,
    reflections: list[dict],
    final_answer: str | None,
    status: str,
) -> None:
    """把整轮（Draft + 每次反思/修订）过程写成 reflection_trace.json，供网页复盘使用。"""
    data = {
        "query": user_query,
        "model": DEEPSEEK_MODEL,
        "paradigm": "reflection",
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "max_reflect_rounds": MAX_REFLECT_ROUNDS,
        "max_total_score": MAX_TOTAL_SCORE,
        "steps": copy.deepcopy(_STEPS),
        "draft_answer": draft_answer,
        "reflections": reflections,
        "final_answer": final_answer,
        "status": status,
    }
    try:
        TRACE_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n[Trace] 过程记录已写入 {TRACE_PATH.name}（共 {len(_STEPS)} 次模型调用）")
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
    for prefix in ("Final Answer:", "最终回答:", "修订后答复:", "修订版:", "答案:", "答复:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    return text


def build_evidence(observations: list[str]) -> str:
    """把工具返回的 Observation 汇总成评审/修订时使用的事实依据文本。"""
    if not observations:
        return "（本轮未调用任何工具，暂无外部事实依据；如答复中出现具体数据请判定为编造）"
    blocks = [f"[证据 {i}] {_trunc(obs, 1200)}" for i, obs in enumerate(observations, 1)]
    return "\n\n".join(blocks)


# ---------------- Reflection 主流程：Draft → (Evaluate → Revise)×N ----------------

def run_draft(user_query: str) -> tuple[str | None, list[str]]:
    """
    Draft 阶段（Actor）：Thought / Action / Finish 工具循环，产出第一版草稿答复。
    返回 (草稿答复, 工具 Observation 列表)；未产出 Finish 时草稿为 None。
    """
    messages: list[dict[str, str]] = [
        {"role": "system", "content": DRAFT_SYSTEM_PROMPT},
        {"role": "user", "content": user_query},
    ]
    observations: list[str] = []
    draft_answer: str | None = None

    for turn in range(1, MAX_DRAFT_ROUNDS + 1):
        print("\n" + "=" * 72)
        print(f"▶ [DRAFT] 第 {turn}/{MAX_DRAFT_ROUNDS} 轮 · 调用 DeepSeek（{DEEPSEEK_MODEL}）")
        text, step = call_llm(
            messages,
            kind="draft",
            title=f"Draft 草稿生成 · 第 {turn} 轮",
            round_no=0,
            temperature=0.3,
        )
        if not text:
            break

        thought = parse_thought(text)
        finish_answer = parse_finish(text)
        action = parse_action(text)
        # 兜底：模型偶尔会把终结动作误写成 Action: Finish[...]，这里归一化为 Finish
        if action is not None and action[0].lower() == "finish":
            if finish_answer is None:
                finish_answer = action[1]
            action = None

        step["thought"] = thought
        step["finish"] = _strip_prefix(finish_answer) if finish_answer else None
        print("Thought:")
        print(_indent(thought if thought else "（模型本轮未按格式输出 Thought，原文见上）"))

        if finish_answer:
            draft_answer = _strip_prefix(finish_answer)
            step["finish"] = draft_answer
            print("\n（Draft 阶段收到 Finish，草稿答复已生成）")
            print(_indent(_wrap(draft_answer)))
            break

        if action is None:
            observation = "未识别到 Action，也没有 Finish。请严格按格式重新输出：先 Thought，再 Action: 工具名[参数]"
        else:
            step["action"] = {"name": action[0], "args": action[1]}
            print(f"\nAction: {action[0]}[{action[1]}]")
            observation = run_tool(action[0], action[1])
        observation = _trunc(observation)
        step["observation"] = observation
        observations.append(observation)
        print("\nObservation:")
        print(_indent(observation))

        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content": f"Observation: {observation}"})

    return draft_answer, observations


def run_evaluate(user_query: str, evidence_text: str, answer: str, round_no: int) -> tuple[dict | None, str]:
    """Evaluate 阶段（Critic）：给当前版本答复打分并给出问题与建议。返回 (解析结果, 原文)。"""
    messages: list[dict[str, str]] = [
        {"role": "system", "content": EVALUATE_SYSTEM_PROMPT},
        {"role": "user", "content": build_evaluate_user(user_query, evidence_text, answer)},
    ]
    print("\n" + "=" * 72)
    print(f"▶ [REFLECT] 第 {round_no}/{MAX_REFLECT_ROUNDS} 轮 · 自我评审（调用 DeepSeek）")
    text, step = call_llm(
        messages,
        kind="evaluate",
        title=f"Reflect 自我评审 · 第 {round_no} 轮",
        round_no=round_no,
        temperature=0.2,
    )
    if not text:
        return None, ""

    review = parse_evaluation(text)
    step["parsed"] = review
    print("评审解析结果：")
    print(_indent(
        f"总分 {review['total']} / {review['max_total']} · 结论 {review['verdict']}"
        + ("" if review["verdict_parsed"] else "（未解析到「结论：」行，按分数阈值兜底）")
    ))
    for s in review["scores"]:
        print(_indent(f"- {s['dim']}: {s['score']}/{s['max']}"))
    if review["issues"]:
        print("主要问题：")
        print(_indent("\n".join(f"{i}. {t}" for i, t in enumerate(review["issues"], 1))))
    if review["suggestions"]:
        print("改进建议：")
        print(_indent("\n".join(f"{i}. {t}" for i, t in enumerate(review["suggestions"], 1))))
    return review, text


def run_revise(
    user_query: str, evidence_text: str, prev_answer: str, review: dict, round_no: int
) -> str | None:
    """Revise 阶段（Revisor）：依据评审意见重写答复，返回修订后的文本。"""
    messages: list[dict[str, str]] = [
        {"role": "system", "content": REVISE_SYSTEM_PROMPT},
        {"role": "user", "content": build_revise_user(user_query, evidence_text, prev_answer, review)},
    ]
    print("\n" + "=" * 72)
    print(f"▶ [REVISE] 第 {round_no}/{MAX_REFLECT_ROUNDS} 轮 · 按评审意见修订（调用 DeepSeek）")
    text, step = call_llm(
        messages,
        kind="revise",
        title=f"Revise 修订改进 · 第 {round_no} 轮",
        round_no=round_no,
        temperature=0.4,
    )
    if not text:
        return None
    new_answer = _strip_prefix(text)
    step["parsed"] = {"answer": new_answer}
    print("修订后答复：")
    print(_indent(_wrap(new_answer)))
    return new_answer


def run_agent(user_query: str) -> None:
    """一次完整运行：Draft → (Evaluate → Revise)×MAX_REFLECT_ROUNDS → 最终答复。"""
    _STEPS.clear()

    print("\n" + "#" * 72)
    print("# REFLECTION 范式 · 旅行助手开始处理")
    print(f"# 用户提问：{user_query}")
    print("#" * 72)

    # ============ 阶段一 · Draft：工具循环产出草稿 ============
    print("\n" + "=" * 72)
    print("[DRAFT] 阶段一 · 生成草稿答复（Thought / Action / Finish，可调用工具）")
    print("=" * 72)
    draft_answer, observations = run_draft(user_query)
    evidence_text = build_evidence(observations)

    if not draft_answer:
        print("\n[Draft 阶段未产出 Finish，无法进入反思环节，本次运行结束]")
        _save_trace(user_query, None, [], None, "draft_failed")
        print("=" * 72)
        return

    print("\n" + "-" * 72)
    print("[DRAFT] 草稿答复（第 0 版）：")
    print(_indent(_wrap(draft_answer)))
    print("-" * 72)
    print(f"[DRAFT] 事实依据（Evidence）共 {len(observations)} 条：")
    print(_indent(evidence_text))

    # ============ 阶段二 · Reflection：评估 → 修订 循环 ============
    current_answer = draft_answer
    reflections: list[dict] = []
    # 评分历史：用于达到轮数上限时挑最好的一版（并列取最新）
    scored: list[tuple[int, str]] = []
    status = "max_rounds"

    for round_no in range(1, MAX_REFLECT_ROUNDS + 1):
        review, raw = run_evaluate(user_query, evidence_text, current_answer, round_no)
        if review is None:
            status = "api_error"
            break

        total = review["total"] if review["total"] is not None else -1
        scored.append((total, current_answer))
        entry: dict = {
            "round": round_no,
            "before": current_answer,
            "after": None,
            "scores": review["scores"],
            "total": review["total"],
            "max_total": review["max_total"],
            "verdict": review["verdict"],
            "verdict_parsed": review["verdict_parsed"],
            "issues": review["issues"],
            "suggestions": review["suggestions"],
            "raw_review": raw,
        }
        reflections.append(entry)

        if review["verdict"] == "PASS":
            status = "passed"
            print(f"\n[REFLECT] 第 {round_no} 轮评审结论 PASS，反思收敛，停止修订。")
            break

        if round_no >= MAX_REFLECT_ROUNDS:
            status = "max_rounds"
            print(f"\n[REFLECT] 已达最大反思轮数 {MAX_REFLECT_ROUNDS} 且仍未 PASS，取评分最高版本作为最终答复。")
            break

        new_answer = run_revise(user_query, evidence_text, current_answer, review, round_no)
        if not new_answer:
            status = "api_error"
            break
        entry["after"] = new_answer
        current_answer = new_answer

    # ============ 阶段三 · 收敛：确定最终答复 ============
    if status == "api_error":
        final_answer = current_answer
    elif status == "passed":
        final_answer = current_answer
    else:
        # 达到轮数上限：取评分最高（并列取最新）的版本
        best_total = max(t for t, _ in scored) if scored else -1
        final_answer = next(a for t, a in reversed(scored) if t == best_total) if scored else current_answer

    print("\n" + "#" * 72)
    print(f"# 运行结束 · 状态 {status} · 共 {len(_STEPS)} 次模型调用 · 反思 {len(reflections)} 轮")
    print("#" * 72)
    print(">> 最终答复：")
    print(_indent(_wrap(final_answer)))
    print("=" * 72)

    _save_trace(user_query, draft_answer, reflections, final_answer, status)


# ---------------- 入口 ----------------

def main() -> None:
    if not DEEPSEEK_API_KEY:
        print("错误: .env 中未找到 DeepSeek API Key（deepseek-api-key）。请在 reflection_agent.py 同目录的 .env 里补上后重试。")
        sys.exit(1)
    if not TAVILY_API_KEY:
        print("警告: .env 中未找到 Tavily API Key，景点推荐（get_attraction）将不可用，天气查询不受影响。")

    if len(sys.argv) > 1:
        run_agent(" ".join(sys.argv[1:]))
        return
    print("旅行小助手（Reflection）已就绪（输入 q 退出）")
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
