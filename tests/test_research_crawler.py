from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest


def test_crawler_parses_qtype1_industry_response():
    """qType=1 返回行业研报, 字段映射到 ResearchItem。"""
    from explain_agent.ingest.research_crawler import EastmoneyResearchCrawler

    fake_resp_p1 = MagicMock()
    fake_resp_p1.json.return_value = {
        "TotalPage": 1,
        "data": [{
            "title": "银行行业快评报告：精准有效实施适度宽松的货币政策",
            "orgSName": "万联证券",
            "publishDate": "2026-05-12 00:00:00.000",
            "infoCode": "AP202605121822224959",
            "industryName": "银行Ⅱ",
            "researcher": "郭懿",
        }],
    }
    with patch("httpx.get", return_value=fake_resp_p1):
        crawler = EastmoneyResearchCrawler()
        items = crawler.crawl(q_type=1, begin=date(2026, 5, 1), end=date(2026, 5, 12), max_pages=1)

    assert len(items) == 1
    it = items[0]
    assert it.title == "银行行业快评报告：精准有效实施适度宽松的货币政策"
    assert it.institution == "万联证券"
    assert it.industry == "银行Ⅱ"
    assert it.researcher == "郭懿"
    assert it.info_code == "AP202605121822224959"
    assert it.q_type == 1
    assert it.published_at == datetime(2026, 5, 12, 0, 0, 0)
    assert it.url and "AP202605121822224959" in it.url
    assert it.url_hash  # sha256 of infoCode


def test_crawler_qtype2_macro_uses_industry_placeholder():
    """qType=2 (宏观策略) 没有 industryName, 用占位 '宏观策略'。"""
    from explain_agent.ingest.research_crawler import EastmoneyResearchCrawler

    fake_resp = MagicMock()
    fake_resp.json.return_value = {
        "TotalPage": 1,
        "data": [{
            "title": "4月通胀数据跟踪",
            "orgSName": "万联证券",
            "publishDate": "2026-05-12 00:00:00.000",
            "infoCode": "AP202605121822224969",
            "industryName": "",
            "researcher": "于天旭",
        }],
    }
    with patch("httpx.get", return_value=fake_resp):
        crawler = EastmoneyResearchCrawler()
        items = crawler.crawl(q_type=2, begin=date(2026, 5, 1), end=date(2026, 5, 12), max_pages=1)

    assert len(items) == 1
    assert items[0].industry == "宏观策略"
    assert items[0].q_type == 2


def test_crawler_handles_empty_response():
    """API 返回空 data 时返回空列表。"""
    from explain_agent.ingest.research_crawler import EastmoneyResearchCrawler

    fake_resp = MagicMock()
    fake_resp.json.return_value = {"TotalPage": 0, "data": []}
    with patch("httpx.get", return_value=fake_resp):
        crawler = EastmoneyResearchCrawler()
        items = crawler.crawl(q_type=1, begin=date(2026, 5, 1), end=date(2026, 5, 12))
    assert items == []
