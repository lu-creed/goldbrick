# P1a runbook：indicator_pre_daily 列式化

> 配套 alembic revision：`7f0d3e8a9c41_columnar_indicator_pre_daily`
> 前置条件：**P0 必须已在生产运行稳定至少 1-2 天**
> 执行位置：腾讯云 Web 终端（非本地 SSH），路径 `/opt/goldbrick`
> 预计总耗时：**30-60 分钟**（migration 瞬时，重建缓存 20-40 分钟）

---

## 它要干什么

把 `indicator_pre_daily` 从 "一列 JSON payload" 改成 "每个指标一列 REAL"。

- **空间回收**：预计 **5-7 GB**（压力大头，17M 行 × 每行从 ~500 字节压到 ~200 字节）
- **速度变化**：读写都**变快**（省 json.dumps / json.loads）
- **业务影响**：零。`load_indicator_map_from_pre` 当前未被任何代码调用，读路径无面；写路径（sync_runner / admin API）改过后行数和语义不变。

**本次仍然不跑 VACUUM**，原因同 P0：VACUUM 需要 ~47 GB 临时空间，超过根分区。
DROP TABLE 释放的 10+ GB 页进入 freelist，后续 CREATE + rebuild 写入会复用这些页，文件物理大小**不变**。

---

## 磁盘峰值核算（确认为什么不会爆盘）

| 阶段 | 动作 | 磁盘 delta | 累计文件大小 |
|---|---|---|---|
| 起点 | — | 0 | 29 GB |
| 1 | `DROP TABLE indicator_pre_daily` | freelist +10 GB | 29 GB |
| 2 | `CREATE TABLE indicator_pre_daily (新 schema)` | freelist +0（新表空） | 29 GB |
| 3 | 跑 rebuild，写入 ~17M 新行 | 消耗 freelist ~5 GB，**freelist 仍余 5 GB** | 29 GB |
| 终点 | — | — | **29 GB（不变）** |

关键：过程中文件大小**始终是 29 GB**，不需要任何额外根分区空间。

---

## 执行步骤

### 0. 本地 Mac 上准备（已完成，不在服务器做）

- [x] 分支 `wip/p1a-columnar-indicator-pre` 已创建并 push
- [ ] **需要你做**：把 P0 和 P1a 按顺序合 main 并 push（触发 Actions → deploy.sh）

### 1. 确认服务器代码和 P0 状态

```bash
cd /opt/goldbrick
git log --oneline -5
.venv/bin/alembic -c backend/alembic.ini current
# 期望看到：a3b17c9d5e24 (head) ← P0 已经在线上
```

**期望看到**：
- 最近 commit 包含 `perf(db): indicator_pre_daily 改为列式` 字样
- alembic current 是 `a3b17c9d5e24`（P0 的 head），说明 P0 已上线

如果 alembic current 还在 `c2fa9427bcf2`，说明 P0 没跑过，先按 P0 runbook 补跑。

### 2. 停服务

```bash
sudo systemctl stop goldbrick-backend
systemctl status goldbrick-backend | head -5   # 确认 inactive
```

### 3. 看一下当前 indicator_pre_daily 的状态（留对比参考）

```bash
sqlite3 backend/data/app.db "SELECT COUNT(*) AS rows, COUNT(DISTINCT adj_mode) AS modes FROM indicator_pre_daily;"
sqlite3 backend/data/app.db "SELECT adj_mode, COUNT(*) FROM indicator_pre_daily GROUP BY adj_mode;"
df -h /
ls -lh backend/data/app.db
```

**期望看到**：
- 总行数 ~17M
- 按 adj_mode 分组：qfq 和 hfq 各约 8.7M（或只有 qfq ~17M，看历史填充路径）
- .db 文件约 29 GB
- 根分区 ~99%

### 4. 跑 alembic 升级

```bash
cd /opt/goldbrick/backend
.venv/bin/alembic current     # 应是 a3b17c9d5e24
.venv/bin/alembic upgrade head
.venv/bin/alembic current     # 应是 7f0d3e8a9c41
```

**期望看到**：
- upgrade 瞬时完成（< 5 秒，就是 DROP + CREATE 两条 DDL）
- current 跳到 `7f0d3e8a9c41 (head)`

此时 `indicator_pre_daily` 是**空表**，结构是新的 55 列。

### 5. 验证新 schema

```bash
sqlite3 backend/data/app.db ".schema indicator_pre_daily"
sqlite3 backend/data/app.db "SELECT COUNT(*) FROM indicator_pre_daily;"
```

