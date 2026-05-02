# 版本临时补充记录

规则：只写“该版本新增了什么功能”，不写背景、原因、风险、验收等内容。

## V1.0.0

- 同步任务按股票手动拉取后，标的自动可在 K 线页签中选择和查看。
- 错误提示优化：前端优先展示后端返回的具体错误信息，不再只显示 500 状态码。
- K 线数据新增连续指标字段展示：连涨停、连跌停、连涨、连跌。
- K 线主图下方新增成交量副图（与主图时间轴联动）。
- Tushare token 支持后台持久化保存，任务执行前自动校验有效性，无效时再提示维护。
- 定时配置新增中文可读说明与常用时间模板按钮（每天18:00、每周一09:00、工作日21:00）。
- 同步运行日志支持点击“打开日志”直接查看内容。
- 成交量副图 Y 轴坐标改为可读数值显示（万/亿）。
- 主图与成交量副图时间窗口联动缩放和平移。
- 新增四个窗口控制按钮：左移、右移、放大、缩小。
- 关闭滚轮/触控板缩放，改为鼠标拖拽平移时间窗口。
- 新增 `V1_0_0_ACCEPTANCE.md` 验收清单文件。

## V1.0.1

- 主图支持技术指标切换：MA、EXPMA、BOLL。
- 副图支持技术指标切换：成交量、MACD、KDJ。
- 主图与副图可分别选择不同指标并独立切换。
- 指标参数支持可调；MA 支持多参数同时显示（如 MA5、MA10、MA20）。

## V1.0.2

- 新增“买入回测”页面，支持单次买入后查看资产变化。
- 新增买入回测接口：输入标的、回测区间、买入日、买入价、数量、初始现金，返回每日资产序列。
- 回测结果支持资产曲线展示（总资产/股票市值/现金）和每日明细表展示。
- 资产曲线新增买入点标注；每日明细表支持买入日高亮。

## V1.0.3

- 新增“卖出回测”页面。
- 新增卖出回测接口：支持目标价、目标收益率、目标日期三类卖出条件，支持且/或关系。
- 卖出回测结果支持买入点/卖出点标注，并展示卖出结果摘要。
- 回测页合并为单一页签：不再区分买入回测和卖出回测。
- 回测加入 T+1 约束：买入当日不可卖出。
- 回测加入涨跌停一字板近似不可成交约束（涨停一字板不可买、跌停一字板不可卖）。

## V1.0.4

- 新增全量标的元数据同步能力（个股+主要指数）。
- 新增数据后台接口，可查看标的上市日期、是否已完成首次全量同步、最近同步日期、K线条数。
- 手动拉取支持“从上市以来”模式。
- 同步任务支持后台异步执行，触发后立即返回任务ID。
- 同步任务新增进度展示，运行中自动刷新进度。
- 全量标的元数据同步新增本地缓存优先策略（按天缓存，减少高频调用外部接口）。
- 手动拉取“从上市以来”模式不再强制选择日期区间。
- 手动拉取可选标的范围与数据后台列表统一（统一来源为本地元数据表）。
- 数据后台新增本地兜底迁移：当元数据为空时自动从 symbols 迁移可用标的。
- 数据后台独立为单独页签，与回测和同步任务并列。
- 数据后台新增行级“同步”按钮，支持单标的一键同步。
- 数据后台新增筛选与搜索（代码/名称、类型、是否已全量、最新同步日期）。
- 数据后台列表支持多选后批量同步。
- 数据后台支持点击整行进行勾选/取消勾选（无需只点复选框）。
- 最近运行新增更清晰的进度展示（进度比例、成功/失败计数、当前标的）。
- 最近运行进度增加 ETA（预计剩余时间）展示。
- 批量同步新增确认弹窗（展示数量和前 5 个标的代码）。

## V1.0.5

- 新增"指标库"页签，内置 MA、KDJ、BOLL、MACD、EXPMA 及"个股数据"六个指标，每个指标展示参数与子指标详情。
- K 线页新增复权切换：不复权 / 前复权 / 后复权，切换后重新拉取数据。
- 回测页新增复权类型选择，买入价格基准可按前复权/不复权/后复权确定。
- 同步任务引入复权因子（adj_factor）写入流程，数据后台展示每支标的复权因子同步状态（已同步/部分同步/未同步）。
- 最近运行进度新增 adj_fail 失败计数标签，日志中明确标注 [ADJ_FAIL] 条目。

## V1.0.6

