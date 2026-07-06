#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import time
import threading
import schedule
import requests
import platform
import zipfile
import logging
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, redirect, url_for
from concurrent.futures import ThreadPoolExecutor, as_completed
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from bs4 import BeautifulSoup
import sqlite3
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET
import feedparser

app = Flask(__name__)
app.secret_key = 'news_monitor_secret_key_2024'

# 配置日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('news_monitor.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class NewsMonitor:
    def __init__(self):
        self.config = self.load_config()
        self.driver_path = None
        self.is_running = False
        self.last_check_time = None
        self.init_database()
        # 在服务启动时检查ChromeDriver
        self.init_chromedriver()
        
    def find_existing_chromedriver(self):
        """查找已存在的ChromeDriver"""
        try:
            # 在drivers目录中查找chromedriver可执行文件
            if os.path.exists('drivers'):
                for root, dirs, files in os.walk('drivers'):
                    for file in files:
                        if file.startswith('chromedriver') and not file.endswith('.zip'):
                            driver_path = os.path.join(root, file)
                            if os.path.isfile(driver_path) and os.access(driver_path, os.X_OK):
                                logger.info(f"找到已存在的ChromeDriver: {driver_path}")
                                return driver_path
            logger.info("未找到已存在的ChromeDriver，将在需要时下载")
            return None
        except Exception as e:
            logger.error(f"查找ChromeDriver时出错: {str(e)}")
            return None
        
    def load_config(self):
        """加载配置文件"""
        default_config = {
            'check_interval': 60,  # 检查间隔（分钟）
            'concurrent_workers': 5,  # 并发检查数量
            'notification': {
                'bark_urls': [],  # 支持多个Bark地址
                'serverchan_keys': [],  # 支持多个Server酱密钥
                # 保持向后兼容
                'bark_url': '',
                'serverchan_key': ''
            },
            'translation': {
                'api_key': '',
                'api_url': '',
                'enabled': False
            },
            'keyword_filters': {
                'enabled': False,
                'rules': []
            },
            'news_sites': [
                {
                    'name': 'BBC News',
                    'url': 'https://www.bbc.com/news',
                    'site_type': 'html',
                    'title_selector': 'h3[data-testid="card-headline"]',
                    'date_selector': 'time',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': 'CNN',
                    'url': 'https://edition.cnn.com/',
                    'site_type': 'html',
                    'title_selector': '.container__headline-text',
                    'date_selector': '.timestamp',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': 'BBC RSS',
                    'url': 'http://feeds.bbci.co.uk/news/rss.xml',
                    'site_type': 'rss',
                    'enabled': True
                },
                {
                    'name': 'Reuters RSS',
                    'url': 'https://www.reuters.com/rssFeed/worldNews',
                    'site_type': 'rss',
                    'enabled': True
                }
            ]
        }
        
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
                # 合并默认配置
                for key in default_config:
                    if key not in config:
                        config[key] = default_config[key]
                
                # 向后兼容：将单个地址转换为数组格式
                if 'notification' in config:
                    notification = config['notification']
                    
                    # 处理Bark URL
                    if 'bark_urls' not in notification:
                        notification['bark_urls'] = []
                    if 'bark_url' in notification and notification['bark_url']:
                        if notification['bark_url'] not in notification['bark_urls']:
                            notification['bark_urls'].append(notification['bark_url'])
                    
                    # 处理Server酱密钥
                    if 'serverchan_keys' not in notification:
                        notification['serverchan_keys'] = []
                    if 'serverchan_key' in notification and notification['serverchan_key']:
                        if notification['serverchan_key'] not in notification['serverchan_keys']:
                            notification['serverchan_keys'].append(notification['serverchan_key'])
                
                return config
        except FileNotFoundError:
            self.save_config(default_config)
            return default_config
    
    def save_config(self, config=None):
        """保存配置文件"""
        if config is None:
            config = self.config
        with open('config.json', 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    
    def init_database(self):
        """初始化数据库"""
        conn = sqlite3.connect('news.db')
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_name TEXT,
                title TEXT,
                translated_title TEXT,
                url TEXT,
                date TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(site_name, title, url)
            )
        ''')
        conn.commit()
        conn.close()
    
    def init_chromedriver(self):
        """初始化ChromeDriver，只在服务启动时检查一次"""
        try:
            # 首先检查是否已存在ChromeDriver
            for root, dirs, files in os.walk('drivers'):
                for file in files:
                    if file.startswith('chromedriver') and not file.endswith('.zip'):
                        driver_candidate = os.path.join(root, file)
                        if os.path.exists(driver_candidate):
                            # 检查文件是否可执行
                            if os.access(driver_candidate, os.X_OK):
                                self.driver_path = driver_candidate
                                logger.info(f"找到已存在的ChromeDriver: {self.driver_path}")
                                return True
                            else:
                                # 给予执行权限
                                os.chmod(driver_candidate, 0o755)
                                self.driver_path = driver_candidate
                                logger.info(f"找到ChromeDriver并设置执行权限: {self.driver_path}")
                                return True
            
            # 如果没有找到，则下载
            logger.info("未找到ChromeDriver，开始下载...")
            if self.download_chromedriver():
                logger.info("ChromeDriver初始化完成")
                return True
            else:
                logger.error("ChromeDriver初始化失败")
                return False
                
        except Exception as e:
            logger.error(f"初始化ChromeDriver时出错: {str(e)}")
            return False
    
    def get_platform_info(self):
        """获取当前平台信息"""
        system = platform.system().lower()
        machine = platform.machine().lower()
        
        if system == 'darwin':  # macOS
            if 'arm' in machine or 'aarch64' in machine:
                return 'mac-arm64'
            else:
                return 'mac-x64'
        elif system == 'linux':
            return 'linux64'
        elif system == 'windows':
            if '64' in machine:
                return 'win64'
            else:
                return 'win32'
        else:
            return 'linux64'  # 默认
    
    def get_chrome_version(self):
        """获取本地Chrome浏览器版本"""
        try:
            system = platform.system().lower()
            
            if system == 'darwin':  # macOS
                import subprocess
                import plistlib
                
                # 尝试从Chrome应用包中读取版本信息
                chrome_path = '/Applications/Google Chrome.app/Contents/Info.plist'
                if os.path.exists(chrome_path):
                    with open(chrome_path, 'rb') as f:
                        plist = plistlib.load(f)
                        version = plist.get('CFBundleShortVersionString', '')
                        if version:
                            logger.info(f"检测到Chrome版本: {version}")
                            return version
                
                # 备用方法：尝试执行Chrome命令
                try:
                    result = subprocess.run([
                        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                        '--version'
                    ], capture_output=True, text=True, timeout=10)
                    if result.returncode == 0:
                        version_line = result.stdout.strip()
                        # 解析版本号，格式通常是 "Google Chrome 120.0.6099.109"
                        version = version_line.split()[-1]
                        logger.info(f"检测到Chrome版本: {version}")
                        return version
                except:
                    pass
                    
            elif system == 'linux':
                import subprocess
                try:
                    # 尝试多个可能的Chrome命令
                    for cmd in ['google-chrome', 'google-chrome-stable', 'chromium-browser']:
                        try:
                            result = subprocess.run([cmd, '--version'], 
                                                   capture_output=True, text=True, timeout=10)
                            if result.returncode == 0:
                                version_line = result.stdout.strip()
                                version = version_line.split()[-1]
                                logger.info(f"检测到Chrome版本: {version}")
                                return version
                        except:
                            continue
                except:
                    pass
                    
            elif system == 'windows':
                import subprocess
                import winreg
                
                # 尝试从注册表读取版本
                try:
                    key_paths = [
                        r'SOFTWARE\Google\Chrome\BLBeacon',
                        r'SOFTWARE\WOW6432Node\Google\Chrome\BLBeacon'
                    ]
                    
                    for key_path in key_paths:
                        try:
                            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                                version, _ = winreg.QueryValueEx(key, 'version')
                                logger.info(f"检测到Chrome版本: {version}")
                                return version
                        except:
                            continue
                except:
                    pass
                
                # 备用方法：尝试执行Chrome命令
                try:
                    chrome_paths = [
                        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe'
                    ]
                    
                    for chrome_path in chrome_paths:
                        if os.path.exists(chrome_path):
                            result = subprocess.run([chrome_path, '--version'], 
                                                   capture_output=True, text=True, timeout=10)
                            if result.returncode == 0:
                                version_line = result.stdout.strip()
                                version = version_line.split()[-1]
                                logger.info(f"检测到Chrome版本: {version}")
                                return version
                except:
                    pass
                    
        except Exception as e:
            logger.warning(f"无法检测Chrome版本: {str(e)}")
        
        logger.warning("无法检测到Chrome版本，将使用最新版本")
        return None
    
    def download_chromedriver(self):
        """下载匹配的ChromeDriver"""
        try:
            logger.info("开始下载ChromeDriver...")
            
            # 获取本地Chrome版本
            local_chrome_version = self.get_chrome_version()
            
            # 获取Chrome版本信息
            response = requests.get('https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json')
            versions_data = response.json()
            
            platform_name = self.get_platform_info()
            logger.info(f"检测到平台: {platform_name}")
            
            # 查找匹配的版本
            target_version = None
            
            if local_chrome_version:
                # 首先尝试找到完全匹配的版本
                for version_info in versions_data['versions']:
                    if version_info['version'] == local_chrome_version:
                        if 'chromedriver' in version_info['downloads']:
                            for download in version_info['downloads']['chromedriver']:
                                if download['platform'] == platform_name:
                                    target_version = version_info
                                    logger.info(f"找到完全匹配的ChromeDriver版本: {local_chrome_version}")
                                    break
                        if target_version:
                            break
                
                # 如果没有完全匹配，尝试找到主版本号匹配的最新版本
                if not target_version:
                    local_major_version = local_chrome_version.split('.')[0]
                    logger.info(f"寻找主版本号 {local_major_version} 匹配的ChromeDriver")
                    
                    for version_info in reversed(versions_data['versions']):
                        version_major = version_info['version'].split('.')[0]
                        if version_major == local_major_version:
                            if 'chromedriver' in version_info['downloads']:
                                for download in version_info['downloads']['chromedriver']:
                                    if download['platform'] == platform_name:
                                        target_version = version_info
                                        logger.info(f"找到主版本号匹配的ChromeDriver版本: {version_info['version']}")
                                        break
                            if target_version:
                                break
            
            # 如果仍然没有找到匹配版本，使用最新版本
            if not target_version:
                logger.warning("未找到匹配的ChromeDriver版本，使用最新版本")
                for version_info in reversed(versions_data['versions']):
                    if 'chromedriver' in version_info['downloads']:
                        for download in version_info['downloads']['chromedriver']:
                            if download['platform'] == platform_name:
                                target_version = version_info
                                break
                        if target_version:
                            break
            
            if not target_version:
                raise Exception(f"未找到适合平台 {platform_name} 的ChromeDriver版本")
            
            version_num = target_version['version']
            logger.info(f"将下载ChromeDriver版本: {version_num}")
            
            # 下载ChromeDriver
            chromedriver_url = f"https://storage.googleapis.com/chrome-for-testing-public/{version_num}/{platform_name}/chromedriver-{platform_name}.zip"
            
            logger.info(f"下载ChromeDriver: {chromedriver_url}")
            response = requests.get(chromedriver_url)
            response.raise_for_status()
            
            # 保存并解压
            os.makedirs('drivers', exist_ok=True)
            zip_path = 'drivers/chromedriver.zip'
            
            with open(zip_path, 'wb') as f:
                f.write(response.content)
            
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall('drivers')
            
            # 查找chromedriver可执行文件
            for root, dirs, files in os.walk('drivers'):
                for file in files:
                    if file.startswith('chromedriver') and not file.endswith('.zip'):
                        self.driver_path = os.path.join(root, file)
                        # 给予执行权限
                        os.chmod(self.driver_path, 0o755)
                        break
                if self.driver_path:
                    break
            
            os.remove(zip_path)
            logger.info(f"ChromeDriver下载完成: {self.driver_path}")
            return True
            
        except Exception as e:
            logger.error(f"下载ChromeDriver失败: {str(e)}")
            return False
    
    def create_webdriver(self):
        """创建Chrome WebDriver"""
        if not self.driver_path or not os.path.exists(self.driver_path):
            raise Exception("ChromeDriver未初始化或文件不存在，请检查服务启动日志")
        
        chrome_options = Options()
        # 确保Chrome窗口在前台显示，以便正确加载动态内容
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument('--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        # 禁用一些可能影响内容加载的功能
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        service = Service(self.driver_path)
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        # 设置窗口位置和大小，确保窗口可见
        driver.set_window_position(0, 0)
        driver.set_window_size(1920, 1080)
        
        # 移除webdriver标识
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        return driver
    
    def translate_text(self, text):
        """翻译文本"""
        if not self.config['translation']['enabled']:
            return text
        
        try:
            # 使用DeepLX翻译API
            api_url = self.config['translation']['api_url']
            api_key = self.config['translation']['api_key']
            
            # DeepLX API请求格式
            headers = {
                'Content-Type': 'application/json'
            }
            
            # 如果有API密钥，添加到请求头
            if api_key:
                headers['Authorization'] = f'Bearer {api_key}'
            
            data = {
                'text': text,
                'source_lang': 'EN',  # 源语言：英文
                'target_lang': 'ZH'   # 目标语言：中文
            }
            
            response = requests.post(api_url, json=data, headers=headers, timeout=10)
            
            if response.status_code == 200:
                result = response.json()
                # 根据DeepLX API返回格式解析翻译结果
                if result.get('code') == 200:
                    return result.get('data', text)
                else:
                    logger.warning(f"翻译API返回错误: {result}")
            else:
                logger.error(f"翻译API请求失败，状态码: {response.status_code}")
        except Exception as e:
            logger.error(f"翻译失败: {str(e)}")
        
        return text

    def match_keyword_rules(self, news_item):
        """根据关键词规则判断是否应该推送该条新闻

        规则逻辑：
        - 如果 keyword_filters 未启用或没有规则，返回 True（全部推送）
        - 遍历所有启用的规则，任一规则匹配即返回 True（规则间是 OR 关系）
        - 每条规则内部根据 mode 字段判断：'or' 任意关键词匹配，'and' 所有关键词同时匹配
        - 匹配范围：标题（title）和翻译标题（translated_title），大小写不敏感
        """
        filters = self.config.get('keyword_filters', {})
        if not filters.get('enabled', False):
            return True

        rules = filters.get('rules', [])
        active_rules = [r for r in rules if r.get('enabled', True) and r.get('keywords')]
        if not active_rules:
            return True

        title = (news_item.get('title', '') or '').lower()
        translated_title = (news_item.get('translated_title', '') or '').lower()
        match_text = f"{title} {translated_title}"

        for rule in active_rules:
            keywords = [kw.lower() for kw in rule['keywords'] if kw.strip()]
            if not keywords:
                continue

            mode = rule.get('mode', 'or')
            if mode == 'or' and any(kw in match_text for kw in keywords):
                return True
            if mode == 'and' and all(kw in match_text for kw in keywords):
                return True

        return False

    def scrape_rss_site(self, site_config):
        """抓取RSS新闻网站"""
        if not site_config.get('enabled', True):
            return []
        
        try:
            logger.info(f"开始抓取RSS {site_config['name']}")
            
            # 使用feedparser解析RSS
            feed = feedparser.parse(site_config['url'])
            
            if feed.bozo:
                logger.warning(f"RSS解析可能有问题: {site_config['name']}")
            
            news_items = []
            
            # 获取RSS条目
            for entry in feed.entries[:10]:  # 限制获取前10条
                title = entry.get('title', '').strip()
                if not title:
                    continue
                
                # 获取链接
                url = entry.get('link', site_config['url'])
                
                # 获取日期
                date_str = datetime.now().strftime('%Y-%m-%d')
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    try:
                        date_str = datetime(*entry.published_parsed[:6]).strftime('%Y-%m-%d')
                    except:
                        pass
                elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                    try:
                        date_str = datetime(*entry.updated_parsed[:6]).strftime('%Y-%m-%d')
                    except:
                        pass
                
                # 翻译标题
                translated_title = self.translate_text(title)
                
                news_items.append({
                    'site_name': site_config['name'],
                    'title': title,
                    'translated_title': translated_title,
                    'url': url,
                    'date': date_str
                })
            
            logger.info(f"从RSS {site_config['name']} 获取到 {len(news_items)} 条新闻")
            return news_items
            
        except Exception as e:
            logger.error(f"抓取RSS {site_config['name']} 失败: {str(e)}")
            return []
    
    def scrape_html_site(self, site_config):
        """抓取HTML新闻网站"""
        if not site_config.get('enabled', True):
            logger.debug(f"跳过已禁用的站点: {site_config['name']}")
            return []
        
        driver = None
        try:
            # 创建WebDriver
            logger.debug(f"正在为 {site_config['name']} 创建WebDriver")
            driver = self.create_webdriver()
            
            logger.info(f"开始抓取HTML {site_config['name']} - URL: {site_config['url']}")
            logger.debug(f"使用选择器: {site_config['title_selector']}")
            
            # 访问页面
            start_time = time.time()
            driver.get(site_config['url'])
            logger.debug(f"页面请求完成，耗时: {time.time() - start_time:.2f}秒")
            
            # 等待页面加载
            logger.debug("等待页面body元素加载...")
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            logger.debug("页面body元素已加载")
            
            # 等待JavaScript执行完成
            logger.debug("等待JavaScript执行完成...")
            WebDriverWait(driver, 15).until(
                lambda driver: driver.execute_script("return document.readyState") == "complete"
            )
            logger.debug("页面readyState已完成")
            
            # 额外等待，确保动态内容加载完成
            initial_wait = 10
            logger.debug(f"初始等待{initial_wait}秒以确保动态内容开始加载")
            time.sleep(initial_wait)
            
            # 动态检测页面内容变化
            logger.debug("开始动态检测页面内容变化...")
            previous_content_length = 0
            stable_count = 0
            max_wait_cycles = 6  # 最多等待6个周期
            
            for cycle in range(max_wait_cycles):
                current_content_length = len(driver.page_source)
                logger.debug(f"第{cycle+1}次检测，页面内容长度: {current_content_length}")
                
                if current_content_length == previous_content_length:
                    stable_count += 1
                    logger.debug(f"页面内容稳定次数: {stable_count}")
                    if stable_count >= 2:  # 连续2次内容长度不变，认为加载完成
                        logger.debug("页面内容已稳定，停止等待")
                        break
                else:
                    stable_count = 0
                    logger.debug("页面内容仍在变化，继续等待")
                
                previous_content_length = current_content_length
                
                # 每次等待时模拟滚动
                try:
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight/3);")
                    time.sleep(1)
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight*2/3);")
                    time.sleep(1)
                    driver.execute_script("window.scrollTo(0, 0);")
                    logger.debug("模拟滚动完成")
                except Exception as scroll_e:
                    logger.debug(f"模拟滚动失败: {str(scroll_e)}")
                
                time.sleep(3)  # 每次检测间隔3秒
            
            # 尝试滚动页面以触发懒加载
            logger.debug("尝试滚动页面以触发懒加载内容...")
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
                time.sleep(3)
                driver.execute_script("window.scrollTo(0, 0);")
                time.sleep(2)
                logger.debug("页面滚动完成")
            except Exception as scroll_e:
                logger.debug(f"页面滚动失败: {str(scroll_e)}")
            
            # 最终等待
            final_wait = 5
            logger.debug(f"最终等待{final_wait}秒")
            time.sleep(final_wait)
            
            # 获取页面信息
            page_title = driver.title
            page_url = driver.current_url
            page_source_length = len(driver.page_source)
            logger.debug(f"页面标题: {page_title}")
            logger.debug(f"当前URL: {page_url}")
            logger.debug(f"页面源码长度: {page_source_length} 字符")
            
            # 检查页面是否正常加载
            if page_source_length < 1000:
                logger.warning(f"页面源码过短({page_source_length}字符)，可能加载不完整")
            
            # 获取页面源码
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            
            # 尝试等待特定的新闻标题元素加载
            logger.debug(f"尝试等待新闻标题元素加载，选择器: {site_config['title_selector']}")
            try:
                # 等待至少一个标题元素出现
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, site_config['title_selector']))
                )
                logger.debug("检测到标题元素已加载")
                
                # 再等待一段时间确保所有元素都加载完成，同时模拟滚动
                try:
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
                    time.sleep(2)
                    driver.execute_script("window.scrollTo(0, 0);")
                    logger.debug("等待期间模拟滚动完成")
                except Exception as scroll_e:
                    logger.debug(f"等待期间模拟滚动失败: {str(scroll_e)}")
                
                time.sleep(3)
                logger.debug("额外等待3秒确保所有标题元素加载完成")
                
            except TimeoutException:
                logger.warning(f"等待标题元素超时，选择器可能不正确: {site_config['title_selector']}")
                # 继续执行，可能页面结构有变化但仍有内容
            
            # 重新获取最新的页面源码
            logger.debug("重新获取页面源码以确保包含最新内容")
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            
            # 查找新闻标题
            news_items = []
            logger.debug(f"开始查找标题元素，选择器: {site_config['title_selector']}")
            title_elements = soup.select(site_config['title_selector'])
            logger.debug(f"找到 {len(title_elements)} 个标题元素")
            
            if len(title_elements) == 0:
                logger.warning(f"未找到任何标题元素，可能选择器不正确或页面结构已变化")
                # 输出页面的一些基本信息用于调试
                body_text_length = len(soup.get_text()) if soup.body else 0
                logger.debug(f"页面文本内容长度: {body_text_length} 字符")
                
                logger.debug("未找到标题元素，输出页面结构信息用于调试")
                
                # 输出页面的基本结构信息
                if soup.body:
                    all_links = soup.find_all('a', href=True)
                    logger.debug(f"页面包含 {len(all_links)} 个链接")
                    
                    # 查找可能包含新闻的div或section
                    news_containers = soup.find_all(['div', 'section', 'article'], class_=lambda x: x and any(keyword in x.lower() for keyword in ['news', 'article', 'post', 'item', 'story']))
                    logger.debug(f"找到 {len(news_containers)} 个可能的新闻容器")
                    
                    # 显示页面的主要标签统计
                    tag_counts = {}
                    for tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                        tag_name = tag.name
                        tag_counts[tag_name] = tag_counts.get(tag_name, 0) + 1
                    logger.debug(f"页面标题标签统计: {tag_counts}")
            
            for i, element in enumerate(title_elements[:10]):  # 限制获取前10条
                logger.debug(f"处理第 {i+1} 个标题元素")
                title = element.get_text().strip()
                if not title:
                    logger.debug(f"第 {i+1} 个元素标题为空，跳过")
                    continue
                
                logger.debug(f"提取到标题: {title[:50]}{'...' if len(title) > 50 else ''}")
                
                # 获取链接
                url = site_config['url']
                link_element = element.find('a') or element.find_parent('a')
                if link_element and link_element.get('href'):
                    url = urljoin(site_config['url'], link_element['href'])
                    logger.debug(f"提取到链接: {url}")
                else:
                    logger.debug("未找到有效链接，使用站点主页")
                
                # 获取日期（可选）
                date_str = datetime.now().strftime('%Y-%m-%d')
                try:
                    if site_config.get('date_selector'):
                        date_element = element.find_next(site_config['date_selector'])
                        if date_element:
                            date_str = date_element.get_text().strip()
                            logger.debug(f"提取到日期: {date_str}")
                        else:
                            logger.debug("未找到日期元素")
                except Exception as date_e:
                    logger.debug(f"日期提取失败: {str(date_e)}")
                
                # 翻译标题
                logger.debug(f"开始翻译标题: {title[:30]}...")
                translated_title = self.translate_text(title)
                if translated_title != title:
                    logger.debug(f"翻译结果: {translated_title[:30]}...")
                else:
                    logger.debug("标题未翻译或翻译失败")
                
                news_items.append({
                    'site_name': site_config['name'],
                    'title': title,
                    'translated_title': translated_title,
                    'url': url,
                    'date': date_str
                })
            
            logger.info(f"从HTML {site_config['name']} 获取到 {len(news_items)} 条新闻")
            if len(news_items) > 0:
                logger.debug(f"第一条新闻标题: {news_items[0]['title'][:50]}...")
            
            return news_items
            
        except TimeoutException as e:
            logger.error(f"抓取HTML {site_config['name']} 超时: 页面加载时间超过10秒")
            logger.debug(f"超时详情: {str(e)}")
            return []
        except WebDriverException as e:
            logger.error(f"抓取HTML {site_config['name']} WebDriver错误: {str(e)}")
            return []
        except Exception as e:
            logger.error(f"抓取HTML {site_config['name']} 失败: {str(e)}")
            logger.debug(f"错误详情: {type(e).__name__}: {str(e)}")
            import traceback
            logger.debug(f"错误堆栈: {traceback.format_exc()}")
            return []
        finally:
            if driver:
                logger.debug(f"关闭 {site_config['name']} 的WebDriver")
                driver.quit()
    
    def scrape_news_site(self, site_config):
        """抓取新闻网站（自动判断类型）"""
        site_type = site_config.get('site_type', 'html').lower()
        
        if site_type == 'rss':
            return self.scrape_rss_site(site_config)
        else:
            return self.scrape_html_site(site_config)
    
    def save_news(self, news_items):
        """保存新闻到数据库"""
        if not news_items:
            return 0, []
        
        conn = sqlite3.connect('news.db')
        cursor = conn.cursor()
        new_count = 0
        new_news_list = []
        
        for item in news_items:
            try:
                cursor.execute('''
                    INSERT OR IGNORE INTO news 
                    (site_name, title, translated_title, url, date)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    item['site_name'],
                    item['title'],
                    item['translated_title'],
                    item['url'],
                    item['date']
                ))
                if cursor.rowcount > 0:
                    new_count += 1
                    new_news_list.append(item)
            except Exception as e:
                logger.error(f"保存新闻失败: {str(e)}")
        
        conn.commit()
        conn.close()
        return new_count, new_news_list
    
    def send_notification(self, message):
        """发送通知"""
        notification_config = self.config['notification']
        
        # 发送Bark通知到所有配置的地址
        bark_urls = notification_config.get('bark_urls', [])
        # 向后兼容：如果有单个bark_url且不在数组中，也发送
        if notification_config.get('bark_url') and notification_config['bark_url'] not in bark_urls:
            bark_urls.append(notification_config['bark_url'])
        
        for bark_url in bark_urls:
            if bark_url.strip():
                try:
                    full_url = f"{bark_url.strip()}/{message}"
                    response = requests.get(full_url, timeout=10)
                    if response.status_code == 200:
                        logger.info(f"Bark通知发送成功: {bark_url}")
                    else:
                        logger.warning(f"Bark通知发送失败，状态码: {response.status_code}, URL: {bark_url}")
                except Exception as e:
                    logger.error(f"Bark通知发送失败: {bark_url}, 错误: {str(e)}")
        
        # 发送Server酱通知到所有配置的密钥
        serverchan_keys = notification_config.get('serverchan_keys', [])
        # 向后兼容：如果有单个serverchan_key且不在数组中，也发送
        if notification_config.get('serverchan_key') and notification_config['serverchan_key'] not in serverchan_keys:
            serverchan_keys.append(notification_config['serverchan_key'])
        
        for serverchan_key in serverchan_keys:
            if serverchan_key.strip():
                try:
                    serverchan_url = f"https://sctapi.ftqq.com/{serverchan_key.strip()}.send"
                    response = requests.post(serverchan_url, {
                        'title': '新闻更新通知',
                        'desp': message
                    }, timeout=10)
                    if response.status_code == 200:
                        logger.info(f"Server酱通知发送成功: {serverchan_key}")
                    else:
                        logger.warning(f"Server酱通知发送失败，状态码: {response.status_code}, 密钥: {serverchan_key}")
                except Exception as e:
                    logger.error(f"Server酱通知发送失败: {serverchan_key}, 错误: {str(e)}")
    
    def send_notification_with_details(self, new_news_list):
        """发送包含新闻详情的通知"""
        if not new_news_list:
            return
        
        # 构建详细的通知消息
        message_lines = [f"📰 发现 {len(new_news_list)} 条新新闻：\n"]
        
        for i, news in enumerate(new_news_list[:5], 1):  # 最多显示5条新闻
            site_name = news.get('site_name', '未知来源')
            title = news.get('title', '无标题')
            translated_title = news.get('translated_title', '')
            url = news.get('url', '')
            
            message_lines.append(f"🔸 {i}. 【{site_name}】")
            message_lines.append(f"   原标题: {title}")
            if translated_title and translated_title != title:
                message_lines.append(f"   中文翻译: {translated_title}")
            if url:
                message_lines.append(f"   链接: {url}")
            message_lines.append("")  # 空行分隔
        
        if len(new_news_list) > 5:
            message_lines.append(f"... 还有 {len(new_news_list) - 5} 条新闻")
        
        message = "\n".join(message_lines)
        
        # 发送通知
        notification_config = self.config['notification']
        
        # 发送Bark通知到所有配置的地址 - 每条新闻单独发送
        bark_urls = notification_config.get('bark_urls', [])
        # 向后兼容：如果有单个bark_url且不在数组中，也发送
        if notification_config.get('bark_url') and notification_config['bark_url'] not in bark_urls:
            bark_urls.append(notification_config['bark_url'])
        
        for bark_url in bark_urls:
            if bark_url.strip():
                # 为每条新闻发送单独的通知
                for news in new_news_list:
                    try:
                        site_name = news.get('site_name', '未知来源')
                        title = news.get('title', '无标题')
                        translated_title = news.get('translated_title', '')
                        url = news.get('url', '')
                        
                        # 构建推送标题和内容
                        push_title = f"📰 {site_name}"
                        
                        # 构建推送内容
                        content_lines = [f"{title}"]
                        if translated_title and translated_title != title:
                            content_lines.append(f"{translated_title}")
                        push_content = "\n".join(content_lines)
                        
                        # URL编码
                        import urllib.parse
                        encoded_title = urllib.parse.quote(push_title)
                        encoded_content = urllib.parse.quote(push_content)
                        
                        # 构建完整的Bark URL
                        if url:
                            encoded_url = urllib.parse.quote(url)
                            full_url = f"{bark_url.strip()}/{encoded_title}/{encoded_content}?url={encoded_url}"
                        else:
                            full_url = f"{bark_url.strip()}/{encoded_title}/{encoded_content}"
                        
                        response = requests.get(full_url, timeout=10)
                        
                        if response.status_code == 200:
                            logger.info(f"Bark通知发送成功: {bark_url} - {title[:30]}...")
                        else:
                            logger.warning(f"Bark通知发送失败，状态码: {response.status_code}, URL: {bark_url}")
                    except Exception as e:
                        logger.error(f"Bark通知发送失败: {bark_url}, 新闻: {title[:30]}..., 错误: {str(e)}")
        
        # 发送Server酱通知到所有配置的密钥
        serverchan_keys = notification_config.get('serverchan_keys', [])
        # 向后兼容：如果有单个serverchan_key且不在数组中，也发送
        if notification_config.get('serverchan_key') and notification_config['serverchan_key'] not in serverchan_keys:
            serverchan_keys.append(notification_config['serverchan_key'])
        
        for serverchan_key in serverchan_keys:
            if serverchan_key.strip():
                try:
                    serverchan_url = f"https://sctapi.ftqq.com/{serverchan_key.strip()}.send"
                    response = requests.post(serverchan_url, {
                        'title': f'📰 新闻更新通知 ({len(new_news_list)}条)',
                        'desp': message
                    }, timeout=10)
                    if response.status_code == 200:
                        logger.info(f"Server酱详细通知发送成功: {serverchan_key}")
                    else:
                        logger.warning(f"Server酱详细通知发送失败，状态码: {response.status_code}, 密钥: {serverchan_key}")
                except Exception as e:
                    logger.error(f"Server酱详细通知发送失败: {serverchan_key}, 错误: {str(e)}")
    
    def clean_log_file(self):
        """清理日志文件，保留最近的日志内容"""
        try:
            log_file_path = 'news_monitor.log'
            if not os.path.exists(log_file_path):
                return
            
            # 获取文件大小（字节）
            file_size = os.path.getsize(log_file_path)
            max_size = 10 * 1024 * 1024  # 10MB
            
            if file_size > max_size:
                # 读取文件内容
                with open(log_file_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                
                # 保留最后的1000行
                keep_lines = 1000
                if len(lines) > keep_lines:
                    # 保留最后的行数
                    lines_to_keep = lines[-keep_lines:]
                    
                    # 写回文件
                    with open(log_file_path, 'w', encoding='utf-8') as f:
                        f.writelines(lines_to_keep)
                    
                    logger.info(f"日志文件已清理，保留最后 {keep_lines} 行，原文件大小: {file_size/1024/1024:.2f}MB")
                    
        except Exception as e:
            logger.error(f"清理日志文件失败: {str(e)}")
    
    def check_news_updates(self):
        """检查新闻更新"""
        if self.is_running:
            logger.info("新闻检查任务已在运行中")
            return
        
        self.is_running = True
        self.last_check_time = datetime.now()
        try:
            # 清理日志文件
            self.clean_log_file()
            logger.info("开始检查新闻更新...")
            all_news = []
            
            # 获取启用的新闻站点
            enabled_sites = [site for site in self.config['news_sites'] if site.get('enabled', True)]
            
            if not enabled_sites:
                logger.info("没有启用的新闻站点")
                return
            
            # 获取并发工作线程数量
            max_workers = self.config.get('concurrent_workers', 5)
            logger.info(f"使用 {max_workers} 个并发线程检查 {len(enabled_sites)} 个新闻站点")
            
            # 使用线程池并发处理
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # 提交所有任务
                future_to_site = {executor.submit(self.scrape_news_site, site): site for site in enabled_sites}
                
                # 收集结果
                for future in as_completed(future_to_site):
                    site = future_to_site[future]
                    try:
                        news_items = future.result()
                        all_news.extend(news_items)
                        logger.info(f"完成检查站点: {site.get('name', 'Unknown')}，获取 {len(news_items)} 条新闻")
                    except Exception as e:
                        logger.error(f"检查站点 {site.get('name', 'Unknown')} 失败: {str(e)}")
            
            new_count, new_news_list = self.save_news(all_news)

            if new_count > 0:
                logger.info(f"发现 {new_count} 条新新闻")

                # 关键词筛选
                filtered_news = [n for n in new_news_list if self.match_keyword_rules(n)]

                if filtered_news:
                    logger.info(f"{len(filtered_news)} 条新闻匹配关键词规则，开始推送")
                    self.send_notification_with_details(filtered_news)
                else:
                    logger.info(f"共 {new_count} 条新新闻，但无匹配关键词规则，跳过推送")
            else:
                logger.info("没有发现新新闻")
                
        except Exception as e:
            logger.error(f"检查新闻更新失败: {str(e)}")
        finally:
            self.is_running = False
    
    def start_scheduler(self):
        """启动定时任务"""
        # 设置初始的last_check_time为当前时间，这样倒计时就会从完整的间隔开始
        self.last_check_time = datetime.now()
        
        schedule.every(self.config['check_interval']).minutes.do(self.check_news_updates)
        
        def run_scheduler():
            while True:
                schedule.run_pending()
                time.sleep(1)
        
        scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
        scheduler_thread.start()
        logger.info(f"定时任务已启动，检查间隔: {self.config['check_interval']} 分钟")

# 全局实例
monitor = NewsMonitor()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/config')
def config_page():
    return render_template('config.html', config=monitor.config)

@app.route('/logs')
def logs_page():
    return render_template('logs.html')

@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    if request.method == 'GET':
        return jsonify(monitor.config)
    
    elif request.method == 'POST':
        try:
            new_config = request.json
            monitor.config = new_config
            monitor.save_config()
            return jsonify({'success': True, 'message': '配置保存成功'})
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)})

