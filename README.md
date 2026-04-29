# GoldBrick（PJ001）

一个面向交易场景的轻量网页工具，当前已覆盖：数据同步、K 线分析、股票复盘、指标库（内置 + DSL 自定义）、条件选股、**大V情绪仪表盘**、**股票回测**与**策略持久化(含 Markdown 研究笔记)**。

## 1. 当前已实现能力（截至 0.0.4-dev）

- 数据同步
  - 支持定时任务配置（cron）
  - 支持手动立即执行
  - 支持按「股票 + 日期范围」手动拉取
  - 支持**全市场拉取**（与数据池个股一致；与手动拉取共用日期范围/从上市以来）
  - 支持**全市场指数拉取**（与数据后台已登记指数一致；`index_daily`，无复权）
  - 运行中的同步支持**暂停 / 继续 / 取消**（协作式：在上一只标的完成后生效，单标的内请求不中断）；**强制结束**（`cancel?force=true`）用于库中直接收口长期 `running`（如进程重启、线程已退出）；结束态为 `cancelled` 时可看日志中已处理进度
  - 个股不再维护「是否参与同步」标记：定时全量（cron / 立即执行）与元数据中**全部个股**对齐；指数须在数据后台登记，日线可单只同步或**全市场指数拉取**一次性拉齐已登记指数
  - token 支持后台持久化，执行前自动校验
  - 元数据（个股+指数）支持本地缓存与严格增量同步
