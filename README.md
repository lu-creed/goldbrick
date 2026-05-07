# GoldBrick（PJ001）

一个面向交易场景的轻量网页工具，当前已覆盖：**用户系统（JWT + 免责声明）、数据同步、K 线分析、股票复盘、指标库（内置 + DSL 自定义 + 人话百科）、条件选股、大V情绪仪表盘、大V看板（DAV，ABCD 分类）、自选股池、股票回测（含基准对比 / 敏感性扫描 / 人话总结+四维星级）、策略持久化 + Markdown 研究笔记、策略广场**。

> 本工具所有内容均为客观数据呈现，**不构成任何投资建议**。历史回测结果不代表未来实际收益。

## 1. 当前已实现能力（截至 0.0.6-dev）

- **用户与权限**
  - JWT Token 认证（存 `localStorage.gb_token`），启动时自动校验；失效跳登录页
  - 启动时自动创建默认 admin 账号（如不存在）；管理员可在 `/admin/users` 创建/禁用用户
  - **免责声明强制确认**：首次访问弹窗（不可点遮罩或 ESC 关闭），按用户名独立记录（`localStorage.goldbrick_disclaimer_read_${username}`），多账号切换后各自独立触发；页面底部固定精简免责声明
  - 顶栏用户名下拉含「查看免责声明」（只读）与「退出登录」
  - 普通用户可访问数据看板/选股/回测/指标库，管理员额外可访问同步与数据池
- 数据同步（仅管理员）
  - 支持定时任务配置（cron）、预设模板（每天 18:00 / 每周一 09:00 / 工作日 21:00）
  - 支持手动立即执行；按「股票 + 日期范围」手动拉取；**全市场拉取**（与数据池个股一致）；**全市场指数拉取**（已登记指数，`index_daily`，无复权）
  - 运行中的同步支持**暂停 / 继续 / 取消**（协作式：在上一只标的完成后生效，单标的内请求不中断）；**强制结束**（`cancel?force=true`）用于库中直接收口长期 `running`（如进程重启、线程已退出）
  - **0.0.4 起**：Tushare 配额/频率错误自动兜底 AKShare（`bars_daily.source` 标注来源；0.0.4 中新增了对「每小时最多/没有调用/rate limit」等常见配额文案的关键词识别）；`indicator_pre_daily` 支持 qfq + hfq 双口径预计算，同步流水线拉完日线后自动重建两份缓存
  - token 支持后台持久化，执行前自动校验
  - 元数据（个股+指数）支持本地缓存与严格增量同步
  - 同步运行记录（`queued → running → paused / success / failed / cancelled`）：**0.0.4 起独立页** `/sync/logs`；进度条解析 `progress X/Y`；复权因子失败数橙色警告；**已取消/失败** 支持删除（进行中状态不允许删除）
  - **0.0.4 起**：日志文件写入失败自动降级为内存日志（错误原因进 `message` 字段），同步仍正常执行
