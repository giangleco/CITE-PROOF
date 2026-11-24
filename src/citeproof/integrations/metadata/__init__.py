"""Tra cứu metadata tài liệu khoa học: Crossref, OpenAlex, Semantic Scholar (spec §7.3).

S3 (và agent A1) chỉ dùng `MetadataGateway` qua `PipelineDeps.metadata`; gateway lo cache
`metadata_cache`, giới hạn tốc độ, thử lại và ngắt mạch theo nguồn.
"""

from citeproof.integrations.metadata.abstracts import abstract_from_inverted_index, clean_jats
from citeproof.integrations.metadata.base import SOURCES, MetadataConnector, user_agent
from citeproof.integrations.metadata.breaker import CircuitBreaker
from citeproof.integrations.metadata.cache import (
    CachedLookup,
    InMemoryMetadataCache,
    LookupCache,
    MetadataCache,
    query_hash,
)
from citeproof.integrations.metadata.crossref import CrossrefConnector
from citeproof.integrations.metadata.gateway import (
    LookupOutcome,
    LookupStatus,
    MetadataGateway,
    make_gateway_factory,
)
from citeproof.integrations.metadata.models import OaLocation, WorkAuthor, WorkRecord
from citeproof.integrations.metadata.openalex import OpenAlexConnector
from citeproof.integrations.metadata.ratelimit import RateLimiter
from citeproof.integrations.metadata.semantic_scholar import SemanticScholarConnector

__all__ = [
    "SOURCES",
    "CachedLookup",
    "CircuitBreaker",
    "CrossrefConnector",
    "InMemoryMetadataCache",
    "LookupCache",
    "LookupOutcome",
    "LookupStatus",
    "MetadataCache",
    "MetadataConnector",
    "MetadataGateway",
    "OaLocation",
    "OpenAlexConnector",
    "RateLimiter",
    "SemanticScholarConnector",
    "WorkAuthor",
    "WorkRecord",
    "abstract_from_inverted_index",
    "clean_jats",
    "make_gateway_factory",
    "query_hash",
    "user_agent",
]