- K线分析
  - 主图蜡烛图 + 副图（成交量/MACD/KDJ/**自定义指标子线**）
  - **日 K** 下副图可选「自定义」：拉取已保存 DSL 指标指定子线，序列与当前主图**同一复权类型**对齐；非日 K 周期不叠加自定义（避免与周线等聚合语义不一致）
  - 周期支持：`1d / 1w / 1M / 1Q / 1y`
  - 指标支持：MA（多参数并存）、EXPMA、BOLL、MACD、KDJ
  - 支持窗口平移/缩放按钮与拖拽联动
- **股票复盘**
  - 单交易日聚合：涨跌/平盘家数、涨跌停家数、涨跌幅分布柱状图（柱顶标注家数）
  - 三大股指卡片（上证指数、深证成指、创业板指）：按当日涨跌使用红/绿/近似黑灰底色（平盘）；**可点击跳转 K 线**（`?ts_code=`，指数未在股票池登记时也会在 K 线下拉中临时展示代码并请求日线）；需在数据池登记并同步对应指数日线（指数日线成交额入库时已按 Tushare「千元」换算为「元」；若曾见成交额偏小约 1000 倍，请对相关指数重新同步以刷新历史行）
  - 默认复盘日：不传日期时取本地最新交易日；统计仅含已同步个股（有昨收的参与分桶与涨跌停规则）
  - 涨跌停口径：ST→5%、新股首日不计涨跌停家数（仍参与分布）、其余按板块 10% / 20% / 30%（北交所 30%）
- **指标库**
  - **内置（种子）**：列表与详情（参数、子指标）；与 K 线主副图使用的指标计算同源；包含 MA5/10/20/30/60、EXPMA12/26、BOLL（上/中/下轨）、MACD（DIF/DEA/MACD柱）、KDJ（K/D/J）、**RSI6/12/24**（Wilder 平滑相对强弱指数）、**ATR14 + ATR14_PCT**（真实波动幅度及占收盘百分比）、**WR10/WR6**（威廉指标）
  - **自定义（PRD DSL）**：多参数、多子线；每子线可配「仅辅助 / 参与选股与回测 / 图形展示（折线/柱）」、适用周期、初始值；公式为 JSON 树：数字、本指标参数、固有行情字段、`sqrt`、四则运算、**引用内置子线**或**兄弟子线**、**`rolling` 节点**（在收盘/最高/最低等字段上做长度为 N 的窗口滚动 **avg/min/max**，N 来自指标参数，用于表达「动态均线」类等能力），且须指定**取数方式**（当前周期 / 前 N 周期 N 来自参数 / 区间均值|最低|最高|标准差，标准差含「基准子公式 + 波动固有字段」）。保存与试算用 `trial_ts_code` 在本地日线试算（不通过则 400）；试算行可带 **diagnostics** 列表（子线、日期、窗口不足、除零、缺内置引用等）便于排查。兼容旧版「单条 expr」：`GET .../variable-names` 仍为 expr 白名单；`POST .../validate-expr` 试算 expr
  - 同步任务在成功拉取日线后仍会写入 `indicator_pre_daily`（前复权等），供后续全市场选股/新回测复用性能
- **条件选股（与指标库同一套 DSL / expr）**
  - 指定**交易日**、已保存自定义指标、用于比较的**子线**（DSL；未指定时默认取第一条「参与选股」子线）、比较符与阈值，在全市场已同步日线上扫描(**前复权 qfq** 口径,与 K 线副图 / 股票回测三处统一可互相对比)
  - 支持 `max_scan` 上限，返回命中列表（收盘价、涨跌幅、指标值等），前端可链到 K 线
  - 结果区顶部**可信度徽章条**:明示口径、扫描数、命中数
- **大V情绪仪表盘**
  - 近 5～120 个交易日市场情绪量化走势；时间范围可切换（近1月/近3月/近半年/近半年+）
  - 情绪分（0～100）：综合涨跌家数与涨停热度的合成指数；≥75 极度乐观、≥60 偏乐观、≥45 中性、≥30 偏悲观、<30 极度悲观
  - 今日快报：情绪分（带文字标签与颜色）、涨停家数及占上涨比、上涨家数及占全市场比、下跌家数及跌停只数
  - 情绪分趋势折线（visualMap 冷暖渐变 + 参考阈值虚线）、涨停/跌停数量双线趋势、涨跌平家数分布堆叠柱状图
  - 大V视角解读：连续3日≥70→警惕连板退潮；连续3日≤30→超跌修复窗口；涨停>100只为热度较高信号
- **股票回测**（DSL 条件引擎）
  - 基于已保存自定义 DSL 指标，在指定时间范围内对全市场逐日执行条件选股回测
  - 买入：每日扫描全市场，指标值满足 buy_op 阈值时按值降序依次建仓，等额分配资金
  - 卖出：持仓中满足 sell_op 阈值时以收盘价平仓
  - 最大同时持仓数（`max_positions`）可配置，初始资金自定义
  - 绩效指标（全部展示在前端三栏卡片中）：总收益率、年化收益、最大回撤、Sharpe 比率、Calmar 比率、Profit Factor、胜率、平均持仓天数、盈亏笔数、平均盈亏%、最大单笔盈亏%
  - 资金曲线（面积图 + 回撤百分比叠加）与交易明细表（含平仓盈亏着色、持有中标签）
  - **可信度徽章条**:结果区顶部一字排开展示本次回测的全部参数与规则(前复权口径 · A 股 T+1 · 涨跌停板块分档 · 成交价模式 · 佣金 · 印花税 · 滑点 · 整手),量化用户无需翻代码就能确认口径
  - **参数敏感性扫描**(0.0.4-dev):在基线回测完成后,一键对买入 / 卖出阈值做 5-11 点扫描(±10%/20%/30% 偏移),绘「参数-收益 + 最大回撤」双轴曲线,检验策略是否对参数过拟合。异步任务 + 进度轮询,单点失败不整批中止。
- **策略持久化 + Markdown 研究笔记**(0.0.4-dev)
  - 选股 / 回测页均支持「保存为策略」,策略按用户隔离,同一用户下按 `code` 唯一
  - 「我的策略」抽屉:左侧策略列表 + 右侧详情(逻辑预览 + Markdown 笔记编辑/预览/并排)
  - 策略可一键加载到当前表单,单条件 / 多条件自动识别并填充对应表单
  - 笔记支持 GFM 语法(表格、任务列表、删除线等),记录用户自己的研究思路、调参观察、市场假设,私有不分享
- **已移除（原 V1.x～V2.0.1 legacy 实现）**
  - 固定买入回测、条件买入+卖出多组合 legacy 引擎、全市场异步 job；与飞书 V0.0.x 新产品路径不重叠，不再提供

## 2. 技术栈

- 后端：FastAPI + SQLAlchemy + APScheduler
- 数据库：SQLite（本地开发默认）
- 前端：React + Vite + Ant Design + ECharts
- 数据源：Tushare（当前版本主用）

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

> 安全建议：不要在文档、代码或聊天里明文暴露 token，建议定期轮换。

## 5. 页面说明

- **顶栏导航**：按**功能模块**展示（不显示版本号）——数据后台、数据看板、条件选股、股票回测；子项与飞书 PRD 二级能力一致。同步日志为 `/sync#sync-runs`；**个股列表**为 `/stock-list`（仅行情维度）；**数据池**仍为 `/data-center`（同步与元数据）。**条件选股**为 `/screening` 实页；**股票回测**「开始回测」为 `/backtest` 实页，「回测记录」为占位页。
- `K线` 页面
  - 选择股票、切换周期、查看主图+副图
  - 支持 URL 参数 `?ts_code=600000.SH`（与个股列表、复盘等跳转一致）
  - 指标切换与参数调节（MA 支持多条）
  - 副图在日 K 下可选「自定义」：选择已保存 DSL 指标及参与图形的子线，与主图所选**复权**一致
  - hover 显示开高低收、量额、换手率、连续指标
- `条件选股` 页面（`/screening`）
  - 选择交易日、自定义指标、子线、比较符（大于/小于/等于等）、阈值与最大扫描标的数；结果表可打开对应 K 线
  - 扫描口径：**未复权**日线截面（与指标库 DSL 求值一致；与 K 线若选前复权时的价格不可直接对比）
- `股票复盘` 页面
  - 选择交易日、查看三大股指与市场情绪图表；指数卡片可打开对应 K 线
  - 「期间复盘」暂不开放（与版本范围一致）
- `大V情绪仪表盘` 页面（`/sentiment`，数据看板下）
  - 时间范围切换（近1月/近3月/近半年/近半年+），实时拉取并渲染情绪走势
  - 今日快报卡片（情绪分、涨停/上涨/下跌家数）；情绪分趋势图（冷暖渐变色 + 参考阈值虚线）；涨停/跌停双线趋势；涨跌家数堆叠柱状图
  - 大V解读说明区（阈值解读规则）
- `个股列表` 页面（`/stock-list`）
  - 按交易日分页展示全市场个股的 OHLC、涨跌幅、换手、量额等；**不含**数据池中的同步条数、复权是否齐全等运维信息
  - 默认交易日为本地 `bars_daily` 最新日；表头可排序（多次点击在升/降序间切换，含 Ant Design 第三次「取消排序」时的衔接）；翻页仅改页码，不与列头排序事件混淆
  - 版式：主内容最大宽度 1200px 居中；筛选区与说明自动换行；表格按列宽横向滚动并支持表头粘顶；窄屏（Ant Design `lg` 以下）隐藏「开/高/低」列以降低拥挤
  - **筛选**：代码/名称/市场/交易所子串，以及涨跌幅、OHLC、成交量、成交额、换手率等数值闭区间；点「应用筛选」后随分页与排序一并请求后端（先筛当日全市场再排序分页）
- `指标库` 页面
  - **内置指标**：列表与详情；含 MA/EXPMA/BOLL/MACD/KDJ 及新增 RSI（6/12/24）、ATR14（含 ATR_PCT）、WR（10/6）
  - **自定义指标**：PRD 构建器（参数/子线/公式树/**rolling**/取数方式）或旧版单条表达式；`GET .../builtin-catalog` 提供分组子线名供「引用内置」；新建可选 DSL 与 legacy；编辑时 DSL 与旧版分别展示；保存与试算依赖本地已同步的样本标的日线；试算表展示 **diagnostics** 列
