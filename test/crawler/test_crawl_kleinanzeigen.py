import os

import pytest
from bs4 import BeautifulSoup

from flathunter.crawler.kleinanzeigen import Kleinanzeigen
from test.utils.config import StringConfig

DUMMY_CONFIG = """
urls:
  - https://www.kleinanzeigen.de/s-wohnung-mieten/muenchen/anbieter:privat/anzeige:angebote/preis:600:1000
    """

TEST_URL = 'https://www.kleinanzeigen.de/s-wohnung-mieten/berlin/preis:1000:1500/c203l3331+wohnung_mieten.qm_d:70,+wohnung_mieten.zimmer_d:2'

@pytest.fixture
def crawler():
    return Kleinanzeigen(StringConfig(string=DUMMY_CONFIG))

@pytest.mark.live
def test_crawler(crawler):
    soup = crawler.get_page(TEST_URL)
    assert soup is not None
    entries = crawler.extract_data(soup)
    assert entries is not None
    assert len(entries) > 0
    assert entries[0]['id'] > 0
    assert entries[0]['url'].startswith("https://www.kleinanzeigen.de/s-anzeige")
    for attr in [ 'title', 'price', 'size', 'rooms', 'address' ]:
        assert entries[0][attr]

@pytest.mark.live
def test_process_expose_fetches_details(crawler):
    soup = crawler.get_page(TEST_URL)
    assert soup is not None
    entries = crawler.extract_data(soup)
    assert entries is not None
    assert len(entries) > 0
    updated_entries = [ crawler.get_expose_details(expose) for expose in entries ]
    for expose in updated_entries:
        print(expose)
        for attr in [ 'title', 'price', 'size', 'rooms', 'address', 'from' ]:
            assert expose[attr]


def _fixture_soup(name):
    with open(os.path.join(os.path.dirname(os.path.realpath(__file__)), "fixtures", name)) as fixture:
        return BeautifulSoup(fixture, 'lxml')


def test_parse_2026_card_layout(crawler):
    entries = crawler.extract_data(_fixture_soup("kleinanzeigen-2026.html"))
    # 27 cards, two promoted ("TOP") ads are listed twice
    assert len(entries) == 25
    assert len({e['id'] for e in entries}) == len(entries)
    first = entries[0]
    assert first['id'] == 3561910032
    assert first['url'] == 'https://www.kleinanzeigen.de/s-anzeige/schoene-helle-wohnung-zur-zwischenmiete-01-01-26-19-03-26-/3561910032-203-3491'
    assert first['title'] == 'schöne helle Wohnung zur Zwischenmiete, 01.01.26-19.03.26.'
    assert first['price'] == '1.100 €'
    assert first['size'] == '70 m²'
    assert first['rooms'] == '1'
    assert first['address'] == '10437 Prenzlauer Berg'
    assert first['image'].startswith('https://img.kleinanzeigen.de/')
    for entry in entries:
        assert entry['url'].startswith("https://www.kleinanzeigen.de/s-anzeige/")
        for attr in ['title', 'price', 'size', 'rooms', 'address']:
            assert entry[attr], f"{attr} missing for {entry['id']}"


def test_parse_card_without_image_and_title_link(crawler):
    entries = crawler.extract_data(_fixture_soup("kleinanzeigen-2026.html"))
    entry = next(e for e in entries if e['id'] == 3572995820)
    assert entry['image'] is None
    assert entry['title'] == 'Wohnung zu vermieten in Berlin Wilmersdorf'
    assert entry['url'] == 'https://www.kleinanzeigen.de/s-anzeige/wohnung-zu-vermieten-in-berlin-wilmersdorf/3572995820-203-3539'


def test_parse_card_with_extra_facts(crawler):
    entries = crawler.extract_data(_fixture_soup("kleinanzeigen-2026.html"))
    entry = next(e for e in entries if e['id'] == 3572001753)
    # "95 m² · 2 Zi. · Online-Besichtigung"
    assert entry['size'] == '95 m²'
    assert entry['rooms'] == '2'


def test_expose_details_available_from(crawler, mocker):
    mocker.patch.object(crawler, 'get_driver', return_value=None)
    mocker.patch.object(crawler, 'get_page', return_value=_fixture_soup("kleinanzeigen-expose-2026.html"))
    expose = crawler.get_expose_details({'url': 'https://www.kleinanzeigen.de/s-anzeige/x/3556298665-203-3482'})
    assert expose['from'] == '01.12.2026'
    assert crawler.load_address(expose['url']) == '12099 Berlin - Tempelhof'
