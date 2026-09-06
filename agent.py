#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent.py — 纯 Python 旅行助手智能体（不引入任何 Agent 框架）

技术栈：openai（DeepSeek 兼容接口）+ requests（wttr.in）+ tavily-python（Tavily 搜索）

工作方式：ReAct 风格。模型每轮输出 Thought / Action，主循环用正则解析 Action 并调用工具，
把结果作为 Observation 回喂给模型，直到模型输出 Finish[...] 或达到最大轮数。

API Key 从同目录 .env 读取：deepseek-api-key 与 tavily-api-key（兼容 tailvy-api-key 拼写）。

用法：
    python3 agent.py "我想去北京玩"     # 命令行直接给需求
    python3 agent.py                     # 交互式输入
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
MAX_ROUNDS = 5                            # 主循环最大轮数
OBS_TRUNCATE = 1500                       # Observation 展示/回喂的最大字符数
ENV_PATH = Path(__file__).resolve().parent / ".env"
TRACE_PATH = Path(__file__).resolve().parent / "agent_trace.json"  # 每轮调用过程的结构化记录（供网页复盘）

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


# ---------------- System Prompt ----------------

SYSTEM_PROMPT = f"""你是一个「旅行小助手」智能体。为了回答用户的旅行需求，你可以在多轮中反复调用工具获取信息，直到信息足够再给出最终建议。

可用工具清单：
1. get_weather[城市名]
   作用：查询该城市当前天气（温度/体感/湿度/风速/天气现象）。
   示例：get_weather[北京]
2. get_attraction[城市名, 天气摘要]
   作用：根据城市和当天天气，检索该城市适合游玩的景点/攻略。
   示例：get_attraction[北京, 晴 26°C 微风]

每一轮你的回复必须严格按下面格式输出（先 Thought 后 Action，且 Action 只能出现一行）：
Thought: 用中文简短说明你在想什么、下一步为什么调这个工具。
Action: get_weather[北京]

如果信息已经足够、要给出最终答复，就不要再写 Action，直接单独输出一行：
Thought: 北京今天有轻度霾、气温 26°C，检索结果里故宫和室内场馆都很合适……
Finish[今天北京是霾天、26°C 较热，建议优先安排室内与博物馆类行程：上午参观故宫博物院（提前预约、以室内展馆为主），中午在王府井用午餐，下午去中国国家博物馆避暑避霾；户外如颐和园等可放到傍晚风起之后。穿衣以轻薄夏装为主，建议佩戴口罩、随身带水。]

规则：
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
        return "系统提示词（工具清单 + 格式约束）"
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


def _save_trace(user_query: str, rounds: list[dict], final_answer: str | None) -> None:
    """把整轮对话过程写成 agent_trace.json，供网页复盘使用。"""
    data = {
        "query": user_query,
        "model": DEEPSEEK_MODEL,
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "rounds": rounds,
        "final_answer": final_answer,
    }
    try:
        TRACE_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n[Trace] 过程记录已写入 {TRACE_PATH.name}（{len(rounds)} 轮）")
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


# ---------------- ReAct 主循环 ----------------

def run_agent(user_query: str) -> None:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_query},
    ]
    last_output = ""
    rounds: list[dict] = []

    for turn in range(1, MAX_ROUNDS + 1):
        print("\n" + "=" * 72)
        print(f"▶ 第 {turn}/{MAX_ROUNDS} 轮 · 调用 DeepSeek（{DEEPSEEK_MODEL}）")
        print("-" * 72)

        # 1) 打印本轮模型调用的完整输入（全部 messages）
        _print_messages(messages, f"第 {turn} 轮 · 模型输入")

        # 2) 调模型（最多尝试 3 次）
        text = ""
        for attempt in range(3):
            try:
                text = chat_once(messages)
                break
            except Exception as exc:
                print(f"  [API 调用失败，重试 {attempt + 1}/3] {type(exc).__name__}: {exc}")
        if not text:
            print("  [模型连续 3 次调用失败，本次对话终止，请检查 .env 中的 DeepSeek Key 与网络]")
            _save_trace(user_query, rounds, None)
            return
        last_output = text

        # 3) 打印本轮模型的完整原始输出
        print("-" * 72)
        print("[模型输出 · 原文]")
        print(text)
        print("-" * 72)

        # 4) 解析 Thought / Action / Finish
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

        # 5) Finish 命中 -> 打印最终答复并退出
        if finish_answer:
            final = _strip_prefix(finish_answer)
            entry["finish"] = final
            entry["history_after"] = copy.deepcopy(
                messages + [{"role": "assistant", "content": text}]
            )
            rounds.append(entry)
            print("\n" + "-" * 72)
            print(f">> 最终答复（第 {turn} 轮 Finish）:")
            print(_indent(_wrap(final)))
            print("=" * 72)
            _save_trace(user_query, rounds, final)
            return

        # 6) 执行工具并打印 Observation
        if action is None:
            observation = "未识别到 Action，也没有 Finish。请严格按格式重新输出：先 Thought，再 Action: 工具名[参数]"
        else:
            observation = run_tool(action[0], action[1])
        observation = _trunc(observation)
        entry["observation"] = observation
        print("\n" + "-" * 72)
        print("Observation:")
        print(_indent(observation))

        # 7) 把本轮输出与观察结果追加进消息历史，进入下一轮
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content": f"Observation: {observation}"})
        entry["history_after"] = copy.deepcopy(messages)
        rounds.append(entry)

    # 达到最大轮数仍未 Finish
    print("\n" + "=" * 72)
    print(f"[已达最大轮数 {MAX_ROUNDS}] 未收到 Finish，对话结束。")
    print("模型最后一轮输出供参考：")
    print(_indent(_wrap(_strip_prefix(last_output))))
    print("=" * 72)
    _save_trace(user_query, rounds, None)


# ---------------- 入口 ----------------

def main() -> None:
    if not DEEPSEEK_API_KEY:
        print("错误: .env 中未找到 DeepSeek API Key（deepseek-api-key）。请在 agent.py 同目录的 .env 里补上后重试。")
        sys.exit(1)
    if not TAVILY_API_KEY:
        print("警告: .env 中未找到 Tavily API Key，景点推荐（get_attraction）将不可用，天气查询不受影响。")

    if len(sys.argv) > 1:
        run_agent(" ".join(sys.argv[1:]))
        return
    print("旅行小助手已就绪（输入 q 退出）")
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