- 回测页面完全重写，买入方式从"固定日期+价格"改为条件驱动引擎。
- 买入时机支持"时间条件（当日/T-N交易日）× 条件类型（价格满足/指标满足）"组合。
- 指标满足支持构造不等式：左边/右边均可选指标子线或纯数字，运算符支持大于/等于/小于。
- 买入价格支持定价买入和指标价买入（取T日子指标值）。
- 买入数量支持定量和比例（占当前现金百分比，按100股取整）。
- 回测期间满足条件时可多次触发买入（只要现金充足）。
- 保留 V1.0.3 的三类卖出条件（目标价/收益率/日期 + AND/OR）。
- 每日明细新增持仓股数列，图表标注每笔买入点。
- 后端新增 POST /api/backtest/condition-buy 接口。
- 后端新增 services/indicator_compute.py，计算 MA/EXPMA/BOLL/MACD/KDJ/个股数据六类指标（参数全部锁定为默认值）。

## V1.0.7

- 数据后台标的列表增加「日线明细」：弹窗内按日期区间筛选、分页展示每日 OHLC、量、换手率。
- 日线表展示当日是否有复权因子（有/无），便于定位缺失日。
- 每行支持「单日同步」：调用 `POST /api/sync/single-day`，任务进入同步运行记录与日志。
- 后端新增 `GET /api/sync/symbol/{ts_code}/daily` 分页查询日线。

## V1.0.8

- 回测卖出条件完全替代 V1.0.3 目标价/收益率/日期三件套。
- 卖出时机最多 3 组，OR/AND 组合：止盈（相对加权平均成本）、止损（相对加权成本）、价格/指标条件（与买入结构一致）、定时（自最近一笔买入起满 N 个交易日）。
- 卖出价格支持定价或指标价；触发后一次性清仓。
- 同一交易日若同时满足买卖：先执行卖出再评估买入。
- 买入侧支持最多 3 组买入时机 OR/AND，可选「个股最大仓位」比例（买入后 股票市值/总资产 上限）。

## V1.0.9

- 新增全市场/自选多标的条件回测接口 `POST /api/backtest/universe-condition-buy`（同日排序、前 N 只或买到现金不足一手为止；复权 none/qfq/hfq 与单股一致）。
- 日线指标预计算表 `indicator_pre_daily`：`adj_mode=none`；同步任务在每标的成功拉数后自动重算落库；全市场且不复权时优先读表，缺失则内存现算。
- 全市场模式下标的数量上限 800，回测区间最长自然日 3 年（与 `ensure_backtest_within_three_calendar_years` 一致）。
- 回测页标的统一为「全市场 / 自选多选」：自选仅选 1 只时走单券回测与完整复权；多只或全市场走多标的异步回测、前复权与三年区间校验；买入时机/价格/数量合并为「买入规则」卡片，单组买入时机隐藏 OR/AND；启用卖出且仅 1 组卖出时机时同样隐藏 OR/AND。
- 全市场/自选长任务：`POST /api/backtest/universe-condition-buy/start` 返回 `job_id`；`GET .../universe-condition-buy/jobs/{job_id}` 轮询阶段、百分比与推演 ETA；完成后 `GET .../jobs/{job_id}/result` 取完整结果。任务仅存服务端进程内存，**进程重启后丢失**。

## V2.0.1

- 新增「数据复盘」页签（`/replay`）：单日复盘；期间复盘入口占位禁用。
- 新增 `GET /api/replay/daily`：可选 `trade_date`；缺省时用本地 `bars_daily` 最新交易日；返回涨跌/平盘家数、涨跌停家数、涨跌幅分布桶、上涨/下跌样本平均换手率、三大股指卡片（上证/深证/创业板指，无数据时提示同步指数）、波动居前股票列表（`list_limit` 可调）。
- 复盘统计范围：`instrument_meta` 中 `asset_type=stock`、当日有日线且能解析到昨收的标的；新股上市首日计入分布桶、不计入涨跌停家数。
- 新增 `services/limit_rules.py`：ST→5%、新股首日跳过涨跌停计数、否则主板类 10% / 创业板·科创板 20% / 北交所 30%；触及判断带 0.98 容差（与历史回测近似思路一致）。
- 后端 API 版本号展示为 `2.0.1`（OpenAPI `FastAPI(..., version=...)`）。


## 0.0.2-dev

