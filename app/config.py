import os


def _bool(name, default="0"):
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


NAMESPACE        = os.getenv("NAMESPACE", "task-pipeline")
DISPLAY_TZ       = os.getenv("DISPLAY_TZ", "Asia/Shanghai")

# 后台线程每隔多少秒向 k8s 拉一次数据、生成一份快照（页面请求只读快照，不再直接打 k8s）
REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL", "5"))
# 访问 k8s API 的读超时（秒）
K8S_TIMEOUT      = int(os.getenv("K8S_TIMEOUT", "25"))

RECENT_PER_CJ    = int(os.getenv("RECENT_PER_CJ", "20"))     # 每个 CronJob 统计最近多少个 Job
RECENT_JOBS_LIMIT = int(os.getenv("RECENT_JOBS_LIMIT", "300"))  # “最近执行”页返回多少条

# 查看日志（默认关闭；页面没有登录保护时不要打开）
ENABLE_LOGS      = _bool("ENABLE_LOGS", "0")
LOG_MAX_BYTES    = int(os.getenv("LOG_MAX_BYTES", str(512 * 1024)))

# 可选的 Basic 认证：两个都设置才启用
UI_USER          = os.getenv("UI_USER", "")
UI_PASS          = os.getenv("UI_PASS", "")

PIPELINE_NAME    = os.getenv("PIPELINE_NAME", "mp-task-board")
COMPANY_NAME     = os.getenv("COMPANY_NAME", "Myproject")
