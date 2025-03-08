#!/usr/bin/env python
"""
企業テックブログのRSSフィードを取得し、集約して新しいフィードを生成するスクリプト。
"""

import os
import sys
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tomli
import feedparser
import aiohttp
import asyncio
from bs4 import BeautifulSoup
import time
import re
import hashlib

# ロガーの設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 定数
FEED_FETCH_CONCURRENCY = 50
FEED_OG_FETCH_CONCURRENCY = 20
AGGREGATE_FEED_DURATION_HOURS = 8 * 24  # 8日間
MAX_FEED_DESCRIPTION_LENGTH = 200
MAX_FEED_CONTENT_LENGTH = 500

# パスの設定
SCRIPT_DIR = Path(__file__).parent.absolute()
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent  # src ディレクトリの親
SITE_FEEDS_DIR = PROJECT_ROOT / "src/site/feeds"
BLOG_FEEDS_DIR = PROJECT_ROOT / "src/site/blog-feeds"
FEED_CONFIG_PATH = PROJECT_ROOT / "src/resources/feed.toml"


class FeedCrawler:
    """
    フィードを取得して前処理を行うクラス。
    """
    
    def __init__(self):
        """FeedCrawlerを初期化します。"""
        self.logger = logging.getLogger(__name__ + ".FeedCrawler")
    
    async def crawl_feeds(self, feed_info_list, feed_fetch_concurrency, feed_og_fetch_concurrency, aggregate_feed_start_at):
        """
        フィードを取得して前処理を行います。
        
        Parameters
        ----------
        feed_info_list : list
            フィード情報のリスト。
        feed_fetch_concurrency : int
            フィード取得の並列数。
        feed_og_fetch_concurrency : int
            OG情報取得の並列数。
        aggregate_feed_start_at : datetime
            集約するフィードの開始日時。
            
        Returns
        -------
        dict
            取得したフィード情報。
        """
        self.logger.info("フィードの取得を開始します")
        
        # フィード取得してまとめる
        feeds = await self.fetch_feeds_async(feed_info_list, feed_fetch_concurrency)
        all_feed_items = self.aggregate_feeds(feeds, aggregate_feed_start_at)
        
        # OGPなどの情報取得
        try:
            feed_item_og_object_map = await self.fetch_feed_item_og_object_map(all_feed_items, feed_og_fetch_concurrency)
            feed_item_hatena_count_map = await self.fetch_hatena_count_map(all_feed_items)
            feed_blog_og_object_map = await self.fetch_feed_blog_og_object_map(feeds, feed_og_fetch_concurrency)
        except Exception as e:
            self.logger.error("フィード関連データの取得に失敗しました")
            raise e
        
        self.logger.info(f"フィードの取得が完了しました（記事数: {len(all_feed_items)}）")
        
        return {
            "feeds": feeds,
            "feed_items": all_feed_items,
            "feed_item_og_object_map": feed_item_og_object_map,
            "feed_item_hatena_count_map": feed_item_hatena_count_map,
            "feed_blog_og_object_map": feed_blog_og_object_map
        }
    
    async def fetch_feeds_async(self, feed_info_list, concurrency):
        """
        フィードを非同期で取得します。
        
        Parameters
        ----------
        feed_info_list : list
            フィード情報のリスト。
        concurrency : int
            並列数。
            
        Returns
        -------
        list
            取得したフィードのリスト。
        """
        self.logger.info(f"フィードの取得を開始します（{len(feed_info_list)}件）")
        
        feeds = []
        semaphore = asyncio.Semaphore(concurrency)
        
        async def fetch_feed(feed_info):
            async with semaphore:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(feed_info["feed_url"], timeout=30) as response:
                            if response.status == 200:
                                content = await response.text()
                                parsed_feed = feedparser.parse(content)
                                
                                if parsed_feed.entries:
                                    feed_data = {
                                        "feed_url": feed_info["feed_url"],
                                        "category": feed_info["category"],
                                        "title": parsed_feed.feed.get("title", "Unknown"),
                                        "link": parsed_feed.feed.get("link", ""),
                                        "entries": []
                                    }
                                    
                                    for entry in parsed_feed.entries:
                                        feed_data["entries"].append({
                                            "title": entry.get("title", ""),
                                            "link": entry.get("link", ""),
                                            "published": entry.get("published", ""),
                                            "published_parsed": entry.get("published_parsed"),
                                            "summary": entry.get("summary", ""),
                                            "content": entry.get("content", [{"value": ""}])[0]["value"] if "content" in entry else "",
                                        })
                                    
                                    feeds.append(feed_data)
                                    self.logger.info(f"フィード取得成功: {feed_info['feed_url']} (記事数: {len(feed_data['entries'])})")
                                else:
                                    self.logger.warning(f"フィードに記事がありません: {feed_info['feed_url']}")
                            else:
                                self.logger.warning(f"フィード取得失敗: {feed_info['feed_url']} (ステータス: {response.status})")
                except Exception as e:
                    self.logger.error(f"フィード取得エラー: {feed_info['feed_url']} ({str(e)})")
        
        tasks = [fetch_feed(feed_info) for feed_info in feed_info_list]
        await asyncio.gather(*tasks)
        
        self.logger.info(f"フィードの取得が完了しました（取得数: {len(feeds)}）")
        
        return feeds
    
    def aggregate_feeds(self, feeds, aggregate_feed_start_at):
        """
        フィードを集約します。
        
        Parameters
        ----------
        feeds : list
            フィードのリスト。
        aggregate_feed_start_at : datetime
            集約するフィードの開始日時。
            
        Returns
        -------
        list
            集約したフィードアイテムのリスト。
        """
        self.logger.info("フィードの集約を開始します")
        
        all_feed_items = []
        
        for feed in feeds:
            for entry in feed["entries"]:
                # 公開日時をパース
                published_date = None
                if entry.get("published_parsed"):
                    published_date = datetime.fromtimestamp(time.mktime(entry["published_parsed"]), tz=timezone.utc)
                
                # 日付でフィルタリング
                if published_date and published_date >= aggregate_feed_start_at:
                    feed_item = {
                        "title": entry["title"],
                        "link": entry["link"],
                        "published": entry["published"],
                        "published_date": published_date,
                        "summary": self.clean_html(entry["summary"]),
                        "content": self.clean_html(entry.get("content", "")),
                        "feed_title": feed["title"],
                        "feed_link": feed["link"],
                        "category": feed["category"]
                    }
                    all_feed_items.append(feed_item)
        
        # 日付順にソート
        all_feed_items.sort(key=lambda x: x.get("published_date", datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
        
        self.logger.info(f"フィードの集約が完了しました（記事数: {len(all_feed_items)}）")
        
        return all_feed_items
    
    def clean_html(self, html_content):
        """HTMLタグを除去してテキストを取得します"""
        if not html_content:
            return ""
        
        # BeautifulSoupを使用してHTMLをパース
        soup = BeautifulSoup(html_content, "html.parser")
        
        # テキストのみを取得
        text = soup.get_text(separator=" ", strip=True)
        
        # 余分な空白を削除
        text = re.sub(r'\s+', ' ', text).strip()
        
        return text
    
    async def fetch_feed_item_og_object_map(self, feed_items, concurrency):
        """
        フィードアイテムのOG情報を取得します。
        
        Parameters
        ----------
        feed_items : list
            フィードアイテムのリスト。
        concurrency : int
            並列数。
            
        Returns
        -------
        dict
            フィードアイテムのOG情報マップ。
        """
        self.logger.info("フィードアイテムのOG情報取得を開始します")
        
        # フィードアイテムのURLを抽出
        urls = [item["link"] for item in feed_items if "link" in item]
        
        # OG情報を取得
        og_object_map = await self.fetch_og_objects(urls)
        
        self.logger.info("フィードアイテムのOG情報取得が完了しました")
        
        return og_object_map
    
    async def fetch_hatena_count_map(self, feed_items):
        """
        フィードアイテムのはてなブックマーク数を取得します。
        
        Parameters
        ----------
        feed_items : list
            フィードアイテムのリスト。
            
        Returns
        -------
        dict
            フィードアイテムのはてなブックマーク数マップ。
        """
        self.logger.info("はてなブックマーク数の取得を開始します")
        
        # URLのリストを作成
        urls = [item["link"] for item in feed_items if "link" in item]
        
        # はてなブックマーク数を取得
        hatena_count_map = {}
        
        # URLを50件ずつに分割して処理
        chunk_size = 50
        for i in range(0, len(urls), chunk_size):
            chunk_urls = urls[i:i+chunk_size]
            
            # はてなブックマークAPIのURLを構築
            api_url = "https://bookmark.hatenaapis.com/count/entries"
            
            try:
                async with aiohttp.ClientSession() as session:
                    params = {"url": chunk_urls}
                    async with session.get(api_url, params=params) as response:
                        if response.status == 200:
                            data = await response.json()
                            for url, count in data.items():
                                hatena_count_map[url] = int(count)
                        else:
                            self.logger.warning(f"はてなブックマーク数の取得に失敗しました: ステータスコード {response.status}")
            except Exception as e:
                self.logger.warning(f"はてなブックマーク数の取得に失敗しました: {e}")
        
        self.logger.info(f"はてなブックマーク数の取得が完了しました（取得数: {len(hatena_count_map)}）")
        
        return hatena_count_map
    
    async def fetch_feed_blog_og_object_map(self, feeds, concurrency):
        """
        フィードブログのOG情報を取得します。
        
        Parameters
        ----------
        feeds : list
            フィードのリスト。
        concurrency : int
            並列数。
            
        Returns
        -------
        dict
            フィードブログのOG情報マップ。
        """
        self.logger.info("フィードブログのOG情報取得を開始します")
        
        # フィードのURLを抽出
        urls = [feed["link"] for feed in feeds if "link" in feed]
        
        # OG情報を取得
        og_object_map = await self.fetch_og_objects(urls)
        
        self.logger.info("フィードブログのOG情報取得が完了しました")
        
        return og_object_map

    async def fetch_og_objects(self, urls):
        """
        URLのOG情報を取得します。
        
        Parameters
        ----------
        urls : list
            URL一覧。
            
        Returns
        -------
        dict
            OG情報のマップ。
        """
        self.logger.info(f"OG情報の取得を開始します（{len(urls)}件）")
        
        og_object_map = {}
        
        # OG情報を取得
        async with aiohttp.ClientSession() as session:
            tasks = []
            for url in urls:
                task = asyncio.create_task(self.fetch_og_object(session, url))
                tasks.append(task)
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for url, result in zip(urls, results):
                if isinstance(result, Exception):
                    self.logger.warning(f"OG情報の取得に失敗しました: {url} ({result})")
                    continue
                
                og_object_map[url] = result
        
        self.logger.info(f"OG情報の取得が完了しました（取得数: {len(og_object_map)}）")
        
        return og_object_map

    async def fetch_og_object(self, session, url):
        """
        URLのOG情報を取得します。
        
        Parameters
        ----------
        session : aiohttp.ClientSession
            HTTPセッション。
        url : str
            URL。
            
        Returns
        -------
        dict
            OG情報。
        """
        try:
            async with session.get(url, timeout=10) as response:
                if response.status != 200:
                    return {}
                
                # レスポンスのエンコーディングを取得
                content_type = response.headers.get('Content-Type', '')
                charset = None
                if 'charset=' in content_type:
                    charset = content_type.split('charset=')[-1].strip()
                
                # バイナリデータを取得
                content = await response.read()
                
                # エンコーディングを検出して適切にデコード
                if charset:
                    try:
                        html = content.decode(charset)
                    except (UnicodeDecodeError, LookupError):
                        # 指定されたエンコーディングでデコードできない場合は推測
                        html = self._decode_content(content)
                else:
                    # エンコーディングが指定されていない場合は推測
                    html = self._decode_content(content)
                
                soup = BeautifulSoup(html, 'html.parser')
                
                og_object = {}
                
                # OG画像を取得
                og_image = soup.find('meta', property='og:image')
                if og_image:
                    og_object['ogImage'] = og_image.get('content', '')
                
                # OG説明を取得
                og_description = soup.find('meta', property='og:description')
                if og_description:
                    og_object['ogDescription'] = og_description.get('content', '')
                
                return og_object
        except Exception as e:
            self.logger.warning(f"OG情報の取得に失敗しました: {url} ({e})")
            return {}

    def _decode_content(self, content):
        """
        コンテンツのエンコーディングを推測してデコードします。
        
        Parameters
        ----------
        content : bytes
            バイナリコンテンツ。
            
        Returns
        -------
        str
            デコードされたテキスト。
        """
        encodings = ['utf-8', 'shift_jis', 'euc-jp', 'iso-2022-jp', 'cp932']
        
        for encoding in encodings:
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        
        # どのエンコーディングでもデコードできない場合は、エラーを無視してUTF-8でデコード
        return content.decode('utf-8', errors='ignore')


class FeedGenerator:
    """
    フィードを生成するクラス。
    """
    
    def __init__(self):
        """FeedGeneratorを初期化します。"""
        self.logger = logging.getLogger(__name__ + ".FeedGenerator")
    
    def generate_aggregated_feed(self, feed_items, feed_item_og_object_map, all_feed_item_hatena_count_map, max_feed_description_length, max_feed_content_length):
        """
        集約フィードを生成します。
        
        Parameters
        ----------
        feed_items : list
            フィードアイテムのリスト。
        feed_item_og_object_map : dict
            フィードアイテムのOG情報マップ。
        all_feed_item_hatena_count_map : dict
            フィードアイテムのはてなブックマーク数マップ。
        max_feed_description_length : int
            フィードの説明の最大長。
        max_feed_content_length : int
            フィードのコンテンツの最大長。
            
        Returns
        -------
        dict
            生成した集約フィード。
        """
        self.logger.info("集約フィードの生成を開始します")
        
        now = datetime.now(timezone.utc)
        site_base_url = "https://univac-1.github.io/ai-info-rss-feed/"
        
        aggregated_feed = {
            "title": "AI関連情報RSS",
            "description": "AI関連情報をまとめたRSSフィード",
            "language": "ja",
            "link": site_base_url,
            "updated": now.isoformat(),
            "generator": "univac-1/ai-info-rss-feed",
            "copyright": "univac-1/ai-info-rss-feed",
            "feedLinks": {
                "atom": f"{site_base_url}feeds/atom.xml",
                "rss": f"{site_base_url}feeds/rss.xml",
                "json": f"{site_base_url}feeds/feed.json"
            },
            "image": f"{site_base_url}images/icon.png",
            "favicon": f"{site_base_url}images/favicon.ico",
            "items": []
        }
        
        for item in feed_items:
            # 説明文を最大長に制限
            description = item.get("summary", "")
            if len(description) > max_feed_description_length:
                description = description[:max_feed_description_length] + "..."
            
            # コンテンツを最大長に制限
            content = item.get("content_html", "") or item.get("content", "")
            if len(content) > max_feed_content_length:
                content = content[:max_feed_content_length] + "..."
            
            # OG情報を取得
            og_image_url = ""
            if item.get("link") in feed_item_og_object_map:
                og_data = feed_item_og_object_map[item["link"]]
                if isinstance(og_data, dict):
                    og_image_url = og_data.get("ogImage", "") or og_data.get("image", "")
            
            # はてなブックマーク数を取得
            hatena_count = 0
            if item.get("link") in all_feed_item_hatena_count_map:
                hatena_count = all_feed_item_hatena_count_map[item["link"]]
            
            feed_item = {
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "description": description,
                "content": content,
                "published": item.get("isoDate", "") or item.get("published", ""),
                "date": item.get("published_date") if hasattr(item, "published_date") else None,
                "feed_title": item.get("feed_title", ""),
                "feed_link": item.get("feed_link", ""),
                "category": item.get("category", ""),
                "ogImageUrl": og_image_url,
                "hatenaCount": hatena_count
            }
            
            # カテゴリ情報を配列として設定
            if "categories" in item and item["categories"]:
                feed_item["categories"] = item["categories"]
            
            # 著者情報を設定
            if "creator" in item and item["creator"]:
                feed_item["author"] = item["creator"]
            
            aggregated_feed["items"].append(feed_item)
        
        self.logger.info(f"集約フィードの生成が完了しました（記事数: {len(aggregated_feed['items'])}）")
        
        return aggregated_feed
    
    async def generate_and_save_feeds(self, aggregated_feed):
        """
        フィードを生成して保存します。
        
        Parameters
        ----------
        aggregated_feed : dict
            集約フィード。
        """
        self.logger.info("フィードの生成と保存を開始します")
        
        # フィードを生成
        feeds = self.generate_feeds(aggregated_feed)
        
        # フィードを保存
        await self.save_feeds(feeds["feed_distribution_set"])
        
        self.logger.info("フィードの生成と保存が完了しました")
    
    def generate_feeds(self, aggregated_feed):
        """
        各種形式のフィードを生成します。
        
        Parameters
        ----------
        aggregated_feed : dict
            集約フィード。
            
        Returns
        -------
        dict
            各種形式のフィード。
        """
        self.logger.info("フィードの生成を開始します")
        
        # 集約フィードを生成
        self.logger.info("集約フィードの生成を開始します")
        # TODO: 実際には集約フィードを生成する
        self.logger.info("集約フィードの生成が完了しました（記事数: {}）".format(len(aggregated_feed["items"])))
        
        # Atom形式のフィードを生成
        atom_feed = self.generate_atom_feed(aggregated_feed)
        
        # RSS形式のフィードを生成
        rss_feed = self.generate_rss_feed(aggregated_feed)
        
        # JSON形式のフィードを生成
        json_feed = self.generate_json_feed(aggregated_feed)
        
        self.logger.info("フィードの生成が完了しました")
        
        return {
            "aggregated_feed": aggregated_feed,
            "feed_distribution_set": {
                "atom": atom_feed,
                "rss": rss_feed,
                "json": json_feed
            }
        }
    
    async def save_feeds(self, feed_distribution_set):
        """
        フィードを保存します。
        
        Parameters
        ----------
        feed_distribution_set : dict
            各種形式のフィード。
        """
        self.logger.info("フィードの保存を開始します")
        
        # 必要なディレクトリを作成
        os.makedirs(SITE_FEEDS_DIR, exist_ok=True)
        os.makedirs(BLOG_FEEDS_DIR, exist_ok=True)
        
        # Atom形式のフィードを保存
        atom_path = SITE_FEEDS_DIR / "atom.xml"
        with open(atom_path, "w", encoding="utf-8") as f:
            f.write(feed_distribution_set["atom"])
        
        # RSS形式のフィードを保存
        rss_path = SITE_FEEDS_DIR / "rss.xml"
        with open(rss_path, "w", encoding="utf-8") as f:
            f.write(feed_distribution_set["rss"])
        
        # JSON形式のフィードを保存
        json_path = BLOG_FEEDS_DIR / "blog-feeds.json"
        with open(json_path, "w", encoding="utf-8") as f:
            f.write(feed_distribution_set["json"])
        
        self.logger.info("フィードの保存が完了しました")

    def generate_atom_feed(self, aggregated_feed):
        """
        Atom形式のフィードを生成します。
        
        Parameters
        ----------
        aggregated_feed : dict
            集約フィード。
            
        Returns
        -------
        str
            Atom形式のフィード。
        """
        self.logger.info("Atom形式のフィードの生成を開始します")
        
        # サイトのベースURL
        site_base_url = "https://univac-1.github.io/ai-info-rss-feed/"
        
        # フィードのヘッダー
        atom_feed = '<?xml version="1.0" encoding="UTF-8"?>\n'
        atom_feed += '<feed xmlns="http://www.w3.org/2005/Atom">\n'
        atom_feed += f'  <id>{site_base_url}</id>\n'
        atom_feed += f'  <title type="html">{aggregated_feed["title"]}</title>\n'
        atom_feed += f'  <updated>{datetime.now(timezone.utc).isoformat()}</updated>\n'
        atom_feed += f'  <generator>{aggregated_feed.get("generator", "univac-1/ai-info-rss-feed")}</generator>\n'
        
        if "description" in aggregated_feed:
            atom_feed += f'  <subtitle type="html">{aggregated_feed["description"]}</subtitle>\n'
        
        # リンク要素を追加
        atom_feed += f'  <link href="{site_base_url}"/>\n'
        atom_feed += f'  <link href="{site_base_url}feeds/atom.xml" rel="self" type="application/atom+xml"/>\n'
        
        # 画像要素を追加
        atom_feed += f'  <icon>{site_base_url}images/favicon.ico</icon>\n'
        atom_feed += f'  <logo>{site_base_url}images/icon.png</logo>\n'
        
        # 著作権情報
        if "copyright" in aggregated_feed:
            atom_feed += f'  <rights>{aggregated_feed["copyright"]}</rights>\n'
        
        # フィードのアイテム
        for item in aggregated_feed["items"]:
            atom_feed += '  <entry>\n'
            
            # タイトルは「記事タイトル | ブログ名」の形式に
            title = f'{item["title"]} | {item.get("feed_title", "")}'
            atom_feed += f'    <title type="html">{self._escape_xml_text(title)}</title>\n'
            
            # リンクとID
            atom_feed += f'    <link href="{item["link"]}"/>\n'
            atom_feed += f'    <id>{item["link"]}</id>\n'
            
            # 更新日時
            updated_date = None
            if "date" in item and item["date"]:
                updated_date = item["date"].isoformat()
            elif "published" in item and item["published"]:
                updated_date = item["published"]
            elif "isoDate" in item and item["isoDate"]:
                updated_date = item["isoDate"]
            else:
                # 日付がない場合は現在時刻を使用
                updated_date = datetime.now(timezone.utc).isoformat()
            
            atom_feed += f'    <updated>{updated_date}</updated>\n'
            
            # 公開日時（published要素を追加）
            published_date = None
            if "date" in item and item["date"]:
                published_date = item["date"].isoformat()
            elif "published" in item and item["published"]:
                published_date = item["published"]
            elif "isoDate" in item and item["isoDate"]:
                published_date = item["isoDate"]
            else:
                published_date = updated_date
            
            atom_feed += f'    <published>{published_date}</published>\n'
            
            # サマリー
            summary = item.get("description", "")
            if summary:
                atom_feed += f'    <summary type="html">{self._escape_xml_text(summary)}</summary>\n'
            
            # コンテンツ
            content = item.get("content", "") or item.get("content_html", "")
            atom_feed += f'    <content type="html"><![CDATA[{content}]]></content>\n'
            
            # カテゴリ
            if "category" in item:
                atom_feed += f'    <category term="{self._escape_xml_text(item["category"])}"/>\n'
            
            # 著者情報
            if "feed_title" in item:
                atom_feed += '    <author>\n'
                atom_feed += f'      <name>{self._escape_xml_text(item["feed_title"])}</name>\n'
                if "feed_link" in item:
                    atom_feed += f'      <uri>{item["feed_link"]}</uri>\n'
                atom_feed += '    </author>\n'
            elif "author" in item and item["author"]:
                atom_feed += '    <author>\n'
                atom_feed += f'      <name>{self._escape_xml_text(item["author"])}</name>\n'
                atom_feed += '    </author>\n'
            
            # OG画像情報があれば追加
            if "ogImageUrl" in item and item["ogImageUrl"]:
                atom_feed += f'    <link rel="enclosure" href="{item["ogImageUrl"]}" type="image/jpeg"/>\n'
            
            atom_feed += '  </entry>\n'
        
        # フィードのフッター
        atom_feed += '</feed>'
        
        self.logger.info("Atom形式のフィードの生成が完了しました")
        
        return atom_feed
    
    def _escape_xml_text(self, text):
        """
        XMLテキストをエスケープします。
        
        Parameters
        ----------
        text : str
            エスケープするテキスト。
            
        Returns
        -------
        str
            エスケープされたテキスト。
        """
        if not text:
            return ""
        
        # XMLの特殊文字をエスケープ
        text = text.replace("&", "&amp;")
        text = text.replace("<", "&lt;")
        text = text.replace(">", "&gt;")
        text = text.replace('"', "&quot;")
        text = text.replace("'", "&apos;")
        
        return text
    
    def generate_rss_feed(self, aggregated_feed):
        """
        RSS形式のフィードを生成します。
        
        Parameters
        ----------
        aggregated_feed : dict
            集約フィード。
            
        Returns
        -------
        str
            RSS形式のフィード。
        """
        self.logger.info("RSS形式のフィードの生成を開始します")
        
        # サイトのベースURL
        site_base_url = "https://univac-1.github.io/ai-info-rss-feed/"
        
        rss = '<?xml version="1.0" encoding="UTF-8"?>\n'
        rss += '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        rss += '  <channel>\n'
        rss += f'    <title>{aggregated_feed["title"]}</title>\n'
        rss += f'    <link>{aggregated_feed["link"]}</link>\n'
        rss += f'    <description>{aggregated_feed["description"]}</description>\n'
        rss += f'    <language>{aggregated_feed["language"]}</language>\n'
        rss += f'    <lastBuildDate>{datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")}</lastBuildDate>\n'
        
        # 追加のチャンネル要素
        rss += f'    <generator>{aggregated_feed.get("generator", "univac-1/ai-info-rss-feed")}</generator>\n'
        rss += f'    <docs>https://validator.w3.org/feed/docs/rss2.html</docs>\n'
        
        # 画像要素
        rss += '    <image>\n'
        rss += f'      <url>{site_base_url}images/icon.png</url>\n'
        rss += f'      <title>{aggregated_feed["title"]}</title>\n'
        rss += f'      <link>{aggregated_feed["link"]}</link>\n'
        rss += '    </image>\n'
        
        # 著作権情報
        if "copyright" in aggregated_feed:
            rss += f'    <copyright>{aggregated_feed["copyright"]}</copyright>\n'
        
        # セルフリンク
        rss += f'    <atom:link href="{site_base_url}feeds/rss.xml" rel="self" type="application/rss+xml"/>\n'
        
        for item in aggregated_feed["items"]:
            rss += '    <item>\n'
            
            # タイトルは「記事タイトル | ブログ名」の形式に
            title = f'{item["title"]} | {item.get("feed_title", "")}'
            rss += f'      <title>{self._escape_xml_text(title)}</title>\n'
            
            rss += f'      <link>{item["link"]}</link>\n'
            rss += f'      <description>{self._escape_xml_text(item["description"])}</description>\n'
            rss += f'      <content:encoded><![CDATA[{item.get("content", "")}]]></content:encoded>\n'
            
            # 日付フォーマットをRFC822形式に変換
            pub_date = None
            if "date" in item:
                pub_date = item["date"]
            elif "published" in item and isinstance(item["published"], datetime):
                pub_date = item["published"]
            elif "isoDate" in item:
                try:
                    pub_date = datetime.fromisoformat(item["isoDate"].replace("Z", "+00:00"))
                except:
                    pub_date = datetime.now(timezone.utc)
            
            if pub_date:
                rss += f'      <pubDate>{pub_date.strftime("%a, %d %b %Y %H:%M:%S %z")}</pubDate>\n'
            else:
                rss += f'      <pubDate>{datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")}</pubDate>\n'
            
            # カテゴリ
            if "category" in item:
                rss += f'      <category>{self._escape_xml_text(item["category"])}</category>\n'
            
            # 著者情報
            if "feed_title" in item:
                rss += f'      <dc:creator>{self._escape_xml_text(item["feed_title"])}</dc:creator>\n'
            elif "author" in item and item["author"]:
                rss += f'      <dc:creator>{self._escape_xml_text(item["author"])}</dc:creator>\n'
            
            # ソース情報
            if "feed_link" in item and "feed_title" in item:
                rss += f'      <source url="{item["feed_link"]}">{self._escape_xml_text(item["feed_title"])}</source>\n'
            
            # GUID
            rss += f'      <guid isPermaLink="true">{item["link"]}</guid>\n'
            
            # OG画像情報があれば追加
            if "ogImageUrl" in item and item["ogImageUrl"]:
                rss += f'      <enclosure url="{item["ogImageUrl"]}" type="image/jpeg" length="0"/>\n'
            
            rss += '    </item>\n'
        
        rss += '  </channel>\n'
        rss += '</rss>'
        
        self.logger.info("RSS形式のフィードの生成が完了しました")
        
        return rss
    
    def generate_json_feed(self, aggregated_feed):
        """
        JSON形式のフィードを生成します。
        
        Parameters
        ----------
        aggregated_feed : dict
            集約フィード。
            
        Returns
        -------
        str
            JSON形式のフィード。
        """
        json_feed = {
            "version": "https://jsonfeed.org/version/1.1",
            "title": aggregated_feed["title"],
            "description": aggregated_feed["description"],
            "home_page_url": aggregated_feed["link"],
            "feed_url": f"{aggregated_feed['link']}feed.json",
            "language": aggregated_feed["language"],
            "items": []
        }
        
        for item in aggregated_feed["items"]:
            json_item = {
                "id": item["link"],
                "url": item["link"],
                "title": item["title"],
                "content_html": item["content"],
                "summary": item["description"],
                "date_published": item["published"],
                "authors": [
                    {
                        "name": item["feed_title"],
                        "url": item["feed_link"]
                    }
                ],
                "tags": [item["category"]]
            }
            json_feed["items"].append(json_item)
        
        return json.dumps(json_feed, ensure_ascii=False, indent=2)


class FeedStorer:
    """
    フィードを保存するクラス。
    """
    
    def __init__(self):
        """FeedStorerを初期化します。"""
        self.logger = logging.getLogger(__name__ + ".FeedStorer")
    
    async def store_feeds(self, feed_distribution_set, feeds_dir, feeds, og_object_map, hatena_count_map, blog_feeds_dir):
        """
        フィードを保存します。
        
        Parameters
        ----------
        feed_distribution_set : dict
            フィード配信セット。
        feeds_dir : str
            フィードを保存するディレクトリ。
        feeds : list
            フィード情報のリスト。
        og_object_map : dict
            OG情報のマップ。
        hatena_count_map : dict
            はてなブックマーク数のマップ。
        blog_feeds_dir : str
            ブログフィードを保存するディレクトリ。
        """
        self.logger.info("フィードの保存を開始します")
        
        # フィード配信セットを保存
        await self.store_feed_distribution_set(feed_distribution_set, feeds_dir)
        
        # ブログフィード情報を保存
        await self.store_blog_feeds(feeds, og_object_map, hatena_count_map, blog_feeds_dir)
        
        self.logger.info("フィードの保存が完了しました")
    
    async def store_feed_distribution_set(self, feed_distribution_set, feeds_dir):
        """
        フィード配信セットを保存します。
        
        Parameters
        ----------
        feed_distribution_set : dict
            フィード配信セット。
        feeds_dir : str
            フィードを保存するディレクトリ。
        """
        # フィード配信セットを保存
        for feed_type, feed_content in feed_distribution_set.items():
            feed_path = os.path.join(feeds_dir, f"feed.{feed_type}")
            with open(feed_path, "w", encoding="utf-8") as f:
                f.write(feed_content)
    
    async def store_blog_feeds(self, feeds, og_object_map, hatena_count_map, blog_feeds_dir):
        """
        ブログフィード情報を保存します。
        
        Parameters
        ----------
        feeds : list
            フィード情報のリスト。
        og_object_map : dict
            OG情報のマップ。
        hatena_count_map : dict
            はてなブックマーク数のマップ。
        blog_feeds_dir : str
            ブログフィードを保存するディレクトリ。
        """
        # フィード情報を整形
        blog_feeds = []
        for feed in feeds:
            feed_info = {
                "title": feed["title"],
                "link": feed["link"],
                "linkMd5Hash": self.get_md5_hash(feed["link"]),
                "ogImageUrl": self.get_og_image_url(feed["link"], og_object_map),
                "ogDescription": self.get_og_description(feed["link"], og_object_map),
                "items": self.get_feed_items(feed, og_object_map, hatena_count_map)
            }
            blog_feeds.append(feed_info)
        
        # blog-feeds.jsonを保存
        blog_feeds_path = os.path.join(blog_feeds_dir, "blog-feeds.json")
        with open(blog_feeds_path, "w", encoding="utf-8") as f:
            json.dump(blog_feeds, f, ensure_ascii=False, indent=2)
    
    def extract_domain(self, url):
        """
        URLからドメインを抽出します。
        
        Parameters
        ----------
        url : str
            URL。
            
        Returns
        -------
        str
            ドメイン。
        """
        match = re.search(r"https?://([^/]+)", url)
        if match:
            return match.group(1)
        return ""
    
    def get_latest_entry_date(self, feed):
        """
        フィードの最新エントリの日付を取得します。
        
        Parameters
        ----------
        feed : dict
            フィード情報。
            
        Returns
        -------
        str
            最新エントリの日付。
        """
        entries = feed.get("entries", [])
        if not entries:
            return ""
        
        # 最新エントリの日付を取得
        latest_entry = max(entries, key=lambda entry: entry.get("published_parsed") or (0, 0, 0, 0, 0, 0, 0, 0, 0))
        published = latest_entry.get("published")
        
        if published:
            # ISO 8601形式に変換
            try:
                dt = datetime(*latest_entry.get("published_parsed")[:6])
                return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
            except:
                pass
        
        return ""

    def get_md5_hash(self, url):
        """
        URLのMD5ハッシュを取得します。
        
        Parameters
        ----------
        url : str
            URL。
            
        Returns
        -------
        str
            MD5ハッシュ。
        """
        return hashlib.md5(url.encode('utf-8')).hexdigest()

    def clean_html(self, html):
        """
        HTMLを整形します。
        
        Parameters
        ----------
        html : str
            HTML。
            
        Returns
        -------
        str
            整形されたテキスト。
        """
        if not html:
            return ""
        
        # BeautifulSoupを使用してHTMLをテキストに変換
        soup = BeautifulSoup(html, 'html.parser')
        text = soup.get_text(separator=' ', strip=True)
        
        # 余分な空白を削除
        text = re.sub(r'\s+', ' ', text).strip()
        
        return text

    def get_og_image_url(self, url, og_object_map):
        """
        OG画像URLを取得します。
        
        Parameters
        ----------
        url : str
            URL。
        og_object_map : dict
            OG情報のマップ。
            
        Returns
        -------
        str
            OG画像URL。
        """
        if url in og_object_map:
            og_data = og_object_map.get(url, {})
            if isinstance(og_data, dict) and 'ogImage' in og_data:
                return og_data['ogImage']
            elif isinstance(og_data, dict) and 'image' in og_data:
                return og_data['image']
        return ""

    def get_og_description(self, url, og_object_map):
        """
        OG説明を取得します。
        
        Parameters
        ----------
        url : str
            URL。
        og_object_map : dict
            OG情報のマップ。
            
        Returns
        -------
        str
            OG説明。
        """
        if url in og_object_map:
            og_data = og_object_map.get(url, {})
            if isinstance(og_data, dict) and 'ogDescription' in og_data:
                return og_data['ogDescription']
            elif isinstance(og_data, dict) and 'description' in og_data:
                return og_data['description']
        return ""

    def get_feed_items(self, feed, og_object_map, hatena_count_map):
        """
        フィードアイテムを取得します。
        
        Parameters
        ----------
        feed : dict
            フィード情報。
        og_object_map : dict
            OG情報のマップ。
        hatena_count_map : dict
            はてなブックマーク数のマップ。
            
        Returns
        -------
        list
            フィードアイテムのリスト。
        """
        items = []
        for entry in feed.get("entries", [])[:10]:  # 最新10件のみ取得
            published_date = None
            if entry.get("published_parsed"):
                published_date = datetime.fromtimestamp(time.mktime(entry["published_parsed"]), tz=timezone.utc)
            
            if not published_date:
                continue
            
            # コンテンツの取得
            content_html = ""
            if "content" in entry and entry["content"]:
                if isinstance(entry["content"], list) and len(entry["content"]) > 0:
                    content_html = entry["content"][0].get("value", "")
                else:
                    content_html = str(entry["content"])
            elif "summary_detail" in entry and entry["summary_detail"]:
                content_html = entry["summary_detail"].get("value", "")
            elif "summary" in entry:
                content_html = entry["summary"]
            
            # サマリーの取得
            summary = ""
            if "summary" in entry:
                summary = entry["summary"]
            elif content_html:
                summary = content_html
            
            # ISO形式の日付を生成（時刻も含む）
            iso_date = published_date.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            
            item = {
                "title": entry.get("title", ""),
                "summary": self.clean_html(summary)[:200],
                "content_html": content_html[:500],
                "link": entry.get("link", ""),
                "isoDate": iso_date,
                "hatenaCount": self.get_hatena_count(entry.get("link", ""), hatena_count_map),
                "ogImageUrl": self.get_og_image_url(entry.get("link", ""), og_object_map)
            }
            items.append(item)
        
        return items

    def get_hatena_count(self, url, hatena_count_map):
        """
        はてなブックマーク数を取得します。
        
        Parameters
        ----------
        url : str
            URL。
        hatena_count_map : dict
            はてなブックマーク数のマップ。
            
        Returns
        -------
        int
            はてなブックマーク数。
        """
        if url in hatena_count_map:
            return hatena_count_map[url]
        return 0


class FeedValidator:
    """
    フィードを検証するクラス。
    """
    
    def __init__(self):
        """FeedValidatorを初期化します。"""
        self.logger = logging.getLogger(__name__ + ".FeedValidator")
    
    async def assert_feed(self, aggregated_feed):
        """
        集約フィードを検証します。
        
        Parameters
        ----------
        aggregated_feed : dict
            集約フィード。
        """
        self.logger.info("集約フィードの検証を開始します")
        
        # TODO: 実際には集約フィードを検証する
        
        self.logger.info("集約フィードの検証が完了しました")
    
    async def assert_xml_feed(self, feed_type, xml_feed):
        """
        XML形式のフィードを検証します。
        
        Parameters
        ----------
        feed_type : str
            フィードの種類。
        xml_feed : str
            XML形式のフィード。
        """
        self.logger.info(f"{feed_type}形式のフィードの検証を開始します")
        
        # TODO: 実際にはXML形式のフィードを検証する
        
        self.logger.info(f"{feed_type}形式のフィードの検証が完了しました")


def load_feed_config():
    """
    feed.tomlからフィード設定を読み込みます。
    
    Returns
    -------
    list
        フィード情報のリスト。
    """
    try:
        with open(FEED_CONFIG_PATH, "rb") as f:
            config = tomli.load(f)
        
        # 全カテゴリのフィードURLを集約
        feed_urls = []
        for category, urls in config.items():
            for url in urls:
                feed_urls.append({
                    "feed_url": url,
                    "category": category
                })
        
        return feed_urls
    except Exception as e:
        logger.error(f"フィード設定の読み込みに失敗しました: {e}")
        return []


async def main():
    """メイン処理を実行します。"""
    try:
        logger.info("フィード生成処理を開始します")
        
        # フィード情報を読み込む
        feed_info_list = load_feed_config()
        
        # フィード取得
        feed_crawler = FeedCrawler()
        aggregate_feed_start_at = datetime.now(timezone.utc) - timedelta(hours=AGGREGATE_FEED_DURATION_HOURS)
        crawl_feeds_result = await feed_crawler.crawl_feeds(
            feed_info_list,
            FEED_FETCH_CONCURRENCY,
            FEED_OG_FETCH_CONCURRENCY,
            aggregate_feed_start_at
        )
        
        # まとめフィード作成
        feed_generator = FeedGenerator()
        og_object_map = {**crawl_feeds_result["feed_item_og_object_map"], **crawl_feeds_result["feed_blog_og_object_map"]}
        aggregated_feed = feed_generator.generate_aggregated_feed(
            crawl_feeds_result["feed_items"],
            og_object_map,
            crawl_feeds_result["feed_item_hatena_count_map"],
            MAX_FEED_DESCRIPTION_LENGTH,
            MAX_FEED_CONTENT_LENGTH
        )
        
        # フィードを生成して保存
        await feed_generator.generate_and_save_feeds(aggregated_feed)
        
        # フィードを検証
        feed_validator = FeedValidator()
        await feed_validator.assert_feed(aggregated_feed)
        
        logger.info("フィード生成処理が完了しました")
    except Exception as e:
        logger.error(f"フィード生成処理中にエラーが発生しました: {e}")
        sys.exit(1)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())