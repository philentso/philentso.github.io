import base64
import gzip
import json
import logging
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from kubernetes.client.exceptions import ApiException

from . import aggregator
from . import config as cfg
from . import k8s_client as k8s

log = logging.getLogger("task-board")
STATIC = Path(__file__).parent / "static"


# ---------- 后台快照 ----------
# 思路：由一个后台线程每隔几秒向 k8s 拉一次数据、算好、转成 JSON 存起来。
# 浏览器请求 /api/overview 时只是把存好的 JSON 原样返回，几乎不耗 CPU，
# 所以不管多少人/多少个请求同时来，都不会再把后端卡住。
class Snapshot:
    state = None      # (json字节, gzip字节, 生成时间)；整体一次赋值，读写不会错乱
    error = ""        # 最近一次刷新失败的原因，成功后清空


SNAP = Snapshot()


def _refresh_loop():
    while True:
        t0 = time.time()
        try:
            data = aggregator.build_overview(k8s.fetch_all())
            data["meta"]["build_ms"] = int((time.time() - t0) * 1000)
            body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
            SNAP.state = (body, gzip.compress(body, 5), time.time())
            SNAP.error = ""
        except Exception as e:                      # 出错也不能让线程退出
            log.exception("refresh failed")
            SNAP.error = f"{type(e).__name__}: {e}"[:200]
        time.sleep(max(1.0, cfg.REFRESH_INTERVAL - (time.time() - t0)))


@asynccontextmanager
async def lifespan(app):
    threading.Thread(target=_refresh_loop, daemon=True, name="snapshot").start()
    yield


app = FastAPI(title=f"{cfg.COMPANY_NAME} · {cfg.PIPELINE_NAME}",
              docs_url="/api/docs", lifespan=lifespan)


# ---------- 可选 Basic 认证（设置了 UI_USER 和 UI_PASS 才启用） ----------
@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if not (cfg.UI_USER and cfg.UI_PASS) or request.url.path == "/healthz":
        return await call_next(request)
    ok = False
    hdr = request.headers.get("authorization", "")
    if hdr.startswith("Basic "):
        try:
            user, _, pw = base64.b64decode(hdr[6:]).decode("utf-8").partition(":")
            ok = (secrets.compare_digest(user.encode(), cfg.UI_USER.encode()) and
                  secrets.compare_digest(pw.encode(), cfg.UI_PASS.encode()))
        except Exception:
            ok = False
    if not ok:
        return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="task-board"'})
    return await call_next(request)


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/healthz")
async def health():
    return {"ok": True}


@app.get("/api/overview")
async def overview(request: Request):
    state = SNAP.state
    if not state:
        raise HTTPException(503, "warming up")      # 刚启动，第一份快照还没算完
    body, gz, built_at = state
    headers = {"Cache-Control": "no-store", "X-Snapshot-Age": str(int(time.time() - built_at))}
    if SNAP.error:
        headers["X-Snapshot-Error"] = quote(SNAP.error)
    if "gzip" in request.headers.get("accept-encoding", ""):
        headers["Content-Encoding"] = "gzip"
        headers["Vary"] = "Accept-Encoding"
        body = gz
    return Response(content=body, media_type="application/json", headers=headers)


# ---------- 日志（ENABLE_LOGS=1 才可用） ----------
_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")


def _api_msg(e):
    try:
        return json.loads(e.body).get("message") or e.reason
    except Exception:
        return e.reason


@app.get("/api/pods/{name}/logs")
def pod_logs(name: str, container: str | None = None, tail: int = 500, previous: bool = False):
    if not cfg.ENABLE_LOGS:
        raise HTTPException(403, "日志功能未开启（设置环境变量 ENABLE_LOGS=1）")
    if len(name) > 253 or not _NAME_RE.match(name):
        raise HTTPException(400, "bad pod name")
    tail = max(1, min(tail, 5000))
    try:
        pod = k8s.CORE.read_namespaced_pod(name, cfg.NAMESPACE, _request_timeout=(3, 10))
        containers = [c.name for c in pod.spec.containers]
        if container not in containers:
            container = containers[0]
        text = k8s.CORE.read_namespaced_pod_log(
            name, cfg.NAMESPACE, container=container, tail_lines=tail, previous=previous,
            limit_bytes=cfg.LOG_MAX_BYTES, _request_timeout=(3, 20))
    except ApiException as e:
        raise HTTPException(e.status if e.status in (400, 404) else 502, _api_msg(e))
    return {"pod": name, "container": container, "containers": containers, "text": text}