**期望看到**：
- schema 输出应该有 55 列（id + symbol_id + trade_date + adj_mode + 51 个 FLOAT 指标列）
- COUNT=0（新表没数据）

### 6. 跑缓存重建脚本

```bash
cd /opt/goldbrick
# 用 tmux，避免 Web 终端断线后任务中止
tmux new-session -s rebuild
# tmux 里面：
cd /opt/goldbrick/backend
.venv/bin/python ../scripts/rebuild_indicator_pre_cache.py --mode both 2>&1 | tee /tmp/rebuild_$(date +%s).log
# 挂起 tmux：Ctrl+B 然后 D
# 回来看：tmux attach -t rebuild
```

**期望看到**：
- 每 50 只股票打印一行进度：`[500/5500] ok=500 fail=0 rows_total=1200000 rate=2.3股/s eta=36m12s`
- 全程约 **20-40 分钟**
- 结尾打印 `完成：耗时 32m15s / 成功 5500 / 失败 0 / 合计写入 17XXXXXX 行`
- fail 应为 0，**若 fail > 几十只**就要看日志找原因（一般是这几只 bars_daily 数据缺失）

**期间你可以做别的**（比如先把步骤 7 的前端抽检单打开）—— 脚本在 tmux 里自己跑。

### 7. 起服务

```bash
sudo systemctl start goldbrick-backend
sudo systemctl status goldbrick-backend | head -20
journalctl -u goldbrick-backend -n 30 --no-pager   # 看最近的启动日志
```

### 8. 抽检前端

- [ ] 登录
- [ ] K 线图：选 **600000.SH**（老股票，历史完整），画 1 年 / 5 年 / 全历史，MA/KDJ/BOLL 都正常显示
- [ ] K 线图：选 **新股**（比如 2024 年上市的），确认 MA60 等"早期数据不足"的指标在前 60 天是空的
- [ ] 全市场选股：跑一个日常条件，**秒级返回**（未被 load_indicator_map_from_pre 拖累 = 本来就不走缓存）
- [ ] 回测：单股 3 年回测正常
- [ ] 数据后台：触发一次小范围**手动同步**（1-2 只股票），日志里看到 `indicator_pre_daily(qfq) rows=N` `indicator_pre_daily(hfq) rows=N` 两行 = 写入路径工作正常

任一项异常 → 走「回滚」。

### 9.（可选）验证磁盘回收

```bash
sqlite3 backend/data/app.db "SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size();"
sqlite3 backend/data/app.db "SELECT freelist_count * page_size FROM pragma_freelist_count(), pragma_page_size();"
```

**期望看到**：
- 文件总字节 ≈ 29 GB（不变）
- freelist 约 5-7 GB（新出现的空闲页）

如果 freelist 跟预计差距大（<3 GB 或 >10 GB），告诉我一声，大概率是 compute_indicators 有某个指标维度与预计不同。

---

## 回滚步骤

**优先用 git revert，不推荐 alembic downgrade**，原因：alembic downgrade 会再次 DROP 新表把你刚 rebuild 的数据抹掉，然后重建旧 JSON schema，你还得再跑一次 rebuild（用回滚后版本的代码）。

**推荐做法**：

```bash
# 1. 在本地 Mac 上 git revert P1a 的两个 commit 并 push（触发 Actions 部署旧代码）
cd /Users/luzheng/Desktop/PJ001
git checkout main
git log --oneline -5   # 记下 P1a 的 commit hash
git revert <p1a-commit-hash>
git push origin main

# 2. 服务器上等 deploy 完成，然后：
cd /opt/goldbrick
sudo systemctl stop goldbrick-backend
cd backend
.venv/bin/alembic downgrade a3b17c9d5e24   # 回到 P0 的 head（JSON schema）
# 然后用旧版本代码重跑 rebuild（sync_runner 或 admin API）
sudo systemctl start goldbrick-backend
```

重跑 rebuild 又是 20-40 分钟。总回滚时长约 **50-80 分钟**。

---

## 后续

P1a 跑稳定后：
- 磁盘虽然 freelist 有 5-7 GB 空闲，**文件物理大小没变**（仍 29 GB），根分区还是 99%
- 想真正回收磁盘（VACUUM 或 VACUUM INTO）需要先给根分区腾出 ~20 GB 空间，见 P0 runbook 第 9 步的"顺手清理"或以后扩容根分区
- 下一阶段 P1b（bars_daily 的 Numeric → REAL）预计还能再省 0.5-0.8 GB，不紧迫，视运行状态决定是否做