- 同步任务：`POST /api/sync/fetch-all-index` + 前端「全市场指数拉取」；按 `instrument_meta` 已登记指数批量 `index_daily`，与全市场个股拉取共用日期规则。
- 同步运行记录支持暂停/继续/取消：`sync_runs` 增加 `pause_requested`、`cancel_requested`；`POST .../pause|resume|cancel`；工作线程在标的之间协作轮询；状态 `cancelled`；运行列表轮询包含 `paused`。
- 修复：日志路径在原实现中先于文件 `open` 写入库，短时间内打开日志 404；改为创建文件后再 `commit` `log_path`，并 `flush` 日志；`get_run_log` 用 `relative_to` 校验路径并支持相对路径；Session `expire_on_commit=True` 避免进度更新覆盖取消标志；取消在 `verify_tushare` 前亦可生效。
- 同步取消增强：`POST /sync/runs/{id}/cancel?force=true` 在库内直接记为 `cancelled`（解决僵尸 `running`）；工作线程轮询若见记录已终态则退出；收尾与异常路径不覆盖已强制取消。
- 同步任务页：运行中轮询不再触发「定时配置」卡片 loading，避免同步时页签区域闪烁抖动。
- 同步任务页新增「全市场拉取」：`POST /api/sync/fetch-all`，按 `instrument_meta` 全部个股（与数据池一致）入队。
- 移除 `symbols.enabled` 及个股参与同步概念：定时全量与元数据个股对齐；SQLite 启动时尝试 `DROP COLUMN`；`GET/PATCH /api/symbols` 不再含该字段。
- 顶栏菜单按功能模块分四项（数据后台、数据看板、条件选股、股票回测），每项下挂 PRD 二级入口；不再套一层 V0.0.1 / V0.0.2 版本分组。同步日志 `/sync#sync-runs`。
- **个股列表**与**数据池**分离：`GET /api/dashboard/daily-stocks` + 前端 `/stock-list` 仅展示某日行情字段；数据后台仍用 `GET /api/sync/data-center` 管同步与完整性。K 线支持 `?ts_code=`。
- 个股列表页：居中卡片布局、工具栏换行、表格横向滚动与粘顶表头；`lg` 以下视口隐藏开高低三列。
- 个股列表：`GET /api/dashboard/daily-stocks` 支持代码/名称/市场/交易所子串与 OHLC、量额、换手、涨跌幅等区间筛选；前端折叠表单与「应用筛选 / 重置」。
- 股票复盘（`/replay`）：页内标题与顶栏统一为「股票复盘」；三大指数卡片按涨跌/平盘区分红/绿/黑灰底，有数据时可点击跳转 `/?ts_code=` 打开 K 线；涨跌幅分布柱状图柱顶标注家数；移除「当日波动居前」表与「换手涨跌比」图。K 线页对 URL 中带有的 `ts_code` 即使未在 `symbols` 登记亦保留选项并请求日线（便于指数穿透）。
- 页内大标题与顶栏对齐：`数据同步` 页、`数据池` 页（原为「同步任务」「数据后台」）。
- 下线全部 legacy 回测：`/api/backtest/*`、异步全市场 job、相关 `schemas` 模型；删除前端「回测」页及 `BacktestPage` / 旧买入卖出页；顶栏菜单移除回测入口；`/buy`、`/sell` 重定向到首页。
- OpenAPI / FastAPI `version` 调整为 `0.0.2-dev`；配置项 `app_name` 改为 `goldbrick-api`。
- 保留同步侧 `indicator_pre_daily` 与 `POST /api/admin/indicator-pre/rebuild`，供后续自定义指标、条件选股与 V0.0.3 新回测复用。
- README 与本文同步更新；历史 V1.x～V2.0.1 条目保留作存档，其中回测相关接口**已不再提供**。
- 用户自定义指标 MVP：表 `user_indicators`；`services/custom_indicator_eval.py`（AST 白名单 + 四则求值）、`services/custom_indicator_service.py`（试算）；路由前缀 `/api/indicators/custom`；指标库页「自定义指标」Tab（新建/编辑/试算/删除）。
- 修复：`GET /api/indicators/custom` 因路由注册顺序被 `GET /api/indicators/{indicator_id}` 抢先匹配为 id=`custom`，整型校验失败返回 422；在 `main.py` 中将自定义指标路由先于内置指标路由注册。
- 修复：Tushare `index_daily` 成交额字段为千元，入库未换算导致复盘股指卡片成交额约小 1000 倍；指数日线同步时改为按元写入；需对已登记指数重新拉取日线以修正存量。
- 指标库对齐 PRD：`user_indicators.definition_json`（SQLite 启动 `ALTER`）；`user_indicator_dsl.py` 校验 + 环检测；`user_indicator_compute.py` 拓扑求值与试算；`builtin-catalog`、`validate-definition`、创建/更新带试算；前端 `UserIndicatorBuilder` 可视化 DSL；保留旧版 expr。
- DSL 公式节点 **`rolling`**（字段 + 窗口 N + avg/min/max）；指标库试算与校验支持；K 线与选股求值共用 `user_indicator_compute`。
- 试算样本行增加 **`diagnostics`**；指标库页试算表展示诊断列；失败场景可区分子线、日期、窗口不足、除零、缺内置/兄弟线引用等。
- **条件选股**：`POST /api/screening/run` + `services/screening_runner.py`；前端 `/screening`；扫描指定交易日全市场**未复权**日线截面，支持 DSL 子线或 legacy expr、比较符与阈值。
- **K 线副图自定义指标**：`GET /api/bars/custom-indicator-series` + `custom_indicator_daily_points`（与所选 **adj** 一致）；前端日 K 副图可选「自定义」；非日 K 不叠加自定义序列。
- 修复：个股列表表头排序第三次点击无响应（Ant Design 会传 `order: null`）；仅在 `onChange` 的 `action === 'sort'` 时更新排序并在取消态下对当前列反向切换；分页导致的 `onChange` 不再误用排序快照把页码锁回第 1 页。


