"""Abstract scraper interface."""

from abc import ABC, abstractmethod


class Scraper[RequestT, ResultT](ABC):
    """Base class for anything that turns a request into a scraped result.

    Subclasses implement :meth:`scrape`; the request and result types are
    supplied as type arguments, e.g. ``Scraper[DocumentRequest, ScrapeResult]``.
    """

    @abstractmethod
    def scrape(self, request: RequestT) -> ResultT:
        """Return the scrape result for ``request``."""
