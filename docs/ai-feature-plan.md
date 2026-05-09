# GoldBrick AI 功能规划

> 状态：待实现（功能优化版本完成后开始）
> 创建日期：2026-05-09

---

## 背景

在现有量化选股/回测功能基础上，引入 AI 能力，让用户可以用自然语言描述交易想法，AI 自动生成对应的选股或回测策略。

**核心原则：**
- LLM 不绑定 Claude，通过管理员后台配置任意 OpenAI 兼容接口（DeepSeek、通义千问、智谱 GLM 等）
- 分三个阶段渐进实现，每阶段独立可验收
- 防护措施（限流、内容过滤、输入限制）在第一阶段全部落地

---

## 阶段总览

```
Phase 1              Phase 2              Phase 3
─────────────────    ─────────────────    ─────────────────
配置三方 LLM          AI 生成策略           AI 自动创建
+ 基础对话验证        （基于现有指标）        DSL 指标
+ 全部防护措施
```

---

## Phase 1：配置三方大模型 + 对话验证

### 目标

- 管理员可在后台配置任意 OpenAI-compatible LLM（base_url + api_key + model）
- 提供基础对话界面，验证 LLM 接入可用
- **所有防护措施在本阶段全部落地**，后续阶段直接复用

### 防护措施（本阶段全部实现）

| 维度 | 实现方式 |
| --- | --- |
| 输入长度 | 单条消息 ≤ 400 字符（前端字数计数 + 后端硬拦截） |
| 每日限额 | 每用户每天最多 N 次（默认 20 次，admin 可配置，超出返回 429） |
| Token 控制 | max_tokens 可配置（默认 2000），防止单次消耗过高 |
| 内容过滤 | System prompt 明确禁止：投资建议、绝对收益承诺、政治内容 |
| 输出校验 | 后续阶段 JSON 解析失败自动重试 1 次，仍失败返回友好错误 |
| DSL 安全 | 后续阶段生成的 DSL 必须通过后端校验才落库（`parse_and_validate_definition`） |

### 后端改动

| 文件 | 改动说明 |
| --- | --- |
| `backend/requirements.txt` | 添加 `openai>=1.0.0` |
| `backend/app/services/runtime_tokens.py` | 新增 `get_llm_config()` / `set_llm_config()`，复用现有 AppSetting 模式，存储 `llm_base_url`、`llm_api_key`、`llm_model_name`、`llm_max_tokens`、`llm_daily_limit` |
| `backend/app/api/ai_chat.py` | **新建**，包含：`GET/POST /api/admin/ai/config`（管理员配置）；`POST /api/ai/chat`（普通用户对话） |
| `backend/app/main.py` | 注册新 router |

**`POST /api/ai/chat` 接口设计：**

```
Auth：需登录
Body：{ messages: [{role, content}], max_history: 10 }
前端维护对话上下文（最多保留 10 条历史）
返回：{ reply: string, token_usage: { prompt, completion } }
```

### 前端改动

| 文件 | 改动说明 |
| --- | --- |
| `frontend/src/api/client.ts` | 新增 `getLLMConfig()`、`setLLMConfig()`、`aiChat()` |
| `frontend/src/pages/AIConfigPage.tsx` | **新建**：Admin 配置页，包含 base_url / api_key / model_name / max_tokens / 每日限额配置 + 「测试连通性」按钮 |
| `frontend/src/pages/AIChatPage.tsx` | **新建**：对话界面（气泡式消息，显示 token 消耗，输入框带字数计数） |
| `frontend/src/App.tsx` | 注册 `/admin/ai-config`（需管理员）和 `/ai/chat`（需登录）路由 |

### 验收标准

1. Admin 填写 DeepSeek/通义千问的 base_url + api_key + model，保存成功
2. 点击「测试连通性」，收到 LLM 正常回复
3. 普通用户进入 `/ai/chat`，可正常多轮对话
4. 触发每日限额，收到 429 友好提示（"今日 AI 对话次数已用完"）
5. 输入超过 400 字符，前端实时提示并禁用发送

---

## Phase 2：AI 生成策略（基于现有指标）

### 目标

- 用户用自然语言描述交易想法，AI 自动生成引用**现有指标**的选股或回测策略
- 生成结果可一键「加载到表单」或「保存为策略」

### 技术背景

现有系统中，策略条件通过 `user_indicator_id`（数据库主键）引用指标。生成前须先获取用户的可用指标列表（含 ID、sub_keys、说明），传给 LLM 作为"可选指标目录"。

### 后端改动

| 文件 | 改动说明 |
| --- | --- |
| `backend/app/services/ai_strategy_gen.py` | **新建**：`generate_strategy_v1(description, kind, available_indicators, llm_config)` → 返回合法 strategy logic JSON |
| `backend/app/api/ai_chat.py` | 新增 `POST /api/ai/generate-strategy`（需登录）：从 DB 取当前用户 user_indicators → 调生成服务 → 返回策略 JSON |

**Prompt 工程要点：**
- 系统角色：「A股量化策略设计助手，只能使用提供的指标列表」
- 传入：可用指标 Markdown 表格（含 id、名称、sub_key、说明）+ 完整策略 JSON schema + 3 个 few-shot 示例
- 输出：强制 JSON 格式，解析失败自动重试 1 次
- 指标引用：直接使用传入列表中的真实 `user_indicator_id`

**Admin 测试面板**（在 AIConfigPage 中新增 Tab）：
- 输入描述 + 选择 screen / backtest 类型 → 点「测试策略生成」
- 展示：LLM 原始响应 / 解析结果 / token 消耗 / 耗时