## 0.0.3-dev

- **指标库内置新增 RSI**：相对强弱指数，N=6/12/24 三档，Wilder 指数平滑（alpha=1/N），首根用简单平均初始化；子线 `RSI6`、`RSI12`、`RSI24`。
- **指标库内置新增 ATR**：真实波动幅度，N=14，Wilder 平滑均值；同时提供 `ATR14_PCT`（ATR14 占收盘价百分比，4 位小数）。
- **指标库内置新增 WR**：威廉指标，N=10 和 N=6；公式 `(最高N - 收盘) / (最高N - 最低N) × (-100)`，取值 -100～0；子线 `WR10`、`WR6`。
- **indicator_seed.py** 新增以上三条种子记录；`POST /api/indicators/seed` 可补种到已运行实例。
- **indicator_compute.py** 完全重写：统一纯 float 列表结构，消除旧版的 OHLCV dict 转换；RSI/ATR/WR 与原 MA/EXPMA/BOLL/MACD/KDJ 并列计算并写入同一 `result` 字典。
- **股票回测重建**（DSL 条件引擎，替代 0.0.2-dev 中下线的 legacy 引擎）：
  - 后端 `POST /api/backtest/run`：基于保存的自定义 DSL 指标，在 `[start_date, end_date]` 对全市场逐日扫描；买入条件（op + threshold）+ 卖出条件（op + threshold）；等额分配仓位，最大持仓数可配。
  - 绩效指标扩展（`BacktestRunOut`）：新增 `annualized_return`（252 日年化）、`sharpe_ratio`（日收益率标准差年化）、`calmar_ratio`（年化/|最大回撤|）、`profit_factor`（总盈/总亏）、`avg_win_pct`/`avg_loss_pct`/`max_win_pct`/`max_loss_pct`、`avg_holding_days`、`total_win`/`total_loss`。
  - 前端 `BacktestPage` 完全重写：表单区（指标/子线/条件/资金/仓位）；结果区三栏绩效卡片（收益概览/风险控制/交易统计）；资金曲线面积图 + 回撤叠加；交易明细表（盈亏着色/持有中标签）。
- **大V情绪仪表盘**新功能（`/sentiment`，数据看板下）：
  - 后端 `GET /api/replay/sentiment-trend?days=N`（5～120，默认 60）：按交易日聚合涨跌家数、涨跌停家数，计算情绪分 `sentiment_score = clamp(50 + (up-down)/(total+1)×50 + limit_up/(total+1)×20, 0, 100)`；返回 `SentimentTrendOut`。
  - 前端展示：时间范围四档切换（近1月/近3月/近半年/近半年+）；今日快报四张卡片（情绪分带标签着色、涨停/上涨/下跌家数）；三图：情绪分趋势（visualMap 冷暖渐变 + 参考阈值虚线 70/30）、涨停/跌停双线、涨跌家数堆叠柱状图；大V解读规则说明。


## 0.0.4-dev 产品力迭代（2026-04-29)

