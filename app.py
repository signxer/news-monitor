#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import copy
import json
import time
import threading
import schedule
import requests
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
import feedparser
from paths import get_config_path, get_db_path, get_log_path, get_template_dir, migrate_from_cwd

# 跨平台路径迁移（首次运行时将旧文件迁移到新位置）
migrate_from_cwd()

# Flask 应用（指定模板目录）
app = Flask(__name__, template_folder=str(get_template_dir()))
app.secret_key = 'news_monitor_secret_key_2024'

# 配置日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(str(get_log_path()), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class NewsMonitor:
    def __init__(self):
        self.config = self.load_config()
        self.is_running = False
        self.last_check_time = None
        self.news_version = 0  # 数据版本号，每次保存新闻后递增
        self.scrape_progress = {
            'current_site': '',
            'completed': 0,
            'total': 0,
            'status': 'idle'  # idle | running | scoring | done
        }
        self.init_database()
        
    def load_config(self):
        """加载配置文件"""
        self.default_config = {
            'check_interval': 60,  # 检查间隔（分钟）
            'concurrent_workers': 5,  # 并发检查数量
            'date_filter_days': 0,  # 日期过滤天数，0=不过滤，N=只抓取最近N天的新闻
            'notification': {
                'bark_urls': [],  # 支持多个Bark地址
                'serverchan_keys': [],  # 支持多个Server酱密钥
                # 保持向后兼容
                'bark_url': '',
                'serverchan_key': '',
                'email': {
                    'enabled': False,
                    'smtp_server': '',
                    'smtp_port': 465,
                    'use_ssl': True,
                    'username': '',
                    'password': '',
                    'from_address': '',
                    'to_addresses': []
                }
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
            'push': {
                'mode': 'immediate',  # 'immediate' 立即推送 | 'scheduled' 定时推送
                'scheduled_times': ['09:00', '18:00'],  # 定时模式下的推送时间点 HH:MM
            },
            'llm_filter': {
                'enabled': False,
                'api_url': 'https://api.deepseek.com/v1/chat/completions',
                'api_key': '',
                'model': 'deepseek-v4-flash',
                'user_prompt': '筛选与以下主题相关的新闻：国际经济、金融市场、科技发展、地缘政治',
                'relevance_threshold': 60,
                'max_retries': 2
            },
            'news_sites': [
                {
                    'name': 'IMF',
                    'url': 'https://www.imf.org/en/Publications/RSS?language=eng&series=IMF%20Working%20Papers',
                    'site_type': 'rss',
                    'title_selector': '.container__headline-text',
                    'date_selector': '.timestamp',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '世界银行',
                    'url': 'https://documents.worldbank.org/en/publication/documents-reports/documentlist?docty_exact=Policy%2BResearch%2BWorking%2BPaper',
                    'site_type': 'html',
                    'title_selector': 'div.search-listing-content > h3 > a.ng-tns-c0-0',
                    'date_selector': 'div.search-listing-content > div > span:nth-child(3)',
                    'date_format': '%B %-d, %Y',
                    'enabled': True
                },
                {
                    'name': '国际清算银行-Papers',
                    'url': 'https://www.bis.org/doclist/bispapers.rss',
                    'site_type': 'rss',
                    'title_selector': 'h3[data-testid=',
                    'date_selector': 'time',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '国际清算银行-Working papers',
                    'url': 'https://www.bis.org/doclist/wppubls.rss',
                    'site_type': 'rss',
                    'title_selector': 'h3[data-testid=',
                    'date_selector': 'time',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '亚洲开发银行',
                    'url': 'https://www.adb.org/publications/series/economics-working-papers',
                    'site_type': 'html',
                    'title_selector': 'li.clearfix > a',
                    'date_selector': 'time',
                    'date_format': '%d %b %Y',
                    'enabled': True
                },
                {
                    'name': '亚太经合组织',
                    'url': 'https://www.apec.org/publications/listings?keyword=&publicationTitle=&publicationNumber=&group=&publicationType=&dateFrom=&dateTo=&page=1',
                    'site_type': 'html',
                    'title_selector': 'div.eyd-card-publication__text > h3',
                    'date_selector': 'span.eyd-card-publication__date > span.eyd-card-publication__meta__text',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '美联储-feds',
                    'url': 'https://www.federalreserve.gov/feeds/feds.xml',
                    'site_type': 'rss',
                    'title_selector': 'h3[data-testid=',
                    'date_selector': 'time',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '美联储-feds_notes',
                    'url': 'https://www.federalreserve.gov/feeds/feds_notes.xml',
                    'site_type': 'rss',
                    'title_selector': 'h3[data-testid=',
                    'date_selector': 'time',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '美联储-ifdp',
                    'url': 'https://www.federalreserve.gov/feeds/ifdp.xml',
                    'site_type': 'rss',
                    'title_selector': 'h3[data-testid=',
                    'date_selector': 'time',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': 'Hoover',
                    'url': 'https://www.hoover.org/research/type/working-papers',
                    'site_type': 'html',
                    'title_selector': 'div > div.content > h6',
                    'date_selector': '.name-date span.date',
                    'date_format': '%B %-d, %Y',
                    'enabled': True
                },
                {
                    'name': '欧洲央行',
                    'url': 'https://www.ecb.europa.eu/press/research-publications/working-papers/html/index.en.html',
                    'site_type': 'html',
                    'title_selector': 'div.title > a',
                    'date_selector': '.foedb-plugin dl dt',
                    'date_format': '%d %B %Y',
                    'enabled': True
                },
                {
                    'name': '美国布鲁金斯学会',
                    'url': 'https://www.brookings.edu/programs/economic-studies/explore-research-and-commentary/',
                    'site_type': 'html',
                    'title_selector': 'article > a > span',
                    'date_selector': 'p.date',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '美国经济研究局',
                    'url': 'https://www.nber.org/papers?page=1&perPage=50&sortBy=public_date',
                    'site_type': 'html',
                    'title_selector': 'div.digest-card__title > a',
                    'date_selector': '.digest-card__date .digest-card__label',
                    'date_format': '%B %Y',
                    'enabled': True
                },
                {
                    'name': '彼得森研究所（PIIE）',
                    'url': 'https://www.piie.com/publications/working-papers',
                    'site_type': 'html',
                    'title_selector': 'h2.teaser__title > a',
                    'date_selector': 'p.teaser__date > time',
                    'date_format': '%Y-%m-%d',
                    'enabled': False
                },
                {
                    'name': '哈德逊研究所',
                    'url': 'https://www.hudson.org/search?hud-content-type=258&expert=&date-from=&date-to=&keywords=&topics=All&region=All',
                    'site_type': 'html',
                    'title_selector': 'a.c-horizontal-card__title > span',
                    'date_selector': 'div.c-horizontal-card__meta > div.c-horizontal-card__date > div > time',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '布鲁盖尔研究所',
                    'url': 'https://www.bruegel.org/publications/working-papers',
                    'site_type': 'html',
                    'title_selector': 'h2.c-list-item__title > a > span',
                    'date_selector': 'p.c-list-item__date',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '法国央行',
                    'url': 'https://www.banque-france.fr/en/publications-and-statistics/publications',
                    'site_type': 'html',
                    'title_selector': 'span.title-truncation',
                    'date_selector': 'div.card-body.py-4.px-5.d-flex.flex-column > small',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '日本央行',
                    'url': 'https://www.boj.or.jp/en/research/wps_rev/index.htm',
                    'site_type': 'html',
                    'title_selector': 'tbody > tr > td:nth-child(4)',
                    'date_selector': 'li.news_list-li time.time',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '加拿大央行',
                    'url': 'https://www.bankofcanada.ca/feed/?content_type=working-papers&post_type%5B0%5D=post&post_type%5B1%5D=page',
                    'site_type': 'rss',
                    'title_selector': 'h3[data-testid=',
                    'date_selector': 'time',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '美国企业研究所',
                    'url': 'https://www.aei.org/feed/',
                    'site_type': 'rss',
                    'title_selector': 'h3[data-testid=',
                    'date_selector': 'time',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '金融稳定委员会（FSB）',
                    'url': 'https://www.fsb.org/publications/',
                    'site_type': 'html',
                    'title_selector': 'div.post-title > h3 > a',
                    'date_selector': 'div.post-date > span',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '经济合作与发展组织（OECD）',
                    'url': 'https://www.oecd.org/en/publications/reports.html?orderBy=mostRelevant&page=0',
                    'site_type': 'html',
                    'title_selector': 'article > div.search-result-list-item__title > a',
                    'date_selector': 'article > div.search-result-list-item__meta > span.search-result-list-item__date',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '联合国贸易和发展会议（UNCTAD）',
                    'url': 'https://unctad.org/publications-search?f%5B0%5D=product%3A389',
                    'site_type': 'html',
                    'title_selector': 'div.title > a',
                    'date_selector': 'div.publisheddate > time',
                    'date_format': '%Y-%m-%d',
                    'enabled': False
                },
                {
                    'name': '香港金融学院（HKIMR）',
                    'url': 'https://www.aof.org.hk/research/HKIMR/publications-and-research/working-papers',
                    'site_type': 'html',
                    'title_selector': 'div.mbm.papertitle',
                    'date_selector': '',
                    'date_format': '',
                    'enabled': True
                },
                {
                    'name': '哈佛商学院',
                    'url': 'https://www.library.hbs.edu/working-knowledge/collections/finance-and-investing',
                    'site_type': 'html',
                    'title_selector': 'h2.hbs-article-tease__title hbs-text-h2 > a',
                    'date_selector': 'div.hbs-article-tease__meta > div > p > time',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '国际金融协会（IIF）-Debt',
                    'url': 'https://www.iif.com/Key-Topics/Debt',
                    'site_type': 'html',
                    'title_selector': 'h4.article--title > a',
                    'date_selector': 'span.article--date',
                    'date_format': '%B %-d, %Y',
                    'enabled': True
                },
                {
                    'name': '国际金融协会（IIF）-Sustainable-Finance',
                    'url': 'https://www.iif.com/Key-Topics/Sustainable-Finance',
                    'site_type': 'html',
                    'title_selector': 'h4.article--title > a',
                    'date_selector': 'span.article--date',
                    'date_format': '%B %-d, %Y',
                    'enabled': True
                },
                {
                    'name': '国际金融协会（IIF）-Digital-Finance',
                    'url': 'https://www.iif.com/Key-Topics/Digital-Finance',
                    'site_type': 'html',
                    'title_selector': 'h4.article--title > a',
                    'date_selector': 'span.article--date',
                    'date_format': '%B %-d, %Y',
                    'enabled': True
                },
                {
                    'name': '牛津经济研究院',
                    'url': 'https://www.oxfordeconomics.com/resource-hub/',
                    'site_type': 'html',
                    'title_selector': 'div.wpgb-card-body > div > div > h3 > a',
                    'date_selector': 'time',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '穆迪',
                    'url': 'https://www.moodys.com/web/en/us/insights.html',
                    'site_type': 'html',
                    'title_selector': 'div.card-content > h3 > a',
                    'date_selector': 'div.card-content > div > span.date',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                # ===== 国际组织（新增） =====
                {
                    'name': '世界贸易组织（WTO）',
                    'url': 'https://www.wto.org/library/rss/latest_news_e.xml',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '东盟与中日韩宏观经济研究办公室（AMRO）',
                    'url': 'https://amro-asia.org/feed/',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '世界经济论坛（WEF）',
                    'url': 'https://www.weforum.org/publications/',
                    'site_type': 'html',
                    'title_selector': 'a.article-card__title',
                    'date_selector': 'time',
                    'date_format': '%Y-%m-%d',
                    'enabled': False
                },
                {
                    'name': '国际货币金融机构官方论坛（OMFIF）',
                    'url': 'https://www.omfif.org/publications/',
                    'site_type': 'html',
                    'title_selector': 'h3.entry-title > a',
                    'date_selector': 'time',
                    'date_format': '%Y-%m-%d',
                    'enabled': False
                },
                # ===== 央行（新增） =====
                {
                    'name': '英格兰银行',
                    'url': 'https://www.bankofengland.co.uk/rss/publications',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                # ===== 政府机构 =====
                {
                    'name': '欧盟委员会',
                    'url': 'https://ec.europa.eu/commission/presscorner/api/rss',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '美国白宫',
                    'url': 'https://www.whitehouse.gov/remarks/feed/',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '美国国会-通知',
                    'url': 'https://www.congress.gov/rss/notification.xml',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '美国国会-法律博客',
                    'url': 'https://blogs.loc.gov/law/feed/',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '美国国会-送交总统',
                    'url': 'https://www.congress.gov/rss/presented-to-president.xml',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '美国国会-众议院',
                    'url': 'https://www.congress.gov/rss/house-floor-today.xml',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '美国国会-参议院',
                    'url': 'https://www.congress.gov/rss/senate-floor-today.xml',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '美国国会-热门法案',
                    'url': 'https://www.congress.gov/rss/most-viewed-bills.xml',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '美国财政部',
                    'url': 'https://home.treasury.gov/news/press-releases',
                    'site_type': 'html',
                    'title_selector': 'a.usa-link',
                    'date_selector': 'time',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '美国战略与国际研究中心（CSIS）',
                    'url': 'https://www.csis.org/analysis',
                    'site_type': 'html',
                    'title_selector': 'h3 > a',
                    'date_selector': 'span.date',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '瑞士洛桑管理发展学院（IMD）',
                    'url': 'https://www.imd.org/feed/',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                # ===== 智库及其他 =====
                {
                    'name': '荣鼎咨询（Rhodium Group）',
                    'url': 'https://rhg.com/feed/',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '兰德智库（RAND）',
                    'url': 'https://www.rand.org/pubs/research_reports.xml',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '大西洋理事会（Atlantic Council）',
                    'url': 'https://www.atlanticcouncil.org/feed/',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '米尔肯研究所（Milken Institute）',
                    'url': 'https://www.milkeninstitute.org/content-hub',
                    'site_type': 'html',
                    'title_selector': 'a.card__title',
                    'date_selector': 'span.card__date',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '彭博-经济',
                    'url': 'https://feeds.bloomberg.com/economics/news.rss',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '华尔街日报-全球新闻',
                    'url': 'https://feeds.a.dj.com/rss/RSSWorldNews.xml',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '英国商会中国（BritCham）',
                    'url': 'https://www.britishchamber.cn/feed/',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                # ===== 反爬较严或需要特殊处理，暂时禁用 =====
                {
                    'name': '美国企业研究所（AEI）-经济',
                    'url': 'https://www.aei.org/feed/?cat=economics',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '美国国民经济研究局（NBER）-新论文',
                    'url': 'https://www.nber.org/rss/new.xml',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '经济学人-金融与经济',
                    'url': 'https://www.economist.com/finance-and-economics/rss.xml',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': False
                },
                {
                    'name': '路透社-商业',
                    'url': 'https://www.reuters.com/arc/outboundfeeds/rss/category/business/?outputType=xml',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': False
                },
                {
                    'name': '外交事务（Foreign Affairs）',
                    'url': 'https://www.foreignaffairs.com/rss.xml',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': False
                },
                {
                    'name': '金融时报（FT）',
                    'url': 'https://www.ft.com/rss/home',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': False
                },
                # ===== 新增：Agent Reach 搜索确认 =====
                {
                    'name': '摩根士丹利',
                    'url': 'https://www.morganstanley.com/press-releases.msfeed.xml',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '中国与全球化智库（CCG）',
                    'url': 'https://www.ccgupdate.org/feed',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '美国商务部',
                    'url': 'https://www.commerce.gov/feeds/news',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '美国贸易代表办公室（USTR）',
                    'url': 'https://ustr.gov/about-us/policy-offices/press-office/press-releases',
                    'site_type': 'html',
                    'title_selector': 'a.usa-link',
                    'date_selector': 'time',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '美国国际贸易委员会（USITC）',
                    'url': 'https://www.usitc.gov/staff_publications/all',
                    'site_type': 'html',
                    'title_selector': 'td.views-field-title a',
                    'date_selector': 'td.views-field-created',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '中美经济与安全审查委员会（USCC）',
                    'url': 'https://www.uscc.gov/research',
                    'site_type': 'html',
                    'title_selector': 'div.views-row a',
                    'date_selector': 'span.date-display-single',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '美中贸易全国委员会（USCBC）',
                    'url': 'https://www.uschina.org/research-analysis/',
                    'site_type': 'html',
                    'title_selector': 'h3 a',
                    'date_selector': 'time',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '美国亚洲研究局（NBR）',
                    'url': 'https://www.nbr.org/publications/',
                    'site_type': 'html',
                    'title_selector': 'h3 a',
                    'date_selector': 'span.date',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '中国欧盟商会',
                    'url': 'https://www.europeanchamber.com.cn/en/publications-archive',
                    'site_type': 'html',
                    'title_selector': 'td a',
                    'date_selector': 'td.views-field-created',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '上海美国商会',
                    'url': 'https://www.amcham-shanghai.org/en/resources/publications',
                    'site_type': 'html',
                    'title_selector': 'h3 a',
                    'date_selector': 'time',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': 'CEPR VoxEU',
                    'url': 'https://cepr.org/rss/vox-content',
                    'site_type': 'rss',
                    'title_selector': '',
                    'date_selector': '',
                    'date_format': '%Y-%m-%d',
                    'enabled': False
                },
                {
                    'name': '日本野村综合研究所（NRI）',
                    'url': 'https://www.nri.com/jp/knowledge/index.html',
                    'site_type': 'html',
                    'title_selector': 'h3.--title',
                    'date_selector': 'time.--date',
                    'date_format': '%Y-%m-%d',
                    'enabled': True
                },
                {
                    'name': '中国美国商会（AmCham China）',
                    'url': 'https://www.amchamchina.org/news/',
                    'site_type': 'html',
                    'title_selector': 'h2.sp-pcp-title a',
                    'date_selector': '.sp-pcp-post-meta .sps-meta-type-date',
                    'date_format': '%B %-d, %Y',
                    'enabled': True
                }
            ]
        }
        
        config_path = str(get_config_path())
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                # 合并默认配置
                for key in self.default_config:
                    if key not in config:
                        config[key] = self.default_config[key]

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

                    # 处理邮件配置
                    if 'email' not in notification:
                        notification['email'] = self.default_config['notification']['email']
                    else:
                        for k, v in self.default_config['notification']['email'].items():
                            if k not in notification['email']:
                                notification['email'][k] = v

                # 向后兼容：补全 llm_filter 缺失字段
                if 'llm_filter' in config:
                    for k, v in self.default_config['llm_filter'].items():
                        if k not in config['llm_filter']:
                            config['llm_filter'][k] = v

                # 向后兼容：补全 push 缺失字段
                if 'push' not in config:
                    config['push'] = self.default_config['push']
                else:
                    for k, v in self.default_config['push'].items():
                        if k not in config['push']:
                            config['push'][k] = v

                return config
        except FileNotFoundError:
            self.save_config(self.default_config)
            return self.default_config
    
    def save_config(self, config=None):
        """保存配置文件"""
        if config is None:
            config = self.config
        config_path = str(get_config_path())
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    def get_default_news_sites(self):
        """获取默认新闻站点列表"""
        return copy.deepcopy(self.default_config.get('news_sites', []))

    def restore_default_news_sites(self):
        """恢复默认新闻站点配置，返回站点数量"""
        default_sites = self.get_default_news_sites()
        self.config['news_sites'] = default_sites
        self.save_config()
        return len(default_sites)

    def init_database(self):
        """初始化数据库"""
        conn = sqlite3.connect(str(get_db_path()))
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_name TEXT,
                title TEXT,
                translated_title TEXT,
                url TEXT,
                date TEXT,
                pushed INTEGER DEFAULT 0,
                llm_relevance INTEGER DEFAULT -1,
                llm_reason TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(site_name, title, url)
            )
        ''')
        # 向后兼容：为已有数据库添加新列
        for col, ddl in [
            ('pushed', 'ALTER TABLE news ADD COLUMN pushed INTEGER DEFAULT 0'),
            ('llm_relevance', 'ALTER TABLE news ADD COLUMN llm_relevance INTEGER DEFAULT -1'),
            ('llm_reason', "ALTER TABLE news ADD COLUMN llm_reason TEXT DEFAULT ''"),
        ]:
            try:
                cursor.execute(ddl)
            except sqlite3.OperationalError:
                pass  # 列已存在
        conn.commit()
        conn.close()

        # 站点统计表
        conn = sqlite3.connect(str(get_db_path()))
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS site_stats (
                site_name TEXT PRIMARY KEY,
                last_check TIMESTAMP,
                last_success TIMESTAMP,
                last_error TEXT DEFAULT '',
                consecutive_errors INTEGER DEFAULT 0,
                total_checks INTEGER DEFAULT 0,
                total_success INTEGER DEFAULT 0,
                total_errors INTEGER DEFAULT 0,
                total_news INTEGER DEFAULT 0,
                last_news_count INTEGER DEFAULT 0,
                avg_response_time REAL DEFAULT 0
            )
        ''')
        conn.commit()
        conn.close()

    def update_site_stats(self, site_name, success, news_count=0, error_msg='', response_time=0):
        """更新站点统计"""
        try:
            conn = sqlite3.connect(str(get_db_path()))
            cursor = conn.cursor()
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            # 尝试更新已有记录
            cursor.execute('SELECT total_checks, total_success, total_errors, total_news, avg_response_time, consecutive_errors FROM site_stats WHERE site_name = ?', (site_name,))
            row = cursor.fetchone()

            if row:
                total_checks = row[0] + 1
                total_success = row[1] + (1 if success else 0)
                total_errors = row[2] + (0 if success else 1)
                total_news = row[3] + news_count
                # 指数移动平均响应时间
                old_avg = row[4]
                avg_time = old_avg * 0.8 + response_time * 0.2 if old_avg > 0 else response_time
                consecutive = 0 if success else (row[5] + 1)

                cursor.execute('''
                    UPDATE site_stats SET
                        last_check = ?,
                        last_success = CASE WHEN ? THEN ? ELSE last_success END,
                        last_error = CASE WHEN ? THEN '' ELSE ? END,
                        consecutive_errors = ?,
                        total_checks = ?,
                        total_success = ?,
                        total_errors = ?,
                        total_news = ?,
                        last_news_count = ?,
                        avg_response_time = ?
                    WHERE site_name = ?
                ''', (now, success, now, success, error_msg, consecutive,
                      total_checks, total_success, total_errors, total_news,
                      news_count, round(avg_time, 2), site_name))
            else:
                cursor.execute('''
                    INSERT INTO site_stats
                    (site_name, last_check, last_success, last_error, consecutive_errors,
                     total_checks, total_success, total_errors, total_news, last_news_count, avg_response_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (site_name, now,
                      now if success else None,
                      '' if success else error_msg,
                      0 if success else 1,
                      1, 1 if success else 0, 0 if success else 1,
                      news_count, news_count, round(response_time, 2)))

            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"更新站点统计失败: {site_name}, {str(e)}")

    def get_site_stats(self):
        """获取所有站点统计"""
        try:
            conn = sqlite3.connect(str(get_db_path()))
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM site_stats ORDER BY site_name')
            rows = cursor.fetchall()
            conn.close()

            stats = []
            for r in rows:
                stats.append({
                    'site_name': r[0],
                    'last_check': r[1],
                    'last_success': r[2],
                    'last_error': r[3] or '',
                    'consecutive_errors': r[4],
                    'total_checks': r[5],
                    'total_success': r[6],
                    'total_errors': r[7],
                    'total_news': r[8],
                    'last_news_count': r[9],
                    'avg_response_time': r[10]
                })
            return stats
        except Exception as e:
            logger.error(f"获取站点统计失败: {str(e)}")
            return []

    def create_webdriver(self):
        """创建Chrome WebDriver（使用 webdriver-manager 自动管理驱动）"""
        chrome_options = Options()
        chrome_options.add_argument('--headless=new')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument('--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)

        # 优先使用 webdriver-manager 自动下载匹配的驱动
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())
            logger.info("使用 webdriver-manager 自动管理 ChromeDriver")
        except Exception:
            # fallback: 使用系统 PATH 中的 chromedriver
            service = Service()
            logger.info("使用系统 PATH 中的 ChromeDriver")

        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.set_page_load_timeout(60)
        driver.set_script_timeout(30)
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

    def _parse_llm_json(self, content):
        """从LLM返回内容中提取JSON对象，过滤掉解释性文字"""
        if not content:
            return None

        # 去除 markdown 代码块标记
        content = re.sub(r'```(?:json)?\s*', '', content)
        content = re.sub(r'```', '', content)
        content = content.strip()

        # 方法1：尝试直接解析整个内容
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # 方法2：提取第一个完整的 JSON 对象（支持嵌套）
        depth = 0
        start = -1
        for i, ch in enumerate(content):
            if ch == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        return json.loads(content[start:i+1])
                    except json.JSONDecodeError:
                        start = -1

        # 方法3：正则兜底（简单结构）
        match = re.search(r'\{[^{}]+\}', content)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        return None

    def llm_score_news(self, news_items):
        """使用大模型对新闻打分并翻译（前置打分，不过滤）

        调用 LLM API（OpenAI 兼容格式）判断每条新闻与用户主题的相关性，
        并同时获取中文翻译。为每条新闻写入 llm_relevance / llm_reason / translated_title。
        API 调用失败时保留原分数（-1 表示未评分）。
        """
        llm_config = self.config.get('llm_filter', {})
        if not llm_config.get('enabled', False):
            return news_items

        api_url = llm_config.get('api_url', '')
        api_key = llm_config.get('api_key', '')
        model = llm_config.get('model', 'deepseek-v4-flash')
        user_prompt = llm_config.get('user_prompt', '')
        max_retries = llm_config.get('max_retries', 2)

        if not api_key:
            logger.warning("LLM筛选已启用但未配置API密钥，跳过打分")
            return news_items

        system_prompt = (
            '你是一个新闻筛选和翻译助手。根据用户提供的筛选主题，判断新闻标题的相关性，并将英文标题翻译为中文。\n'
            '\n'
            '【输出要求】\n'
            '- 只输出一个JSON对象，不要输出任何其他文字、解释、说明或markdown标记\n'
            '- 不要输出```json```代码块标记\n'
            '- 不要在JSON前后添加任何内容\n'
            '- translation字段必须为非空的中文翻译，不得留空或返回原文\n'
            '\n'
            '【JSON格式】\n'
            '{"relevance":0-100的整数,"reason":"中文理由","translation":"中文翻译"}'
        )

        for item in news_items:
            title = item.get('title', '')
            if not title:
                continue

            success = False
            for attempt in range(1, max_retries + 1):
                try:
                    full_user_prompt = f"{user_prompt}\n\n新闻标题：{title}"
                    response = requests.post(
                        api_url,
                        headers={
                            'Content-Type': 'application/json',
                            'Authorization': f'Bearer {api_key}'
                        },
                        json={
                            'model': model,
                            'messages': [
                                {'role': 'system', 'content': system_prompt},
                                {'role': 'user', 'content': full_user_prompt}
                            ],
                            'temperature': 0.1,
                            'max_tokens': 256
                        },
                        timeout=30
                    )
                    if response.status_code == 200:
                        result = response.json()
                        content = result['choices'][0]['message']['content']
                        llm_result = self._parse_llm_json(content)
                        if llm_result:
                            relevance = llm_result.get('relevance', 0)
                            translated = (llm_result.get('translation') or '').strip()
                            if not translated or translated == title:
                                translated = self.translate_text(title)
                            item['translated_title'] = translated
                            item['llm_relevance'] = relevance
                            item['llm_reason'] = llm_result.get('reason', '')
                            logger.info(f"LLM打分: [{relevance}分] {title}")
                            success = True
                            break
                        else:
                            logger.warning(f"LLM返回格式异常(第{attempt}次): {content[:200]}")
                    else:
                        logger.error(f"LLM API请求失败(第{attempt}次): {response.status_code}")
                except Exception as e:
                    logger.error(f"LLM打分异常(第{attempt}次): {str(e)}")

                if attempt < max_retries:
                    time.sleep(1)

            if not success:
                logger.warning(f"LLM打分{max_retries}次重试均失败，保留原文: {title}")
                # llm_relevance 保持 -1 表示未评分

        return news_items

    def parse_date_string(self, date_str, date_format=None):
        """解析各种格式的日期字符串，返回 datetime 对象，解析失败返回 None"""
        if not date_str:
            return None

        date_str = date_str.strip()

        # 如果指定了格式，优先使用
        if date_format:
            try:
                return datetime.strptime(date_str, date_format)
            except (ValueError, TypeError):
                pass

        # 常见日期格式列表（从精确到模糊）
        formats = [
            '%Y-%m-%d',              # 2025-06-20
            '%Y/%m/%d',              # 2025/06/20
            '%Y-%m-%dT%H:%M:%S',     # 2025-06-20T14:30:00
            '%Y-%m-%dT%H:%M:%SZ',    # 2025-06-20T14:30:00Z
            '%Y-%m-%d %H:%M:%S',     # 2025-06-20 14:30:00
            '%Y-%m-%d %H:%M',        # 2025-06-20 14:30
            '%Y.%m.%d',              # 2025.06.20
            '%b %d, %Y',             # Jun 20, 2025
            '%B %d, %Y',             # June 20, 2025
            '%d %b %Y',              # 20 Jun 2025
            '%d %B %Y',              # 20 June 2025
            '%m/%d/%Y',              # 06/20/2025
            '%d/%m/%Y',              # 20/06/2025
            '%Y年%m月%d日',           # 2025年06月20日
            '%m-%d-%Y',              # 06-20-2025
        ]

        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt)
            except (ValueError, TypeError):
                continue

        # 尝试用正则提取年月日
        m = re.search(r'(\d{4})[/-年](\d{1,2})[/-月](\d{1,2})', date_str)
        if m:
            try:
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except (ValueError, TypeError):
                pass

        return None

    def is_within_date_filter(self, date_str, date_format=None):
        """检查日期是否在配置的时间范围内，返回 True 表示应保留"""
        days = self.config.get('date_filter_days', 0)
        if days <= 0:
            return True  # 不过滤

        parsed = self.parse_date_string(date_str, date_format)
        if not parsed:
            return True  # 解析失败时保留（不丢弃）

        cutoff = datetime.now() - timedelta(days=days)
        return parsed >= cutoff

    def scrape_rss_site(self, site_config):
        """抓取RSS新闻网站"""
        if not site_config.get('enabled', True):
            return []
        
        try:
            logger.info(f"开始抓取RSS {site_config['name']}")

            # 使用requests带超时获取RSS内容，再交给feedparser解析
            resp = requests.get(site_config['url'], timeout=30, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; NewsMonitor/1.0)'
            })
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            
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

                # 日期过滤：跳过超出时间范围的新闻
                if not self.is_within_date_filter(date_str):
                    logger.debug(f"RSS新闻超出时间范围，跳过: {title[:30]}... (日期: {date_str})")
                    continue

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
                # 优先：元素本身就是 <a> 标签
                if element.name == 'a' and element.get('href'):
                    url = urljoin(site_config['url'], element['href'])
                    logger.debug(f"提取到链接（元素自身为a标签）: {url}")
                else:
                    # 其次：查找子元素中的 <a> 标签
                    link_element = element.find('a')
                    if link_element and link_element.get('href'):
                        url = urljoin(site_config['url'], link_element['href'])
                        logger.debug(f"提取到链接（子a标签）: {url}")
                    else:
                        # 最后：查找父级 <a> 标签
                        link_element = element.find_parent('a')
                        if link_element and link_element.get('href'):
                            url = urljoin(site_config['url'], link_element['href'])
                            logger.debug(f"提取到链接（父a标签）: {url}")
                        else:
                            logger.debug("未找到有效链接，使用站点主页")

                # 获取日期（可选）
                date_str = datetime.now().strftime('%Y-%m-%d')
                try:
                    if site_config.get('date_selector'):
                        # 向上逐层查找父容器，直到找到包含日期元素的公共祖先
                        date_element = None
                        parent = element
                        for _ in range(6):
                            parent = parent.find_parent()
                            if not parent:
                                break
                            date_element = parent.select_one(site_config['date_selector'])
                            if date_element:
                                break
                        if date_element:
                            # 优先读取 <time> 标签的 datetime 属性（更可靠）
                            datetime_attr = date_element.get('datetime', '').strip()
                            if datetime_attr:
                                date_str = datetime_attr
                                logger.debug(f"提取到日期（datetime属性）: {date_str}")
                            else:
                                date_str = date_element.get_text().strip()
                                logger.debug(f"提取到日期: {date_str}")
                        else:
                            # 回退：尝试 find_next（仅对简单标签名有效）
                            date_element = element.find_next(site_config['date_selector'])
                            if date_element:
                                datetime_attr = date_element.get('datetime', '').strip()
                                if datetime_attr:
                                    date_str = datetime_attr
                                    logger.debug(f"提取到日期（find_next回退, datetime属性）: {date_str}")
                                else:
                                    date_str = date_element.get_text().strip()
                                    logger.debug(f"提取到日期（find_next回退）: {date_str}")
                            else:
                                logger.debug("未找到日期元素")
                except Exception as date_e:
                    logger.debug(f"日期提取失败: {str(date_e)}")

                # 日期过滤：跳过超出时间范围的新闻
                date_format = site_config.get('date_format', '')
                if not self.is_within_date_filter(date_str, date_format or None):
                    logger.debug(f"HTML新闻超出时间范围，跳过: {title[:30]}... (日期: {date_str})")
                    continue

                # 日期归一化：统一存为 YYYY-MM-DD 格式
                parsed_date = self.parse_date_string(date_str, date_format or None)
                if parsed_date:
                    date_str = parsed_date.strftime('%Y-%m-%d')

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

        conn = sqlite3.connect(str(get_db_path()))
        cursor = conn.cursor()
        new_count = 0
        new_news_list = []

        for item in news_items:
            try:
                cursor.execute('''
                    INSERT OR IGNORE INTO news
                    (site_name, title, translated_title, url, date, llm_relevance, llm_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    item['site_name'],
                    item['title'],
                    item['translated_title'],
                    item['url'],
                    item['date'],
                    item.get('llm_relevance', -1),
                    item.get('llm_reason', '')
                ))
                if cursor.rowcount > 0:
                    new_count += 1
                    new_news_list.append(item)
            except Exception as e:
                logger.error(f"保存新闻失败: {str(e)}")

        conn.commit()
        conn.close()
        return new_count, new_news_list

    def mark_as_pushed(self, news_list):
        """标记新闻为已推送"""
        if not news_list:
            return
        conn = sqlite3.connect(str(get_db_path()))
        cursor = conn.cursor()
        for item in news_list:
            try:
                cursor.execute('''
                    UPDATE news SET pushed = 1
                    WHERE site_name = ? AND title = ? AND url = ?
                ''', (item['site_name'], item['title'], item['url']))
            except Exception as e:
                logger.error(f"标记已推送失败: {str(e)}")
        conn.commit()
        conn.close()

    def mark_as_filtered(self, news_list):
        """标记新闻为主题无关（pushed=2）"""
        if not news_list:
            return
        conn = sqlite3.connect(str(get_db_path()))
        cursor = conn.cursor()
        for item in news_list:
            try:
                cursor.execute('''
                    UPDATE news SET pushed = 2
                    WHERE site_name = ? AND title = ? AND url = ?
                ''', (item['site_name'], item['title'], item['url']))
            except Exception as e:
                logger.error(f"标记主题无关失败: {str(e)}")
        conn.commit()
        conn.close()

    def get_pending_count(self):
        """获取待推送新闻数量"""
        conn = sqlite3.connect(str(get_db_path()))
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM news WHERE pushed = 0')
        count = cursor.fetchone()[0]
        conn.close()
        return count

    def get_unrated_count(self):
        """获取LLM打分失败（未评分）的新闻数量"""
        conn = sqlite3.connect(str(get_db_path()))
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM news WHERE llm_relevance = -1 AND pushed = 0')
        count = cursor.fetchone()[0]
        conn.close()
        return count

    def retry_llm_scoring(self):
        """对LLM打分失败的新闻重新打分"""
        conn = sqlite3.connect(str(get_db_path()))
        cursor = conn.cursor()
        cursor.execute('''
            SELECT site_name, title, translated_title, url, date
            FROM news WHERE llm_relevance = -1 AND pushed = 0
            ORDER BY created_at DESC
        ''')
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return 0

        news_items = [{
            'site_name': r[0], 'title': r[1], 'translated_title': r[2],
            'url': r[3], 'date': r[4]
        } for r in rows]

        logger.info(f"重试LLM打分：共 {len(news_items)} 条未评分新闻")
        scored_items = self.llm_score_news(news_items)

        # 将打分结果写回数据库
        updated = 0
        conn = sqlite3.connect(str(get_db_path()))
        cursor = conn.cursor()
        for item in scored_items:
            if item.get('llm_relevance', -1) >= 0:
                cursor.execute('''
                    UPDATE news SET llm_relevance = ?, llm_reason = ?, translated_title = ?
                    WHERE site_name = ? AND title = ? AND url = ?
                ''', (
                    item['llm_relevance'],
                    item.get('llm_reason', ''),
                    item.get('translated_title', ''),
                    item['site_name'],
                    item['title'],
                    item['url']
                ))
                if cursor.rowcount > 0:
                    updated += 1
        conn.commit()
        conn.close()

        # 低于阈值的标记为主题无关
        threshold = self.config.get('llm_filter', {}).get('relevance_threshold', 60)
        below = [i for i in scored_items if i.get('llm_relevance', -1) >= 0 and i['llm_relevance'] < threshold]
        if below:
            self.mark_as_filtered(below)
            logger.info(f"重试LLM打分：{len(below)} 条低分新闻标记为主题无关")

        logger.info(f"重试LLM打分完成：成功 {updated} 条")
        return updated

    def push_pending_news(self):
        """定时推送：获取所有未推送新闻，统一发送，标记已推送"""
        logger.info("定时推送任务触发")
        conn = sqlite3.connect(str(get_db_path()))
        cursor = conn.cursor()
        cursor.execute('''
            SELECT site_name, title, translated_title, url, date, llm_relevance
            FROM news WHERE pushed = 0
            ORDER BY created_at ASC
        ''')
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            logger.info("定时推送：无待推送新闻，跳过")
            return

        news_list = [{'site_name': r[0], 'title': r[1], 'translated_title': r[2],
                      'url': r[3], 'date': r[4], 'llm_relevance': r[5]} for r in rows]
        logger.info(f"定时推送：共 {len(news_list)} 条待推送新闻")

        # 关键词筛选
        filtered = [n for n in news_list if self.match_keyword_rules(n)]

        # LLM阈值筛选（使用已存储的分数）
        threshold = self.config.get('llm_filter', {}).get('relevance_threshold', 60)
        if self.config.get('llm_filter', {}).get('enabled', False):
            before = len(filtered)
            # 低于阈值的新闻：标记为主题无关（pushed=2）
            below_threshold = [n for n in filtered if n.get('llm_relevance', -1) >= 0 and n.get('llm_relevance', 0) < threshold]
            if below_threshold:
                self.mark_as_filtered(below_threshold)
                logger.info(f"定时推送：{len(below_threshold)} 条低分新闻标记为主题无关")
            filtered = [n for n in filtered if n.get('llm_relevance', -1) < 0 or n.get('llm_relevance', 0) >= threshold]
            logger.info(f"定时推送：LLM阈值筛选 {before} -> {len(filtered)} 条 (阈值: {threshold})")

        if filtered:
            logger.info(f"定时推送：{len(filtered)} 条新闻通过筛选，开始推送")
            self.send_notification_with_details(filtered, title_prefix='定时新闻汇总')
            self.mark_as_pushed(filtered)
        else:
            logger.info("定时推送：无通过筛选的新闻，跳过推送")
            # 即使没有通过筛选，也标记为已推送，避免下次重复筛选
            self.mark_as_pushed(news_list)

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
                    response = requests.post(bark_url.strip(), json={
                        "title": "📰 新闻更新通知",
                        "body": message,
                        "group": "新闻监控",
                    }, timeout=10)
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
    
    def send_notification_with_details(self, new_news_list, title_prefix='新闻更新'):
        """发送包含新闻详情的通知"""
        if not new_news_list:
            return

        # 构建详细的通知消息（完整列表，带URL）
        message_lines = [f"📰 {title_prefix}：发现 {len(new_news_list)} 条新闻\n"]

        for i, news in enumerate(new_news_list, 1):
            site_name = news.get('site_name', '未知来源')
            title = news.get('title', '无标题')
            translated_title = news.get('translated_title', '')
            url = news.get('url', '')

            message_lines.append(f"🔸 {i}. 【{site_name}】")
            if translated_title and translated_title != title:
                message_lines.append(f"   {translated_title}")
                message_lines.append(f"   {title}")
            else:
                message_lines.append(f"   {title}")
            if url:
                message_lines.append(f"   {url}")
            message_lines.append("")

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
                if title_prefix == '定时新闻汇总':
                    # 定时推送模式：构建完整列表，按4KB分片发送
                    try:
                        # 构建每条新闻的内容（带URL，同时显示翻译和原文标题）
                        item_lines = []
                        for i, news in enumerate(new_news_list, 1):
                            title = news.get('title', '')
                            translated = news.get('translated_title', '')
                            u = news.get('url', '')
                            if translated and translated != title:
                                line = f"{i}. {translated}\n   {title}"
                            else:
                                line = f"{i}. {title}"
                            if u:
                                line += f"\n   {u}"
                            item_lines.append(line)

                        # 按URL长度限制分片（中文URL编码后膨胀约3倍，需保守估算）
                        max_bytes = 1000
                        chunks = []
                        current_chunk = []
                        current_size = 0
                        for line in item_lines:
                            line_bytes = len(line.encode('utf-8')) + 1  # +1 for newline
                            if current_size + line_bytes > max_bytes and current_chunk:
                                chunks.append(current_chunk)
                                current_chunk = []
                                current_size = 0
                            current_chunk.append(line)
                            current_size += line_bytes
                        if current_chunk:
                            chunks.append(current_chunk)

                        total_chunks = len(chunks)
                        for idx, chunk in enumerate(chunks):
                            if total_chunks > 1:
                                push_title = f"📰 {title_prefix} ({idx+1}/{total_chunks})"
                            else:
                                push_title = f"📰 {title_prefix}（{len(new_news_list)}条）"
                            push_content = "\n".join(chunk)

                            response = requests.post(bark_url.strip(), json={
                                "title": push_title,
                                "body": push_content,
                                "group": "新闻监控",
                            }, timeout=10)
                            if response.status_code == 200:
                                logger.info(f"Bark定时汇总通知发送成功: {bark_url} ({idx+1}/{total_chunks})")
                            else:
                                logger.warning(f"Bark定时汇总通知发送失败，状态码: {response.status_code}")

                            # 多条之间稍作延迟，避免被限流
                            if idx < total_chunks - 1:
                                time.sleep(0.5)
                    except Exception as e:
                        logger.error(f"Bark定时汇总通知发送失败: {bark_url}, 错误: {str(e)}")
                else:
                    # 立即推送模式：每条新闻单独发送
                    for news in new_news_list:
                        try:
                            site_name = news.get('site_name', '未知来源')
                            title = news.get('title', '无标题')
                            translated_title = news.get('translated_title', '')
                            url = news.get('url', '')

                            push_title = f"📰 {site_name}"
                            if translated_title and translated_title != title:
                                push_content = f"{translated_title}\n{title}"
                            else:
                                push_content = title

                            payload = {
                                "title": push_title,
                                "body": push_content,
                                "group": "新闻监控",
                            }
                            if url:
                                payload["url"] = url

                            response = requests.post(bark_url.strip(), json=payload, timeout=10)

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
                        'title': f'📰 {title_prefix} ({len(new_news_list)}条)',
                        'desp': message
                    }, timeout=10)
                    if response.status_code == 200:
                        logger.info(f"Server酱详细通知发送成功: {serverchan_key}")
                    else:
                        logger.warning(f"Server酱详细通知发送失败，状态码: {response.status_code}, 密钥: {serverchan_key}")
                except Exception as e:
                    logger.error(f"Server酱详细通知发送失败: {serverchan_key}, 错误: {str(e)}")

        # 发送邮件通知
        email_config = notification_config.get('email', {})
        if email_config.get('enabled', False):
            smtp_server = email_config.get('smtp_server', '')
            smtp_port = email_config.get('smtp_port', 465)
            use_ssl = email_config.get('use_ssl', True)
            username = email_config.get('username', '')
            password = email_config.get('password', '')
            from_address = email_config.get('from_address', '') or username
            to_addresses = email_config.get('to_addresses', [])

            if smtp_server and username and password and to_addresses:
                try:
                    import smtplib
                    from email.mime.text import MIMEText
                    from email.mime.multipart import MIMEMultipart

                    msg = MIMEMultipart('alternative')
                    msg['Subject'] = f'📰 {title_prefix} ({len(new_news_list)}条)'
                    msg['From'] = from_address
                    msg['To'] = ', '.join(to_addresses)

                    # 纯文本内容
                    msg.attach(MIMEText(message, 'plain', 'utf-8'))

                    # HTML 内容（完整列表，带URL）
                    html_lines = ['<html><body style="font-family: sans-serif; padding: 20px;">']
                    html_lines.append(f'<h2>📰 {title_prefix}：{len(new_news_list)} 条新闻</h2>')
                    for i, news in enumerate(new_news_list, 1):
                        site_name = news.get('site_name', '未知来源')
                        title = news.get('title', '无标题')
                        translated_title = news.get('translated_title', '')
                        url = news.get('url', '')
                        llm_reason = news.get('llm_reason', '')
                        html_lines.append(f'<div style="margin-bottom:16px;padding:12px;background:#f8f9fa;border-radius:8px;border-left:4px solid #0d6efd;">')
                        html_lines.append(f'<div style="color:#666;font-size:13px;margin-bottom:4px;">{i}. 【{site_name}】</div>')
                        if translated_title and translated_title != title:
                            html_lines.append(f'<div style="font-weight:bold;font-size:15px;">{translated_title}</div>')
                            if url:
                                html_lines.append(f'<a href="{url}" style="color:#0d6efd;text-decoration:none;font-size:13px;">{title}</a>')
                            else:
                                html_lines.append(f'<div style="color:#555;font-size:13px;">{title}</div>')
                        else:
                            if url:
                                html_lines.append(f'<a href="{url}" style="color:#0d6efd;text-decoration:none;font-weight:bold;font-size:15px;">{title}</a>')
                            else:
                                html_lines.append(f'<div style="font-weight:bold;font-size:15px;">{title}</div>')
                        if url:
                            html_lines.append(f'<div style="color:#0d6efd;font-size:12px;margin-top:4px;"><a href="{url}" style="color:#0d6efd;">{url}</a></div>')
                        if llm_reason:
                            html_lines.append(f'<div style="color:#888;font-size:12px;margin-top:4px;">🤖 {llm_reason}</div>')
                        html_lines.append('</div>')
                    html_lines.append('</body></html>')
                    msg.attach(MIMEText('\n'.join(html_lines), 'html', 'utf-8'))

                    if use_ssl:
                        server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=15)
                    else:
                        server = smtplib.SMTP(smtp_server, smtp_port, timeout=15)
                        server.starttls()
                    server.login(username, password)
                    server.sendmail(from_address, to_addresses, msg.as_string())
                    server.quit()
                    logger.info(f"邮件通知发送成功: {', '.join(to_addresses)}")
                except Exception as e:
                    logger.error(f"邮件通知发送失败: {str(e)}")
            else:
                logger.warning("邮件配置不完整，跳过发送")

    def clean_log_file(self):
        """清理日志文件，保留最近的日志内容"""
        try:
            log_file_path = str(get_log_path())
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

            # 初始化进度
            self.scrape_progress = {
                'current_site': '',
                'completed': 0,
                'total': len(enabled_sites),
                'status': 'running'
            }

            # 获取并发工作线程数量
            max_workers = self.config.get('concurrent_workers', 5)
            logger.info(f"使用 {max_workers} 个并发线程检查 {len(enabled_sites)} 个新闻站点")

            # 使用线程池并发处理
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # 提交所有任务（记录提交时间）
                future_to_site = {}
                future_to_start = {}
                for site in enabled_sites:
                    future = executor.submit(self.scrape_news_site, site)
                    future_to_site[future] = site
                    future_to_start[future] = time.time()

                # 收集结果，每完成一个站点就立即保存
                for future in as_completed(future_to_site):
                    site = future_to_site[future]
                    site_name = site.get('name', 'Unknown')
                    response_time = time.time() - future_to_start[future]
                    # 更新进度
                    self.scrape_progress['current_site'] = site_name
                    self.scrape_progress['completed'] += 1
                    try:
                        news_items = future.result(timeout=180)
                        all_news.extend(news_items)
                        self.update_site_stats(site_name, True, len(news_items), '', response_time)
                        logger.info(f"完成检查站点: {site_name}，获取 {len(news_items)} 条新闻")
                    except TimeoutError:
                        self.update_site_stats(site_name, False, 0, '站点抓取超时（超过180秒）', response_time)
                        logger.error(f"检查站点 {site_name} 超时（180秒），已跳过")
                    except Exception as e:
                        self.update_site_stats(site_name, False, 0, str(e), response_time)
                        logger.error(f"检查站点 {site_name} 失败: {str(e)}")

            # LLM前置打分（在保存之前，分数会写入 item 字典）
            if all_news and self.config.get('llm_filter', {}).get('enabled', False):
                self.scrape_progress['status'] = 'scoring'
                self.scrape_progress['current_site'] = 'LLM大模型打分中...'
                logger.info(f"开始LLM前置打分，共 {len(all_news)} 条新闻")
                all_news = self.llm_score_news(all_news)
                logger.info(f"LLM打分完成")

            self.scrape_progress['status'] = 'done'
            self.scrape_progress['current_site'] = ''

            new_count, new_news_list = self.save_news(all_news)

            # 更新数据版本号，通知前端刷新
            self.news_version = getattr(self, 'news_version', 0) + 1

            if new_count > 0:
                logger.info(f"发现 {new_count} 条新新闻")

                # 关键词筛选
                filtered_news = [n for n in new_news_list if self.match_keyword_rules(n)]

                # LLM阈值筛选（分数已在前置打分时写入）
                threshold = self.config.get('llm_filter', {}).get('relevance_threshold', 60)
                if self.config.get('llm_filter', {}).get('enabled', False):
                    before = len(filtered_news)
                    # 低于阈值的新闻：标记为主题无关（pushed=2），不进入待推送队列
                    below_threshold = [n for n in filtered_news if n.get('llm_relevance', -1) >= 0 and n.get('llm_relevance', 0) < threshold]
                    if below_threshold:
                        self.mark_as_filtered(below_threshold)
                        logger.info(f"LLM筛选：{len(below_threshold)} 条低分新闻标记为主题无关")
                    filtered_news = [n for n in filtered_news if n.get('llm_relevance', -1) < 0 or n.get('llm_relevance', 0) >= threshold]
                    logger.info(f"LLM阈值筛选: {before} -> {len(filtered_news)} 条 (阈值: {threshold})")

                if filtered_news:
                    push_mode = self.config.get('push', {}).get('mode', 'immediate')
                    if push_mode == 'scheduled':
                        logger.info(f"定时模式：{len(filtered_news)} 条新闻已存入待推送队列")
                    else:
                        logger.info(f"{len(filtered_news)} 条新闻通过筛选，开始推送")
                        self.send_notification_with_details(filtered_news)
                        self.mark_as_pushed(filtered_news)
                else:
                    logger.info(f"共 {new_count} 条新新闻，但无通过筛选的新闻，跳过推送")
            else:
                logger.info("没有发现新新闻")
                
        except Exception as e:
            logger.error(f"检查新闻更新失败: {str(e)}")
        finally:
            self.is_running = False
            self.scrape_progress = {
                'current_site': '',
                'completed': 0,
                'total': 0,
                'status': 'idle'
            }
    
    def start_scheduler(self):
        """启动定时任务"""
        # 设置初始的last_check_time为当前时间，这样倒计时就会从完整的间隔开始
        self.last_check_time = datetime.now()

        schedule.every(self.config['check_interval']).minutes.do(self.check_news_updates)

        # 定时推送任务
        push_config = self.config.get('push', {})
        if push_config.get('mode') == 'scheduled':
            for time_str in push_config.get('scheduled_times', []):
                if time_str.strip():
                    schedule.every().day.at(time_str.strip()).do(self.push_pending_news)
                    logger.info(f"定时推送任务已添加：{time_str.strip()}")

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
    return render_template('config.html', config=monitor.config, config_path=str(get_config_path()))