### 前端改动

| 文件 | 改动说明 |
| --- | --- |
| `frontend/src/components/AIStrategyModal.tsx` | **新建**：通用 AI 生成模态框，供多个页面复用 |
| `frontend/src/pages/BacktestPage.tsx` | 买卖条件区域添加「AI 生成」按钮 |
| `frontend/src/pages/ScreeningPage.tsx` | 条件区域添加「AI 生成」按钮 |
| `frontend/src/pages/StrategyGalleryPage.tsx` | 顶部添加「AI 描述策略」入口 |

**AIStrategyModal 交互流程：**

```
用户输入描述（400字限制）
  ↓ 点击 [生成策略]
  ↓ loading...
  ↓
💡 AI 说明：MA 金叉结合量能确认，趋势跟踪类策略

买入条件：MA5 上穿 MA20  AND  量能放大比 > 1.5
卖出条件：MA5 下穿 MA20

[加载到表单]    [保存为策略]
```

### 验收标准

1. 输入"MA5 上穿 MA20 且成交量放大买入，下穿卖出"
2. 确认生成合法策略 JSON，条件正确填入回测表单
3. 直接运行回测，无 schema 错误
4. 「保存为策略」后，可在「我的策略」列表中看到

---

## Phase 3：AI 自动创建 DSL 指标

### 目标

- 当用户描述的想法超出现有指标能力时，AI 自动生成新的 DSL 自定义指标定义
- 后端校验通过后自动入库，策略引用新指标后返回前端

### DSL 能力说明

现有 DSL 支持 19 种节点类型，可覆盖绝大多数常见技术指标：

| 能力 | 支持情况 |
| --- | --- |
| MA 金叉/死叉 | ✅ cross_above / cross_below 节点 |
| 成交量放大 | ✅ rolling 节点 + 比值计算 |
| 动量/涨跌幅 | ✅ pct_chg 节点 |
| 连续 N 日条件 | ✅ count_if 节点 |
| 引用内置指标 | ✅ ref_builtin（MACD/KDJ/BOLL/RSI 等） |
| 布林带、ATR | ✅ 通过 ref_builtin 或手写 rolling+std |
| if/else 分支 | ❌ 不支持 |
| log/exp/幂运算 | ❌ 不支持 |

### 生成流程

```
LLM 返回 JSON：
{
  "new_indicators": [
    {
      "display_name": "量能放大比",
      "definition": { DSL formula tree }
    }
  ],
  "strategy": {
    "buy_logic": {
      "conditions 中用占位符引用": "new:0 或 existing:7"
    },
    "sell_logic": { ... },
    "reasoning": "中文说明"
  }
}

后端处理：
  1. 对每个 new_indicator 调用 parse_and_validate_definition() 校验
  2. 全部通过 → 批量创建 user_indicators，获得真实 ID
  3. 将占位符（new:0, existing:7）替换为真实 user_indicator_id
  4. 校验最终 strategy logic
  5. 返回完整策略 + 新建指标列表给前端
```

### 改动说明

| 文件 | 改动说明 |
| --- | --- |
| `backend/app/services/ai_strategy_gen.py` | 升级为 `generate_strategy_v2()`，支持 new_indicators + 占位符机制 |
| `backend/app/api/ai_chat.py` | `/api/ai/generate-strategy` 升级，处理新指标创建 |
| `frontend/src/components/AIStrategyModal.tsx` | 结果区新增「已自动创建 N 个新指标：[名称]」提示 |

**Prompt 升级内容：** 在 Phase 2 基础上增加 DSL schema 文档 + 2 个 DSL few-shot 示例。

### 验收标准

1. 输入"近 3 日成交量均值超过 20 日量均值 1.5 倍时买入"（现有指标中无此指标）
2. 确认 AI 生成了新指标「量能放大比」并自动入库
3. 策略正确引用新指标，运行回测无报错

---

## 关键文件索引

### 新建文件

| 文件路径 | 用途 |
| --- | --- |
| `backend/app/services/ai_strategy_gen.py` | AI 策略/指标生成核心逻辑 |
| `backend/app/api/ai_chat.py` | 所有 AI 相关 API 端点 |
| `frontend/src/pages/AIConfigPage.tsx` | Admin LLM 配置页 + 测试面板 |
| `frontend/src/pages/AIChatPage.tsx` | 用户对话界面 |
| `frontend/src/components/AIStrategyModal.tsx` | 策略生成模态框（复用组件） |

### 修改文件

| 文件路径 | 修改内容 |
| --- | --- |
| `backend/requirements.txt` | 添加 openai 依赖 |
| `backend/app/services/runtime_tokens.py` | 新增 LLM 配置读写函数 |
| `backend/app/main.py` | 注册新 router |
| `frontend/src/api/client.ts` | 新增 AI 相关 API 调用函数 |
| `frontend/src/App.tsx` | 注册新路由 |
| `frontend/src/pages/BacktestPage.tsx` | 添加 AI 生成入口 |
| `frontend/src/pages/ScreeningPage.tsx` | 添加 AI 生成入口 |
| `frontend/src/pages/StrategyGalleryPage.tsx` | 添加 AI 描述策略入口 |

### 复用文件（无需修改）

| 文件路径 | 复用方式 |
| --- | --- |
| `backend/app/services/user_indicator_dsl.py` | DSL 校验（`parse_and_validate_definition`） |
| `backend/app/models.py` AppSetting 表 | LLM 配置持久化 |
| `backend/app/api/custom_indicators.py` | Phase 3 自动创建指标时复用现有入库逻辑 |
