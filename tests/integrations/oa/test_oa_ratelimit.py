"""Giới hạn tốc độ theo host khi tìm và tải bản mở (S5): arXiv 1 yêu cầu/3 s, ACL Anthology và
Unpaywall 1 yêu cầu/s, host khác không giới hạn. Đồng hồ giả như `RateLimiter` của T-304.
"""

import asyncio
import uuid

import httpx
import pytest

from citeproof.integrations.metadata import RateLimiter
from citeproof.integrations.oa import HostRateLimits, UnpaywallAccess, WorkRef, make_resolvers
from tests.integrations.metadata.fakes import FakeApi, json_response
from tests.integrations.metadata.test_metadata_ratelimit import FakeClock
from tests.integrations.oa.fakes import JUMPER_DOI, load_oa, openalex_gateway_factory


def test_hosts_map_to_shared_limiters() -> None:
    limits = HostRateLimits()
    export = limits.limiter_for("https://export.arxiv.org/pdf/1706.03762")
    assert isinstance(export, RateLimiter)
    assert export.rate_per_second == pytest.approx(1 / 3)
    # Mọi host của arXiv dùng chung một limiter.
    assert limits.limiter_for("http://arxiv.org/pdf/1706.03762v2") is export
    assert limits.limiter_for("https://ARXIV.org./pdf/x") is export
    acl = limits.limiter_for("https://aclanthology.org/N19-1423.pdf")
    unpaywall = limits.limiter_for("https://api.unpaywall.org/v2/10.1/x")
    assert acl is not None and unpaywall is not None
    assert acl.rate_per_second == unpaywall.rate_per_second == 1.0
    assert acl is not unpaywall and acl is not export


@pytest.mark.parametrize(
    "url",
    ["https://www.nature.com/x.pdf", "https://arxiv.org.evil.example/x", "khong-phai-url", ""],
)
async def test_other_hosts_unlimited(url: str) -> None:
    clock = FakeClock()
    limits = HostRateLimits(clock=clock, sleep=clock.sleep)
    assert limits.limiter_for(url) is None
    for _ in range(3):
        await limits.acquire(url)
    assert clock.sleeps == []


async def test_unpaywall_lookups_spaced_one_second() -> None:
    clock = FakeClock()
    times: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        times.append(clock.now)
        return json_response(load_oa("unpaywall_doi_jumper"))

    api = FakeApi(handler)
    limits = HostRateLimits(clock=clock, sleep=clock.sleep)
    access = UnpaywallAccess(api.client(), "ops@b.vn", limits=limits)
    work = WorkRef(work_id=uuid.uuid4(), doi=JUMPER_DOI, arxiv_id=None)
    await asyncio.gather(access.resolve(work), access.resolve(work))
    assert times == [0.0, 1.0]


async def test_make_resolvers_passes_limits_to_unpaywall() -> None:
    clock = FakeClock()
    times: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        times.append(clock.now)
        return json_response(load_oa("unpaywall_doi_jumper"))

    api = FakeApi(handler)
    limits = HostRateLimits(clock=clock, sleep=clock.sleep)
    *_, unpaywall = make_resolvers(
        http=api.client(),
        gateway_factory=openalex_gateway_factory(lambda request: httpx.Response(404)),
        contact_email="ops@b.vn",
        limits=limits,
    )
    work = WorkRef(work_id=uuid.uuid4(), doi=JUMPER_DOI, arxiv_id=None)
    await unpaywall.resolve(work)
    await unpaywall.resolve(work)
    assert times == [0.0, 1.0]
