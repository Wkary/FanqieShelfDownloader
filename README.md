# 番茄小说批量下载器（书架版）

基于浏览器登录态（Cookie）的番茄小说书架批量工具：多账号管理、按分组**多实例并发下载**、实时进度显示、下载后自动整理到桌面时间戳文件夹，并可**按书籍标签（完结/连载/断更）在书架分组间移动书籍**。

> 本项目是 [Tomato-Novel-Downloader](https://github.com/zhongbai2333/Tomato-Novel-Downloader)（MIT, © zhongbai2333）的**外围调度脚本**：正文下载由该下载器的 Web 服务完成（内置官方加密），本仓库仅负责书架扫描、并发队列与分组整理。

## 功能

- **多账号管理**：添加 / 切换 / 删除账号，Cookie 存于本地 `accounts.json`（不入库）。
- **Cookie 校验**：启动即校验；失效时提示并自动打开浏览器登录页 `https://fanqienovel.com/bookshelf?enter_from=menu`，扫码登录后粘贴新 Cookie。
- **多实例并发下载**：默认启动 2 个下载器实例（`--workers N` 可调），书籍分配到各实例并行下载。
- **实时进度**：每本显示 `XX.X% [####] 已存/总章`。
- **自动整理**：下载后把书籍移动到桌面时间戳文件夹（如 `2611092136`），并清理数字缓存文件夹。
- **按标签移动分组**：扫描指定分组每本书的标签，按标签批量移动到目标书架分组（走番茄书架 update 接口）。

## 快速开始

1. 浏览器登录 `fanqienovel.com` → F12 → Network → 复制任一请求的整段 `Cookie`。
2. 把 Cookie 粘贴到同目录的 `cookie.txt`（或用 `Z 账号设置 → 添加账号`）。
3. 运行：

   ```bash
   python 番茄书架批量下载.py
   ```

   程序会自动启动下载器实例，无需手动开 exe。

## 主菜单

```
1  下载指定分组书籍（多实例并发 + 进度 + 自动整理）
2  书籍移动分组（按标签移动到目标书架分组）
Z  账号设置（添加 / 切换 / 删除）
0  退出
```

## 常用参数

| 参数 | 说明 |
|---|---|
| `--workers 3` | 并发下载实例数（默认 2） |
| `--format txt` | 输出格式 `txt/epub/pdf`（默认 txt） |
| `--password xxx` | 下载器 Web 锁密码（若启用） |
| `--host / --port` | 下载器 Web 地址（默认 127.0.0.1:18423） |

## 运行环境

- Python 3.8+（仅标准库，无需 pip 安装任何依赖）
- Windows（捆绑 `TomatoNovelDownloader.exe`）

## 文件

```
番茄书架批量下载.py        主程序
TomatoNovelDownloader.exe   下载器（负责正文下载）
config.yml                  下载器默认配置
README.md                   本说明
```

> `cookie.txt` 与 `accounts.json` 含登录态，已在 `.gitignore` 中排除，**不会上传**。

## 免责声明

- 仅供个人已购/已读内容的本地备份与整理，请遵守番茄小说用户协议，勿用于商业分发。
- 账号 Cookie 属敏感信息，请妥善保管，不要泄露或提交到公开仓库。

## 许可

本项目脚本采用 MIT 许可；捆绑的下载器二进制版权归 [zhongbai2333](https://github.com/zhongbai2333) 所有，遵循其上游 MIT 许可。
