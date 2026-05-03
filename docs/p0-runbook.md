# P0 runbook：删除冗余单列索引

> 配套 alembic revision：`a3b17c9d5e24_drop_redundant_symbol_id_indexes`
> 执行位置：**腾讯云 Web 终端**（非本地 SSH），路径 `/opt/goldbrick`
> 预计总耗时：**10-20 分钟**（含备份，不含 VACUUM —— 本次**不跑 VACUUM**）

---

## 它要干什么

删掉三个冗余单列索引：
- `ix_bars_daily_symbol_id`
- `ix_adj_factors_daily_symbol_id`
- `ix_indicator_pre_daily_symbol_id`

这三个索引的"排序前缀"已经被 `UNIQUE(symbol_id, trade_date[, adj_mode])` 复合索引完全覆盖，属于重复维护的额外副本。删掉：
- 写入更快一点（少维护一份索引）
- 读取**不会变慢**（规划器会改用复合唯一索引，覆盖列更多反而更快）
- 约 1.5-2.5 GB 的页面进入 SQLite freelist

---

## 这次**不跑 VACUUM**，为什么

VACUUM 需要 `旧文件 + 新文件` 同时存在，峰值约 47 GB（29 GB 旧 + 18 GB 新），**超过 40 GB 根分区**，一定会爆盘。

所以这次只改"逻辑结构"，不改"物理大小"：
- 释放的索引页留在 freelist 里，下次 INSERT 会自动复用
- .db 文件仍是 29 GB，但内部"可用空位"增加
- 根分区占用率**暂时不变**（99%）

真正回收物理空间要等以后扩容根分区或迁 PG。

---

## 执行步骤

### 0. 准备工作（本地 Mac 上已完成，不在服务器做）

- [x] wip 分支 `wip/p0-drop-redundant-indexes` 已创建
- [x] alembic migration 和 models.py 改动已提交
- [ ] **需要你做**：合并到 main 并 push，触发部署（GitHub Actions → deploy.sh 拉代码到 `/opt/goldbrick`）

### 1. 确认服务器代码是最新

在腾讯云 Web 终端里：

```bash
cd /opt/goldbrick
git log --oneline -3
```

**期望看到**：最上面一条是 `drop redundant symbol_id indexes` 的 commit。
如果不是，说明 GitHub Actions 还没跑完，等 1-2 分钟再看。

### 2. 停服务

```bash
sudo systemctl stop goldbrick-backend
# 如果服务名不同，用 systemctl list-units --type=service | grep gold 查
```

**期望看到**：命令无输出，正常返回。
**验证**：`systemctl status goldbrick-backend` 应该显示 `inactive (dead)`。

### 3. 看一下当前 .db 大小、根分区占用（留个对比参考）

```bash
df -h /
ls -lh backend/data/app.db
sqlite3 backend/data/app.db "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'ix_%_symbol_id';"
```

**期望看到**：
- 根分区 ~99% 使用
- app.db 约 29 GB
- 三个索引名都列出来：`ix_bars_daily_symbol_id` / `ix_adj_factors_daily_symbol_id` / `ix_indicator_pre_daily_symbol_id`

### 4. 跑 alembic 升级

```bash
cd /opt/goldbrick/backend
.venv/bin/alembic current    # 确认当前是 c2fa9427bcf2
.venv/bin/alembic upgrade head
.venv/bin/alembic current    # 确认变成 a3b17c9d5e24
```

**期望看到**：
- 第一条 `current` 输出 `c2fa9427bcf2 (head)` 或 `c2fa9427bcf2`
- `upgrade head` 输出三行 `Running upgrade c2fa9427bcf2 -> a3b17c9d5e24`
- 第二条 `current` 输出 `a3b17c9d5e24 (head)`
- **耗时应该 < 10 秒**（只是 DROP INDEX，不动数据）

### 5. 验证三个索引已删除

```bash
sqlite3 backend/data/app.db "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'ix_%_symbol_id';"
```

**期望看到**：无输出（三个索引都已消失）。

同时确认**复合唯一索引还在**：
```bash
sqlite3 backend/data/app.db "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'uq_%';"
```

**期望看到**：`uq_symbol_trade_date` / `uq_symbol_adj_trade_date` / `uq_indicator_pre_symbol_date_adj` 这三个都在。

### 6. 起服务

```bash
sudo systemctl start goldbrick-backend
sudo systemctl status goldbrick-backend | head -20
```

**期望看到**：状态 `active (running)`，无 error 日志。

### 7. 抽检前端关键功能

打开前端（你平时的地址），逐项试：
- [ ] 登录
- [ ] K 线图：选一只股票（如 600000.SH）画图，指标能正常显示
- [ ] 全市场选股：跑一次日常策略，秒级返回
- [ ] 回测：跑一次 3 年单股回测，正常出图
- [ ] 数据后台：看"上次同步"时间正常

任一项异常 → 立刻走「回滚」章节。

---

## 回滚步骤（出问题时用）

```bash
sudo systemctl stop goldbrick-backend
cd /opt/goldbrick/backend
.venv/bin/alembic downgrade c2fa9427bcf2
sudo systemctl start goldbrick-backend
```

downgrade 会重建三个索引，SQLite 会从 freelist 里取页，**不需要额外磁盘空间**，也不需要 VACUUM。耗时约 **5-15 分钟**（要扫 14M 行建 B-tree 索引）。

---

## 顺手清理：让根分区从 99% 下降（可选，不做也能继续）

如果你想给根分区松口气，在步骤 7 验证通过后可以跑：

```bash
# 1. 清 APT 包缓存（通常回收 0.5-1.5 GB）
sudo apt clean
sudo apt autoclean

# 2. 清旧日志（journalctl 常常有 0.5-2 GB）
sudo journalctl --vacuum-time=7d

# 3. 清 pip 缓存（根用户跑过安装的话）
sudo rm -rf /root/.cache/pip/*
rm -rf ~/.cache/pip/*

# 4. 清项目自己的老同步日志（保留近 30 天）
find /opt/goldbrick/backend/logs/ -type f -mtime +30 -delete

df -h /     # 看回收效果
```

这一步**不是必须的**，但能让你根分区从 99% 掉到 90% 左右，多出几个 GB 缓冲空间，直到下一步 P1a 上线。

---

## 后续

P0 稳定运行 **1-2 天**后，开始 P1a（indicator_pre_daily 列式化）。届时会有另一份 runbook。P1a 的磁盘策略是"DROP 表 + 重建 → 不 VACUUM"，峰值需求是**0 额外空间**（所有写入复用 freelist），所以 P1a 也不会爆盘。
