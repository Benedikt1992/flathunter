"""Expose crawler for WgGesucht"""
import re
from typing import Optional, List, Dict, Any, Union

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from flathunter.logging import logger
from flathunter.abstract_crawler import Crawler


def get_title(title_row: Tag) -> str:
    """Parse the title from the expose title element"""
    return title_row.text.strip()


def get_url(title_row: Tag) -> Optional[str]:
    """Parse the expose URL from the expose title element"""
    a_element = title_row.find('a')
    if not isinstance(a_element, Tag) \
            or not a_element.has_attr('href') \
            or not isinstance(a_element.attrs['href'], str):
        return None
    return 'https://www.wg-gesucht.de/' + a_element.attrs['href'].removeprefix("/")


def extract_href_style(row: Tag) -> Optional[str]:
    """Extract the style attribute from a image div"""
    div = row.find('div', {"class": "card_image"})
    if not isinstance(div, Tag):
        return None
    a_element = div.find('a')
    if not isinstance(a_element, Tag) or not a_element.has_attr('style'):
        return None
    style = a_element.attrs['style']
    if not isinstance(style, str):
        return None
    return style


def get_image_url(row: Tag) -> Optional[str]:
    """Parse the image url from the expose"""
    href_style = extract_href_style(row)
    if href_style is None:
        return None
    image_match = re.match(r'background-image: url\((.*)\);', href_style)
    if image_match is None:
        return None
    return image_match[1]


def get_rooms(row: Tag) -> str:
    """Parse the number of rooms from the expose"""
    details_el = row.find("div", {"class": "col-xs-11"})
    if not isinstance(details_el, Tag):
        return ""
    detail_string = details_el.text.strip().split("|")
    details_array = list(map(lambda s: re.sub(' +', ' ',
                                              re.sub(r'\W', ' ', s.strip())),
                             detail_string))
    rooms_tmp = re.findall(r'\d Zimmer', details_array[0])
    return rooms_tmp[0][:1] if rooms_tmp else ""


def get_price(numbers_row: Tag) -> Optional[str]:
    """Parse the price from the expose"""
    price_el = numbers_row.find("div", {"class": "col-xs-3"})
    if not isinstance(price_el, Tag):
        return None
    return price_el.text.strip()


def get_dates(numbers_row: Tag) -> List[str]:
    """Parse the advert dates from the expose"""
    date_el = numbers_row.find("div", {"class": "text-center"})
    if not isinstance(date_el, Tag):
        return []
    return re.findall(r'\d{2}.\d{2}.\d{4}', date_el.text)


def get_size(numbers_row: Tag) -> List[str]:
    """Parse the room size from the expose"""
    size_el = numbers_row.find("div", {"class": "text-right"})
    if not isinstance(size_el, Tag):
        return []
    return re.findall(r'\d{1,4}\sm²', size_el.text)

def is_verified_company(row: Tag) -> bool:
    """Filter out ads from 'Verified Companies'"""
    verified_el = row.find("span", {"class": "label_verified"})
    if isinstance(verified_el, Tag):
        return True
    return False