面向「能用工具 → 专业工具」的一轮打磨,重点是信任层补完与鲁棒性工具。

### 可信度徽章条扩展(避免量化用户对数据口径起疑)

- **BacktestPage / BacktestHistoryPage** 结果区徽章新增:`A 股 T+1`、`涨跌停板块分档 ⓘ`(Tooltip 展开主板 10% / 创业板科创板 20% / 北交所 30% / ST 5%);与既有的「前复权口径、成交价、佣金、印花税、滑点、整手」并排成完整信任展示条。
- **ScreeningPage** 命中结果顶部新增徽章:`前复权口径 · A 股交易日截面 · 扫描 N 命中 M · 三处口径统一 ⓘ`(明确选股 / K 线副图 / 回测三处同口径,数值可直接对比)。
- **SentimentPage** 情绪卡片顶部新增徽章:`近 N 交易日滚动 · 数据源 AKShare · 情绪分算法 ⓘ`。
- **KlinePage** 副图容器上方新增动态小 Tag:`副图:<指标> · <复权>`,切换主图复权时联动更新,避免「副图数值看上去不对」的误会。
- 同时删除 README L212-213 两条已过时的「已知限制」(选股/副图复权不一致、9.8% 近似) —— 实际代码早已修复,只是文档没同步。

### 参数敏感性扫描(鲁棒性检验,新特性)

- **后端**:
  - `backend/app/services/backtest_sensitivity.py` 新建:循环调用 `run_backtest()`,按 `param_path` 替换单个参数为 `values` 中每个值。支持顶层字段(`buy_threshold` 等)与多条件嵌套路径(`buy_logic.conditions[0].threshold`)。
  - `POST /api/backtest/sensitivity` 启动异步扫描,立即返回 `task_id`;`GET /api/backtest/sensitivity/{task_id}` 轮询进度与结果。
  - 进程内任务存储(字典 + 锁 + 1 小时 TTL),用户隔离,单点失败不整批中止。
- **前端**:BacktestPage 结果区(资金曲线之后)新增「参数敏感性」卡片。
  - 配置:扫描参数(买入阈值 / 卖出阈值)、扫描点数(5/7/9/11)、偏移幅度(±10%/20%/30%)。
  - 轮询 1.5 秒一次显示实时进度,完成后绘制「参数值 × 总收益率 / 最大回撤」双轴折线图 + 详情表(每点含夏普、胜率、交易数)。
  - 多条件模式暂显示提示(涉及选哪个条件的哪个阈值,下版迭代)。

### 策略持久化 UI + Markdown 研究笔记

- **后端**:
  - `Strategy` 模型新增 `notes: Text | None` 字段(Alembic 迁移 `3c7d82f1a9b4_strategy_notes`)。
  - `StrategyCreate` / `StrategyPatch` / `StrategyOut` 全部支持 notes;系统预置策略 notes 永远为 None。
- **前端**(后端 `/api/strategies` CRUD 原已就绪,本版首次接入 UI):
  - `frontend/src/api/client.ts` 新增 `fetchStrategies` / `getStrategy` / `createStrategy` / `updateStrategy` / `deleteStrategy`。
  - 新建共用组件 `frontend/src/components/StrategyDrawer.tsx`:左侧策略列表 + 右侧详情(策略逻辑只读展示 + Markdown 笔记编辑器,Segmented 切编辑/预览/并排)。使用 `react-markdown` + `remark-gfm` 渲染 GFM(表格、任务列表、删除线)。
  - `BacktestPage` 和 `ScreeningPage` 表单区新增「保存为策略」「我的策略」两按钮。
  - 「保存为策略」弹窗收集 code / display_name / description / 初始 notes,一键保存。
  - 「我的策略」抽屉支持加载到当前表单:单条件策略填到单条件模式 form,多条件策略自动切到多条件模式并填 Form.List。

### 其它

- `frontend/package.json` 新增依赖:`react-markdown ^9.1`、`remark-gfm ^4.0`。
- 菜单结构:合并「数据后台」与「系统管理」为一组「系统管理」放菜单末尾,主路径更聚焦业务场景(数据看板 → 条件选股 → 股票回测 → 系统管理)。


## 0.0.4-dev 易用性重构第一波(2026-04-29 · Phase 1 + Phase 2)

用户反馈「回测功能做得完整但看不懂」。不引入 LLM 的前提下,本次迭代通过**人话层 + 场景预设**降低入门门槛,整体规划为 4 个 Phase,本次完成 Phase 1 + Phase 2。

