# Project1 - 交易数据回测网站

一个面向交易场景的轻量网页工具，当前已覆盖：数据同步、K线分析、统一回测（买入+卖出条件）。

## 1. 当前已实现能力（截至 V1.0.6）

- 数据同步
  - 支持定时任务配置（cron）
  - 支持手动立即执行
  - 支持按“股票 + 日期范围”手动拉取
  - token 支持后台持久化，执行前自动校验
  - 元数据（个股+指数）支持本地缓存与严格增量同步
- K线分析
  - 主图蜡烛图 + 副图（成交量/MACD/KDJ）
  - 周期支持：`1d / 1w / 1M / 1Q / 1y`
  - 指标支持：MA（多参数并存）、EXPMA、BOLL、MACD、KDJ
  - 支持窗口平移/缩放按钮与拖拽联动
- 统一回测
  - 单页完成买入回测与卖出条件回测
  - 卖出条件支持：目标价、目标收益率、目标日期，支持 AND/OR
  - 输出资产曲线、买卖点标注、每日资产明细、最大回撤
  - 已加入 T+1 规则与涨跌停一字板近似不可成交约束

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

- `TUSHARE_TOKEN`：Tushare token（建议在“同步任务”页面校验后使用）
- `TICKFLOW_API_KEY`：预留
- `TICKFLOW_BASE_URL`：预留

> 安全建议：不要在文档、代码或聊天里明文暴露 token，建议定期轮换。

## 5. 页面说明

- `K线` 页面
  - 选择股票、切换周期、查看主图+副图
  - 指标切换与参数调节（MA 支持多条）
  - hover 显示开高低收、量额、换手率、连续指标
- `回测` 页面
  - 输入买入参数执行基础回测
  - 可选启用卖出条件（目标价/收益率/日期 + AND/OR）
  - 查看资产曲线、买卖点、最大回撤、每日明细
- `同步任务` 页面
  - 配置 cron 与启停
  - 手动执行同步
  - 按股票+日期范围拉取
  - 查看最近运行记录与日志
  - 查看数据后台元数据（代码、上市日期、是否已全量、K线条数）

## 6. 关键接口（后端）

- 健康检查：`GET /api/health`
- 股票池：
  - `GET /api/symbols`
  - `POST /api/symbols`
  - `PATCH /api/symbols/{symbol_id}`
- K 线查询：`GET /api/bars`
- 回测：
  - `POST /api/backtest/buy-once`
  - `POST /api/backtest/buy-sell`
  - `POST /api/backtest/condition-buy`
- 同步任务：
  - `GET /api/sync/job`
  - `PUT /api/sync/job`
  - `POST /api/sync/run`
  - `POST /api/sync/fetch`
  - `GET /api/sync/runs`
  - `GET /api/sync/runs/{run_id}/log`
  - `POST /api/sync/universe/sync`
  - `GET /api/sync/data-center`
- Tushare 管理：
  - `GET /api/admin/tushare/token-status`
  - `POST /api/admin/tushare/token`
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

## 8. 当前限制（已知）

- 连涨停/连跌停为 V1 简化规则（按 10% 涨跌停近似，阈值 9.8%）
- 暂未细分 ST、20cm、北交所等差异化规则
- 回测撮合为简化模型（按日线，不含盘口深度）
- 鉴权、限流、计费能力待后续补齐

## 9. 数据后台同步策略

- 元数据来源：个股（`stock_basic`）+ 主要指数（`index_basic`），入库到 `instrument_meta`
- 默认行为：优先使用本地缓存，减少日内重复调用外部接口
- 严格增量：再次同步时仅插入新代码，已有代码默认不更新
- 手动拉取范围：与数据后台列表同源，保证两处口径一致
- 从上市以来拉取：勾选后忽略开始日期，按上市日期（缺失时回退固定早期日期）到今天拉取

## 10. 版本轨迹

- `V1.0.0`：同步任务 + 基础K线
- `V1.0.1`：技术指标与参数切换
- `V1.0.2`：买入回测与资产变化
- `V1.0.3`：卖出条件回测（AND/OR）
- `V1.0.4`：A股交易约束增强（T+1 + 涨跌停一字板近似不可成交）
- `V1.0.5`：指标库 + 复权支持（前复权/不复权/后复权）
- `V1.0.6`：条件驱动买入回测（时间条件 × 价格/指标条件，支持多次触发）
