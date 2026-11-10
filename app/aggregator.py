"""把 k8s 返回的原始 JSON(dict) 整理成页面要用的数据。

注意：这里所有对象都是普通 dict（不是 SDK 的模型对象），
所以取值用 obj.get("字段") 或下面的 _g(obj, "a", "b") 逐层取。
"""
import re
import time
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter

from . import config as cfg


# ---------- 小工具 ----------
def _g(d, *keys, default=None):
    """逐层取值：_g(pod, "status", "phase")，中间任何一层缺失就返回 default"""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
        if d is None:
            return default
    return d


def _ts(s):
    """k8s 的 ISO 时间字符串 -> 秒级时间戳；空值/格式不对返回 None"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _dur(start, end):
    if start is None or end is None:
        return None
    return max(0.0, end - start)


def _short_image(img):
    """harbor.xxx:30443/feagine/semantic-cpu@sha256:d55dde18... -> feagine/semantic-cpu@d55dde18"""
    name, _, digest = (img or "").partition("@")
    parts = name.split("/")
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0]):
        parts = parts[1:]                       # 去掉镜像仓库域名
    s = "/".join(parts)
    return f"{s}@{digest.split(':')[-1][:8]}" if digest else s


def _first_image(tpl_spec):
    cs = (tpl_spec or {}).get("containers") or []
    return cs[0].get("image", "") if cs else ""


# ---------- Cron 时间计算 ----------
def _next_run(schedule, tzname):
    """下次触发时间。按 CronJob 自己的 timeZone 算（没写就是 UTC），
    不能用页面显示时区算，否则 '15 3 * * *' 这类固定时刻会差 8 小时。"""
    try:
        return croniter(schedule, datetime.now(ZoneInfo(tzname))).get_next(float)
    except Exception:
        return None


def _cron_gap(schedule, tzname):
    """未来 12 次触发之间的最大间隔(秒)，用来判断“多久没调度算异常”。"""
    try:
        it = croniter(schedule, datetime.now(ZoneInfo(tzname)))
        ts = [it.get_next(float) for _ in range(12)]
        return max(b - a for a, b in zip(ts, ts[1:]))
    except Exception:
        return None


# ---------- Pod 相关 ----------
def _job_name_of_pod(pod):
    labels = _g(pod, "metadata", "labels", default={})
    return labels.get("job-name") or labels.get("batch.kubernetes.io/job-name")


def _pod_term_reason(pod):
    """Pod 里第一个“非 0 退出”的容器原因，例如 OOMKilled(exit 137)"""
    st = pod.get("status") or {}
    for c in (st.get("initContainerStatuses") or []) + (st.get("containerStatuses") or []):
        t = _g(c, "state", "terminated") or _g(c, "lastState", "terminated")
        if t and t.get("exitCode"):
            return f"{t.get('reason') or 'Error'}(exit {t['exitCode']})"
    if st.get("phase") == "Failed" and st.get("reason"):
        return st["reason"]                       # 例如 Evicted
    return ""


def _pod_status(pod):
    """类似 kubectl 的 STATUS 列，返回 (状态文字, "就绪数/容器数", 是否全部就绪)"""
    st = pod.get("status") or {}
    cs = st.get("containerStatuses") or []
    ics = st.get("initContainerStatuses") or []
    status = st.get("reason") or st.get("phase") or "Unknown"

    init_done = sum(1 for c in ics if _g(c, "state", "terminated", "exitCode") == 0)
    if ics and init_done < len(ics):
        status = f"Init:{init_done}/{len(ics)}"
        for c in ics:
            w = _g(c, "state", "waiting")
            if w and w.get("reason") and w["reason"] != "PodInitializing":
                status = f"Init:{w['reason']}"
                break
    else:
        for c in cs:
            w = _g(c, "state", "waiting")
            if w and w.get("reason"):
                status = w["reason"]              # CrashLoopBackOff / ImagePullBackOff ...
                break

    ready_n = sum(1 for c in cs if c.get("ready"))
    total = len(cs) or len(_g(pod, "spec", "containers", default=[]))
    return status, f"{ready_n}/{total}", (total > 0 and ready_n == total)


def _workload_of(pod, job_to_cj):
    """Pod 属于哪个工作负载：返回 (类型, 名字)"""
    refs = _g(pod, "metadata", "ownerReferences", default=[])
    if not refs:
        return "Pod", pod["metadata"]["name"]
    kind, name = refs[0].get("kind"), refs[0].get("name", "")
    if kind == "Job":
        return ("CronJob", job_to_cj[name]) if name in job_to_cj else ("Job", name)
    if kind == "ReplicaSet":                      # 去掉末尾的 pod-template-hash
        return "Deployment", re.sub(r"-[a-z0-9]{5,10}$", "", name)
    return kind, name


# ---------- Job ----------
def _job_phase(job):
    st = job.get("status") or {}
    for c in st.get("conditions") or []:
        if c.get("status") == "True":
            if c.get("type") == "Complete":
                return "Success"
            if c.get("type") == "Failed":
                return "Failed"
    return "Running" if st.get("active") else "Pending"


def _job_view(j, pods_by_job, now):
    meta, st = j["metadata"], j.get("status") or {}
    phase = _job_phase(j)
    start = _ts(st.get("startTime"))
    end = _ts(st.get("completionTime"))
    reason = ""

    if phase == "Failed":
        for c in st.get("conditions") or []:
            if c.get("type") == "Failed" and c.get("status") == "True":
                end = end or _ts(c.get("lastTransitionTime"))   # 失败的 Job 没有 completionTime
                reason = c.get("reason") or ""
    jp = pods_by_job.get(meta["name"], [])                       # 已按创建时间倒序
    if phase == "Failed":
        for p in jp:
            d = _pod_term_reason(p)
            if d:
                reason = f"{reason} · {d}" if reason else d
                break

    if end is None and phase in ("Running", "Pending"):
        end_for_dur = now                                        # 进行中的 Job：算到现在
    else:
        end_for_dur = end

    return {
        "name":           meta["name"],
        "cronjob":        _cronjob_of(j),
        "phase":          phase,
        "start":          start,
        "end":            end,
        "duration":       _dur(start, end_for_dur),
        "active":         st.get("active") or 0,
        "failed_retries": st.get("failed") or 0,
        "reason":         reason,
        "pod":            jp[0]["metadata"]["name"] if jp else None,
        "created":        meta.get("creationTimestamp", ""),
    }


def _cronjob_of(job):
    meta = job["metadata"]
    labels = meta.get("labels") or {}
    for k in ("batch.kubernetes.io/cronjob-name", "cronjob-name"):
        if labels.get(k):
            return labels[k]
    for ref in meta.get("ownerReferences") or []:
        if ref.get("kind") == "CronJob":
            return ref.get("name")
    return None


# ---------- CronJob ----------
def build_cronjobs(cronjobs, views_by_cj, now):
    out = []
    for cj in cronjobs:
        meta, spec, st = cj["metadata"], cj.get("spec") or {}, cj.get("status") or {}
        name = meta["name"]
        cj_views = sorted(views_by_cj.get(name, []), key=lambda v: v["created"], reverse=True)
        recent = cj_views[:cfg.RECENT_PER_CJ]

        succ = sum(1 for v in recent if v["phase"] == "Success")
        fail = sum(1 for v in recent if v["phase"] == "Failed")
        last = recent[0] if recent else None
        last_fail = next((v for v in cj_views if v["phase"] == "Failed"), None)
        running = [v for v in cj_views if v["phase"] == "Running"]

        sched = spec.get("schedule")
        tzname = spec.get("timeZone") or "UTC"
        suspend = bool(spec.get("suspend"))
        active_jobs = len(st.get("active") or [])
        last_sched = _ts(st.get("lastScheduleTime"))

        # 调度异常判断
        flag = None
        gap = _cron_gap(sched, tzname) if (sched and not suspend) else None
        if gap:
            if active_jobs:
                starts = [v["start"] for v in running if v["start"]]
                # Forbid 策略下，上一个 Job 还没跑完，新的调度会被跳过
                if spec.get("concurrencyPolicy") == "Forbid" and starts and now - min(starts) > gap:
                    flag = "blocked"
            elif last_sched and now - last_sched > 2 * gap + 120:
                flag = "stale"

        out.append({
            "name":          name,
            "group":         name.split("-")[0],
            "schedule":      sched,
            "timezone":      tzname,
            "suspend":       suspend,
            "concurrency":   spec.get("concurrencyPolicy") or "Allow",
            "next_run":      None if suspend else _next_run(sched, tzname),
            "last_schedule": last_sched,
            "last_success":  _ts(st.get("lastSuccessfulTime")),
            "active_jobs":   active_jobs,
            "active_pods":   sum(v["active"] for v in running),
            "success_rate":  round(succ / (succ + fail) * 100, 1) if (succ + fail) else None,
            "recent_total":  len(recent),
            "recent_failed": fail,
            "flag":          flag,
            "last_job": None if not last else {
                k: last[k] for k in ("name", "phase", "start", "end", "duration", "reason")},
            "last_fail": None if not last_fail else {
                "name": last_fail["name"],
                "time": last_fail["end"] or last_fail["start"],
                "reason": last_fail["reason"]},
        })

    out.sort(key=lambda x: (0 if x["flag"] else 1, -(x["active_pods"] > 0),
                            -x["recent_failed"], x["name"]))
    return out


# ---------- 运行中 / 异常 Pod ----------
def build_pods(pods, job_to_cj, now):
    items = []
    for p in pods:
        phase = _g(p, "status", "phase")
        if phase not in ("Running", "Pending"):          # Succeeded/Failed 属于“最近执行”的范畴
            continue
        cs = _g(p, "status", "containerStatuses", default=[])
        status, ready, ready_ok = _pod_status(p)
        kind, workload = _workload_of(p, job_to_cj)

        last_reason, last_at = "", None
        for c in cs:
            t = _g(c, "lastState", "terminated")
            if t:
                at = _ts(t.get("finishedAt"))
                if at and (last_at is None or at > last_at):
                    last_at, last_reason = at, t.get("reason") or ""

        start = _ts(_g(p, "status", "startTime")) or _ts(p["metadata"].get("creationTimestamp"))
        items.append({
            "name":         p["metadata"]["name"],
            "kind":         kind,
            "workload":     workload,
            "node":         _g(p, "spec", "nodeName"),
            "start":        start,
            "age":          _dur(start, now),
            "status":       status,
            "ready":        ready,
            "ready_ok":     ready_ok,
            "restarts":     sum(c.get("restartCount") or 0 for c in cs),
            "last_reason":  last_reason,
            "last_restart": last_at,
        })
    # 没就绪的排最前，其余按启动时间
    items.sort(key=lambda x: (x["ready_ok"], x["start"] or 0))
    return items


# ---------- 常驻服务 ----------
def _match_pods(pods, selector):
    """只支持 matchLabels（覆盖绝大多数 workload）"""
    labels = (selector or {}).get("matchLabels") or {}
    if not labels:
        return []
    return [p for p in pods
            if all(_g(p, "metadata", "labels", default={}).get(k) == v for k, v in labels.items())]


def _pod_stats(matched):
    return {
        "running":  sum(1 for p in matched if _g(p, "status", "phase") == "Running"),
        "total":    len(matched),
        "restarts": sum(c.get("restartCount") or 0
                        for p in matched for c in _g(p, "status", "containerStatuses", default=[])),
    }


def _replicas(spec):
    r = spec.get("replicas")
    return 1 if r is None else r              # k8s 默认 1


def build_workloads(raw, now):
    pods = raw["pods"]
    out = {"deployments": [], "statefulsets": [], "daemonsets": [], "services": []}

    for d in raw["deployments"]:
        spec, st = d.get("spec") or {}, d.get("status") or {}
        conds = {c.get("type"): c.get("status") for c in st.get("conditions") or []}
        want, ready = _replicas(spec), st.get("readyReplicas") or 0
        img = _first_image(_g(spec, "template", "spec"))
        out["deployments"].append({
            "name": d["metadata"]["name"], "ready": f"{ready}/{want}",
            "ready_num": ready, "desired_num": want,
            "image": _short_image(img), "image_full": img,
            "age": _dur(_ts(d["metadata"].get("creationTimestamp")), now),
            "available": conds.get("Available") == "True",
            "pods": _pod_stats(_match_pods(pods, spec.get("selector"))),
        })

    for s in raw["statefulsets"]:
        spec, st = s.get("spec") or {}, s.get("status") or {}
        want, ready = _replicas(spec), st.get("readyReplicas") or 0
        img = _first_image(_g(spec, "template", "spec"))
        out["statefulsets"].append({
            "name": s["metadata"]["name"], "ready": f"{ready}/{want}",
            "ready_num": ready, "desired_num": want,
            "image": _short_image(img), "image_full": img,
            "age": _dur(_ts(s["metadata"].get("creationTimestamp")), now),
            "pods": _pod_stats(_match_pods(pods, spec.get("selector"))),
        })

    for ds in raw["daemonsets"]:
        spec, st = ds.get("spec") or {}, ds.get("status") or {}
        want, ready = st.get("desiredNumberScheduled") or 0, st.get("numberReady") or 0
        img = _first_image(_g(spec, "template", "spec"))
        out["daemonsets"].append({
            "name": ds["metadata"]["name"], "ready": f"{ready}/{want}",
            "ready_num": ready, "desired_num": want,
            "image": _short_image(img), "image_full": img,
            "age": _dur(_ts(ds["metadata"].get("creationTimestamp")), now),
            "pods": _pod_stats(_match_pods(pods, spec.get("selector"))),
        })

    for svc in raw["services"]:
        spec = svc.get("spec") or {}
        ports = []
        for p in spec.get("ports") or []:
            s = f"{p.get('port')}→{p.get('targetPort')}/{p.get('protocol') or 'TCP'}"
            if p.get("nodePort"):
                s += f" (nodePort {p['nodePort']})"
            ports.append(s)
        ing = _g(svc, "status", "loadBalancer", "ingress", default=[])
        out["services"].append({
            "name": svc["metadata"]["name"],
            "type": spec.get("type") or "ClusterIP",
            "cluster_ip": spec.get("clusterIP") or "-",
            "external": (ing[0].get("ip") or ing[0].get("hostname") or "") if ing else "",
            "ports": ", ".join(ports),
            "age": _dur(_ts(svc["metadata"].get("creationTimestamp")), now),
        })

    for k in out:
        out[k].sort(key=lambda x: x["name"])
    return out


# ---------- 聚合总览 ----------
def build_overview(raw):
    """raw 是 k8s_client.fetch_all() 的结果"""
    now = time.time()

    # 1) Pod 按所属 Job 分组（最新的在前）
    pods_by_job = defaultdict(list)
    for p in sorted(raw["pods"], key=lambda p: p["metadata"].get("creationTimestamp", ""), reverse=True):
        jn = _job_name_of_pod(p)
        if jn:
            pods_by_job[jn].append(p)

    # 2) 每个 Job 生成一份视图；再按 CronJob 分组
    views = [_job_view(j, pods_by_job, now) for j in raw["jobs"]]
    views_by_cj = defaultdict(list)
    job_to_cj = {}
    for v in views:
        if v["cronjob"]:
            views_by_cj[v["cronjob"]].append(v)
            job_to_cj[v["name"]] = v["cronjob"]

    cjs = build_cronjobs(raw["cronjobs"], views_by_cj, now)
    pods = build_pods(raw["pods"], job_to_cj, now)
    wl = build_workloads(raw, now)

    recent_jobs = sorted(views, key=lambda v: v["created"], reverse=True)[:cfg.RECENT_JOBS_LIMIT]
    for v in recent_jobs:
        v.pop("created", None)

    groups = defaultdict(lambda: {"total": 0, "active": 0, "failed": 0, "rs": 0.0, "rn": 0})
    for c in cjs:
        g = groups[c["group"]]
        g["total"] += 1
        g["active"] += c["active_pods"]
        g["failed"] += c["recent_failed"]
        if c["success_rate"] is not None:
            g["rs"] += c["success_rate"]
            g["rn"] += 1
    for g in groups.values():
        g["avg_rate"] = round(g["rs"] / g["rn"], 1) if g["rn"] else None
        del g["rs"], g["rn"]

    deploys = wl["deployments"] + wl["statefulsets"]
    return {
        "meta": {
            "company":      cfg.COMPANY_NAME,
            "pipeline":     cfg.PIPELINE_NAME,
            "namespace":    cfg.NAMESPACE,
            "server_time":  now,
            "display_tz":   cfg.DISPLAY_TZ,
            "logs_enabled": cfg.ENABLE_LOGS,
        },
        "counts": {
            "cronjobs":       len(cjs),
            "running_pods":   sum(1 for p in pods if p["status"] == "Running"),
            "active_jobs":    sum(c["active_jobs"] for c in cjs),
            "suspended":      sum(1 for c in cjs if c["suspend"]),
            "failed_recent":  sum(c["recent_failed"] for c in cjs),
            "last_failed":    sum(1 for c in cjs if c["last_job"] and c["last_job"]["phase"] == "Failed"),
            "flagged":        sum(1 for c in cjs if c["flag"]),
            "bad_pods":       sum(1 for p in pods if not p["ready_ok"]),
            "services_up":    sum(1 for x in deploys if x["desired_num"] > 0),
            "services_bad":   sum(1 for x in deploys if x["desired_num"] > 0 and x["ready_num"] < x["desired_num"]),
            "deployments":    len(wl["deployments"]),
            "statefulsets":   len(wl["statefulsets"]),
            "daemonsets":     len(wl["daemonsets"]),
            "services":       len(wl["services"]),
        },
        "groups":       dict(sorted(groups.items())),
        "cronjobs":     cjs,
        "running_pods": pods,
        "recent_jobs":  recent_jobs,
        "workloads":    wl,
    }
