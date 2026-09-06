# 从零手写智能体：ReAct / Plan-Solve / Reflection 三个范式

一个**不依赖任何 Agent 框架**（不用 LangChain / LlamaIndex / AutoGen）的教学示例项目：
只用 `openai` SDK（DeepSeek 兼容接口）+ `requests` + `tavily-python`，纯手写主循环，
把同一个「旅行小助手」分别用三种主流 Agent 范式实现一遍，并把**每一次模型调用的完整输入 / 输出**
落盘成结构化 Trace，再渲染成可交互的复盘网页。

同一份需求（例如「我想去北京玩」）在三种范式下的差异，可以直接对照着看：
ReAct 边想边做、Plan-Solve 先规划后执行、Reflection 生成后自我评审再修订。

## 目录结构

| 文件 | 说明 |
| --- | --- |
| `agent.py` | **ReAct 范式**：`Thought → Action → Observation` 单循环，直到 `Finish[...]` |
| `plan_solve_agent.py` | **Plan-Solve 范式**：Phase 1 先出计划，Phase 2 再按计划逐步执行 |
| `reflection_agent.py` | **Reflection 反思范式**：Draft（生成）→ Evaluate（评审打分）→ Revise（修订）闭环 |
| `make_trace_html.py` | 把 `agent_trace.json` 渲染成单文件网页 `agent_trace.html` |
| `make_plan_solve_html.py` | 把 `plan_solve_trace.json` 渲染成单文件网页 `plan_solve_trace.html` |
| `make_reflection_html.py` | 把 `reflection_trace.json` 渲染成单文件网页 `reflection_trace.html` |
| `*_trace.json` / `*.html` | 一次真实运行的示例产物，可直接打开网页复盘 |
| `*_run_log.txt` | 终端输出的完整日志（三个范式各一份） |

## 快速开始

```bash
# 1. 安装依赖
pip install openai requests tavily-python

# 2. 在项目根目录创建 .env（两个 Key 都不要提交到 Git）
cat > .env <<'EOF'
deepseek-api-key=你的 DeepSeek API Key
tavily-api-key=你的 Tavily API Key
EOF

# 3. 运行 ReAct 版本
python3 agent.py "我想去北京玩"
python3 make_trace_html.py        # 生成复盘网页，双击 agent_trace.html 即可查看

# 4. 运行 Plan-Solve 版本
python3 plan_solve_agent.py "我想去北京玩"
python3 make_plan_solve_html.py   # 生成复盘网页，双击 plan_solve_trace.html 即可查看

# 5. 运行 Reflection 版本
python3 reflection_agent.py "我想去北京玩"
python3 make_reflection_html.py   # 生成复盘网页，双击 reflection_trace.html 即可查看
```

不带参数运行则进入交互式模式（输入 `q` 退出）：

```bash
python3 agent.py
```

## 两个工具

| 工具 | 数据来源 | 说明 |
| --- | --- | --- |
| `get_weather[城市名]` | `wttr.in` | 查询城市当前天气：温度 / 体感 / 湿度 / 风速 / 天气现象（已做中文化映射） |
| `get_attraction[城市名, 天气摘要]` | Tavily Search | 按「城市 + 当天天气」检索适合的景点与攻略 |

## 三种范式的对照

**ReAct（`agent.py`）**

```text
System Prompt（角色 + 工具清单 + 格式约束）+ 用户问题
        │
        ▼
   Thought（模型的思考）
   Action: 工具名[参数]   ──►  执行工具  ──►  Observation（工具结果回喂）
        │                                          │
        └──────────────────────────────────────────┘
                     循环，直到 Finish[最终答复]
```

**Plan-Solve（`plan_solve_agent.py`）**

```text
Phase 1 · Plan   System Prompt + 用户问题 ──► 「计划：1. … 2. …」（不调用任何工具）
                                                      │ 注入
                                                      ▼
Phase 2 · Solve  Thought / Action / Observation 逐轮执行 ──► Finish[最终答复]
```

**Reflection（`reflection_agent.py`）**

```text
Draft（Actor）    Thought / Action / Observation 收集事实 ──► 第一版答复 + Evidence（工具事实依据）
        │
        ▼
Evaluate（Critic） 换「评审专家」角色，按 5 个维度打分（满分 25）──► PASS / NEED_REVISION
        │                                                                  │
        │◄──────────── PASS 即收敛，输出最终答复                             │ NEED_REVISION
        │                                                                  ▼
        │                                        Revise（Revisor）注入「问题 + 建议 + Evidence + 上版答复」
        └──────────────────────────────────────────────────────────────────┘
                     达到 MAX_REFLECT_ROUNDS 仍未 PASS：取评分最高（并列取最新）的版本兜底
```

共同的关键实现细节：

- **输出格式靠正则解析**：`Action\s*[:：]\s*(\w+)\s*\[([^\]]*)\]`、`Finish\s*[:：]?\s*\[\s*(.+?)\s*\]`；
  并对模型偶尔误写的 `Action: Finish[...]` 做归一化兜底。
- **工具失败不外抛**：所有异常统一转成 `工具错误: …` 字符串回喂，由模型自己决定重试策略。
- **Trace 全量落盘**：每次调用 deepcopy 一份 `messages`，记录 `input_messages / model_output /
  thought / action / observation / history_after`，供网页逐步展开复盘。
- **提示词即协议**：`Finish` 不是工具，只是终止信号，写在 System Prompt 里约束模型。
- **评审必须锚定事实**：Reflection 的 Critic 只依据 Draft 阶段沉淀的 Evidence 判分，
  避免评审器凭空臆断；收敛不了时按历史最高分兜底，保证一定有可用输出。

## 安全提示

- `.env` 已在 `.gitignore` 中忽略，请勿提交任何真实 API Key。
- 若要公开分享运行结果，注意 `*_trace.json` 与 `*_run_log.txt` 中包含完整的模型输入与输出。

## 环境要求

Python 3.10+（代码使用了 `X | None` 类型语法与 `from __future__ import annotations`）。
