import unittest

from crawler_base import CrawlConfig
from iboss_crawler import IBossCrawler
from mamentor_crawler import MamentorCrawler
from sellclub_crawler import SellClubCrawler


def _cfg(match_in: str = "title_or_body") -> CrawlConfig:
    return CrawlConfig(
        keywords=["구글리뷰", "마멘토"],
        boards=[],
        pages_per_board=1,
        match_in=match_in,
    )


class CrawlerKeywordSearchTest(unittest.TestCase):
    def test_mamentor_search_url_uses_keyword(self):
        crawler = object.__new__(MamentorCrawler)
        url = crawler._list_url("blog_mkt", 1, "구글리뷰", "wr_subject||wr_content")

        self.assertIn("bo_table=blog_mkt", url)
        self.assertIn("sfl=wr_subject||wr_content", url)
        self.assertIn("stx=%EA%B5%AC%EA%B8%80%EB%A6%AC%EB%B7%B0", url)

    def test_sellclub_search_url_uses_site_encoding(self):
        crawler = object.__new__(SellClubCrawler)
        url = crawler._list_url("maket_5_3", 1, "", "구글리뷰", "wr_content")

        self.assertIn("bo_table=maket_5_3", url)
        self.assertIn("sfl=wr_content", url)
        self.assertIn("stx=%B1%B8%B1%DB%B8%AE%BA%E4", url)

    def test_iboss_search_url_uses_board_search_form(self):
        crawler = object.__new__(IBossCrawler)
        url = crawler._list_url(1, "", "구글리뷰", "comment_text_1")

        self.assertIn("design_file=2986.php", url)
        self.assertIn("board=BD2986", url)
        self.assertIn("search_item=comment_text_1", url)
        self.assertIn("search_value=%EA%B5%AC%EA%B8%80%EB%A6%AC%EB%B7%B0", url)
        self.assertNotIn("page=1", url)

    def test_iboss_search_url_uses_board_pagination_key(self):
        crawler = object.__new__(IBossCrawler)
        url = crawler._list_url(2, "", "구글리뷰", "subject")

        self.assertIn("PB_1388626082=2", url)

    def test_search_fields_follow_match_scope(self):
        self.assertEqual(MamentorCrawler._search_fields(_cfg("title")), ["wr_subject"])
        self.assertEqual(SellClubCrawler._search_fields(_cfg()), ["wr_subject", "wr_content"])
        self.assertEqual(IBossCrawler._search_items(_cfg()), ["subject", "comment_text_1"])

    def test_sellclub_parse_search_links_with_extra_query(self):
        crawler = object.__new__(SellClubCrawler)
        html = """
        <input name="stx" value="%B1%B8%B1%DB%B8%AE%BA%E4">
        <a href="../bbs/board.php?bo_table=maket_5_3&wr_id=971469">
            검색 결과 위 고정글
        </a>
        <a href="../bbs/board.php?bo_table=maket_5_3&wr_id=971420&sfl=wr_content&stx=%B1%B8%B1%DB%B8%AE%BA%E4&sop=and&page=1">
            플레이스 웹사이트 자동완성 실행합니다
        </a>
        """
        items = crawler._parse_list(html, "maket_5_3")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["wr_id"], "971420")
        self.assertEqual(items[0]["title"], "플레이스 웹사이트 자동완성 실행합니다")


if __name__ == "__main__":
    unittest.main()
