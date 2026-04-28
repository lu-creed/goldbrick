"""自动更新核心逻辑：检查 GitHub 远程有无新 commit、触发 update.sh 部署。

对外导出：
- get_or_create_config(db)  —— 读/建自动更新配置（单行）
- run_check_once()          —— 同步执行一次完整检查（阻塞到 git fetch 完成）
- trigger_check_now()        —— 后台线程跑一次（API 立即返回不阻塞请求）
- cleanup_old_logs(days)    —— 清理 N 天前的 AutoUpdateLog

工作流：
1. 写一条"last_run_at = now"到 config（便于前端显示"上次检查时间"）
2. `git rev-parse HEAD` 拿本地 hash
3. `git fetch origin main --quiet` 再 `git rev-parse origin/main` 拿远程 hash
4. 比较：
   - 两者任一拿不到 → 记 error 日志
   - 相等 → 记 no-change 日志
   - 不等 → 记 ok 日志 + spawn update.sh（detached，自身进程会被 pm2 restart 杀掉但不影响 update.sh）
"""
from __future__ import annotations

import logging
import subprocess
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import AutoUpdateConfig, AutoUpdateLog

log = logging.getLogger(__name__)

# 项目根目录：backend/app/services/auto_update.py → parents[3] 定位到项目根
# 服务器上通常是 /opt/goldbrick
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def get_or_create_config(db: Session) -> AutoUpdateConfig:
    """读取唯一一条自动更新配置，不存在则用默认值创建。"""
    cfg = db.query(AutoUpdateConfig).first()
    if cfg is None:
        cfg = AutoUpdateConfig(enabled=False, interval_minutes=5)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


def _add_log(
    db: Session,
    action: str,
    status: str,
    details: str = "",
    duration_ms: Optional[int] = None,
) -> None:
    """追加一条日志记录。details 超过 2000 字符会截断，避免 Text 字段存超长内容。"""
    row = AutoUpdateLog(
        action=action,
        status=status,
        details=(details[:2000] if details else None),
        duration_ms=duration_ms,
    )
    db.add(row)
    db.commit()


def _run_git(*args: str, timeout: int = 60) -> tuple[int, str, str]:
    """跑一条 git 命令，返回 (returncode, stdout, stderr)。异常时 returncode=-1。"""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", (
            f"git {args[0]} 超时（>{timeout}s）；"
            "可能是国内访问 GitHub 缓慢，建议配置 HTTP 代理：\n"
            "  git config --global http.proxy http://<your-proxy>:port\n"
            "或适当提高系统 git 超时：\n"
            "  git config --global http.lowSpeedLimit 0\n"
            "  git config --global http.lowSpeedTime 999"
        )
    except Exception as ex:
        return -1, "", str(ex)


def _get_local_hash() -> Optional[str]:
    code, out, _ = _run_git("rev-parse", "HEAD", timeout=10)
    return out if code == 0 and out else None


def _fetch_and_get_remote_hash() -> tuple[Optional[str], Optional[str]]:
    """fetch 远程 + 读 origin/main 的 hash。返回 (hash, err_detail)。"""
    code, _, err = _run_git("fetch", "origin", "main", "--quiet", timeout=120)
    if code != 0:
        detail = err or "unknown"
        if "insufficient permission" in detail or "Permission denied" in detail:
            detail += (
                f"\n💡 修复：后端进程对 .git/objects 目录没有写权限，"
                f"请在服务器上执行：\n"
                f"  sudo chown -R $(whoami) {PROJECT_ROOT}/.git"
            )
        return None, f"git fetch 失败: {detail}"
    code, out, err = _run_git("rev-parse", "origin/main", timeout=10)
    if code != 0 or not out:
        return None, f"git rev-parse origin/main 失败: {err or 'empty output'}"
    return out, None


def _spawn_update_script() -> bool:
    """在独立进程组启动 update.sh，不阻塞当前线程。

    start_new_session=True 让子进程脱离当前 session：当 pm2 重启后端时，
    update.sh 不会被连带杀掉，能完整跑完 git pull / npm build / pm2 restart。
    返回 True 表示成功启动（不代表部署成功，只代表进程已拉起）。
    """
    script = PROJECT_ROOT / "scripts" / "update.sh"
    if not script.exists():
        log.warning("update.sh 不存在：%s", script)
        return False
    try:
        subprocess.Popen(
            ["bash", str(script)],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        log.info("spawned update.sh (detached)")
        return True
    except Exception:
        log.exception("failed to spawn update.sh")
        return False


def run_check_once() -> None:
    """执行一次检查：比对本地与远程 hash，有差异则触发 update.sh。

    同步执行（包含 git fetch，约 1~5 秒），调用方应在后台线程里运行。
    """
    db = SessionLocal()
    try:
        cfg = get_or_create_config(db)
        start = datetime.utcnow()
        cfg.last_run_at = start
        db.commit()

        local_hash = _get_local_hash()
        if not local_hash:
            _add_log(db, "check", "error", "无法读取本地 HEAD（可能不是 git 仓库）")
            return

        remote_hash, err = _fetch_and_get_remote_hash()
        duration = int((datetime.utcnow() - start).total_seconds() * 1000)

        if not remote_hash:
            _add_log(db, "check", "error", err or "获取远程 hash 失败", duration)
            return

        cfg.last_commit_hash = remote_hash
        db.commit()

        if local_hash == remote_hash:
            _add_log(db, "check", "no-change", f"已是最新（{local_hash[:8]}）", duration)
            return

        details = f"发现新提交：本地 {local_hash[:8]} → 远程 {remote_hash[:8]}"
        _add_log(db, "check", "ok", details, duration)

        if _spawn_update_script():
            _add_log(db, "deploy", "ok", "已拉起 update.sh（后台运行，pm2 将重启后端）")
        else:
            _add_log(db, "deploy", "error", "update.sh 启动失败，请检查 scripts/update.sh 是否存在")
    except Exception as ex:
        log.exception("auto-update check failed")
        try:
            _add_log(db, "check", "error", f"未预期异常: {ex!r}"[:500])
        except Exception:
            pass
    finally:
        db.close()


def trigger_check_now() -> None:
    """在 daemon 线程里执行一次检查。API handler 用这个，不阻塞 HTTP 请求。"""
    t = threading.Thread(target=run_check_once, daemon=True)
    t.start()


def cleanup_old_logs(days: int = 30) -> int:
    """删除 N 天前的 AutoUpdateLog，返回实际删除行数。"""
    cutoff = datetime.utcnow() - timedelta(days=days)
    db = SessionLocal()
    try:
        deleted = (
            db.query(AutoUpdateLog)
            .filter(AutoUpdateLog.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        db.commit()
        if deleted:
            log.info("cleaned up %d old auto-update logs (> %d days)", deleted, days)
        return deleted
    finally:
        db.close()
