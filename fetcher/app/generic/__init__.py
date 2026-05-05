"""Demonstration that the scraping primitives in app/scraping are not LinkedIn-specific.

Re-uses TLS spoofing, classified retry, jittered rate limiting, and the same
parser-version pattern against a non-LinkedIn target.
"""
from .fetcher import GenericScraper

__all__ = ["GenericScraper"]