- `股票回测` 页面（`/backtest`）
  - 选择已保存自定义指标与子线；配置买入/卖出条件（op + 阈值）、初始资金、最大持仓数
  - 绩效卡片三栏：收益概览（总收益/年化/最大回撤）、风险控制（Sharpe/Calmar/Profit Factor）、交易统计（胜率/平均持仓/盈亏笔数/平均盈亏%）
  - 资金曲线（面积图）+ 回撤百分比叠加；交易明细表（平仓盈亏着色 / 持有中标签）
- `数据同步` 页面（顶栏「数据同步」）
  - 配置 cron 与启停；有运行中/排队任务时仅静默刷新下方运行记录，不对「定时配置」卡片反复进入 loading，避免整页抖动
  - 手动执行同步
  - 按股票+日期范围拉取；**全市场拉取**（同一套日期规则，后端按元数据全部个股取码，与数据池列表对齐）；**全市场指数拉取**（已登记指数）
  - 查看最近运行记录与日志；对排队中/运行中/已暂停的任务可暂停、继续、取消
  - 查看数据后台元数据（代码、上市日期、是否已全量、K线条数）
- `数据池` 页面（顶栏「数据池」，运维同步与元数据）
  - 标的列表、筛选、批量/单行同步、日线穿透、单日补数等（见接口说明）；`?tab=index` 可直达指数登记页签

