"""访问 k8s API 的薄封装。

提速的关键：list 时加 _preload_content=False，拿到未解析的原始 JSON，
自己用 json.loads 解析成 dict。官方 SDK 默认会把每个 Pod/Job 转成模型对象，
几百个对象在小 CPU 配额下要好几秒；直接 json 解析只要几十毫秒。
"""
import json
from concurrent.futures import ThreadPoolExecutor

from kubernetes import client, config

from . import config as cfg


def _build_clients():
    try:
        config.load_incluster_config()       # 在集群里跑：用 ServiceAccount
    except config.ConfigException:
        config.load_kube_config()            # 本地调试：用 ~/.kube/config
    return client.BatchV1Api(), client.CoreV1Api(), client.AppsV1Api()


BATCH, CORE, APPS = _build_clients()

_TIMEOUT = (3, cfg.K8S_TIMEOUT)             # (连接超时, 读取超时)


def _list(fn):
    """调用某个 list_namespaced_xxx，返回 items(list[dict])。"""
    resp = fn(cfg.NAMESPACE,
              resource_version="0",          # 让 apiserver 用自己的缓存回答，更快、对 etcd 压力小
              _preload_content=False,        # 不转模型对象，拿原始响应
              _request_timeout=_TIMEOUT)
    try:
        return json.loads(resp.data).get("items", [])
    finally:
        resp.release_conn()


_LOADERS = {
    "cronjobs":     BATCH.list_namespaced_cron_job,
    "jobs":         BATCH.list_namespaced_job,
    "pods":         CORE.list_namespaced_pod,
    "deployments":  APPS.list_namespaced_deployment,
    "statefulsets": APPS.list_namespaced_stateful_set,
    "daemonsets":   APPS.list_namespaced_daemon_set,
    "services":     CORE.list_namespaced_service,
}

_pool = ThreadPoolExecutor(max_workers=len(_LOADERS))


def fetch_all():
    """并行拉 7 类资源，返回 {"cronjobs": [...], "jobs": [...], ...}"""
    futs = {k: _pool.submit(_list, fn) for k, fn in _LOADERS.items()}
    return {k: f.result() for k, f in futs.items()}
