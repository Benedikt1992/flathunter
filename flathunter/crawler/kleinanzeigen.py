"""Expose crawler for Kleinanzeigen"""
import re
import datetime

from bs4 import Tag

from flathunter.webdriver_crawler import WebdriverCrawler
from flathunter.logging import logger

class Kleinanzeigen(WebdriverCrawler):
    """Implementation of Crawler interface for Kleinanzeigen"""

    URL_PATTERN = re.compile(r'https://www\.kleinanzeigen\.de')
    MONTHS = {
        "Januar": "01",
        "Februar": "02",
        "März": "03",
        "April": "04",
        "Mai": "05",
        "Juni": "06",
        "Juli": "07",
        "August": "08",
        "September": "09",
        "Oktober": "10",
        "November": "11",
        "Dezember": "12"
    }

    def get_expose_details(self, expose):
        soup = self.get_page(expose['url'], self.get_driver())
        for detail in soup.find_all('li', {"class": "addetailslist--detail"}):
            detail_text = " ".join(detail.text.split())
            date_string = re.match(r'Verfügbar ab (\w+) (\d{4})', detail_text)
            if date_string is not None and date_string[1] in self.MONTHS:
                expose['from'] = "01." + self.MONTHS[date_string[1]] + "." + date_string[2]
        if 'from' not in expose:
            expose['from'] = datetime.datetime.now().strftime('%02d.%02m.%Y')
        return expose

    def extract_data(self, raw_data):
        """Extracts all exposes from a provided Soup object"""
        entries = []
        seen_ids = set()
        soup = raw_data.find(id="srchrslt-adtable")
        if not isinstance(soup, Tag):
            logger.warning("No search result list found on Kleinanzeigen page")
            return entries

        for expose in soup.find_all("article", attrs={"data-adid": True}):
            if "aditem" in (expose.get("class") or []):
                details = self._parse_legacy_aditem(expose)
            else:
                details = self._parse_card(expose)
            if details is None or details['id'] in seen_ids:
                continue
            seen_ids.add(details['id'])
            entries.append(details)

        logger.debug('Number of entries found: %d', len(entries))

        return entries

    def _parse_card(self, expose: Tag):
        """Parse a search result card (Kleinanzeigen layout since 2026)"""
        title_heading = expose.find("h3")
        href = expose.get("data-href")
        if not isinstance(href, str) and isinstance(title_heading, Tag):
            title_link = title_heading.find("a", href=True)
            href = title_link.get("href") if isinstance(title_link, Tag) else None
        ad_id = expose.get("data-adid")
        if not isinstance(title_heading, Tag) or not isinstance(href, str) \
                or not isinstance(ad_id, str) or not ad_id.isdigit():
            # Without a link we can't provide an URL
            return None

        price = ""
        size = ""
        rooms = ""
        for paragraph in expose.find_all("p"):
            text = " ".join(paragraph.get_text(" ", strip=True).split())
            facts_match = re.fullmatch(
                r'(?:(?P<size>[\d.,]+ m²))?(?: · )?(?:(?P<rooms>[\d.,]+) Zi\.)?(?: · .*)?', text)
            if text and facts_match is not None and (facts_match['size'] or facts_match['rooms']):
                size = facts_match['size'] or size
                rooms = facts_match['rooms'] or rooms
            elif not price and re.fullmatch(r'[\d.,]+ €(?: VB)?|VB|Zu verschenken', text):
                price = text

        address = ""
        location_icon = expose.find("svg", attrs={"data-title": "locationOutline"})
        if isinstance(location_icon, Tag) and isinstance(location_icon.parent, Tag):
            address = " ".join(location_icon.parent.get_text(" ", strip=True).split())

        image = None
        image_element = expose.find("img", src=True)
        if isinstance(image_element, Tag):
            image = image_element["src"]

        return {
            'id': int(ad_id),
            'image': image,
            'url': "https://www.kleinanzeigen.de" + href,
            'title': " ".join(title_heading.get_text(" ", strip=True).split()),
            'price': price,
            'size': size,
            'rooms': rooms,
            'address': address,
            'crawler': self.get_name()
        }

    # pylint: disable=too-many-locals
    def _parse_legacy_aditem(self, expose):
        """Parse a search result item (Kleinanzeigen layout before 2026)"""
        title_elem = expose.find(class_="ellipsis")
        if title_elem.get("href"):
            url = title_elem.get("href")
        else:
            # If there is no title element, just continue since we can't provide an URL
            return None

        try:
            price = expose.find(
                class_="aditem-main--middle--price-shipping--price").text.strip()
            tags = expose.find_all(class_="simpletag")
            address = expose.find("div", {"class": "aditem-main--top--left"})
            image_element = expose.find("div", {"class": "galleryimage-element"})
        except AttributeError as error:
            logger.warning("Unable to process eBay expose: %s", str(error))
            return None

        if image_element is not None:
            image = image_element["data-imgsrc"]
        else:
            image = None

        address = address.text.strip()
        address = address.replace('\n', ' ').replace('\r', '')
        address = " ".join(address.split())

        rooms = ""
        if len(tags) > 1:
            rooms_match = re.search(r'\d+[.|,]*\d*', tags[1].text, flags=re.MULTILINE)
            if rooms_match is not None:
                rooms = rooms_match.group()

        try:
            size = tags[0].text.strip()
        except (IndexError, TypeError):
            size = ""

        details = {
            'id': int(expose.get("data-adid")),
            'image': image,
            'url': ("https://www.kleinanzeigen.de" + url),
            'title': title_elem.text.strip(),
            'price': price,
            'size': size,
            'rooms': rooms,
            'address': address,
            'crawler': self.get_name()
        }
        return details

    def load_address(self, url):
        """Extract address from expose itself"""
        expose_soup = self.get_page(url)
        street_raw = ""
        street_el = expose_soup.find(id="street-address")
        if isinstance(street_el, Tag):
            street_raw = street_el.text
        address_raw = ""
        address_el = expose_soup.find(id="viewad-locality")
        if isinstance(address_el, Tag):
            address_raw = address_el.text

        return " ".join((address_raw.strip().replace("\n", "") + " " + street_raw.strip()).split())
