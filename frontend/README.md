# MathModelAgent Web 前端

本目录是默认 `main` 分支的 Vue 3 + TypeScript + Vite 前端。界面通过后端服务完成建模任务；Pi 开发分支的用途见[仓库导航](../docs/README.md)。

## 安装与开发

使用 `package.json` 指定的 pnpm 10.6.3，以下命令从本目录执行：

```bash
pnpm install
pnpm run dev
```

默认开发端口为 `5173`。先启动后端与 Redis，按 `.env.development` 和根目录 README 核对后端地址；完整安装顺序见[项目首页](../README.md)。

## 构建与预览

```bash
pnpm run build
pnpm run preview
```

`build` 先运行 `vue-tsc -b` 做类型检查，再执行 Vite 构建；预览用于查看构建后的前端页面，业务操作仍需要后端服务。

## 常用文件

| 路径 | 内容 |
| --- | --- |
| [src/](src/) | 页面、组件、路由和状态逻辑 |
| [package.json](package.json) | 开发、构建与预览命令 |
| [pnpm-lock.yaml](pnpm-lock.yaml) | 前端依赖锁定信息 |
| [.env.development](.env.development) | 开发环境配置 |

容器方式从仓库根目录的 `docker-compose.yml` 启动，前端构建定义在本目录的 Dockerfile 中。