@app.route('/logs')
def logs_page():
    return render_template('logs.html')

@app.route('/sites')
def sites_page():
    return render_template('sites.html')

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

@app.route('/api/config/restore-news-sites', methods=['POST'])
def api_restore_news_sites():
    """恢复默认新闻站点配置"""
    try:
        count = monitor.restore_default_news_sites()
        return jsonify({'success': True, 'count': count, 'message': f'已恢复 {count} 个默认网站配置'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/news')
def api_news():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        per_page = min(per_page, 100)  # 上限100条
        site_filter = request.args.get('site', '')
        pushed_filter = request.args.get('pushed', '')  # '' | '0' | '1' | '2' | 'filtered'

        offset = (page - 1) * per_page

        conn = sqlite3.connect(str(get_db_path()))
        cursor = conn.cursor()

        # 构建 WHERE 条件
        conditions = []
        params = []
        llm_threshold = monitor.config.get('llm_filter', {}).get('relevance_threshold', 60)
        if site_filter:
            conditions.append('site_name = ?')
            params.append(site_filter)
        if pushed_filter == '1':
            conditions.append('pushed = 1')
        elif pushed_filter == '2':
            conditions.append('pushed = 2')
        elif pushed_filter == '0':
            # 未推送：排除已推送和主题无关
            conditions.append('pushed = 0')
            conditions.append(f'(llm_relevance < 0 OR llm_relevance >= {llm_threshold})')
        elif pushed_filter == 'filtered':
            # 主题无关：pushed=2 或（旧数据：未推送且LLM分数低于阈值）
            conditions.append(f'''(pushed = 2 OR (pushed = 0 AND llm_relevance >= 0 AND llm_relevance < {llm_threshold}))''')
        where_clause = (' WHERE ' + ' AND '.join(conditions)) if conditions else ''

        # 总数
        cursor.execute(f'SELECT COUNT(*) FROM news{where_clause}', params)
        total = cursor.fetchone()[0]

        # 分页数据
        cursor.execute(f'''
            SELECT site_name, title, translated_title, url, date, created_at, pushed, llm_relevance, llm_reason
            FROM news{where_clause}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        ''', params + [per_page, offset])
        news = cursor.fetchall()

        # 获取所有来源列表（用于筛选下拉）
        cursor.execute('SELECT DISTINCT site_name FROM news ORDER BY site_name')
        site_names = [row[0] for row in cursor.fetchall()]

        # LLM阈值（用于前端判断"主题无关"）
        llm_threshold = monitor.config.get('llm_filter', {}).get('relevance_threshold', 60)

        conn.close()

        news_list = []
        for item in news:
            relevance = item[7]
            pushed = item[6]  # 0=未推送, 1=已推送, 2=主题无关
            # 推送状态：已推送(1) / 主题无关(2) / 未推送(0)
            if pushed == 2:
                push_status = 'filtered'
            elif pushed == 1:
                push_status = 'pushed'
            elif relevance >= 0 and relevance < llm_threshold:
                push_status = 'filtered'
            else:
                push_status = 'pending'
            news_list.append({
                'site_name': item[0],
                'title': item[1],
                'translated_title': item[2],
                'url': item[3],
                'date': item[4],
                'created_at': item[5],
                'pushed': pushed,
                'push_status': push_status,
                'llm_relevance': relevance,
                'llm_reason': item[8] or ''
            })

        return jsonify({
            'items': news_list,
            'total': total,
            'page': page,
            'per_page': per_page,
            'pages': (total + per_page - 1) // per_page,
            'site_names': site_names,
            'llm_threshold': llm_threshold
        })
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/api/check_now')
def api_check_now():
    try:
        threading.Thread(target=monitor.check_news_updates, daemon=True).start()
        return jsonify({'success': True, 'message': '开始检查新闻更新'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/push_pending', methods=['POST'])
def api_push_pending():
    """手动触发定时推送"""
    try:
        pending_count = monitor.get_pending_count()
        if pending_count == 0:
            return jsonify({'success': False, 'message': '没有待推送的新闻'})
        threading.Thread(target=monitor.push_pending_news, daemon=True).start()
        return jsonify({'success': True, 'message': f'开始推送 {pending_count} 条待发新闻'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/site_stats')
def api_site_stats():
    """获取站点统计"""
    try:
        stats = monitor.get_site_stats()
        return jsonify({'stats': stats})
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/api/retry_llm', methods=['POST'])
def api_retry_llm():
    """重新对LLM打分失败的新闻进行评分"""
    try:
        if monitor.is_running:
            return jsonify({'success': False, 'message': '正在采集中，请稍后再试'})
        if not monitor.config.get('llm_filter', {}).get('enabled', False):
            return jsonify({'success': False, 'message': 'LLM筛选未启用'})
        unrated = monitor.get_unrated_count()
        if unrated == 0:
            return jsonify({'success': True, 'message': '没有需要重试的新闻', 'updated': 0})
        updated = monitor.retry_llm_scoring()
        return jsonify({'success': True, 'message': f'重试完成，成功评分 {updated} 条', 'updated': updated})
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
        with open(str(get_log_path()), 'r', encoding='utf-8') as f:
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

    push_config = monitor.config.get('push', {})
    push_mode = push_config.get('mode', 'immediate')

    # 计算下次推送时间（定时模式）
    next_push_time = None
    if push_mode == 'scheduled':
        scheduled_times = push_config.get('scheduled_times', [])
        if scheduled_times:
            now = datetime.now()
            today = now.strftime('%Y-%m-%d')
            candidates = []
            for t in scheduled_times:
                t = t.strip()
                if not t:
                    continue
                try:
                    dt = datetime.strptime(f"{today} {t}", '%Y-%m-%d %H:%M')
                    if dt > now:
                        candidates.append(dt)
                except ValueError:
                    pass
            # 如果今天没有剩余时间点，取明天第一个
            if not candidates:
                for t in sorted(scheduled_times):
                    t = t.strip()
                    if not t:
                        continue
                    try:
                        dt = datetime.strptime(f"{today} {t}", '%Y-%m-%d %H:%M') + timedelta(days=1)
                        candidates.append(dt)
                        break
                    except ValueError:
                        pass
            if candidates:
                next_push_time = min(candidates)

    return jsonify({
        'is_running': monitor.is_running,
        'driver_available': True,
        'config_loaded': monitor.config is not None,
        'check_interval': monitor.config['check_interval'],
        'next_check_time': next_check_time.isoformat() if next_check_time else None,
        'last_check_time': monitor.last_check_time.isoformat() if hasattr(monitor, 'last_check_time') and monitor.last_check_time else None,
        'push_mode': push_mode,
        'pending_count': monitor.get_pending_count() if push_mode == 'scheduled' else 0,
        'next_push_time': next_push_time.isoformat() if next_push_time else None,
        'scrape_progress': monitor.scrape_progress,
        'news_version': getattr(monitor, 'news_version', 0),
        'unrated_count': monitor.get_unrated_count() if monitor.config.get('llm_filter', {}).get('enabled', False) else 0
    })

@app.route('/api/test_notification', methods=['POST'])
def api_test_notification():
    """测试通知功能，发送最新5条新闻"""
    try:
        # 从数据库获取最新5条新闻
        conn = sqlite3.connect(str(get_db_path()))
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