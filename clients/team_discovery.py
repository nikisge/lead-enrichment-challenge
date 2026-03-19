"""
Team Page Discovery for Lead Enrichment - V2.

Smart "2-Klicks" approach to find team pages like a human would:

1. Direct URL Check - Try common team page URLs (/team, /ueber-uns, etc.)
2. Sitemap Scan - Parse sitemap.xml for team-related URLs
3. Homepage Link Scan - Find team/about links on homepage (no AI, just pattern matching)
4. Improved Scraping - Better Playwright waits for JS-heavy team pages

Goal: Find the same contacts a human would find with "2 clicks" on the company website.

Cost: $0 extra (no AI for URL discovery, only for contact extraction)
Time: ~25-30 seconds
"""

import json
import logging
import asyncio
import re
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass
from urllib.parse import urlparse, urljoin
import xml.etree.ElementTree as ET

from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup

from config import get_settings
from clients.ai_extractor import extract_contacts_with_priority, ExtractedContact

logger = logging.getLogger(__name__)

# Maximum page size to scrape (100KB for team pages - they can be image-heavy)
MAX_PAGE_SIZE = 100_000

# Maximum text to extract from page
MAX_TEXT_EXTRACT = 30_000


def _sync_full_page_scroll(page) -> None:
    """Scroll sync Playwright page fully to trigger lazy loading."""
    try:
        height = page.evaluate("document.body.scrollHeight")
        viewport_height = 900
        current = 0
        while current < height:
            current += viewport_height
            page.evaluate(f"window.scrollTo(0, {current})")
            page.wait_for_timeout(300)
        page.wait_for_timeout(1000)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(500)
        page.evaluate(f"window.scrollTo(0, {height // 2})")
        page.wait_for_timeout(500)
    except Exception as e:
        logger.debug(f"Scroll error: {e}")

# Common team page URL patterns (ordered by priority)
TEAM_URL_PATTERNS = [
    "/team",
    "/unser-team",
    "/das-team",
    "/ueber-uns",
    "/uber-uns",
    "/ueber-uns/team",
    "/about-us",
    "/about",
    "/about/team",
    "/unternehmen",
    "/unternehmen/team",
    "/ansprechpartner",
    "/kontakt",
    "/contact",
    "/mitarbeiter",
    "/menschen",
    "/people",
    "/wir",
    "/management",
    "/geschaeftsfuehrung",
    "/geschaeftsleitung",
    "/fuehrungsteam",
    "/leadership",
    "/wir-ueber-uns",
    "/das-sind-wir",
]

# Keywords to find team links on homepage (German + English)
TEAM_LINK_KEYWORDS = [
    "team", "über uns", "ueber uns", "about us", "about",
    "ansprechpartner", "mitarbeiter", "menschen", "people",
    "wir über uns", "das sind wir", "management",
    "geschäftsführung", "leadership", "unternehmen",
    "über ", "ueber ",  # catches "Über moresophy", "Über das Team" etc.
    "kontakt", "contact",
]

# Team-specific CSS selectors to wait for
TEAM_PAGE_SELECTORS = [
    ".team-member", ".team-card", ".employee", ".mitarbeiter",
    ".person", ".staff", ".member", ".ansprechpartner",
    "[class*='team']", "[class*='employee']", "[class*='member']",
    "[class*='person']", "[class*='staff']", "[class*='mitarbeiter']",
    ".leadership", ".management", ".geschaeftsfuehrung",
    # Common grid/card patterns
    ".person-card", ".team-grid", ".people-grid",
    # WordPress patterns
    ".wp-block-team", ".elementor-team-member",
]


@dataclass
class DiscoveredPage:
    """A page discovered for team contact extraction."""
    url: str
    source: str  # "direct_url", "sitemap", "homepage_link"
    relevance_score: float = 0.0
    title: str = ""


@dataclass
class TeamDiscoveryResult:
    """Result from team discovery process."""
    contacts: List[ExtractedContact]
    source_urls: List[str]
    discovery_method: str = ""  # How we found the team page
    fallback_used: bool = False
    success: bool = False


