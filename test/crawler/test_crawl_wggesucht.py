import pytest
import os
import unittest
import unittest.mock
from typing import Dict
from functools import reduce
from bs4 import BeautifulSoup
from flathunter.crawler.wggesucht import WgGesucht, get_card_url
from test.utils.config import StringConfig

class WgGesuchtCrawlerTest(unittest.TestCase):

    TEST_URL = 'https://www.wg-gesucht.de/wohnungen-in-Berlin.8.2.1.0.html?offer_filter=1&city_id=8&noDeact=1&categories%5B%5D=2&rent_types%5B%5D=0&sMin=70&rMax=3000&rmMin=2&fur=2&sin=2&exc=2&img_only=1'
    DUMMY_CONFIG = """
    urls:
      - https://www.wg-gesucht.de/wohnungen-in-Munchen.90.2.1.0.html
        """
    def setUp(self):
        self.crawler = WgGesucht(StringConfig(string=self.DUMMY_CONFIG))

    @pytest.mark.live
    def test(self):
        soup = self.crawler.get_page(self.TEST_URL)
        self.assertIsNotNone(soup, "Should get a soup from the URL")
        entries = self.crawler.extract_data(soup)
        self.assertIsNotNone(entries, "Should parse entries from search URL")
        self.assertTrue(len(entries) > 0, "Should have at least one entry")
        self.assertTrue(entries[0]['id'] > 0, "Id should be parsed")
        self.assertTrue(entries[0]['url'].startswith("https://www.wg-gesucht.de/wohnungen"), u"URL should be an apartment link")
        for attr in [ 'title', 'price', 'size', 'rooms', 'address', 'image', 'from' ]:
            self.assertIsNotNone(entries[0][attr], attr + " should be set")
        def shrink(i: bool, e: Dict) -> bool:
            return 'to' in e or i
        found = reduce(shrink, entries, False)
        self.assertTrue(found, "Expected 'to' to sometimes be set")

    def test_filter_spotahome_ads(self):
        with open(os.path.join(os.path.dirname(os.path.realpath(__file__)), "fixtures", "wg-gesucht-spotahome.html")) as fixture:
            soup = BeautifulSoup(fixture, 'lxml')
        entries = self.crawler.extract_data(soup)
        assert len(entries) == 20

    def _fixture_soup(self, name):
        with open(os.path.join(os.path.dirname(os.path.realpath(__file__)), "fixtures", name)) as fixture:
            return BeautifulSoup(fixture, 'lxml')

    def test_parse_2026_card_layout(self):
        entries = self.crawler.extract_data(self._fixture_soup("wg-gesucht-2026.html"))
        # 28 cards: 9 from verified companies are skipped
        assert len(entries) == 19
        assert len({e['id'] for e in entries}) == len(entries)
        first = entries[0]
        assert first['id'] == 14138987
        assert first['url'] == 'https://www.wg-gesucht.de/wohnungen-in-Berlin-Mitte.14138987.html'
        assert first['title'] == 'Leipziger Straße 49 10117 Berlin ab dem 01.12.2026'
        assert first['price'] == '2750 €'
        assert first['size'] == '110 m²'
        assert first['rooms'] == '4'
        assert first['from'] == '01.12.2026'
        assert 'to' not in first
        assert first['image'].startswith('https://img.wg-gesucht.de/')
        limited = next(e for e in entries if e['id'] == 14370382)
        assert limited['rooms'] == '2,5'
        assert limited['from'] == '01.12.2026'
        assert limited['to'] == '01.12.2030'
        assert limited['title'].endswith('vom 01.12.2026 bis 01.12.2030')

    def test_promoted_card_links_to_expose(self):
        entries = self.crawler.extract_data(self._fixture_soup("wg-gesucht-2026.html"))
        for entry in entries:
            assert entry['url'] == entry['address']
            assert entry['url'].endswith(f".{entry['id']}.html")
            assert '?' not in entry['url']
        promoted = BeautifulSoup(
            '<a class="wgg-list-card" data-id="13643683" '
            'href="/wohnungen-in-Berlin.8.2.1.0.html?asset_id=13643683&amp;pu=1"></a>',
            'lxml').a
        assert promoted is not None
        assert get_card_url(promoted, 13643683) == \
            'https://www.wg-gesucht.de/wohnungen-in-Berlin.13643683.html'

    def test_load_address_2026_layout(self):
        soup = self._fixture_soup("wg-gesucht-expose-2026.html")
        with unittest.mock.patch.object(self.crawler, 'get_soup_from_url', return_value=soup):
            address = self.crawler.load_address("https://www.wg-gesucht.de/wohnungen-in-Berlin-Mitte.14138987.html")
        assert address == 'Leipziger Straße 49 10117 Berlin Mitte'