## 6. 关键接口（后端）

- 健康检查：`GET /api/health`
- 股票池：
  - `GET /api/symbols`（返回全部本地 symbols 行；个股元数据同步会按需补齐）
  - `POST /api/symbols`
  - `PATCH /api/symbols/{symbol_id}`（仅可改 `name`）
- K 线查询：
  - `GET /api/bars`
  - `GET /api/bars/custom-indicator-series`（查询参数：`ts_code`、`user_indicator_id`、`sub_key`、可选 `adj`、`start`、`end`；返回与区间对齐的自定义子线日线点列，**建议在日 K 下与主图同 `adj` 使用**）
- 条件选股：
  - `POST /api/screening/run`（body：`trade_date`、`user_indicator_id`、可选 `sub_key`、可选 `compare_op` 默认 gt、`threshold`、`max_scan`；返回 `scanned`、`matched`、命中 `items`）
- 股票复盘：
  - `GET /api/replay/daily`（可选 `trade_date`、`list_limit`；无 `trade_date` 时用本地最新交易日）
  - `GET /api/replay/sentiment-trend`（查询参数 `days` 5～120，默认 60；返回每日情绪分、涨跌停/家数、up_ratio、limit_up_ratio、sentiment_score）
- 股票回测：
  - `POST /api/backtest/run`（body：`start_date`、`end_date`、`user_indicator_id`、`sub_key`、`buy_op`/`buy_threshold`、`sell_op`/`sell_threshold`、`initial_capital`、`max_positions`、`max_scan`；返回 `BacktestRunOut`，含资金曲线、交易记录及完整绩效指标：`total_return_pct`、`annualized_return`、`max_drawdown_pct`、`sharpe_ratio`、`calmar_ratio`、`profit_factor`、`win_rate`、`avg_holding_days`、`total_win`/`total_loss`、`avg_win_pct`/`avg_loss_pct`、`max_win_pct`/`max_loss_pct`）
- 数据看板 · 个股列表：
  - `GET /api/dashboard/daily-stocks`（可选 `trade_date`；分页 `page`、`page_size`；排序 `sort`=ts_code|pct_change|close|volume|amount|turnover_rate，`order`=asc|desc）
  - 可选筛选（均为查询参数，可同时组合）：`code_contains`、`name_contains`、`market_contains`、`exchange_contains`（子串，代码为大小写不敏感）；`pct_min`/`pct_max`、`open_*`、`high_*`、`low_*`、`close_*`、`volume_min`/`volume_max`、`amount_min`/`amount_max`、`turnover_min`/`turnover_max`（闭区间，上下界填反时服务端会交换）。对涨跌幅或换手率设下限时，无法计算或缺失值的行会被排除
- 指标库（内置）：
  - `GET /api/indicators`
  - `GET /api/indicators/{indicator_id}`
  - `POST /api/indicators/seed`
- 指标库（自定义）：
  - `GET /api/indicators/custom/builtin-catalog`（内置指标及子线名，供公式引用）
  - `GET /api/indicators/custom/variable-names`（旧版 expr 白名单）
  - `GET /api/indicators/custom`
  - `POST /api/indicators/custom`（body：`definition` JSON 或 `expr`；可选 `trial_ts_code` 默认 `600000.SH`，保存前试算）
  - `GET /api/indicators/custom/{id}`
  - `PATCH /api/indicators/custom/{id}`（可选 `trial_ts_code`；改 `definition` 或 `expr`）
  - `DELETE /api/indicators/custom/{id}`
  - `POST /api/indicators/custom/validate-expr`（未落库 expr 试算）
  - `POST /api/indicators/custom/validate-definition`（未落库 DSL 试算）
  - `POST /api/indicators/custom/{id}/validate`（已保存指标试算；DSL 返回多子线 `values`）
  - 后端路由：`/api/indicators/custom` 与内置 `/api/indicators/{id}` 同属一层路径，须在应用入口**先注册自定义指标**再注册内置列表，否则列表请求会被误判为 `id=custom` 而返回 422。
