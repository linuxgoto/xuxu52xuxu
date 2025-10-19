# -*- coding: utf-8 -*-
import os
import time
import random
import logging
import platform
import requests
import html
import io
from datetime import datetime
from configparser import ConfigParser
from tabulate import tabulate
from playwright.sync_api import sync_playwright, TimeoutError
from config import reply_generator

# 创建一个 StringIO 对象用于捕获日志
log_stream = io.StringIO()

# 创建日志记录器
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 创建控制台输出的处理器
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# 创建 log_stream 处理器
stream_handler = logging.StreamHandler(log_stream)
stream_handler.setLevel(logging.INFO)

# 创建格式化器
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# 为处理器设置格式化器
console_handler.setFormatter(formatter)
stream_handler.setFormatter(formatter)

# 将处理器添加到日志记录器中
logger.addHandler(console_handler)
logger.addHandler(stream_handler)

# 自动判断运行环境
IS_GITHUB_ACTIONS = 'GITHUB_ACTIONS' in os.environ
IS_SERVER = platform.system() == "Linux" and not IS_GITHUB_ACTIONS

# 从配置文件或环境变量中读取配置信息
def load_config():
    config = ConfigParser()
    if IS_SERVER:
        config_file = './config/config.ini'
    elif IS_GITHUB_ACTIONS:
        config_file = None
    else:
        config_file = 'config/config.ini'
    
    if config_file and os.path.exists(config_file):
        config.read(config_file)
    
    return config

config = load_config()

USERNAME = os.getenv("LINUXDO_USERNAME", config.get('credentials', 'username', fallback=None))
PASSWORD = os.getenv("LINUXDO_PASSWORD", config.get('credentials', 'password', fallback=None))
LIKE_PROBABILITY = float(os.getenv("LIKE_PROBABILITY", config.get('settings', 'like_probability', fallback='0.02')))
REPLY_PROBABILITY = float(os.getenv("REPLY_PROBABILITY", config.get('settings', 'reply_probability', fallback='0')))
COLLECT_PROBABILITY = float(os.getenv("COLLECT_PROBABILITY", config.get('settings', 'collect_probability', fallback='0.02')))
HOME_URL = config.get('urls', 'home_url', fallback="https://linux.do/")
CONNECT_URL = config.get('urls', 'connect_url', fallback="https://connect.linux.do/")
USE_WXPUSHER = os.getenv("USE_WXPUSHER", config.get('wxpusher', 'use_wxpusher', fallback='false')).lower() == 'true'
APP_TOKEN = os.getenv("APP_TOKEN", config.get('wxpusher', 'app_token', fallback=None))
TOPIC_ID = os.getenv("TOPIC_ID", config.get('wxpusher', 'topic_id', fallback=None))
MAX_TOPICS = int(os.getenv("MAX_TOPICS", config.get('settings', 'max_topics', fallback='10')))

# 检查必要配置
missing_configs = []

if not USERNAME:
    missing_configs.append("USERNAME")
if not PASSWORD:
    missing_configs.append("PASSWORD")
if USE_WXPUSHER and not APP_TOKEN:
    missing_configs.append("APP_TOKEN")
if USE_WXPUSHER and not TOPIC_ID:
    missing_configs.append("TOPIC_ID")

if missing_configs:
    logging.error(f"缺少必要配置: {', '.join(missing_configs)}，请在环境变量或配置文件中设置。")
    exit(1)

class NotificationManager:
    def __init__(self, use_wxpusher, app_token, topic_id):
        self.use_wxpusher = use_wxpusher
        self.app_token = app_token
        self.topic_id = topic_id
    
    def send_message(self, content, summary):
        if self.use_wxpusher:
            try:
                data = {
                    "appToken": self.app_token,
                    "content": content,
                    "summary": summary,
                    "contentType": 2,
                    "topicIds": [self.topic_id],
                    "verifyPayType": 0
                }
                # 使用单独的请求日志记录器来避免混淆
                request_logger = logging.getLogger("request_logger")
                request_logger.info("发送 wxpusher 消息...")
                response = requests.post("https://wxpusher.zjiecode.com/api/send/message", json=data)

                if response.status_code == 200:
                    request_logger.info("wxpusher 消息发送成功")
                else:
                    request_logger.error(f"wxpusher 消息发送失败: {response.status_code}, {response.text}")
                    
            except Exception as e:
                request_logger.error(f"发送 wxpusher 消息时出错: {e}")

