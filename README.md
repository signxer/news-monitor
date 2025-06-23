# 新闻监控系统

一个功能完整的新闻监控系统，具备自动ChromeDriver下载、新闻抓取、翻译和通知功能。

## 功能特性

### 🚀 核心功能
- **自动ChromeDriver管理**: 自动从官方源下载与当前平台匹配的ChromeDriver
- **智能新闻抓取**: 使用Selenium + BeautifulSoup抓取指定新闻网站内容
- **标题翻译**: 支持接入翻译API进行新闻标题翻译
- **实时通知**: 支持Bark和Server酱推送服务
- **定时监控**: 可配置的定时检查机制
- **Web管理界面**: 现代化的Web前端管理系统

### 🎯 技术特点
- **跨平台支持**: 自动检测macOS (ARM64/x64)、Linux、Windows平台
- **配置化管理**: 所有设置通过Web界面或配置文件管理
- **数据持久化**: SQLite数据库存储新闻数据
- **实时日志**: 完整的日志记录和Web界面查看
- **响应式设计**: 支持桌面和移动设备访问

## 安装部署

### 环境要求
- Python 3.8+
- Chrome浏览器（系统已安装）

### 快速开始

1. **克隆项目**
```bash
cd /Users/livrestrela/news-monitor
```

2. **安装依赖**
```bash
pip install -r requirements.txt
```

3. **启动服务**
```bash
python app.py
```

4. **访问系统**
打开浏览器访问: http://localhost:5000

## 使用指南

### 首次配置

1. **访问配置页面**: 点击侧边栏的"配置"菜单
2. **基本设置**: 设置检查间隔（建议30-60分钟）
3. **通知配置**: 
   - Bark: 填入完整的Bark推送URL
   - Server酱: 填入Server酱的SendKey
4. **翻译设置**: 如需翻译功能，启用并配置翻译API
5. **新闻网站**: 配置要监控的新闻网站

### 新闻网站配置

每个新闻网站需要配置以下信息：
- **网站名称**: 显示名称
- **网站URL**: 新闻列表页面地址
- **标题选择器**: CSS选择器，用于提取新闻标题
- **日期选择器**: CSS选择器，用于提取发布日期（可选）

#### 常用网站配置示例

**BBC News**
```
名称: BBC News
URL: https://www.bbc.com/news
标题选择器: h3[data-testid="card-headline"]
日期选择器: time
```

**CNN**
```
名称: CNN
URL: https://edition.cnn.com/
标题选择器: .container__headline-text
日期选择器: .timestamp
```

### 通知服务配置

#### Bark (iOS推送)
1. 在App Store下载Bark应用
2. 获取推送URL（格式：https://api.day.app/your_key）
3. 在配置页面填入完整URL

#### Server酱 (微信推送)
1. 访问 https://sct.ftqq.com/ 注册账号
2. 获取SendKey
3. 在配置页面填入SendKey

### 翻译API配置

系统支持接入各种翻译API，需要根据具体API调整代码中的翻译逻辑。

## 系统架构

```
新闻监控系统
├── Web前端 (Flask + Bootstrap)
│   ├── 首页 - 新闻列表和统计
│   ├── 配置 - 系统参数设置
│   └── 日志 - 实时日志查看
├── 后端服务
│   ├── ChromeDriver管理
│   ├── 新闻抓取引擎
│   ├── 翻译服务
│   ├── 通知服务
│   └── 定时任务调度
└── 数据存储
    ├── SQLite数据库
    ├── 配置文件 (JSON)
    └── 日志文件
```

## API接口

### 配置管理
- `GET /api/config` - 获取当前配置
- `POST /api/config` - 更新配置

### 新闻数据
- `GET /api/news` - 获取新闻列表
- `GET /api/check_now` - 立即检查更新

### 系统状态
- `GET /api/status` - 获取系统状态
- `GET /api/logs` - 获取系统日志

## 文件结构

```
news-monitor/
├── app.py              # 主应用文件
├── requirements.txt    # Python依赖
├── README.md          # 项目文档
├── config.json        # 配置文件（自动生成）
├── news.db           # SQLite数据库（自动生成）
├── news_monitor.log  # 日志文件（自动生成）
├── drivers/          # ChromeDriver存储目录（自动生成）
└── templates/        # HTML模板
    ├── base.html     # 基础模板
    ├── index.html    # 首页
    ├── config.html   # 配置页面
    └── logs.html     # 日志页面
```

## 常见问题

### Q: ChromeDriver下载失败
A: 检查网络连接，确保能访问Google服务。如果网络受限，可以手动下载ChromeDriver并放置在drivers目录下。

### Q: 新闻抓取失败
A: 检查CSS选择器是否正确，网站结构可能已发生变化。可以通过浏览器开发者工具重新确认选择器。

### Q: 通知发送失败
A: 检查通知服务的配置是否正确，确认API密钥和URL的有效性。

### Q: 翻译功能不工作
A: 确认翻译API配置正确，并根据具体API调整代码中的翻译逻辑。

## 开发说明

### 添加新的新闻网站
1. 在配置页面添加网站信息
2. 使用浏览器开发者工具确定CSS选择器
3. 测试抓取效果

### 自定义翻译API
修改 `app.py` 中的 `translate_text` 方法，适配你的翻译API接口。

### 扩展通知方式
在 `send_notification` 方法中添加新的通知服务支持。

## 许可证

MIT License

## 贡献

欢迎提交Issue和Pull Request来改进这个项目。

## 更新日志

### v1.0.0
- 初始版本发布
- 支持自动ChromeDriver下载
- 实现新闻抓取和通知功能
- 提供完整的Web管理界面