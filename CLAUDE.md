# GoldBrick(PJ001) — 给 Claude 的项目说明

这份文档给未来每次打开这个项目的 Claude 读,让它快速进入状态并避开本项目的特有坑。

---

## 1. 项目一句话概览

GoldBrick 是一个面向交易场景的轻量网页工具(股票数据同步、K 线分析、指标库+DSL、条件选股、回测、大V情绪、大V看板等)。全栈:Python FastAPI 后端 + React/Vite 前端 + SQLite。

详细功能列表见 `README.md`,版本演进见 `VERSION_SUPPLEMENTS.md`,DSL 规范见 `DSL_REFERENCE.md`。

---

## 2. 技术栈与目录

- 后端:`backend/`,Python 3.10+,FastAPI + uvicorn,入口 `backend/app/main.py`,默认端口 **8000**
  - 依赖:`backend/requirements.txt`
  - 数据库迁移:Alembic(`backend/alembic/`、`alembic.ini`)
  - 数据库:SQLite(文件在 `backend/data/`,已 gitignore)
- 前端:`frontend/`,React + Vite + TypeScript + Tailwind,默认端口 **5173**
  - 依赖:`frontend/package.json`
- 脚本:`scripts/`(含服务器上的 `deploy.sh` 对应源)
- 一键启动:根目录 `bash start.sh`(Mac / Windows Git Bash / Linux 都兼容)

---

## 3. 启动 / 常用命令

**本地开发(Mac / 公司电脑):**
```bash
# 根目录一键启动(推荐,会自动装依赖并开两个终端)
bash start.sh

# 或手动起:
# 后端
cd backend && python3 -m uvicorn app.main:app --reload --port 8000
# 前端
cd frontend && npm run dev
```

**依赖安装注意镜像源**(start.sh 里已经固化这个选择):
- pip 用清华源:`-i https://pypi.tuna.tsinghua.edu.cn/simple`
- npm 用 npmmirror:`--registry https://registry.npmmirror.com`
- 不要擅自去掉这些镜像源参数,国内直连官方源经常超时。

**数据库迁移**(改了 `backend/app/models.py` 之后):
```bash
cd backend
alembic revision --autogenerate -m "描述"
alembic upgrade head
```

---

## 4. 多机协作工作流(非常重要!)

这个项目同时在 **3 个位置** 运行:

| 位置 | 用途 | 对代码的动作 |
|---|---|---|
| 家里 Mac | 晚上写代码 | 编辑 + push |
| 公司电脑 | 白天写代码 | 编辑 + push |
| 腾讯云 Ubuntu 服务器 | 线上跑 | **只消费代码**,不编辑 |

### 部署机制

`.github/workflows/deploy.yml` 定义:**push 到 `main` 分支 → GitHub Actions 通过 SSH 连上服务器,运行 `sudo bash /opt/goldbrick/scripts/deploy.sh`**。

也就是说:**push 到 main = 几分钟内上线**。

### Claude 协助时必须遵守的规则

1. **开始工作前先提醒我 `git pull --rebase origin main`**
   - 避免在落后版本上改,免得后面合并冲突
   - 用 `--rebase` 让提交历史更干净

2. **半成品、未测试的代码绝对不要 push 到 `main`**
   - 一 push 上去就会自动部署到线上,用户正在用
   - 如果我要换机器但工作没做完,引导我选:
     - 把未完成工作 commit 到**新分支**(例如 `wip/xxx`)再 push,`main` 保持干净
     - 或用 `git stash` 临时存起来(但 stash 不跨机器,这种方式只适合短时间离开同一台机器)
   - **绝不要为了方便,把未测试代码直接推上 main**

3. **不要在服务器上直接改代码**
   - 服务器通过 deploy.sh 从 GitHub 拉最新 main → 任何在服务器上的本地修改,下次部署都会被覆盖或产生冲突
   - 服务器只用来:看日志、起/停进程、查数据库状态
   - 如果确实发现服务器上有本地改动(未 commit 文件),先帮我搞清楚是什么,不要直接 `git reset`

4. **结束工作前提醒我 `git push`**
   - 否则换到另一台机器就拿不到今天的进度

5. **`.env` 和数据库文件不跨机器同步**
   - `.gitignore` 已经屏蔽 `.env`、`.env.*`、`*.sqlite`、`*.db`
   - 每台机器(Mac / 公司电脑 / 服务器)各自维护一份 `.env`
   - 如果新增环境变量,要明确提醒我"这三个地方都要加一次"

---

## 5. 本机路径 vs 服务器路径

- 我的本地开发路径:`/Users/luzheng/Desktop/自力更生/PJ001`(**含中文和空格**)
- 服务器路径:`/opt/goldbrick`(纯英文)
- **后果**:任何涉及绝对路径的脚本/配置都不能硬编码我 Mac 上的路径,否则服务器上会跑挂
  - 优先使用相对路径,或用 `os.path.dirname(__file__)` / `$(dirname "$0")` 推导
  - 如果必须写绝对路径,用环境变量(放 `.env`)

---

## 6. 服务器访问方式

我**不用本地 SSH**,服务器只通过**腾讯云控制台 Web 终端**登录(详见 `~/.claude` auto memory `goldbrick_infra`)。

- 涉及服务器调试的步骤,优先给能在 Web 终端完成的命令
- 不要建议我配 SSH key / 跳板机 / VS Code Remote SSH 之类(除非先征得同意并详细指导)
- Web 终端关浏览器会掉线,长时间运行的命令要用 `tmux` 或 `nohup`

---

## 7. 和我协作的偏好

我是**业余开发者**,不是专业程序员。请:

- **一步步讲,不要跳步**。每条命令说清楚"在哪台机器执行"、"预期看到什么输出"
- **解释 WHY 比 WHAT 重要**。我会基于原理判断,不会盲跟步骤;如果某个步骤的原因不清楚,我会追问
- **多方案时帮我对比利弊**,让我做决定,不要擅自选
- **术语能白话就白话**(示例:GitHub Actions 叫"搬运工",MaxStartups 叫"限流保护")
- 我会问看起来很基础的问题(比如"怎么看项目类型"、"服务器上怎么跑起来"),请**不要简化**回答,按实际详细度回
- **方案要支持"从任何地点工作"**,不要依赖单台机器的 IP / 本地环境 / 某个特定文件夹

---

## 8. 常见容易踩的坑(Claude 请主动规避)

- ❌ 在 `main` 分支直接 push 半成品 → ✅ 开 `wip/` 或 `feature/` 分支
- ❌ 硬编码 `/Users/luzheng/Desktop/自力更生/PJ001` 这种路径 → ✅ 用相对路径或环境变量
- ❌ 建议我在服务器上 `vim` 改代码救急 → ✅ 本地改 + push,或明确告诉我这次是临时抢救,事后要同步回 GitHub
- ❌ 把 `.env` 里的密钥或 token 写进代码 / commit → ✅ 一律走 `.env`
- ❌ 装依赖时去掉镜像源参数 → ✅ 保留清华源 / npmmirror
- ❌ 建议跨机器同步 SQLite 文件 → ✅ 每台机器各自的数据库,或用"dump 一份到服务器再导入"这种显式方式
