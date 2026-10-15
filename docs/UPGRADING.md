# 更新与可选功能配置

本页对应 `main` 分支的 Web 工程。Pi 集成的安装、契约与更新步骤使用开发分支中的 `PI.md` 和 `pi/REFACTOR.md`，入口见[仓库导航](README.md)。

## 更新前记录

在仓库根目录检查当前分支、版本与本地改动：

```bash
git branch --show-current
git rev-parse HEAD
git status --short
```

先保存本地修改，备份 `backend/.env.dev`、`frontend/.env.development` 与生成项目目录 `backend/project/work_dir/`。容器方式同时保留 Redis 数据卷，更新配置时逐项对照模板。

## 更新源码与依赖

在确认当前分支与工作区后运行：

```bash
git pull --ff-only
```

本地方式在 `backend/` 中运行 `uv sync`，在 `frontend/` 中运行 `pnpm install`。使用根目录 Compose 的部署，在仓库根目录运行 `docker compose up -d --build`，随后查看 `docker compose logs -f`，确认各服务启动状态。

具体启动参数见[项目首页](../README.md)，前端命令见[前端说明](../frontend/README.md)。后端服务默认使用 `8000`，Web 前端默认使用 `5173`。

## 核对可选功能

首页列出的可选功能由后端配置控制：

| 功能 | 配置项 | 核对内容 |
| --- | --- | --- |
| Web Search | `SEARCH_ENABLED`、`TAVILY_API_KEY` | 同时核对开关与服务密钥 |
| RAG 知识库 | `RAG_ENABLED` | 准备本地知识库与检索相关依赖 |
| HIL 人机协作 | `HIL_ENABLED` | 确认任务中的人工交互流程 |
| Fallback Hand Off | `FALLBACK_*` | 核对备用模型的配置与连接 |
| Evaluator + Feedback | `EVALUATOR_*` | 核对评估模型与反馈设置 |

变量的具体取值按 `backend/.env.example` 与当前分支代码填写。修改运行环境文件后重启相应服务，通过一次单独的开发任务确认配置生效。

## 保留项目数据

Compose 挂载生成项目目录，并使用 `redis_data` 与 `backend_venv` 命名卷。更新时保留原数据与环境配置，按所选版本同步依赖；记录更新前后的提交号和检查结果，便于定位行为变化。