@app.route('/api/news')
def api_news():
    try:
        conn = sqlite3.connect('news.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT site_name, title, translated_title, url, date, created_at
            FROM news
            ORDER BY created_at DESC

        ''')
        news = cursor.fetchall()
        conn.close()
        
        news_list = []
        for item in news:
            news_list.append({
                'site_name': item[0],
                'title': item[1],
                'translated_title': item[2],
                'url': item[3],
                'date': item[4],
                'created_at': item[5]
            })
        
        return jsonify(news_list)
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/api/check_now')
def api_check_now():
    try:
        threading.Thread(target=monitor.check_news_updates, daemon=True).start()
        return jsonify({'success': True, 'message': '开始检查新闻更新'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/restart', methods=['POST'])
def api_restart():
    """重启服务以应用新配置"""
    try:
        def restart_server():
            import time
            import os
            import sys
            time.sleep(1)  # 给响应时间返回
            logger.info('正在重启服务以应用新配置...')
            os.execv(sys.executable, ['python'] + sys.argv)
        
        # 在后台线程中执行重启
        threading.Thread(target=restart_server, daemon=True).start()
        return jsonify({'success': True, 'message': '服务正在重启...'})
    except Exception as e:
        logger.error(f'重启服务失败: {str(e)}')
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/logs')
def api_logs():
    try:
        with open('news_monitor.log', 'r', encoding='utf-8') as f:
            logs = f.readlines()[-100:]  # 获取最后100行
        return jsonify({'logs': logs})
    except Exception as e:
        return jsonify({'logs': [f'读取日志失败: {str(e)}']})

@app.route('/api/status')
def api_status():
    # 计算下次检查时间
    next_check_time = None
    if hasattr(monitor, 'last_check_time') and monitor.last_check_time:
        next_check_time = monitor.last_check_time + timedelta(minutes=monitor.config['check_interval'])
    else:
        # 如果没有上次检查时间，使用当前时间加上检查间隔
        next_check_time = datetime.now() + timedelta(minutes=monitor.config['check_interval'])
    
    return jsonify({
        'is_running': monitor.is_running,
        'driver_available': monitor.driver_path is not None and os.path.exists(monitor.driver_path) if monitor.driver_path else False,
        'config_loaded': monitor.config is not None,
        'check_interval': monitor.config['check_interval'],
        'next_check_time': next_check_time.isoformat() if next_check_time else None,
        'last_check_time': monitor.last_check_time.isoformat() if hasattr(monitor, 'last_check_time') and monitor.last_check_time else None
    })

@app.route('/api/test_notification', methods=['POST'])
def api_test_notification():
    """测试通知功能，发送最新5条新闻"""
    try:
        # 从数据库获取最新5条新闻
        conn = sqlite3.connect('news.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT site_name, title, translated_title, url, date, created_at
            FROM news
            ORDER BY created_at DESC
            LIMIT 5
        ''')
        news_rows = cursor.fetchall()
        conn.close()
        
        if not news_rows:
            return jsonify({
                'success': False, 
                'message': '数据库中没有新闻数据，请先运行新闻检查或添加新闻源'
            })
        
        # 构造新闻列表
        test_news_list = []
        for row in news_rows:
            test_news_list.append({
                'site_name': row[0],
                'title': row[1],
                'translated_title': row[2],
                'url': row[3],
                'date': row[4],
                'created_at': row[5]
            })
        
        # 发送测试通知
        monitor.send_notification_with_details(test_news_list)
        
        return jsonify({
            'success': True,
            'message': f'测试通知已发送！包含 {len(test_news_list)} 条新闻'
        })
        
    except Exception as e:
        logger.error(f"测试通知发送失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'发送失败: {str(e)}'
        })

def find_available_port(start_port=5000, max_attempts=10):
    """查找可用端口"""
    import socket
    
    for port in range(start_port, start_port + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('0.0.0.0', port))
                logger.info(f'找到可用端口: {port}')
                return port
        except OSError:
            logger.info(f'端口 {port} 已被占用，尝试下一个端口')
            continue
    
    raise RuntimeError(f'无法找到可用端口，已尝试端口范围: {start_port}-{start_port + max_attempts - 1}')

if __name__ == '__main__':
    # 启动定时任务
    monitor.start_scheduler()
    
    # 查找可用端口并启动Flask应用
    try:
        available_port = find_available_port()
        logger.info(f'启动Flask应用，端口: {available_port}')
        
        # 自动打开浏览器
        def open_browser():
            import webbrowser
            import time
            time.sleep(2)  # 等待服务器启动
            url = f'http://localhost:{available_port}'
            logger.info(f'自动打开浏览器: {url}')
            webbrowser.open(url)
        
        # 在后台线程中打开浏览器
        threading.Thread(target=open_browser, daemon=True).start()
        
        app.run(host='0.0.0.0', port=available_port, debug=False)
    except RuntimeError as e:
        logger.error(f'启动失败: {e}')
        exit(1)