# pylint: disable=too-many-return-statements
def parse_expose_element_to_details(row: Tag, crawler: str) -> Optional[Dict]:
    """Parse an Expose soup element to an Expose details dictionary"""
    # WG-Gesucht switched from h3 to h2 for titles; accept both layouts
    title_row = row.find(['h2', 'h3'], {"class": "truncate_title"})
    if title_row is None or not isinstance(title_row, Tag):
        logger.warning("No title found - skipping")
        return None
    if is_verified_company(row):
        logger.warning("Advert found - skipping")
        return None
    title = get_title(title_row)
    url = get_url(title_row)
    if url is None:
        logger.warning("No expose URL found - skipping")
        return None
    image = get_image_url(row)
    rooms = get_rooms(row)
    numbers_row = row.find("div", {"class": "middle"})
    if not isinstance(numbers_row, Tag):
        logger.warning("No numbers row found - skipping")
        return None
    price = get_price(numbers_row)
    dates = get_dates(numbers_row)
    if len(dates) == 0:
        logger.warning("No dates found - skipping")
        return None
    size = get_size(numbers_row)
    if len(size) == 0:
        logger.warning("No size found - skipping")
        return None

    if len(dates) == 2:
        title = f"{title} vom {dates[0]} bis {dates[1]}"
    else:
        title = f"{title} ab dem {dates[0]}"

    details = {
        'id': int(url.split('.')[-2]),
        'image': image,
        'url': url,
        'title': title,
        'price': price,
        'size': size[0],
        'rooms': rooms,
        'address': url,
        'crawler': crawler
    }
    if len(dates) == 2:
        details['from'] = dates[0]
        details['to'] = dates[1]
    elif len(dates) == 1:
        details['from'] = dates[0]
    return details


def _clean_text(element: Optional[Union[Tag, NavigableString, int]]) -> str:
    """Return the whitespace-normalised text of a soup element"""
    if not isinstance(element, Tag):
        return ""
    return ' '.join(element.get_text(' ', strip=True).split())


def get_card_url(card: Tag, expose_id: int) -> Optional[str]:
    """Build the expose URL from a list card (2026 layout)

    Promoted cards link to the search page with an asset_id parameter
    instead of the expose, so the canonical URL is rebuilt from the
    category/city prefix of the link and the expose id."""
    href = card.attrs.get('href')
    if not isinstance(href, str):
        return None
    path_match = re.match(r'/?([^.?/]+)\.', href)
    if path_match is None:
        return None
    return f'https://www.wg-gesucht.de/{path_match[1]}.{expose_id}.html'


def get_card_image_url(card: Tag) -> Optional[str]:
    """Return the first slider image of a list card (2026 layout)"""
    img = card.find('img', attrs={'data-src': True})
    if not isinstance(img, Tag):
        return None
    src = img.attrs.get('data-src')
    return src if isinstance(src, str) else None


def get_card_rooms(card: Tag) -> str:
    """Parse the number of rooms, e.g. '2,5' from '2,5-Zimmer-Wohnung'"""
    details_el = card.find('div', {"class": "card-offer-details-text"})
    rooms_match = re.search(r'(\d+(?:,\d+)?)-Zimmer', _clean_text(details_el))
    return rooms_match[1] if rooms_match else ""


def parse_card_element_to_details(card: Tag, crawler: str) -> Optional[Dict]:
    """Parse a list card (WG-Gesucht layout since 2026) to an Expose details dictionary"""
    if is_verified_company(card):
        logger.debug("Advert found - skipping")
        return None
    data_id = card.attrs.get('data-id')
    if not isinstance(data_id, str) or not data_id.isdigit():
        logger.warning("No expose id found - skipping")
        return None
    expose_id = int(data_id)
    title = _clean_text(card.find(class_="card-offer-title"))
    if not title:
        logger.warning("No title found - skipping")
        return None
    url = get_card_url(card, expose_id)
    if url is None:
        logger.warning("No expose URL found - skipping")
        return None
    price_row = card.find('div', {"class": "price-row"})
    price_el = price_row.find(class_="card-main-text") if isinstance(price_row, Tag) else None
    price = _clean_text(price_el)
    size = re.findall(r'\d{1,4}\s?m²', _clean_text(card.find(class_="card-offer-property-size")))
    if len(size) == 0:
        logger.warning("No size found - skipping")
        return None
    dates = re.findall(r'\d{2}\.\d{2}\.\d{4}',
                       _clean_text(card.find(class_="card-offer-availability")))
    if len(dates) == 0:
        logger.warning("No dates found - skipping")
        return None

    if len(dates) >= 2:
        title = f"{title} vom {dates[0]} bis {dates[1]}"
    else:
        title = f"{title} ab dem {dates[0]}"

    details = {
        'id': expose_id,
        'image': get_card_image_url(card),
        'url': url,
        'title': title,
        'price': price,
        'size': size[0],
        'rooms': get_card_rooms(card),
        'address': url,
        'crawler': crawler,
        'from': dates[0]
    }
    if len(dates) >= 2:
        details['to'] = dates[1]
    return details


