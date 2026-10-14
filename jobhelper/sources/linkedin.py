from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup

from jobhelper.sources.base import JobPosting

GUEST_SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"


class LinkedInJobSource:
    """Public LinkedIn job cards (no login). Easy Apply is never automated."""

    name = "linkedin"

    def __init__(self, geo_id: str = "") -> None:
        self.geo_id = geo_id

    async def search(self, keywords: str, location: str) -> list[JobPosting]:
        params: dict[str, str | int] = {
            "keywords": keywords,
            "location": location,
            "f_TPR": "r86400",
            "start": 0,
        }
        if self.geo_id:
            params["geoId"] = self.geo_id
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Orchy/0.1",
            "Accept-Language": "en-US,en;q=0.9",
        }
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=headers) as client:
            response = await client.get(GUEST_SEARCH, params=params)
            if response.status_code >= 400:
                raise RuntimeError(f"LinkedIn guest search HTTP {response.status_code}")
            jobs = _parse_cards(response.text)
            for job in jobs[:8]:
                job.description = await _job_description(client, job.external_id)
        return jobs


def _parse_cards(html: str) -> list[JobPosting]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("li")
    jobs: list[JobPosting] = []
    for card in cards:
        title_el = card.select_one("h3")
        company_el = card.select_one("h4")
        location_el = card.select_one(".job-search-card__location")
        link_el = card.select_one("a.base-card__full-link, a[href*='/jobs/view/']")
        if not title_el or not link_el:
            continue
        href = link_el.get("href", "")
        job_id = _job_id(href)
        if not job_id:
            continue
        jobs.append(
            JobPosting(
                external_id=job_id,
                title=title_el.get_text(strip=True),
                company=company_el.get_text(strip=True) if company_el else "",
                location=location_el.get_text(strip=True) if location_el else "",
                url=href.split("?")[0],
                description="",
                source="linkedin",
            )
        )
    return jobs


async def _job_description(client: httpx.AsyncClient, job_id: str) -> str:
    url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
    try:
        response = await client.get(url)
        if response.status_code >= 400:
            return ""
        soup = BeautifulSoup(response.text, "html.parser")
        body = soup.select_one(".show-more-less-html__markup, .description__text, section")
        return body.get_text(" ", strip=True)[:5000] if body else soup.get_text(" ", strip=True)[:2000]
    except httpx.HTTPError:
        return ""


def _job_id(url: str) -> str:
    match = re.search(r"/jobs/view/[^/]*?(\d+)", url)
    if match:
        return match.group(1)
    match = re.search(r"currentJobId=(\d+)", url)
    return match.group(1) if match else ""
