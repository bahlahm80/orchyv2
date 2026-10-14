from jobhelper.sources.base import JobPosting


class SampleJobSource:
    """Offline listings so the dashboard works without LinkedIn access."""

    name = "sample"

    async def search(self, keywords: str, location: str) -> list[JobPosting]:
        _ = (keywords, location)
        return [
            JobPosting(
                external_id="sample-backend-1",
                title="Backend Software Engineer",
                company="Northwind Labs",
                location="Remote, United States",
                url="https://www.linkedin.com/jobs/view/sample-backend-1",
                description="Python FastAPI service work, SQLite/Postgres, REST APIs, LLM tooling.",
                source="sample",
            ),
            JobPosting(
                external_id="sample-fullstack-2",
                title="Full-Stack Engineer",
                company="Harbor & Co",
                location="New York, NY",
                url="https://www.linkedin.com/jobs/view/sample-fullstack-2",
                description="JavaScript UI plus Python APIs. Human-in-the-loop workflow tools.",
                source="sample",
            ),
            JobPosting(
                external_id="sample-sales-3",
                title="Enterprise Account Executive",
                company="QuotaForge",
                location="United States",
                url="https://www.linkedin.com/jobs/view/sample-sales-3",
                description="Quota-carrying SaaS sales. Cold outbound, closing enterprise deals.",
                source="sample",
            ),
        ]