def liste_attribute_filter(element: Union[Tag, str]) -> bool:
    """Return true for elements whose 'id' attribute starts with 'liste-' 
    and are not contained in the 'premium_user_extra_list' container"""
    if not isinstance(element, Tag):
        return False
    if not element.attrs or "id" not in element.attrs:
        return False
    if not element.parent or not element.parent.attrs or "class" not in element.parent.attrs:
        return False
    return element.attrs["id"].startswith('liste-') and \
        'premium_user_extra_list' not in element.parent.attrs["class"]


class WgGesucht(Crawler):
    """Implementation of Crawler interface for WgGesucht"""

    URL_PATTERN = re.compile(r'https://www\.wg-gesucht\.de')

    def __init__(self, config):
        super().__init__(config)
        self.config = config

    # pylint: disable=too-many-locals
    def extract_data(self, raw_data: BeautifulSoup) -> List[Dict]:
        """Extracts all exposes from a provided Soup object"""
        entries = []

        findings = raw_data.find_all(liste_attribute_filter)
        existing_findings = [
            e for e in findings
            if isinstance(e, Tag) and e.has_attr('class') and not 'display-none' in e['class']
        ]
        seen_ids = set()
        for row in existing_findings:
            if 'wgg-list-card' in row['class']:
                details = parse_card_element_to_details(row, self.get_name())
            else:
                details = parse_expose_element_to_details(row, self.get_name())
            if details is None or details['id'] in seen_ids:
                continue
            seen_ids.add(details['id'])
            entries.append(details)

        logger.debug('Number of entries found: %d', len(entries))

        return entries

    def load_address(self, url) -> Optional[str]:
        """Extract address from expose itself"""
        response = self.get_soup_from_url(url)
        address_link = response.find("a", {"href": "#map_container"})
        if isinstance(address_link, Tag):
            address = _clean_text(address_link.find("span", {"class": "section_panel_detail"}))
            if address:
                return address
        address_div = response.find('div', {"class": "col-sm-4 mb10"})
        if not isinstance(address_div, Tag):
            logger.debug("No address in response for URL: %s", url)
            return None
        a_element = address_div.find("a", {"href": "#mapContainer"})
        if not isinstance(a_element, Tag):
            logger.debug("No address in response for URL: %s", url)
            return None
        return ' '.join(a_element.text.strip().split())

    def get_soup_from_url(
            self,
            url: str,
            driver: Optional[Any] = None,
            checkbox: bool = False,
            afterlogin_string: Optional[str] = None) -> BeautifulSoup:
        """
        Creates a Soup object from the HTML at the provided URL

        Overwrites the method inherited from abstract_crawler. This is
        necessary as we need to reload the page once for all filters to
        be applied correctly on wg-gesucht.
        """
        sess = requests.session()
        # First page load to set filters; response is discarded
        sess.get(url, headers=self.HEADERS)
        # Second page load
        resp = sess.get(url, headers=self.HEADERS)

        if resp.status_code not in (200, 405):
            logger.error("Got response (%i): %s",
                         resp.status_code, resp.content)
        if self.config.use_proxy():
            return self.get_soup_with_proxy(url)
        if driver is not None:
            driver.get(url)
            if re.search("initGeetest", driver.page_source):
                self.resolve_geetest(driver)
            elif re.search("g-recaptcha", driver.page_source):
                self.resolve_recaptcha(
                    driver, checkbox, afterlogin_string or "")
            return BeautifulSoup(driver.page_source, 'lxml')
        return BeautifulSoup(resp.content, 'lxml')