class TeamDiscovery:
    """
    Smart team page discovery - finds contacts like a human with "2 clicks".
    """

    def __init__(self):
        settings = get_settings()
        self.timeout = settings.api_timeout
        self._http_client: Optional[AsyncSession] = None

    async def _get_client(self) -> AsyncSession:
        """Get or create curl_cffi session with Chrome 136 impersonation."""
        if self._http_client is None:
            self._http_client = AsyncSession(impersonate="chrome136")
        return self._http_client

    async def discover_and_extract(
        self,
        company_name: str,
        domain: Optional[str] = None,
        job_category: Optional[str] = None,
        target_titles: List[str] = [],
        max_pages: int = 2
    ) -> TeamDiscoveryResult:
        """
        Full discovery process: Find team pages smartly, scrape, extract contacts.

        Strategy (in order):
        1. Direct URL check - Try common /team, /ueber-uns URLs
        2. Sitemap scan - Parse sitemap.xml for team URLs
        3. Homepage link scan - Find team links on homepage
        4. Scrape found pages with improved Playwright

        Args:
            company_name: Company name
            domain: Company domain (required for website scraping)
            job_category: Job category for relevance
            target_titles: Ideal decision-maker titles for contact scoring

        Returns:
            TeamDiscoveryResult with contacts and metadata
        """
        # Store for use in _scrape_team_page
        self._job_category = job_category
        self._target_titles = target_titles
        logger.info(f"━━━ TEAM DISCOVERY START: {company_name} ━━━")

        if not domain:
            logger.warning(f"⚠️ No domain provided for {company_name} - cannot discover team pages")
            return TeamDiscoveryResult(
                contacts=[],
                source_urls=[],
                discovery_method="no_domain",
                success=False
            )

        base_url = f"https://{domain}"
        logger.info(f"🌐 Base URL: {base_url}")

        # Step 1: Try direct URL patterns
        logger.info("📍 Step 1: Checking direct team page URLs...")
        direct_pages = await self._check_direct_urls(base_url)

        if direct_pages:
            logger.info(f"✓ Found {len(direct_pages)} direct team page(s)")
            for page in direct_pages:
                logger.info(f"  → {page.url} (score: {page.relevance_score})")
        else:
            logger.info("✗ No direct team URLs found")

        # Step 2: Check sitemap
        discovered_pages = list(direct_pages)  # Start with direct URLs

        if len(discovered_pages) < 2:
            logger.info("📍 Step 2: Scanning sitemap.xml...")
            sitemap_pages = await self._scan_sitemap(base_url)

            if sitemap_pages:
                logger.info(f"✓ Found {len(sitemap_pages)} team page(s) in sitemap")
                for page in sitemap_pages:
                    logger.info(f"  → {page.url}")
                discovered_pages.extend(sitemap_pages)
            else:
                logger.info("✗ No team pages in sitemap (or no sitemap)")

        # Step 3: Always scan homepage for team links — this catches custom nav URLs
        # like /ueber-moresophy that can't be guessed by pattern lists or sitemap keywords.
        # Fast operation: no AI, just <a> tag parsing.
        logger.info("📍 Step 3: Scanning homepage for team links...")
        homepage_links = await self._scan_homepage_links(base_url)

        if homepage_links:
            logger.info(f"✓ Found {len(homepage_links)} team link(s) on homepage")
            for page in homepage_links:
                logger.info(f"  → {page.url} ('{page.title}')")
            discovered_pages.extend(homepage_links)
        else:
            logger.info("✗ No team links found on homepage")

        # Deduplicate and sort by relevance
        discovered_pages = self._deduplicate_pages(discovered_pages)
        discovered_pages.sort(key=lambda x: x.relevance_score, reverse=True)

        if not discovered_pages:
            logger.warning(f"❌ No team pages found for {company_name}")
            return TeamDiscoveryResult(
                contacts=[],
                source_urls=[],
                discovery_method="none_found",
                success=False
            )

        logger.info(f"📍 Step 4: Scraping {min(len(discovered_pages), max_pages)} best page(s)...")

        # Step 4: Scrape best pages
        all_contacts = []
        scraped_urls = []
        discovery_methods = set()

        for page in discovered_pages[:max_pages]:
            logger.info(f"🔍 Scraping: {page.url}")
            contacts = await self._scrape_team_page(page.url, company_name)

            if contacts:
                all_contacts.extend(contacts)
                scraped_urls.append(page.url)
                discovery_methods.add(page.source)
                logger.info(f"  ✓ Extracted {len(contacts)} contact(s)")
                for c in contacts:
                    logger.info(f"    → {c.name} ({c.title or 'no title'})")
            else:
                logger.info(f"  ✗ No contacts extracted")

        # Deduplicate contacts by name
        unique_contacts = self._deduplicate_contacts(all_contacts)

        method_str = "+".join(sorted(discovery_methods)) if discovery_methods else "none"

        logger.info(f"━━━ TEAM DISCOVERY COMPLETE: {len(unique_contacts)} contacts ━━━")

        return TeamDiscoveryResult(
            contacts=unique_contacts,
            source_urls=scraped_urls,
            discovery_method=method_str,
            fallback_used=False,
            success=len(unique_contacts) > 0
        )

    async def _check_direct_urls(self, base_url: str) -> List[DiscoveredPage]:
        """
        Check common team page URL patterns with HEAD requests.
        Fast and free - just checking if URLs exist.
        """
        client = await self._get_client()
        found_pages = []

        # Check URLs in parallel (fast)
        async def check_url(pattern: str, priority: int) -> Optional[DiscoveredPage]:
            url = f"{base_url}{pattern}"
            try:
                response = await client.head(url, timeout=5)
                if response.status_code == 200:
                    # Calculate relevance score based on pattern priority
                    score = 1.0 - (priority * 0.05)  # Earlier patterns = higher score
                    return DiscoveredPage(
                        url=url,
                        source="direct_url",
                        relevance_score=score
                    )
            except Exception as e:
                logger.debug(f"  HEAD {pattern}: failed ({e})")
            return None

        # Check all patterns in parallel
        tasks = [check_url(pattern, i) for i, pattern in enumerate(TEAM_URL_PATTERNS)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, DiscoveredPage):
                found_pages.append(result)

        return found_pages

    async def _scan_sitemap(self, base_url: str) -> List[DiscoveredPage]:
        """
        Parse sitemap.xml to find team-related URLs.
        No AI needed - just XML parsing + keyword matching.
        """
        client = await self._get_client()
        sitemap_urls = [
            f"{base_url}/sitemap.xml",
            f"{base_url}/sitemap_index.xml",
            f"{base_url}/sitemap-index.xml",
        ]

        for sitemap_url in sitemap_urls:
            try:
                response = await client.get(sitemap_url, timeout=10)
                if response.status_code != 200:
                    continue

                # Parse XML
                try:
                    root = ET.fromstring(response.text)
                except ET.ParseError:
                    logger.debug(f"Failed to parse sitemap: {sitemap_url}")
                    continue

                # Handle sitemap index (contains other sitemaps)
                namespaces = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}

                # Check if this is a sitemap index
                sitemap_refs = root.findall('.//ns:sitemap/ns:loc', namespaces)
                if sitemap_refs:
                    # It's an index - look for sub-sitemaps about pages
                    for sitemap_ref in sitemap_refs:
                        sub_url = sitemap_ref.text
                        if any(kw in sub_url.lower() for kw in ['page', 'post', 'content']):
                            # Recursively check this sub-sitemap
                            sub_pages = await self._parse_sitemap_urls(sub_url, client)
                            if sub_pages:
                                return sub_pages

                # Parse URLs directly
                return await self._parse_sitemap_urls(sitemap_url, client, xml_content=response.text)

            except Exception as e:
                logger.debug(f"Sitemap check failed for {sitemap_url}: {e}")

        return []

    async def _parse_sitemap_urls(
        self,
        sitemap_url: str,
        client: AsyncSession,
        xml_content: Optional[str] = None
    ) -> List[DiscoveredPage]:
        """Parse a sitemap and find team-related URLs."""
        try:
            if xml_content is None:
                response = await client.get(sitemap_url, timeout=10)
                if response.status_code != 200:
                    return []
                xml_content = response.text

            root = ET.fromstring(xml_content)
            namespaces = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}

            pages = []
            urls = root.findall('.//ns:url/ns:loc', namespaces)

            # Also try without namespace (some sitemaps don't use it)
            if not urls:
                urls = root.findall('.//url/loc')

            for url_elem in urls:
                url = url_elem.text
                if not url:
                    continue

                # Only check the top-level path segments (max depth 2) to avoid
                # matching blog posts like /blog/post-about-the-hype or
                # /blog/something/management-tips which are not team pages.
                try:
                    path_segments = urlparse(url).path.lower().strip('/').split('/')
                    # Exclude deep paths (blog posts, articles) — team pages are shallow
                    if len(path_segments) > 2:
                        continue
                    top_path = '/'.join(path_segments)
                except Exception:
                    continue

                # Check if a top-level segment IS a team-related keyword (exact or prefix match)
                team_keywords = [
                    'team', 'ueber-uns', 'uber-uns', 'about',
                    'kontakt', 'contact', 'ansprechpartner',
                    'mitarbeiter', 'menschen', 'people',
                    'management', 'fuehrung', 'leadership',
                    'unternehmen', 'ueber', 'uber',
                ]

                for keyword in team_keywords:
                    # Match if keyword IS a segment or the segment STARTS WITH it
                    # e.g. /ueber-moresophy matches 'ueber', /team-members matches 'team'
                    if any(seg == keyword or seg.startswith(keyword + '-') for seg in path_segments):
                        score = 0.8 - (team_keywords.index(keyword) * 0.04)
                        pages.append(DiscoveredPage(
                            url=url,
                            source="sitemap",
                            relevance_score=score
                        ))
                        break

            return pages[:5]  # Max 5 from sitemap

        except Exception as e:
            logger.debug(f"Sitemap parsing failed: {e}")
            return []

    async def _scan_homepage_links(self, base_url: str) -> List[DiscoveredPage]:
        """
        Scan homepage for team/about links.
        No AI needed - just find <a> tags with team-related text.
        """
        client = await self._get_client()

        try:
            response = await client.get(base_url, timeout=15)
            if response.status_code != 200:
                logger.debug(f"Homepage request failed: {response.status_code}")
                return []

            soup = BeautifulSoup(response.text, 'lxml')

            # Find all links
            found_pages = []
            seen_urls = set()

            for link in soup.find_all('a', href=True):
                href = link.get('href', '')
                text = link.get_text(strip=True).lower()

                # Skip empty or javascript links
                if not href or href.startswith('#') or href.startswith('javascript:'):
                    continue

                # Make URL absolute
                full_url = urljoin(base_url, href)

                # Skip external links
                if not full_url.startswith(base_url):
                    continue

                # Skip if already seen
                if full_url in seen_urls:
                    continue

                # Check if link text or URL contains team keywords
                url_lower = full_url.lower()

                for keyword in TEAM_LINK_KEYWORDS:
                    if keyword in text or keyword.replace(' ', '-') in url_lower or keyword.replace(' ', '') in url_lower:
                        seen_urls.add(full_url)

                        # Calculate score based on keyword match
                        score = 0.7 - (TEAM_LINK_KEYWORDS.index(keyword) * 0.03)

                        # Boost if keyword is in link text (more reliable)
                        if keyword in text:
                            score += 0.1

                        found_pages.append(DiscoveredPage(
                            url=full_url,
                            source="homepage_link",
                            relevance_score=score,
                            title=link.get_text(strip=True)[:50]
                        ))
                        break

            # Sort by score and return top 5
            found_pages.sort(key=lambda x: x.relevance_score, reverse=True)
            return found_pages[:5]

        except Exception as e:
            logger.debug(f"Homepage scan failed: {e}")
            return []

    async def _scrape_team_page(
        self,
        url: str,
        company_name: str
    ) -> List[ExtractedContact]:
        """
        Scrape a team page with improved Playwright settings.

        Better than before:
        - Longer wait times for JS SPAs
        - Team-specific selector waiting
        - Full page scroll to trigger lazy loading
        - Multiple scroll passes
        """
        html = await self._scrape_with_playwright_v2(url)

        if not html:
            logger.warning(f"⚠️ Playwright scrape failed, trying httpx fallback")
            html = await self._scrape_with_httpx(url)

        if not html:
            logger.warning(f"❌ All scraping methods failed for {url}")
            return []

        # Parse and extract text
        soup = BeautifulSoup(html, "lxml")

        # Log raw body size
        body = soup.find('body')
        if body:
            raw_text = body.get_text(separator=" ", strip=True)
            logger.info(f"📄 Raw body text: {len(raw_text)} chars")

        # For SPAs (Next.js/React), extract SSR JSON from __NEXT_DATA__ or JSON-LD
        # before stripping script tags — the rendered text lives there
        ssr_text = ""
        next_data_tag = soup.find("script", id="__NEXT_DATA__")
        if next_data_tag and next_data_tag.string:
            try:
                import json as _json
                next_data = _json.loads(next_data_tag.string)
                # Flatten to string - person names/titles are buried in the props tree
                ssr_text = _json.dumps(next_data, ensure_ascii=False)[:MAX_TEXT_EXTRACT]
                logger.info(f"📦 Extracted __NEXT_DATA__ SSR content: {len(ssr_text)} chars")
            except Exception:
                pass

        # Remove non-content elements
        for elem in soup(["script", "style", "nav", "noscript", "svg", "iframe"]):
            elem.decompose()

        # Try to find team-specific sections first
        text = ""
        team_sections = soup.select(', '.join([
            "[class*='team']", "[class*='mitarbeiter']", "[class*='employee']",
            "[class*='people']", "[class*='staff']", "[class*='member']",
            "[id*='team']", "[id*='mitarbeiter']", "[id*='about']",
            "main", "article", ".content", "#content"
        ]))

        if team_sections:
            section_texts = []
            for section in team_sections[:5]:  # Max 5 sections
                section_text = section.get_text(separator="\n", strip=True)
                # Team member cards can be very short (e.g. "Thomas\nTeamleiter Windows")
                # Lower threshold to 15 chars to avoid dropping valid name+title cards
                if len(section_text) > 15:
                    section_texts.append(section_text)
            text = "\n\n".join(section_texts)
            logger.info(f"📦 Extracted from {len(team_sections)} team section(s): {len(text)} chars")

        # Fallback to full body if sections didn't yield much
        if len(text) < 200:
            text = soup.get_text(separator="\n", strip=True)
            logger.info(f"📦 Fallback to full body: {len(text)} chars")

        # Last resort: use SSR JSON data from Next.js for SPAs with no visible text
        if len(text) < 100 and ssr_text:
            text = ssr_text
            logger.info(f"📦 Using SSR/Next.js data as text source: {len(text)} chars")

        # Truncate if needed
        if len(text) > MAX_TEXT_EXTRACT:
            text = text[:MAX_TEXT_EXTRACT]
            logger.info(f"📦 Truncated to {MAX_TEXT_EXTRACT} chars")

        # Skip AI extraction if we have almost no text — 500 chars minimum to
        # avoid wasting LLM calls on bot-block pages that return ~200 chars of boilerplate
        if len(text) < 500:
            logger.warning(f"⚠️ Not enough text for extraction ({len(text)} chars)")
            return []

        # Bonus Fix 9: Try JSON-LD structured data first (free, no LLM needed)
        json_ld_contacts = self._extract_json_ld_contacts(html)
        if json_ld_contacts:
            logger.info(f"📋 JSON-LD extracted {len(json_ld_contacts)} contacts (structured data)")

        # Use AI to extract contacts with priority scoring (single merged LLM call)
        logger.info(f"🤖 Running AI contact extraction with priority scoring...")
        ai_contacts = await extract_contacts_with_priority(
            text,
            company_name,
            job_category=getattr(self, '_job_category', None),
            target_titles=getattr(self, '_target_titles', [])
        )

        # Prepend JSON-LD contacts (structured data is more reliable than AI extraction)
        if json_ld_contacts:
            seen = {c.name.lower() for c in json_ld_contacts}
            for c in ai_contacts:
                if c.name.lower() not in seen:
                    json_ld_contacts.append(c)
            return json_ld_contacts

        return ai_contacts

    def _extract_json_ld_contacts(self, html: str) -> List[ExtractedContact]:
        """
        Extract contacts from Schema.org / JSON-LD structured data.

        No LLM, no HTTP — pure JSON parse of HTML already fetched.
        Many DACH company websites embed <script type="application/ld+json">
        with @type: Person or Organization employee arrays.
        """
        try:
            soup = BeautifulSoup(html, 'lxml')
        except Exception:
            return []

        contacts = []
        for tag in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(tag.string or '')
            except (json.JSONDecodeError, AttributeError, TypeError):
                continue

            if not isinstance(data, dict):
                continue

            schema_type = data.get('@type', '')

            # Handle @type: Person directly
            if schema_type == 'Person':
                name = data.get('name', '').strip()
                if name and len(name.split()) >= 2:
                    contacts.append(ExtractedContact(
                        name=name,
                        title=data.get('jobTitle'),
                        email=data.get('email'),
                        phone=data.get('telephone'),
                        source='team_json_ld',
                        confidence=0.95
                    ))

            # Handle @type: Organization — look for employee arrays
            elif schema_type in ('Organization', 'LocalBusiness', 'Corporation'):
                for employee in data.get('employee', []):
                    if not isinstance(employee, dict):
                        continue
                    name = employee.get('name', '').strip()
                    if name and len(name.split()) >= 2:
                        contacts.append(ExtractedContact(
                            name=name,
                            title=employee.get('jobTitle'),
                            email=employee.get('email'),
                            source='team_json_ld',
                            confidence=0.95
                        ))

        return contacts

    async def _scrape_with_playwright_v2(self, url: str) -> Optional[str]:
        """
        Improved Playwright scraping for JS-heavy team pages.
        Uses sync Playwright in a thread executor to avoid event loop conflicts on Windows.
        """
        import asyncio

        def _sync_scrape() -> Optional[str]:
            try:
                from playwright.sync_api import sync_playwright

                logger.info(f"🎭 Starting Playwright (improved settings)...")

                with sync_playwright() as p:
                    browser = p.chromium.launch(
                        headless=True,
                        args=['--disable-http2', '--disable-blink-features=AutomationControlled']
                    )
                    try:
                        context = browser.new_context(
                            viewport={'width': 1280, 'height': 900},
                            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                        )
                        page = context.new_page()

                        logger.info(f"  → Navigating to {url}")
                        try:
                            page.goto(url, wait_until='domcontentloaded', timeout=20000)
                        except Exception as e:
                            logger.warning(f"  ⚠️ Navigation error: {e}")
                            return None

                        try:
                            page.wait_for_load_state('networkidle', timeout=10000)
                        except Exception:
                            logger.debug("  → Network idle timeout (continuing)")

                        # Try to wait for team-specific selectors
                        team_found = False
                        for selector in TEAM_PAGE_SELECTORS[:10]:
                            try:
                                page.wait_for_selector(selector, timeout=2000)
                                logger.info(f"  ✓ Found team selector: {selector}")
                                team_found = True
                                break
                            except Exception:
                                continue

                        wait_ms = 2000 if team_found else 8000
                        page.wait_for_timeout(wait_ms)

                        # Full page scroll to trigger lazy loading
                        logger.info("  → Scrolling page to load lazy content...")
                        _sync_full_page_scroll(page)

                        html = page.content()
                        logger.info(f"  ✓ Got {len(html)} bytes HTML")
                        # < 1KB after JS execution = bot-block redirect, not real content
                        if len(html) < 1024:
                            logger.warning(f"  ⚠️ HTML too small ({len(html)} bytes) — likely bot-blocked, skipping")
                            return None
                        # Do NOT truncate Playwright HTML here — the first 100KB is often
                        # just React/JS bundles. BeautifulSoup handles large DOMs fine and
                        # text truncation happens AFTER extraction in _scrape_team_page.
                        return html

                    finally:
                        browser.close()

            except ImportError:
                logger.warning("⚠️ Playwright not installed")
                return None
            except Exception as e:
                logger.warning(f"❌ Playwright error: {e}")
                return None

        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, _sync_scrape)
        except Exception as e:
            logger.warning(f"❌ Playwright executor failed: {e}")
            return None

    async def _scrape_with_httpx(self, url: str) -> Optional[str]:
        """Fallback scraping with curl_cffi (Chrome 136 TLS fingerprint)."""
        client = await self._get_client()

        try:
            response = await client.get(url, timeout=15, allow_redirects=True)
            if response.status_code != 200:
                return None

            content = response.text
            if len(content) > MAX_PAGE_SIZE:
                content = content[:MAX_PAGE_SIZE]

            return content

        except Exception as e:
            logger.debug(f"curl_cffi scrape failed: {e}")
            return None

    def _deduplicate_pages(self, pages: List[DiscoveredPage]) -> List[DiscoveredPage]:
        """Remove duplicate pages by URL."""
        seen = set()
        unique = []
        for page in pages:
            # Normalize URL
            url = page.url.rstrip('/')
            if url not in seen:
                seen.add(url)
                unique.append(page)
        return unique

    def _deduplicate_contacts(self, contacts: List[ExtractedContact]) -> List[ExtractedContact]:
        """Remove duplicate contacts by name."""
        seen_names = set()
        unique = []

        for contact in contacts:
            name_lower = contact.name.lower().strip()
            if name_lower not in seen_names:
                seen_names.add(name_lower)
                unique.append(contact)

        return unique

    async def close(self):
        """Close curl_cffi session."""
        if self._http_client:
            await self._http_client.close()
            self._http_client = None


# Convenience function (maintains backwards compatibility)
async def discover_team_contacts(
    company_name: str,
    domain: Optional[str] = None,
    job_category: Optional[str] = None,
    target_titles: List[str] = []
) -> TeamDiscoveryResult:
    """
    Discover team contacts for a company.

    Uses the new "2-clicks" smart discovery approach.
    """
    discovery = TeamDiscovery()
    try:
        return await discovery.discover_and_extract(
            company_name=company_name,
            domain=domain,
            job_category=job_category,
            target_titles=target_titles
        )
    finally:
        await discovery.close()