class LinuxDoBrowser:
    def __init__(self) -> None:
        logging.info("启动 Playwright...")
        self.pw = sync_playwright().start()
        logging.info("以无头模式启动 Firefox...")
        
        # 改进的浏览器配置，添加更多反检测措施
        self.browser = self.pw.firefox.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--disable-extensions',
                '--disable-gpu',
                '--disable-web-security',
                '--allow-running-insecure-content',
                '--disable-background-timer-throttling',
                '--disable-backgrounding-occluded-windows',
                '--disable-renderer-backgrounding'
            ]
        )
        
        # 创建上下文时添加更真实的用户代理和其他设置
        self.context = self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0',
            locale='zh-CN',
            timezone_id='Asia/Shanghai',
            extra_http_headers={
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'Cache-Control': 'max-age=0'
            }
        )
        
        self.page = self.context.new_page()
        
        # 添加 JavaScript 来隐藏 webdriver 特征
        self.page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
            });
            
            // 覆盖 plugins 属性
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5],
            });
            
            // 覆盖 languages 属性
            Object.defineProperty(navigator, 'languages', {
                get: () => ['zh-CN', 'zh', 'en'],
            });
            
            // 模拟真实的 Chrome 对象
            window.chrome = {
                runtime: {},
            };
            
            // 覆盖权限查询
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Cypress.config('isInteractive') ? 'granted' : 'default' }) :
                    originalQuery(parameters)
            );
        """)
        
        logging.info(f"导航到 {HOME_URL}...")
        
        # 增加页面加载超时时间并添加重试机制
        try:
            self.page.goto(HOME_URL, wait_until='domcontentloaded', timeout=60000)
            # 等待 Cloudflare 挑战完成
            self.wait_for_cloudflare_challenge()
        except Exception as e:
            logging.error(f"初始页面加载失败: {e}")
            raise
            
        logging.info("初始化完成。")

    def wait_for_cloudflare_challenge(self):
        """等待 Cloudflare 挑战完成"""
        logging.info("检查是否需要通过 Cloudflare 验证...")
        
        max_wait_time = 30  # 最大等待30秒
        start_time = time.time()
        
        while time.time() - start_time < max_wait_time:
            try:
                # 检查页面标题是否包含 "Just a moment"
                title = self.page.title()
                if "Just a moment" in title or "Please wait" in title:
                    logging.info("检测到 Cloudflare 挑战，等待验证完成...")
                    time.sleep(2)
                    continue
                
                # 检查是否已经到达正常页面
                if self.page.url.startswith(HOME_URL) and "linux.do" in title.lower():
                    logging.info("Cloudflare 验证完成，页面加载成功")
                    return True
                    
                # 检查是否有登录按钮（表示页面正常加载）
                login_button = self.page.query_selector(".login-button")
                if login_button:
                    logging.info("页面加载完成，找到登录按钮")
                    return True
                    
                time.sleep(1)
                
            except Exception as e:
                logging.warning(f"等待页面加载时出错: {e}")
                time.sleep(1)
        
        # 如果超时，尝试刷新页面
        logging.warning("Cloudflare 验证超时，尝试刷新页面...")
        try:
            self.page.reload(wait_until='domcontentloaded', timeout=30000)
            time.sleep(5)
        except Exception as e:
            logging.error(f"页面刷新失败: {e}")
        
        return False

    def load_messages(self, filename):
        """从指定的文件加载消息并返回消息列表。"""
        script_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(script_dir, filename)
        with open(file_path, 'r', encoding='utf-8') as file:
            messages = file.readlines()
        return [message.strip() for message in messages if message.strip()]

    def get_random_message(self, messages):
        """从列表中选择一个随机消息。"""
        return random.choice(messages)

    def login(self) -> bool:
        try:
            logging.info("尝试登录...")
            
            # 等待页面完全加载
            time.sleep(3)
            
            # 尝试多种可能的登录按钮选择器
            login_selectors = [
                ".login-button .d-button-label",
                ".login-button",
                "button[data-ember-action*='login']",
                ".btn-primary:has-text('登录')",
                ".btn-primary:has-text('Login')",
                "a[href*='login']",
                "#login-link"
            ]
            
            login_clicked = False
            for selector in login_selectors:
                try:
                    if self.page.query_selector(selector):
                        logging.info(f"找到登录按钮: {selector}")
                        self.page.click(selector)
                        login_clicked = True
                        break
                except Exception as e:
                    logging.debug(f"尝试选择器 {selector} 失败: {e}")
                    continue
            
            if not login_clicked:
                logging.error("未找到登录按钮")
                return False
            
            time.sleep(3)
            
            # 尝试多种用户名输入框选择器
            username_selectors = [
                "#login-account-name",
                "input[name='username']",
                "input[type='text']",
                "input[placeholder*='用户名']",
                "input[placeholder*='username']"
            ]
            
            username_filled = False
            for selector in username_selectors:
                try:
                    if self.page.query_selector(selector):
                        logging.info(f"找到用户名输入框: {selector}")
                        self.page.fill(selector, USERNAME)
                        username_filled = True
                        break
                except Exception as e:
                    logging.debug(f"尝试用户名选择器 {selector} 失败: {e}")
                    continue
            
            if not username_filled:
                logging.error("未找到用户名输入框")
                return False
            
            time.sleep(2)
            
            # 尝试多种密码输入框选择器
            password_selectors = [
                "#login-account-password",
                "input[name='password']",
                "input[type='password']",
                "input[placeholder*='密码']",
                "input[placeholder*='password']"
            ]
            
            password_filled = False
            for selector in password_selectors:
                try:
                    if self.page.query_selector(selector):
                        logging.info(f"找到密码输入框: {selector}")
                        self.page.fill(selector, PASSWORD)
                        password_filled = True
                        break
                except Exception as e:
                    logging.debug(f"尝试密码选择器 {selector} 失败: {e}")
                    continue
            
            if not password_filled:
                logging.error("未找到密码输入框")
                return False
            
            time.sleep(2)
            
            # 尝试多种登录提交按钮选择器
            submit_selectors = [
                "#login-button",
                "button[type='submit']",
                ".btn-primary:has-text('登录')",
                ".btn-primary:has-text('Login')",
                "button:has-text('登录')",
                "button:has-text('Login')"
            ]
            
            submit_clicked = False
            for selector in submit_selectors:
                try:
                    if self.page.query_selector(selector):
                        logging.info(f"找到提交按钮: {selector}")
                        self.page.click(selector)
                        submit_clicked = True
                        break
                except Exception as e:
                    logging.debug(f"尝试提交按钮选择器 {selector} 失败: {e}")
                    continue
            
            if not submit_clicked:
                logging.error("未找到提交按钮")
                return False
            
            # 等待登录完成，增加等待时间
            time.sleep(15)
            
            # 检查登录是否成功
            user_selectors = [
                "#current-user",
                ".current-user",
                ".user-menu",
                ".header-dropdown-toggle.current-user"
            ]
            
            for selector in user_selectors:
                user_ele = self.page.query_selector(selector)
                if user_ele:
                    logging.info("登录成功")
                    return True
            
            logging.error("登录失败，请检查账号密码及是否关闭二次认证")
            return False
            
        except TimeoutError:
            logging.error("登录失败：页面加载超时或元素未找到")
            return False
        except Exception as e:
            logging.error(f"登录过程中出现异常: {e}")
            return False

    def click_topic(self):
        try:
            logging.info("开始处理主题...")
            # 随机滚动页面
            self.visit_article_and_scroll(self.page)
            
            # 尝试多种主题列表选择器
            topic_selectors = [
                "#list-area .title",
                ".topic-list .title",
                ".topic-list-item .title",
                "a[data-topic-id]",
                ".topic-title"
            ]
            
            topics = []
            for selector in topic_selectors:
                topics = self.page.query_selector_all(selector)
                if topics:
                    logging.info(f"使用选择器找到主题: {selector}")
                    break
            
            if not topics:
                logging.error("未找到任何主题")
                return
                
            total_topics = len(topics)
            logging.info(f"共找到 {total_topics} 个主题。")

            # 限制处理的最大主题数
            if total_topics > MAX_TOPICS:
                logging.info(f"处理主题数超过最大限制 {MAX_TOPICS}，仅处理前 {MAX_TOPICS} 个主题。")
                topics = topics[:MAX_TOPICS]

            skip_articles = []
            skip_count = 0
            browsed_articles = []
            browsed_count = 0
            liked_articles = []
            like_count = 0
            replied_articles = []
            reply_count = 0
            collected_articles = []
            collect_count = 0

            for idx, topic in enumerate(topics):
                try:
                    article_title = topic.text_content().strip()
                    article_url = topic.get_attribute("href")
                    
                    if not article_url:
                        continue
                        
                    if not article_url.startswith('http'):
                        article_url = HOME_URL.rstrip('/') + '/' + article_url.lstrip('/')

                    # 检查是否为置顶帖子
                    try:
                        parent_element = topic.evaluate_handle("(element) => element.closest('tr') || element.closest('.topic-list-item')")
                        is_pinned = parent_element.query_selector_all(".topic-statuses .pinned, .pinned")
                        
                        if is_pinned:
                            skip_articles.append({"title": article_title, "url": article_url})
                            skip_count += 1
                            logging.info(f"跳过置顶的帖子：{article_title}")
                            continue
                    except Exception as e:
                        logging.debug(f"检查置顶状态时出错: {e}")

                    logging.info(f"打开第 {idx + 1}/{len(topics)} 个主题 ：{article_title} ... ")
                    
                    page = self.context.new_page()
                    
                    try:
                        # 访问文章页面
                        page.goto(article_url, wait_until='domcontentloaded', timeout=30000)
                        # 访问文章数累加
                        browsed_count += 1
                        # 访问文章数信息记录
                        browsed_articles.append({"title": article_title, "url": article_url})
                        # 等待页面完全加载
                        time.sleep(3)  
                        # 随机滚动页面
                        self.visit_article_and_scroll(page)
                        
                        if random.random() < LIKE_PROBABILITY:
                            self.click_like(page)
                            liked_articles.append({"title": article_title, "url": article_url})
                            like_count += 1
                            
                        if random.random() < REPLY_PROBABILITY:
                            reply_message = self.click_reply(page)
                            if reply_message:
                                replied_articles.append(
                                    {"title": article_title, "url": article_url, "reply": reply_message})
                                reply_count += 1
                                
                        if random.random() < COLLECT_PROBABILITY:
                            self.click_collect(page)
                            collected_articles.append({"title": article_title, "url": article_url})
                            collect_count += 1

                    except TimeoutError:
                        logging.warning(f"打开主题 ： {article_title} 超时，跳过该主题。")
                    except Exception as e:
                        logging.warning(f"处理主题 {article_title} 时出错: {e}")
                    finally:
                        time.sleep(3)  # 等待一段时间，防止操作过快导致出错
                        page.close()
                        logging.info(f"已关闭第 {idx + 1}/{len(topics)} 个主题 ： {article_title} ...")
                        
                except Exception as e:
                    logging.error(f"处理主题 {idx + 1} 时出错: {e}")
                    continue

            # 打印统计信息
            logging.info(f"一共跳过了 {skip_count} 篇文章。")
            if skip_count > 0:
                logging.info("--------------跳过的文章信息-----------------")
                logging.info("\n%s",tabulate(skip_articles, headers="keys", tablefmt="pretty"))

            logging.info(f"一共浏览了 {browsed_count} 篇文章。")
            if browsed_count > 0:
                logging.info("--------------浏览的文章信息-----------------")
                logging.info("\n%s",tabulate(browsed_articles, headers="keys", tablefmt="pretty"))

            logging.info(f"一共点赞了 {like_count} 篇文章。")
            if like_count > 0:
                logging.info("--------------点赞的文章信息-----------------")
                logging.info("\n%s",tabulate(liked_articles, headers="keys", tablefmt="pretty"))

            logging.info(f"一共回复了 {reply_count} 篇文章。")
            if reply_count > 0:
                logging.info("--------------回复的文章信息-----------------")
                logging.info("\n%s",tabulate(replied_articles, headers="keys", tablefmt="pretty"))

            logging.info(f"一共加入书签了 {collect_count} 篇文章。")
            if collect_count > 0:
                logging.info("--------------加入书签的文章信息-----------------")
                logging.info("\n%s", tabulate(collected_articles, headers="keys", tablefmt="pretty"))

        except Exception as e:
            logging.error(f"处理主题时出错: {e}")

    def run(self):
        start_time = datetime.now()
        logging.info(f"开始执行时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        try:
            logging.info("开始运行自动化流程...")
            if not self.login():
                return
            self.click_topic()
            self.print_connect_info()
            self.logout()
        except Exception as e:
            logging.error(f"运行过程中出错: {e}")
        finally:
            end_time = datetime.now()
            logging.info(f"结束执行时间: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
            self.context.close()
            self.browser.close()
            self.pw.stop()

            if USE_WXPUSHER:
                elapsed_time = end_time - start_time
                summary = f"Linux.do保活脚本 {end_time.strftime('%Y-%m-%d %H:%M:%S')}"
                
                # 获取并转义日志内容
                log_content = log_stream.getvalue()
                escaped_log_content = html.escape(log_content)
                html_log_content = f"<pre>{escaped_log_content}</pre>"

                # 创建 HTML 格式的内容
                content = (
                    f"<h1>Linux.do保活脚本 {end_time.strftime('%Y-%m-%d %H:%M:%S')}</h1>"
                    f"<br/><p style='color:red;'>"
                    f"账号: {USERNAME}<br/>"
                    f"开始执行时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}<br/>"
                    f"结束执行时间: {end_time.strftime('%Y-%m-%d %H:%M:%S')}<br/>"
                    f"总耗时: {elapsed_time}<br/>"
                    f"</p>"
                    f"<h2>日志内容</h2>"
                    f"{html_log_content}"
                )
                
                wx_pusher = NotificationManager(USE_WXPUSHER, APP_TOKEN, TOPIC_ID)
                wx_pusher.send_message(content, summary)

    def print_connect_info(self):
        try:
            logging.info(f"导航到 {CONNECT_URL}...")
            self.page.goto(CONNECT_URL, wait_until='domcontentloaded', timeout=30000)
            time.sleep(2)
            logging.info(f"当前页面URL: {self.page.url}")
            time.sleep(2)
            rows = self.page.query_selector_all("table tr")
            info = []
            for row in rows:
                cells = row.query_selector_all("td")
                if len(cells) >= 3:
                    project = cells[0].text_content().strip()
                    current = cells[1].text_content().strip()
                    requirement = cells[2].text_content().strip()
                    info.append([project, current, requirement])

            logging.info("--------------Connect Info 在过去 💯 天内-----------------")
            logging.info("\n%s", tabulate(info, headers=["项目", "当前", "要求"], tablefmt="pretty"))
        except TimeoutError:
            logging.error("连接信息页面加载超时")
        except Exception as e:
            logging.error(f"打印连接信息时出错: {e}")

    def click_like(self, page):
        try:
            # 尝试多种点赞按钮选择器
            like_selectors = [
                ".discourse-reactions-reaction-button button",
                ".like-button",
                "button[title*='赞']",
                "button[title*='like']",
                ".btn-like"
            ]
            
            for selector in like_selectors:
                try:
                    page.wait_for_selector(selector, timeout=2000)
                    like_button = page.locator(selector).first
                    if like_button:
                        like_button.click()
                        logging.info("文章已点赞")
                        return
                except Exception:
                    continue
                    
            logging.info("未找到点赞按钮")
        except Exception as e:
            logging.error(f"点赞操作失败: {e}")

    def click_reply(self, page):
        try:
            # 加载消息
            random_message = reply_generator.get_random_reply()

            # 尝试多种回复按钮选择器
            reply_selectors = [
                ".reply.create.btn-icon-text",
                ".reply-button",
                "button[title*='回复']",
                "button[title*='reply']",
                ".btn-reply"
            ]
            
            reply_clicked = False
            for selector in reply_selectors:
                try:
                    page.wait_for_selector(selector, timeout=2000)
                    reply_button = page.locator(selector).first
                    if reply_button:
                        reply_button.click()
                        logging.info("回复按钮已点击")
                        reply_clicked = True
                        break
                except Exception:
                    continue
            
            if not reply_clicked:
                logging.info("未找到回复按钮")
                return None

            # 等待文本区域可见并填入内容
            text_area_selectors = [
                ".d-editor-input",
                "textarea[placeholder*='回复']",
                "textarea[placeholder*='reply']",
                ".composer-input"
            ]
            
            text_filled = False
            for selector in text_area_selectors:
                try:
                    page.wait_for_selector(selector, timeout=2000)
                    text_area = page.locator(selector).first
                    if text_area:
                        text_area.fill(random_message)
                        logging.info(f"回复内容: {random_message}")
                        text_filled = True
                        break
                except Exception:
                    continue
            
            if not text_filled:
                logging.warning("未找到回复文本框")
                return None

            # 点击提交按钮
            submit_selectors = [
                ".save-or-cancel .btn-primary.create",
                ".submit-reply",
                "button[type='submit']",
                ".btn-primary:has-text('回复')",
                ".btn-primary:has-text('Reply')"
            ]
            
            for selector in submit_selectors:
                try:
                    page.wait_for_selector(selector, timeout=2000)
                    submit_button = page.locator(selector).first
                    if submit_button:
                        time.sleep(2)
                        submit_button.click()
                        logging.info("回复已提交")
                        return random_message
                except Exception:
                    continue
                    
            logging.warning("未找到提交按钮")
            return None

        except Exception as e:
            logging.error(f"回复操作失败: {e}")
            return None

    def click_collect(self, page):
        try:
            # 尝试多种书签按钮选择器
            bookmark_selectors = [
                ".btn.bookmark-menu-trigger",
                ".bookmark-button",
                "button[title*='书签']",
                "button[title*='bookmark']",
                ".btn-bookmark"
            ]
            
            for selector in bookmark_selectors:
                try:
                    page.wait_for_selector(selector, timeout=2000)
                    bookmark_button = page.locator(selector).first
                    if bookmark_button:
                        time.sleep(2)
                        bookmark_button.click()
                        logging.info("帖子已加入书签")
                        return
                except Exception:
                    continue
                    
            logging.warning("未找到书签按钮")

        except Exception as e:
            logging.error(f"加入书签操作失败: {e}")

    def visit_article_and_scroll(self, page):
        try:
            # 随机滚动页面5到10秒
            scroll_duration = random.randint(5, 10)
            logging.info(f"随机滚动页面 {scroll_duration} 秒...")
            scroll_end_time = time.time() + scroll_duration

            while time.time() < scroll_end_time:
                scroll_distance = random.randint(300, 600)  # 每次滚动的距离，随机选择
                page.mouse.wheel(0, scroll_distance)
                time.sleep(random.uniform(0.5, 1.5))  # 随机等待0.5到1.5秒再滚动

            logging.info("页面滚动完成")

        except Exception as e:
            logging.error(f"滚动页面时出错: {e}")

    def logout(self):
        try:
            logging.info(f"导航到 {HOME_URL}...")
            self.page.goto(HOME_URL, wait_until='domcontentloaded', timeout=30000)
            time.sleep(2)

            # 尝试多种用户菜单按钮选择器
            user_menu_selectors = [
                "#current-user .icon",
                ".current-user .icon",
                ".user-menu-button",
                ".header-dropdown-toggle"
            ]
            
            menu_clicked = False
            for selector in user_menu_selectors:
                try:
                    self.page.wait_for_selector(selector, timeout=2000)
                    user_menu_button = self.page.locator(selector).first
                    if user_menu_button:
                        user_menu_button.click()
                        logging.info("成功点击用户菜单按钮")
                        menu_clicked = True
                        break
                except Exception:
                    continue
            
            if not menu_clicked:
                logging.info("未找到用户菜单按钮")
                return

            time.sleep(2)

            # 尝试多种个人资料标签选择器
            profile_selectors = [
                "#user-menu-button-profile",
                ".user-menu-profile",
                "button:has-text('个人资料')",
                "button:has-text('Profile')"
            ]
            
            profile_clicked = False
            for selector in profile_selectors:
                try:
                    self.page.wait_for_selector(selector, timeout=2000)
                    profile_tab_button = self.page.locator(selector).first
                    if profile_tab_button:
                        profile_tab_button.click()
                        logging.info("成功点击个人资料标签")
                        profile_clicked = True
                        break
                except Exception:
                    continue
            
            if not profile_clicked:
                logging.info("未找到个人资料标签")
                return

            time.sleep(2)

            # 尝试多种退出按钮选择器
            logout_selectors = [
                ".logout .btn",
                ".logout-button",
                "button:has-text('退出')",
                "button:has-text('Logout')",
                "a:has-text('退出')",
                "a:has-text('Logout')"
            ]
            
            for selector in logout_selectors:
                try:
                    self.page.wait_for_selector(selector, timeout=2000)
                    logout_button = self.page.locator(selector).first
                    if logout_button:
                        logout_button.click()
                        logging.info("成功点击退出按钮")
                        return
                except Exception:
                    continue
                    
            logging.info("未找到退出按钮")

        except Exception as e:
            logging.error(f"退出操作失败: {e}")

if __name__ == "__main__":
    ldb = LinuxDoBrowser()
    ldb.run()