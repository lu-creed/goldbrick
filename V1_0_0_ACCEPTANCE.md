# V1.0.0 验收清单

## 版本目标
- 初始化并持续拉取个股日 K 数据。
- 支持定时任务配置与立即执行。
- 通过蜡烛图展示多周期 K 线，并展示关键 bar 信息。

## 功能验收（可打勾）
- [ ] 后端 `GET /api/health` 返回 `{"status":"ok"}`。
- [ ] 同步任务支持查询：`GET /api/sync/job`。
- [ ] 同步任务支持配置 cron 与启停：`PUT /api/sync/job`。
- [ ] 同步任务支持立即执行：`POST /api/sync/run`。
- [ ] 同步支持按股票 + 日期范围手动拉取：`POST /api/sync/fetch`。
- [ ] 运行记录可查询：`GET /api/sync/runs`。
- [ ] Tushare token 支持状态查询与设置校验：`GET /api/admin/tushare/token-status`、`POST /api/admin/tushare/token`。
- [ ] K 线查询支持周期：`1d / 1w / 1M / 1Q / 1y`。
- [ ] K 线返回字段包含：开高低收、成交量、成交额、换手率（日均）、连涨停、连跌停、连涨、连跌。

## 当前实现约束（V1.0.0）
- 连涨停/连跌停按简化规则计算（默认按 10% 涨跌停近似，阈值 9.8%）。
- ST、20cm、北交所等差异化涨跌停规则暂未细分（留到后续版本）。
- 当前仍以本地 SQLite + 前后端本地开发模式为主。