- K线分析
  - 主图蜡烛图 + 副图（成交量/MACD/KDJ/**自定义指标子线**）
  - **日 K** 下副图可选「自定义」：拉取已保存 DSL 指标指定子线，序列与当前主图**同一复权类型**对齐；非日 K 周期不叠加自定义（避免与周线等聚合语义不一致）
  - 周期支持：`1d / 1w / 1M / 1Q / 1y`
  - 指标支持：MA（多参数并存）、EXPMA、BOLL、MACD、KDJ
  - 支持窗口平移/缩放按钮与拖拽联动；**关闭**滚轮/触控板缩放，避免误操作
- **股票复盘**
  - 单交易日聚合：涨跌/平盘家数、涨跌停家数、涨跌幅分布柱状图（柱顶标注家数）
  - 三大股指卡片（上证指数、深证成指、创业板指）：按当日涨跌使用红/绿/近似黑灰底色（平盘）；**可点击跳转 K 线**
  - 默认复盘日：不传日期时取本地最新交易日；统计仅含已同步个股（有昨收的参与分桶与涨跌停规则）
  - 涨跌停口径：ST→5%、新股首日不计涨跌停家数（仍参与分布）、其余按板块 10% / 20% / 30%（北交所 30%）；阈值带 0.98 容差
- **大V情绪仪表盘**（`/sentiment`）
  - 近 30～120 个交易日市场情绪量化走势；时间范围切换（近1月/近3月/近半年/近半年+）
  - 情绪分（0～100）：综合涨跌家数与涨停热度的合成指数；≥75 极度乐观、≥60 偏乐观、≥45 中性、≥30 偏悲观、<30 极度悲观
  - 今日快报：情绪分（带文字标签与颜色）、涨停家数及占上涨比、上涨家数及占全市场比、下跌家数及跌停只数
  - 三张趋势图：情绪分折线（visualMap 冷暖渐变 + 参考阈值虚线 70/30）、涨停/跌停双线趋势、涨跌平家数分布堆叠柱状图
  - 大V视角解读：连续3日≥70→警惕连板退潮；连续3日≤30→超跌修复窗口；涨停>100只为热度较高信号
- **大V看板（DAV）**（`/dav`，ABCD 分类框架，v1 阶段)
  - 基于 Mr. Dang ABCD 分类框架，精细化管理少数核心关注股票的**预期股息率**
  - **ABCD 分类**：A 高确定性核心 / B 次优级 / C 候选观察 / D 待确认仅观察
  - **预期股息率公式**：`派息率(%) × 预测EPS(元) ÷ 最新收盘价(元) × 100`（三值齐备时自动计算，否则 N/A）
  - **数据维护**：手动填写「近两年平均派息率」与「预测全年 EPS」；备注字段记录纠正依据（行业基准/大股东诉求/公告纠正）
  - **最新收盘价**：自动从本地 `bars_daily` 读取最新交易日收盘
  - **交互**：按 ABCD 排序展示、同类内按代码排；搜索添加时在 `instrument_meta` 模糊匹配；支持改分类/派息率/EPS/备注、从看板移除
  - > **尚未覆盖的后续阶段**：多步纠正（行业基准/大股东诉求/公告纠正分别结构化）、历史派息率自动拉取、行业默认分类建议、合理股息率下限参考等，见 PRD 3.10 最终形态
- **自选股池（Watchlist）**（`/watchlist`）
  - 轻量收藏（代码 + 可选名称/备注），按加入时间倒序展示；一键跳转 K 线
  - **不触发任何同步**：与 `symbols`（同步池）和 `dav_stock_watch`（大V看板）三者独立
- **指标库**
  - **内置（种子）**：列表与详情（参数、子指标）；与 K 线主副图使用的指标计算同源；包含 MA5/10/20/30/60、EXPMA12/26、BOLL（上/中/下轨）、MACD（DIF/DEA/MACD柱）、KDJ（K/D/J）、**RSI6/12/24**（Wilder 平滑相对强弱指数）、**ATR14 + ATR14_PCT**（真实波动幅度及占收盘百分比）、**WR10/WR6**（威廉指标）
  - **自定义（PRD DSL）**：多参数、多子线；每子线可配「仅辅助 / 参与选股与回测 / 图形展示（折线/柱）」、适用周期、初始值；公式为 JSON 树：数字、本指标参数、固有行情字段、`sqrt`、四则运算、**引用内置子线**或**兄弟子线**、**`rolling` 节点**、`pct_chg`、`highest` / `lowest`、`count_if`、**信号节点** `cross_above` / `cross_below`，且取数方式支持 `current` / `prev_n`（N 来自参数） / `range`（区间均值|最低|最高|标准差）。保存与试算用 `trial_ts_code` 在本地日线试算（不通过则 400）；试算行可带 **diagnostics** 列表（子线、日期、窗口不足、除零、缺内置/兄弟线引用等）便于排查；DSL 保存前自动做环检测防止子线循环引用。兼容旧版「单条 expr」
  - 同步任务在成功拉取日线后仍会写入 `indicator_pre_daily`（qfq/hfq 双口径），供全市场选股/新回测复用性能
- **内置指标人话百科**（0.0.4-dev 易用性重构）
  - 后端收录 20 个系统内置指标的「人话解释」：一句话描述、典型信号（带陷阱提示）、适合 / 不适合场景、常见搭配
  - 接口 `GET /api/indicators/pedia` / `GET /api/indicators/pedia/{code}`，为 Phase 4 指标百科页做数据准备
- **条件选股**（与指标库同一套 DSL / expr）
  - 指定**交易日**、已保存自定义指标、比较子线、比较符与阈值，在全市场已同步日线上扫描
  - **0.0.4 起**：默认改为 **前复权（qfq）** 口径（与 K 线副图一致），结果头部 Tag 显式标注「前复权口径」
  - 支持 `max_scan` 上限（100~8000，默认 6000）；扫描按批次 450 只/批；命中按指标值降序
  - **历史记录自动保存** `screening_history`：指标名称冗余存储（指标删除后仍可查），按执行时间倒序分页；支持一键还原条件、删除历史
- **股票回测**（DSL 条件引擎）
  - 基于已保存自定义 DSL 指标，在指定时间范围内对全市场逐日执行条件选股回测
  - 买入：每日扫描全市场，指标值满足 `buy_op/buy_threshold` 时按值降序依次建仓
  - 卖出：持仓中满足 `sell_op/sell_threshold` 以配置的成交价模式平仓
  - 等额分配资金；`max_positions` 1~10（默认 3）；`max_scan` 100~8000（默认 3000）
  - **0.0.4 起（精度升级）**：默认**前复权**口径；**T+1 次日开盘成交**（可切回收盘价），一字跌停延后到下一日、一字涨停跳过；**佣金**（双边万 2.5，最低 5 元）+ **印花税**（千 1，卖出）+ **滑点**（10bp）可配；**A 股 100 股整手约束**；结果页叠加基准曲线（沪深 300 / 中证 500 / 上证指数）并输出 α
  - 绩效指标（完整 11 项）：总收益率、年化收益、最大回撤、Sharpe、Calmar、Profit Factor、胜率、平均持仓天数、盈亏笔数、平均盈亏%、最大单笔盈亏%
  - 资金曲线（面积图 + 回撤百分比叠加）与交易明细表（含平仓盈亏着色、持有中标签）；每笔交易可展开 Drawer 查看该股回测区间 K 线 + 指标子线验证
  - **回测历史记录**：`/backtest/history`，每次自动保存（绩效冗余存储），按执行时间倒序分页；支持还原参数、删除历史
  - **可信度徽章条**（0.0.4-dev）：结果区顶部一字排开展示本次回测的全部参数与规则（前复权口径 · A 股 T+1 · 涨跌停板块分档 · 成交价模式 · 佣金 · 印花税 · 滑点 · 整手），量化用户无需翻代码就能确认口径
  - **参数敏感性扫描**（0.0.4-dev）：在基线回测完成后，一键对买入 / 卖出阈值做 5~11 点扫描（±10%/20%/30% 偏移），绘「参数-收益 + 最大回撤」双轴曲线，检验策略是否对参数过拟合；异步任务 + 进度轮询，单点失败不整批中止
  - **回测结果人话总结 + 四维星级**（0.0.4-dev 易用性重构）：结果区顶部动态人话总结（如「过去 3 年共交易 47 次，跑赢基准 15 个百分点…」），非量化用户也能看懂；四维星级评估（收益性 / 风险控制 / 稳定性 / 交易频率），每颗星按硬阈值客观计算（非主观打分），hover 显示评分规则
- **策略持久化 + Markdown 研究笔记**（0.0.4-dev）
  - 选股 / 回测页均支持「保存为策略」，策略按用户隔离，同一用户下按 `code` 唯一
  - 「我的策略」抽屉：左侧策略列表 + 右侧详情（逻辑预览 + Markdown 笔记编辑/预览/并排）
  - 策略可一键加载到当前表单，单条件 / 多条件自动识别并填充对应表单
  - 笔记支持 GFM 语法（表格、任务列表、删除线等），记录用户自己的研究思路、调参观察、市场假设，私有不分享
- **策略广场**（0.0.4-dev 易用性重构）
  - 12 个开箱即用的系统预置策略，覆盖逆势 / 趋势 / 突破 / 价值 4 类风格
  - 每张卡片含：人话一句话描述、参考回测数据（总收益 / 最大回撤 / 交易数）、适合 / 不适合场景
  - 一键「用这个策略」跳转回测页，参数自动填充，用户只需确认时间和资金
  - 卡片数据为硬写参考值，真实数据需在回测页自己跑
- **个股列表**（`/stock-list`）
  - 按交易日分页展示全市场个股的 OHLC、涨跌幅、换手、量额等；**不含**数据池中的同步条数、复权是否齐全等运维信息
  - 默认交易日为本地 `bars_daily` 最新日；表头可排序（升/降/取消三态）；翻页与排序事件互不混淆
  - 版式：主内容最大宽度 1200px 居中；窄屏（Ant Design `lg` 以下）隐藏「开/高/低」列；表格横向滚动 + 表头粘顶
  - 筛选：代码/名称/市场/交易所子串 + 涨跌幅、OHLC、成交量、成交额、换手率等闭区间；点「应用筛选」后与分页、排序一并请求后端
- **期间复盘**（`/replay/period`，需求设计，暂未开发）
  - 期间选择（近 5 / 10 / 20 / 60 日，或自定义日期区间）
  - 区间涨跌幅分布（分桶口径与单日复盘一致）、连续涨停排行、涨/跌家数堆叠趋势、个股区间涨跌幅 Top N
  - 数据模型层**无需新增后端表**，全部基于 `bars_daily` + `instrument_meta` 聚合
  - 计划接口：`GET /api/replay/period`

## 2. 技术栈

- 后端：FastAPI + SQLAlchemy + APScheduler
- 数据库：SQLite（本地开发默认）；**0.0.4 预案**：`database.py` 迁移辅助已跨方言化，`scripts/migrate_sqlite_to_postgres.py` + `docs/POSTGRES_MIGRATION.md` 就绪；触发切换条件：`database is locked` 高频 / uvicorn `workers>1` / 并发用户 > 10
- 前端：React + Vite + Ant Design + **TradingView Lightweight Charts v5**（K 线）+ ECharts（其余图表）
- 数据源：Tushare（主用）+ AKShare（配额/频率错误自动兜底）

## 3. 快速启动（本地）

在项目根目录执行：

```bash
./scripts/dev.sh
```

脚本会自动完成：
- 创建后端虚拟环境（首次）
- 安装后端依赖（`requirements.txt` + `requirements-sync.txt`）
- 安装前端依赖（首次）
- 启动后端（`127.0.0.1:8000`）
- 启动前端（默认 `127.0.0.1:5173`）

停止后端：

```bash
./scripts/stop.sh
```

## 4. 环境变量

后端配置文件位于 `backend/.env`（首次启动会从 `.env.example` 自动复制）。

核心变量：

- `TUSHARE_TOKEN`：Tushare token（建议在「同步任务」页面校验后使用）
- `TICKFLOW_API_KEY`：预留
- `TICKFLOW_BASE_URL`：预留
- `RATE_LIMIT_ENABLED`：限流开关（默认关闭，公测前设 `true` 启用）

> 安全建议：不要在文档、代码或聊天里明文暴露 token，建议定期轮换。

## 5. 页面说明

- **顶栏导航**：按**功能模块**展示（不显示版本号）——数据看板、条件选股、股票回测、数据后台（含数据同步、同步日志、数据池、用户管理，仅管理员）。用户名下拉含「查看免责声明」「退出登录」
- `登录` 页（`/login`）：首次访问或 Token 失效自动跳转；默认 admin 账号可用于初始登录
- `K线` 页面（`/`，首页）
  - 选择股票、切换周期、查看主图+副图
  - 支持 URL 参数 `?ts_code=600000.SH`（与个股列表、复盘等跳转一致）
  - 指标切换与参数调节（MA 支持多条）
  - 副图在日 K 下可选「自定义」：选择已保存 DSL 指标及子线，与主图所选**复权**一致
  - hover 显示开高低收、量额、换手率、连续涨跌停/涨跌天数
- `条件选股` 页面（`/screening`）
  - 选择交易日、自定义指标、子线、比较符（大于/小于/等于等）、阈值与最大扫描标的数；结果表可打开对应 K 线
  - 扫描口径（0.0.4 起）：**前复权**日线截面，与 K 线副图一致；结果头部 Tag 显式标注
  - 「历史记录」tab：分页查看过往选股（按执行时间倒序），可一键还原条件或删除
- `股票复盘` 页面（`/replay`）
  - 选择交易日、查看三大股指与市场情绪图表；指数卡片可打开对应 K 线
  - 「期间复盘」为需求设计，暂不开放
- `大V情绪仪表盘` 页面（`/sentiment`，数据看板下）
  - 时间范围切换（近1月/近3月/近半年/近半年+），实时拉取并渲染情绪走势
  - 今日快报卡片 + 三张趋势图（情绪折线 / 涨跌停双线 / 涨跌家数堆叠柱）
  - 大V解读说明区（阈值解读规则）
- `大V看板` 页面（`/dav`，数据看板下）
  - 按 ABCD 分类展示用户自选标的；表格显示 ts_code、名称、分类、派息率、EPS、最新价、预期股息率、备注
  - 搜索添加（模糊匹配 `instrument_meta`）、编辑（分类/派息率/EPS/备注）、移除
- `自选股池` 页面（`/watchlist`，数据看板下）
  - 列表展示收藏标的，可添加/移除/编辑备注；一键跳转 K 线
- `个股列表` 页面（`/stock-list`，数据看板下）
  - 按交易日分页展示全市场 OHLC + 涨跌幅等；多条件筛选 + 表头排序；窄屏自适应
- `指标库` 页面（`/indicators`）
  - **内置指标**：列表与详情；含 MA/EXPMA/BOLL/MACD/KDJ/RSI/ATR/WR 8 类
  - **自定义指标**：PRD 构建器（参数/子线/公式树/`rolling`/信号节点/取数方式）或旧版单条表达式；`GET .../builtin-catalog` 提供分组子线名供「引用内置」；新建可选 DSL 与 legacy；编辑时 DSL 与旧版分别展示；保存与试算依赖本地已同步的样本标的日线；试算表展示 **diagnostics** 列
- `股票回测` 页面（`/backtest`）
  - 选择已保存自定义指标与子线；配置买入/卖出条件（op + 阈值）、初始资金、最大持仓数、成交价模式
  - 绩效卡片三栏：收益概览 / 风险控制 / 交易统计
  - 资金曲线（面积图）+ 回撤百分比叠加 + **基准曲线对比**（沪深 300 / 中证 500 / 上证指数）；交易明细表（平仓盈亏着色 / 持有中标签）；每笔交易支持展开 K 线验证图
  - `/backtest/history`：回测历史列表，支持还原参数与删除
- `数据同步` 页面（`/sync`，仅管理员）
  - 配置 cron 与启停；Token 配置与校验；手动执行同步；按股票+日期范围拉取；全市场拉取（个股/指数）
  - **不再混显运行记录**，已拆分至 `/sync/logs`
- `同步日志` 页面（`/sync/logs`，仅管理员）
  - 独立页展示最近 50 条同步运行；进度条解析 `progress X/Y`；复权因子失败数橙色警告
  - 存在 `queued/running/paused` 时每 2.5 秒静默轮询；支持暂停/继续/取消/强制结束；**已取消/失败** 支持删除
- `数据池` 页面（`/data-center`，仅管理员）
  - 标的列表、筛选、批量/单行同步、日线穿透、单日补数等；`?tab=index` 可直达指数登记页签
- `用户管理` 页面（`/admin/users`，仅管理员）
  - 创建 / 禁用用户账号

## 6. 关键接口（后端）

- 认证 & 健康检查
  - `POST /api/auth/login`：登录，返回 JWT
  - `GET /api/auth/me`：当前用户信息
  - `GET /api/health`：健康检查
- 股票池
  - `GET /api/symbols` / `POST /api/symbols` / `PATCH /api/symbols/{symbol_id}`（仅可改 `name`）
- K 线
  - `GET /api/bars`
  - `GET /api/bars/custom-indicator-series`（`ts_code`、`user_indicator_id`、`sub_key`、可选 `adj`、`start`、`end`）
- 条件选股
  - `POST /api/screening/run`（body：`trade_date`、`user_indicator_id`、可选 `sub_key`、`compare_op` 默认 gt、`threshold`、`max_scan`；自动保存历史）
  - `GET /api/screening/history`（分页）/ `GET /api/screening/history/{id}` / `DELETE /api/screening/history/{id}`
- 股票复盘
  - `GET /api/replay/daily`（可选 `trade_date`、`list_limit`）
  - `GET /api/replay/sentiment-trend`（`days` 5～120，默认 60）
  - （规划）`GET /api/replay/period`
- 股票回测
  - `POST /api/backtest/run`（body：时间范围、DSL 指标与子线、买卖 op/阈值、初始资金、最大持仓、`max_scan`；返回资金曲线 + 交易记录 + 完整绩效 + 基准对比）
  - `GET /api/backtest/records`（分页）/ `GET /api/backtest/records/{id}` / `DELETE /api/backtest/records/{id}`
  - `GET /api/backtest/trade-chart`（单笔交易 K 线验证图）
- 数据看板 · 个股列表
  - `GET /api/dashboard/daily-stocks`（分页 + 排序 + 多条件筛选）
- 指标库（内置）
  - `GET /api/indicators` / `GET /api/indicators/{id}` / `POST /api/indicators/seed`
- 指标库（自定义）
  - `GET /api/indicators/custom/builtin-catalog`（内置指标及子线名）
  - `GET /api/indicators/custom/variable-names`（旧版 expr 白名单）
  - `GET /api/indicators/custom` / `POST /api/indicators/custom`（可选 `trial_ts_code`）
  - `GET /api/indicators/custom/{id}` / `PATCH /api/indicators/custom/{id}` / `DELETE /api/indicators/custom/{id}`
  - `POST /api/indicators/custom/validate-expr`（未落库 expr 试算）
  - `POST /api/indicators/custom/validate-definition`（未落库 DSL 试算）
  - `POST /api/indicators/custom/{id}/validate`（已保存指标试算；DSL 返回多子线 `values`）
  - > 路由注册顺序：必须先注册 `/api/indicators/custom` 再注册 `/api/indicators/{id}`，否则列表请求会被误判为 `id=custom` 返回 422
- **大V看板（DAV）**
  - `GET /api/dav/stocks`（看板列表含最新价与预期股息率）
  - `POST /api/dav/stocks`（添加）
  - `PATCH /api/dav/stocks/{ts_code}`（改分类/派息率/EPS/备注）
  - `DELETE /api/dav/stocks/{ts_code}`（移除）
  - `GET /api/dav/stocks/search`（搜索本地股票供添加）
- **自选股**
  - `GET /api/watchlist/` / `POST /api/watchlist/` / `DELETE /api/watchlist/{ts_code}`
- 同步任务（仅管理员）
  - `GET /api/sync/job` / `PUT /api/sync/job`
  - `POST /api/sync/run` / `POST /api/sync/fetch` / `POST /api/sync/fetch-all` / `POST /api/sync/fetch-all-index`
  - `GET /api/sync/runs`
  - `POST /api/sync/runs/{run_id}/pause` / `.../resume` / `.../cancel`（`cancel` 可加 `?force=true`）
  - `GET /api/sync/runs/{run_id}/log`
  - `DELETE /api/sync/runs/{run_id}`（仅 `cancelled` 或 `failed` 状态）
  - `POST /api/sync/universe/sync` / `GET /api/sync/data-center`
  - `GET /api/sync/symbol/{ts_code}/daily`（分页日线 + 是否含复权因子）
  - `POST /api/sync/single-day`
- Tushare / 管理（仅管理员）
  - `GET /api/admin/tushare/token-status` / `POST /api/admin/tushare/token`
  - `POST /api/admin/indicator-pre/rebuild`（可选 `ts_codes`；重建日线指标预计算）
  - `GET /api/tushare/symbols`

## 7. 数据模型（核心表）

| 表名 | 说明 | 核心字段 |
|---|---|---|
| `users` | 用户账号 | id / username / hashed_password / is_admin / is_active / created_at |
| `symbols` | 本地股票/指数同步池（影响同步任务） | id / ts_code / name |
| `bars_daily` | 日线行情 | symbol_id / trade_date / OHLC / volume / amount / turnover_rate / consecutive_limit_up_days / consecutive_limit_down_days / consecutive_up_days / consecutive_down_days / source |
| `adj_factors_daily` | 每日复权因子 | symbol_id / trade_date / adj_factor |
| `sync_jobs` | 定时同步配置（全局一条） | cron_expr / enabled / last_run_at / last_status / last_error |
| `sync_runs` | 每次同步运行记录 | started_at / finished_at / trigger / status / message / log_path / pause_requested / cancel_requested |
| `app_settings` | 应用全局键值对 | key / value（如 `tushare_token`） |
| `instrument_meta` | 证券元数据（个股+指数） | ts_code / name / asset_type(stock\|index) / list_date / market / exchange |
| `indicators` / `indicator_params` / `indicator_sub_indicators` | 内置指标库 | — |
| `indicator_pre_daily` | 日线指标预计算缓存 | symbol_id / trade_date / adj_mode(none\|qfq\|hfq) / payload(JSON) |
| `user_indicators` | 用户自定义指标 | user_id / code / display_name / description / expr / definition_json |
| `screening_history` | 条件选股历史 | user_id / trade_date / indicator_name / indicator_code / sub_key / compare_op / threshold / scanned / matched / result_json |
| `backtest_records` | 回测历史 | user_id / start_date / end_date / buy/sell op/threshold / initial_capital / max_positions / 绩效冗余 / result_json |
| `dav_stock_watch` | **大V看板标的** | user_id / ts_code / dav_class(A\|B\|C\|D) / manual_payout_ratio / manual_eps / auto_payout_ratio / auto_eps / notes |
| `watchlist` | 自选股池 | user_id / ts_code / name / note / created_at |

> 数据量估算：全 A 股约 5000 只，每日日线 ~5000 行，一年约 125 万行 `bars_daily`；`indicator_pre_daily` 行数与其基本相同（×复权档位）。

## 8. 测试与验收

后端冒烟测试（建议使用项目虚拟环境）：

```bash
cd backend
./.venv/bin/python -m unittest discover -s tests -p "test_*.py"
```

前端构建检查：

```bash
cd frontend
npm run build
```

版本过程记录见：`VERSION_SUPPLEMENTS.md`

**维护约定**：复盘、同步、指标、DAV 等**用户可见行为或接口**有变更时，请**同步更新本 README 对应小节**（能力列表、页面说明、接口、数据模型等），并与 `VERSION_SUPPLEMENTS.md` 和 PRD 条目对齐，避免文档与线上不一致。

## 9. 当前限制（已知）

- **K 线连续涨跌停字段**：仍为早期简化口径（约 10%、阈值 9.8%），与复盘侧涨跌停规则（按板块 10% / 20% / 30%）尚未完全统一
- **新股识别不完整**：仅按「上市首日」识别新股，注册制其他无涨跌幅限制日（如第 2~5 日）未单独处理
- **数据库并发**：SQLite 单机适用；触发 Postgres 切换的条件为 `database is locked` 高频 / uvicorn `workers>1` / 并发用户 > 10（0.0.4 已备好迁移脚本）
- **大V看板只覆盖 v1 阶段**：仅支持手填「已纠正的最终派息率 + EPS」，Mr. Dang 5 步法中第 3（行业基准纠正）/ 4（大股东需求纠正）/ 5（公告纠正）目前都塌缩到一个自由文本 `notes` 字段；历史派息率自动拉取与行业默认分类建议尚未实现，见 PRD 3.10 最终形态
- **已在 0.0.4 修复**：条件选股 vs K 线副图口径差异（统一前复权）、鉴权与限流（slowapi + IP 白名单，登录端点默认 5/min）、回测精度（佣金/印花税/滑点/整手/次日开盘/基准对比/一字板跳过）、数据源依赖（AKShare 兜底）、复权因子（indicator_pre_daily 双口径）

## 10. 数据后台同步策略

- 元数据来源：个股（`stock_basic`）+ 主要指数（`index_basic`），入库到 `instrument_meta`
- 默认行为：优先使用本地缓存，减少日内重复调用外部接口
- 严格增量：再次同步时仅插入新代码，已有代码默认不更新
- 手动拉取范围：与数据后台列表同源，保证两处口径一致；全市场指数拉取仅包含已登记指数行
- 从上市以来拉取：勾选后忽略开始日期，按上市日期（缺失时回退固定早期日期）到今天拉取
- 指数成交额入库时按 Tushare「千元」原始值 ×1000 换算为「元」

## 11. 版本轨迹

- `V1.0.0`～`V2.0.1`：历史能力（含多版回测与复盘能力）；详见 `VERSION_SUPPLEMENTS.md` 旧条目
- `0.0.2-dev`：架构重整，移除 legacy `/api/backtest/*` 与前端回测页；DSL 自定义指标、条件选股、K 线副图自定义序列、个股列表等就绪
- `0.0.3-dev`：新回测引擎（DSL 条件全市场扫描 + 11 项绩效指标）、指标库新增 RSI/ATR/WR、**大V情绪仪表盘**上线、**大V看板（DAV）**上线、**自选股池**上线
- `0.0.4-dev`：
  - **Bug 修复**：数据池慢查询（子查询优化）、同步日志权限错误（内存日志降级）
  - **新功能**：同步日志独立页 `/sync/logs`、运行记录删除、用户下拉菜单含免责声明入口、免责声明状态按用户名隔离
  - **精度升级**：选股/回测统一前复权、回测加佣金/印花税/滑点/整手/T+1 次日开盘/基准对比（α 计算）、一字涨跌停自动跳过、`indicator_pre_daily` 双口径、回测结果区可信度徽章条、参数敏感性扫描（±10%/20%/30% 偏移）
  - **易用性重构**：策略广场（12 个预置策略）、回测结果人话总结 + 四维星级、内置指标人话百科（20 个指标人话解释 + `/api/indicators/pedia`）、策略持久化 + Markdown 研究笔记
  - **稳定性**：AKShare 兜底（配额/频率错误识别增强）、slowapi 限流 + IP 白名单（登录 5/min）
  - **预案**：Postgres 迁移脚本与文档就绪，按并发条件触发切换
  - **需求设计**：期间复盘（`/replay/period`，待开发）、大V看板 v2 最终形态（5 步纠正法结构化）
- `0.0.5-dev`：访客/登录功能分层——未登录可浏览 K 线/复盘/情绪/个股列表/内置指标；LoginGateModal 替代硬跳登录页；后端可选鉴权 `get_current_user_optional`，放开 7 个只读接口；AuthProvider + ProtectedRoute + useAuth 前端基础设施；免责声明按登录态分叉
- `0.0.6-dev`（当前）：
  - **第三数据源**：baostock 作为第三级兜底（Tushare → AKShare → baostock），免费无需 token，新建 `backend/app/services/baostock_ingestion.py`
  - **指标库升级**：`indicator_compute.py` 全面改用 pandas + numpy 向量化（key 名不变）；新增 **VWAP**（20日成交量加权均价）、**MFI14**（资金流量指数）、**StochRSI_K/D**（随机RSI）三组内置指标
  - **K 线图升级**：ECharts → TradingView Lightweight Charts v5；新建 `frontend/src/components/KlineChart.tsx`；三区域多 pane 布局（主图 65% + 量图 18% + 副图 20%）；原生金融图表交互体验
