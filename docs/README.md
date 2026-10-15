# MathModelAgent 仓库导航

这是 `jihe520/MathModelAgent` 的派生仓库。默认 `main` 保留上游 Web、后端、SKILLS 和桌面版说明；本仓库的 Pi 集成与可靠性交付改动保存在开发分支。

## 先选择分支

| 分支 | 用途 | 文档 |
| --- | --- | --- |
| `main` | 本页对应的默认分支，阅读上游项目与 Web 工程 | [项目首页](../README.md) |
| `pi-integration` | Pi 集成与工作流改造 | [PI.md](https://github.com/yangzz1125/MathModelAgent/blob/pi-integration/PI.md) |
| `pi-reliability-efficiency-refactor` | 可靠性与效率改造入口 | [重构说明](https://github.com/yangzz1125/MathModelAgent/blob/pi-reliability-efficiency-refactor/pi/REFACTOR.md) |

需要 Pi 版本时，可单独克隆开发分支，并按该分支的说明配置：

```bash
git clone --branch pi-integration https://github.com/yangzz1125/MathModelAgent.git MathModelAgent-pi
```

上游 Releases 下载的是上游发行版；本仓库开发分支的改动以对应分支源码和说明为准。

## main 分支的工程结构

| 路径 | 职责 |
| --- | --- |
| [backend/](../backend/) | Python / FastAPI 后端、建模流程与生成工作目录 |
| [frontend/](../frontend/) | Vue 3 + TypeScript + Vite 页面 |
| [docker-compose.yml](../docker-compose.yml) | Redis、后端与前端的组合运行 |
| [docs/md/tutorial.md](md/tutorial.md) | 原项目使用教程 |
| [docs/UPGRADING.md](UPGRADING.md) | 更新源码、保留数据与核对可选配置 |
| [README_EN.md](../README_EN.md) | 英文介绍 |

后端依赖要求 Python 3.12 以上；前端包管理器由 `frontend/package.json` 指定为 pnpm 10.6.3。Compose 默认端口为 Redis `6379`、后端 `8000`、前端 `5173`。

## Web 工程启动前

准备 `backend/.env.dev` 与 `frontend/.env.development`，按首页和 `backend/.env.example` 填写模型、Redis 等配置。后端环境文件包含运行时配置，按所选部署方式单独准备。

前端开发命令见[前端说明](../frontend/README.md)。Docker、本地后端和社区脚本的详细步骤继续保留在项目首页，按同一种方式完成安装与启动。

## 来源

上游为 [jihe520/MathModelAgent](https://github.com/jihe520/MathModelAgent)。上游的发行入口、许可证、模板来源和贡献说明保留在原文中。