- 同步任务：
  - `GET /api/sync/job`
  - `PUT /api/sync/job`
  - `POST /api/sync/run`
  - `POST /api/sync/fetch`
  - `POST /api/sync/fetch-all`（全市场手动拉取：`instrument_meta` 个股；请求体与 `/fetch` 一致，省略 `ts_codes`）
  - `POST /api/sync/fetch-all-index`（全市场指数：`instrument_meta` 中 `asset_type=index`；请求体与 `fetch-all` 相同）
  - `GET /api/sync/runs`
  - `POST /api/sync/runs/{run_id}/pause`（请求暂停，下一只标的前进入 paused）
  - `POST /api/sync/runs/{run_id}/resume`（继续）
  - `POST /api/sync/runs/{run_id}/cancel`（`cancel_requested` 协作取消；可选查询参数 `force=true` 在库内立刻记为 `cancelled`，用于线程已丢/长期卡住仍显示 running）
  - `GET /api/sync/runs/{run_id}/log`
  - `POST /api/sync/universe/sync`
  - `GET /api/sync/data-center`
  - `GET /api/sync/symbol/{ts_code}/daily`（分页日线 + 是否含复权因子）
  - `POST /api/sync/single-day`（单日补数/覆盖，写入运行记录）
- Tushare / 管理：
  - `GET /api/admin/tushare/token-status`
  - `POST /api/admin/tushare/token`
  - `POST /api/admin/indicator-pre/rebuild`（可选 `ts_codes`；重建日线指标预计算，adj=none）
  - `GET /api/tushare/symbols`

## 7. 测试与验收

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

**维护约定**：复盘、同步、指标等**用户可见行为或接口**有变更时，请**同步更新本 README 对应小节**（能力列表、页面说明、接口等），并与 `VERSION_SUPPLEMENTS.md` 条目对齐，避免文档与线上不一致。

## 8. 当前限制（已知）

- **股票复盘** 已按 ST / 新股首日 / 板块 10%·20%·30% 做涨跌停统计与分桶；新股仅按「上市首日」识别，注册制其它无涨跌幅日未单独展开
- **复权口径统一**：条件选股、K 线副图、股票回测三处统一使用**前复权（qfq）**日线，数值可直接对齐（0.0.4-dev 已完成）
- **K 线连续涨跌停**：已按板块分档限价（主板 10% / 创业板·科创板 20% / 北交所 30% / ST 5%）+「最高价触及限价容差 0.98」判定，与复盘、回测口径统一（0.0.4-dev 已完成）
- 鉴权、限流、计费能力待后续补齐

## 9. 数据后台同步策略

- 元数据来源：个股（`stock_basic`）+ 主要指数（`index_basic`），入库到 `instrument_meta`
- 默认行为：优先使用本地缓存，减少日内重复调用外部接口
- 严格增量：再次同步时仅插入新代码，已有代码默认不更新
- 手动拉取范围：与数据后台列表同源，保证两处口径一致；全市场指数拉取仅包含已登记指数行
- 从上市以来拉取：勾选后忽略开始日期，按上市日期（缺失时回退固定早期日期）到今天拉取

## 10. 版本轨迹

- `V1.0.0`～`V2.0.1`：历史能力（含多版回测与复盘能力）；详见 `VERSION_SUPPLEMENTS.md` 旧条目
- `0.0.2-dev`（工程里程碑）：移除全部 legacy `/api/backtest/*` 与前端回测页；DSL 自定义指标、条件选股、K 线副图自定义序列、个股列表等功能就绪；OpenAPI 版本号对齐
- `0.0.3-dev`（当前）：新回测引擎（DSL 条件全市场扫描 + 完整绩效指标）、指标库新增 RSI/ATR/WR、大V情绪仪表盘上线