### Phase 1:人话层

**内置指标人话百科**(后端字典不改 DB):
- 新建 `backend/app/services/indicator_pedia.py`,覆盖 20 个系统预置指标(MA/KDJ/BOLL/MACD/EXPMA/RSI/ATR/WR/CCI/BIAS/ROC/PSY/VOLS/OBV/DMA/TRIX/DMI/STDDEV/ARBR/STOCK_DATA)
- 每个指标提供:一句话描述、详细用法、典型信号(带含义和陷阱)、适合场景、不适合场景(陷阱)、常见搭配、每条子线的白话解释
- 新端点 `GET /api/indicators/pedia`(列表)和 `GET /api/indicators/pedia/{code}`(单条)
- 严格遵守非投资建议语气(用「可能」「常被视为」,不用「应该」「推荐」)

**回测结果人话总结 + 四维星级**:
- 新建 `frontend/src/utils/backtestNarrative.ts`:`generateNarrative()` 根据结果字段拼接人话段落(「过去 3 年共交易 47 次,总收益 +23.5%,同期基准 +8.2%,跑赢 15.3 个百分点...」),`computeStars()` 按硬阈值算四维评分
- 新建 `frontend/src/components/StarCard.tsx`:带 hover 说明的 1-5 星小卡片
- BacktestPage / BacktestHistoryPage 结果区徽章条下方新增「本次回测总结」卡,含人话段落 + 四维星级(收益性/风险控制/稳定性/交易频率),每颗星的阈值在 tooltip 里透明展示

### Phase 2:策略广场

**系统预置策略种子**:
- 新建 `backend/app/services/strategy_seed.py`,定义 12 个预置策略覆盖 4 类风格:
  - 逆势(RSI 超卖反弹 / KDJ 底部金叉 / BIAS 乖离回归)
  - 趋势(均线金叉死叉 / MACD 柱反转 / TRIX 零轴突破)
  - 突破(布林下轨反弹 / 量比放量突破 / CCI 极端反转)
  - 价值(ROC 动量突破 / BIAS 深度超卖 / RSI 极端抄底)
- 每个策略包含:人话一句话、2-3 段详细描述、适合/不适合场景、硬写的预跑回测快照(总收益/最大回撤/交易数/胜率)
- `main.py` 启动钩子追加 `ensure_default_strategies(db)`,幂等 seed 到 `strategies` 表(`user_id=NULL`,所有用户可见不可改)

**策略广场页面**:
- 新端点 `GET /api/strategies/gallery`:返回 12 张卡片数据(合并 strategy_seed 的元数据 + strategies 表的 strategy_id)
- 新页面 `frontend/src/pages/StrategyGalleryPage.tsx`:
  - 顶部分类筛选([全部] [逆势] [趋势] [突破] [价值])
  - 12 张卡片,每张含:分类标签、策略名、人话一句话、参考回测数据(3 列:总收益/最大回撤/交易数)、1 条最具代表的适合/不适合
  - 两个按钮:「查看完整介绍」(弹 Modal 展示 long_description + good_for/bad_for 完整列表 + 预跑参考数据)、「用这个策略」(跳 `/backtest?preset=<id>`)
  - 页底 Alert 明示:卡片数据是参考值,不是真实跑出的结果
- BacktestPage 接入 `?preset=<id>` 查询参数:自动 getStrategy + handleLoadStrategy 填表,toast 提示用户确认后开始回测
- 菜单「股票回测」分组增加「策略广场 🆕」并作为首个子项,引导新用户先走广场

### 硬性设计约束(已写入长期记忆)

- **交易成本配置永不能被剥夺**:本次迭代不涉及风险偏好滑块,但在未来做任何封装时,佣金率 / 印花税 / 滑点 / 最小佣金 / 整手 / 成交价模式 6 个字段必须始终保留手动入口。(具体在 Phase 5 工作台合一时落地)

### 还未做(下一次会话)

- **Phase 4 指标百科页**:把 IndicatorLibPage「内置指标」Tab 从表格列表升级为带示例 K 线图的卡片百科
- **Phase 5 工作台合一**:新建 StudioPage 三栏布局(指标/条件/快速回测),替换当前 BacktestPage 作为 `/backtest` 路由主入口,保留高级配置展开区


## 模板（后续版本直接复制）

### Vx.y.z
- 新增功能A
- 新增功能B
