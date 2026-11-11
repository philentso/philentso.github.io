# Myproject · mp-task-board 流水线看板

这是一个专为 K8s 数据处理流水线设计的轻量级 Web 可视化管理面板。不仅能够实时查看各业务线的任务状态、运行中的 Pod, 还能看到健康（成功率）状态，运行错误等信息，更直观的看到当前业务命名空间下的 Cronjob 运行状况。

## 核心功能

- **任务总览**：自动关联 CronJob 与 Job，展示 Schedule、下次执行时间、上次耗时及成功率，支持按业务前缀（如 `cpu-pg`、`gpu-qa`、`gpu-source` 等）分组过滤。
- **运行中 Pod**：实时监控当前运行的所有 Pod，包括所属 Owner、已运行时长、重启次数、异常容器状态（如 `CrashLoopBackOff`）。
- **常驻服务**：全面覆盖并展示 Deployment、StatefulSet、DaemonSet、Service，直观查看 Ready 状态、镜像及 Pod 存活比例。
- **高并发优化**：后端采用线程池并发拉取 K8s API 并自带超时限制；前端采用防抖与并发锁，防止请求堆积卡死。

## 页面展示功能项

1. 任务总数
2. 运行中 Pod
3. 活跃 Job
4. 上次失败的任务
5. 调度异常
6. 未就绪 Pod
7. 常驻服务 异常/总数
8. 挂起任务
9. 近期失败 Job 数

## 目录结构

```
myproject-pipeline-ui/
├── requirements.txt        # Python 依赖
├── Dockerfile              # 容器构建文件
├── app/
│   ├── __init__.py
│   ├── main.py             # FastAPI 入口与路由
│   ├── config.py           # 环境变量配置
│   ├── k8s_client.py       # K8s API 客户端（带超时与缓存）
│   ├── aggregator.py       # 核心聚合逻辑（并发拉取与数据清洗）
│   └── static/
│       └── index.html      # 前端单文件（HTML + CSS + JS）
└── deploy/
    ├── rbac.yaml           # 只读 RBAC 权限配置
    └── deployment.yaml     # K8s 部署与 Service 配置
```

## 快速开始（本地运行）

### 1. 准备环境

确保本机已安装 Python 3.9+，且配置了能够访问目标 K8s 集群的 kubeconfig。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置环境变量

可以在启动时传入环境变量，或者直接写在代码 `app/config.py` 中：

| 变量名 | 默认值 | 说明 |
| --- | --- | --- |
| `NAMESPACE` | `gpu-pipeline` | 目标 K8s 命名空间 |
| `DISPLAY_TZ` | `Asia/Shanghai` | Web 界面展示的时区 |
| `CACHE_TTL` | `5` | K8s 数据本地缓存时间（秒） |

### 3. 后台启动

使用 `nohup` 让程序在后台长期运行（防止终端断开）：

```bash
nohup uvicorn app.main:app --host 0.0.0.0 --port 8080 > myproject-ui.log 2>&1 &
```

- **访问**：浏览器打开 `http://<服务器IP>:8080`
- **日志**：`tail -f myproject-ui.log`
- **停止**：`pkill -9 -f uvicorn`

## 部署到 Kubernetes 集群

### 1. 构建镜像

```bash
docker build -t myproject/pipeline-ui:0.1 .
```

> 如果是私有仓库或离线环境，请使用 `docker save` / `docker load` 或推送到内网 Harbor。

### 2. 配置 RBAC 权限

应用只读权限清单（包含 Pods、Services、Jobs、CronJobs、Deployments、StatefulSets、DaemonSets 等）：

```bash
kubectl apply -f deploy/rbac.yaml
```

### 3. 部署应用

```bash
kubectl apply -f deploy/deployment.yaml
```

### 4. 访问服务

你可以直接通过端口转发访问：

```bash
kubectl -n gpu-pipeline port-forward svc/myproject-pipeline-ui 8080:80
```

## 常见问题排查（Troubleshooting）

### 1. 页面空白或报错 HTTP 500 / ERR_EMPTY_RESPONSE

- 检查后台日志：`tail -n 50 myproject-ui.log`
- 检查 `curl http://127.0.0.1:8080/api/overview` 是否有 JSON 返回
- 若有 K8s API 报错，通常是 kubeconfig 权限不足，或在集群内未正确绑定 ServiceAccount

### 2. 历史记录数据为空

- 系统每被请求一次 `/api/overview`，就会抓取一次当前状态入库。请等待几秒钟并刷新页面。
- 检查项目根目录是否生成了 `pipeline.db` 文件。

### 3. 请求一直 pending 卡住

- 确认服务已使用单进程 uvicorn 启动，且 `app/k8s_client.py` 中已加入 `_request_timeout` 限制。
- 如果页面卡住，请按 `Ctrl + F5` 强刷缓存，或使用无痕模式访问（排除浏览器插件或旧缓存干扰）。