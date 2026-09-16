# agent-mesh edge — nosystemd / microVM special build (1.6.3-docker)

**特供版，不在主线内。** 面向**没有 systemd / cron / 可控 entrypoint** 的环境
（Firecracker 微虚拟机、普通容器等），用「登录 shell 钩子 + 后台常驻」实现
**随机器启动 / 登录自动运行**。

> 主线探针的打包与升级流程**不受影响**；本包不参与 `scripts/build-agent-bootstrap.py`
> 与项目的版本升级链。

## 内容

```
bin/agent-mesh-edge        # wrapper：source etc/edge.env → exec .bin（保留回滚逻辑）
bin/agent-mesh-edge.bin    # 真实探针二进制
bin/opencode               # 内置 opencode（llm 任务用）
VERSION                    # 1.6.3-docker
install-nosystemd.sh       # 安装脚本
start.sh stop.sh status.sh keepalive.sh
```

安装后落在 `INSTALL_DIR`（默认 `/opt/agent-mesh-agent`）：

```
bin/  etc/edge.env  etc/agent_version  work/  logs/  run/
start.sh stop.sh status.sh keepalive.sh
```

## 安装

```sh
tar -xzf agent-mesh-edge-nosystemd-1.6.3-docker.tar.gz -C /tmp
ORCHESTRATOR_URL=http://<orchestrator>:8000 \
TOKEN=<user-api-token> \
EDGE_ALIAS=dugz \
FORCE_REINSTALL=1 \
  bash /tmp/agent-mesh-edge-nosystemd-1.6.3-docker/install-nosystemd.sh
```

- 需要 **root**（写 `/opt` 与 `/etc/profile.d`）。
- `FORCE_REINSTALL=1`：替换已存在的安装（会先停掉旧探针）。
- 可选：`EDGE_LLM_API_KEY` / `EDGE_LLM_BASE_URL` / `EDGE_LLM_MODEL` /
  `EDGE_LLM_MODELS` / `EDGE_SYSTEM_PROMPT` / `WORK_DIR`。

## 自启原理

安装脚本写入 `/etc/profile.d/agent-mesh-edge.sh`：

```sh
if [ -x /opt/agent-mesh-agent/start.sh ]; then
    /opt/agent-mesh-agent/start.sh >/dev/null 2>&1 || true
fi
:
```

- 该文件由 `/etc/profile` source；而 **Firecracker init 启动时拉起的正是
  `sh -lc ...`（login shell）**，所以：
  - **机器启动时** → 触发一次（无需登录、无需手动）；
  - **每次 SSH 登录** → 再触发一次（幂等）。
- `start.sh` 用 `setsid nohup` 把 `keepalive.sh` 彻底脱离登录会话，登出不影响；
  `keepalive.sh` 用 `flock` 保证单实例，并循环保活（进程退出后 5s 重启）。
- 钩子脚本必须**永不 exit / 永不返回非 0**（父 shell 带 `set -e`），故写法极简且以 `:` 收尾。

## 控制

```sh
/opt/agent-mesh-agent/status.sh          # 版本 / 进程 / 最近日志
/opt/agent-mesh-agent/stop.sh            # 停止（keepalive + 二进制）
/opt/agent-mesh-agent/start.sh           # 启动（幂等）
tail -f /opt/agent-mesh-agent/logs/edge.log
```

## 升级

**已由服务端关闭自动升级（`auto_upgrade=0`），本包不做自升级。**
需要更新时：取新的特供包，再执行一次安装（`FORCE_REINSTALL=1`）即可替换二进制。

> 本环境无 systemd，主线探针的 systemctl/execv 重启回退在此不可靠，因此不依赖自升级。

## 持久化与身份

- 安装目录在微虚拟机磁盘上，`etc/edge.env`（持久化的 agent token）、
  `machine-id`、`etc/agent_version` 都会保留；`device_id` 稳定，不会每次登录变成新节点。
- 若微虚拟机被**重置/重建**，需重新安装并重新注册。

## 排障

- 探针没起来：`/opt/agent-mesh-agent/status.sh` 看 `keepalive`/`agent` 是否 running。
- 启动即退出：看 `logs/edge.log` 顶部报错（常见为 `ORCHESTRATOR_URL` 不可达、
  `EDGE_TOKEN` 失效）。
- 手动触发：`/opt/agent-mesh-agent/start.sh`。
- 确认自启钩子存在：`cat /etc/profile.d/agent-mesh-edge.sh`。
