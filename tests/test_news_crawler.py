from datetime import datetime
from unittest.mock import patch
import pandas as pd
from explain_agent.ingest.news_crawler import AkshareNewsCrawler


def test_crawl_returns_normalized_items():
    mock_df = pd.DataFrame(
        {
            "关键词": ["半导体", "半导体"],
            "新闻标题": ["半导体涨停", "AI 利好"],
            "新闻内容": ["正文1", "正文2"],
            "发布时间": ["2026-05-10 09:30:00", "2026-05-10 10:00:00"],
            "文章来源": ["东方财富", "新浪财经"],
            "新闻链接": ["http://a.com/1", "http://a.com/2"],
        }
    )
    with patch("akshare.stock_news_em", return_value=mock_df):
        items = AkshareNewsCrawler().crawl_symbol("半导体")
    assert len(items) == 2
    assert items[0].title == "半导体涨停"
    assert items[0].source == "东方财富"
    assert isinstance(items[0].published_at, datetime)
    assert items[0].url_hash  # 非空


def test_crawl_dedupes_by_url_hash():
    mock_df = pd.DataFrame(
        {
            "新闻标题": ["t1", "t1"],
            "新闻内容": ["c", "c"],
            "发布时间": ["2026-05-10 09:30:00", "2026-05-10 09:30:00"],
            "文章来源": ["s", "s"],
            "新闻链接": ["http://a.com/x", "http://a.com/x"],
        }
    )
    with patch("akshare.stock_news_em", return_value=mock_df):
        items = AkshareNewsCrawler().crawl_symbol("半导体")
    assert len(items) == 1
