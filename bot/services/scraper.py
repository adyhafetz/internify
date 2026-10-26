from __future__ import annotations
import logging
import httpx
from bs4 import BeautifulSoup
from bot import config

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


async def scrape_job_url(url: str) -> str:
    """
    Scrapes the text/markdown content of a job posting URL.
    Attempts ScrapingAnt first if an API key is configured.
    Falls back to direct HTTP scraping using httpx and BeautifulSoup.
    """
    if config.SCRAPINGANT_API_KEY:
        try:
            logger.info("Scraping URL via ScrapingAnt: %s", url)
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.get(
                    "https://api.scrapingant.com/v2/markdown",
                    params={"url": url},
                    headers={"x-api-key": config.SCRAPINGANT_API_KEY},
                )
                if resp.status_code == 200 and resp.text.strip():
                    return resp.text.strip()
                logger.warning(
                    "ScrapingAnt returned %d: %s. Falling back to direct fetch.",
                    resp.status_code,
                    resp.text[:200],
                )
        except Exception as e:
            logger.warning("ScrapingAnt fetch failed (%s). Falling back to direct fetch.", e)

    # Fallback direct fetch
    logger.info("Scraping URL directly: %s", url)
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # Remove script, style, and header/nav elements that clutter job descriptions
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript", "svg"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    # Clean up excess whitespace while preserving line structure
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)

