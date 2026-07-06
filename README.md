# 新闻监控系统

实时监控全球各大机构（央行、智库、国际组织等）的最新研究论文和报告，支持关键词筛选推送、自动翻译、多平台通知。

## 功能特性

- **多源监控** — 支持 HTML 网页和 RSS 订阅两种抓取方式，内置 30+ 机构源
- **关键词筛选** — 多组规则，支持 OR（任意匹配）和 AND（全部匹配）模式，只推送关心的新闻
- **自动翻译** — 接入 DeepLX 等翻译 API，自动翻译英文标题
- **多端推送** — 支持 Bark（iOS）和 Server酱（微信）通知，可配置多个接收地址
- **Web 管理** — 现代化 Web 界面，配置、新闻浏览、日志查看一站搞定
- **跨平台** — Windows / macOS / Linux 全平台支持，提供预编译安装包

## 快速安装

### 方式一：下载预编译包（推荐）

从 [Releases](https://github.com/signxer/news-monitor/releases/latest) 页面下载对应平台的安装包：

| 平台 | 文件 | 说明 |
|------|------|------|
| Windows x64 | `news_monitor.exe` | 双击运行 |
| Linux x64 | `news-monitor_*_amd64.deb` | `sudo dpkg -i` 安装 |
| Linux arm64 | `news-monitor_*_arm64.deb` | `sudo dpkg -i` 安装 |
| macOS Intel | `NewsMonitor-macOS-x64.dmg` | 打开 DMG 双击运行 |
| macOS Apple Silicon | `NewsMonitor-macOS-arm64.dmg` | 打开 DMG 双击运行 |

> ⚠️ 运行前需确保系统已安装 [Google Chrome](https://www.google.com/chrome/) 浏览器，ChromeDriver 会在首次运行时自动下载。

### 方式二：源码运行

```bash
git clone https://github.com/signxer/news-monitor.git
cd news-monitor
pip install -r requirements.txt
python app.py
```

启动后浏览器自动打开 `http://localhost:5000`。

## 使用说明

### 1. 配置通知

在「配置」页面填写推送地址：

- **Bark**：打开 Bark App 获取推送 URL（格式 `https://api.day.app/your_key`）
- **Server酱**：在 [sct.ftqq.com](https://sct.ftqq.com/) 获取 SendKey

支持配置多个地址，同时推送到多台设备。

### 2. 添加新闻源

每个网站需要配置：
- **网站名称** — 显示名称
- **网站 URL** — 新闻列表页地址
- **网站类型** — HTML 或 RSS
- **标题选择器** — CSS 选择器（HTML 类型需要）

内置 IMF、世界银行、美联储、欧央行、BIS 等 30+ 机构源，可直接启用。

### 3. 关键词筛选

在「配置」页面开启关键词筛选，添加规则：

- **任意匹配（OR）** — 规则内任一关键词出现即推送，如：`economy, inflation, GDP`
- **全部匹配（AND）** — 规则内所有关键词同时出现才推送，如：`China` + `digital currency`

多条规则之间是 OR 关系，命中任一规则即推送。匹配范围包括原标题和翻译标题，大小写不敏感。

### 4. 翻译设置

启用翻译后，英文标题会自动翻译为中文。需要配置翻译 API 地址和密钥。

## 项目结构

```
news-monitor/
├── app.py              # 主应用
├── paths.py            # 跨平台路径管理
├── requirements.txt    # Python 依赖
├── news_monitor.spec   # PyInstaller 打包配置
├── .github/workflows/
│   └── build.yml       # GitHub Actions 自动构建
└── templates/          # Web 前端模板
    ├── base.html
    ├── index.html       # 首页（新闻列表 + 分页）
    ├── config.html      # 配置页
    └── logs.html        # 日志页
```

运行时数据（配置、数据库、日志）存储在平台标准目录：
- macOS：`~/Library/Application Support/news-monitor/`
- Windows：`%APPDATA%/news-monitor/`
- Linux：`~/.local/share/news-monitor/`

## API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/config` | GET/POST | 获取/更新配置 |
| `/api/news?page=1&per_page=20` | GET | 分页获取新闻 |
| `/api/check_now` | GET | 立即检查更新 |
| `/api/status` | GET | 系统状态 |
| `/api/logs` | GET | 系统日志 |
| `/api/test_notification` | POST | 测试推送通知 |

## 自行构建

```bash
pip install pyinstaller -r requirements.txt
pyinstaller news_monitor.spec
```

产物在 `dist/` 目录下。推送 `v*` 格式的 tag 会自动触发 GitHub Actions 构建所有平台的安装包：

```bash
git tag v1.1.0
git push origin v1.1.0
```

## 许可证

MIT License
