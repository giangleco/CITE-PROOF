"""Truy vấn lexical: `build_tsquery` và `search_chunks` (spec §7.7, QĐ-24)."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.modules.retrieval.lexical import STOPWORDS, build_tsquery, search_chunks
from tests.modules.retrieval.helpers import add_source

SessionFactory = async_sessionmaker[AsyncSession]


def test_build_tsquery() -> None:
    assert (
        build_tsquery("The model's accuracy is 95% on SQuAD")
        == "'model' | 'accuracy' | '95' | 'squad'"
    )


def test_build_tsquery_dedupes_in_order_and_caps_terms() -> None:
    assert build_tsquery("Alpha beta ALPHA gamma beta") == "'alpha' | 'beta' | 'gamma'"
    words = " ".join(f"w{i:02d}" for i in range(40))
    query = build_tsquery(words, max_terms=32)
    assert query is not None and query.count("|") == 31


def test_build_tsquery_empty() -> None:
    assert build_tsquery("the of và của") is None
    assert build_tsquery("a b c") is None
    assert build_tsquery("") is None
    assert {"the", "và", "của"} <= STOPWORDS


@pytest.mark.db
async def test_build_tsquery_adversarial_input_is_valid(
    session_factory: SessionFactory, clean_db: None
) -> None:
    query = build_tsquery("x' | & ! ( :* <-> y SELECT '' zz'")
    assert query is not None
    async with session_factory() as session:
        await session.execute(text("SELECT to_tsquery('simple', :q)"), {"q": query})


@pytest.mark.db
async def test_search_orders_by_rank_then_index(
    session_factory: SessionFactory, clean_db: None
) -> None:
    source_id = await add_source(
        session_factory,
        [
            "encoder only once here",
            "nothing relevant",
            "encoder encoder encoder repeated",
            "encoder once again",
        ],
    )
    async with session_factory() as session:
        hits = await search_chunks(session, source_id, "'encoder'", 10)
        limited = await search_chunks(session, source_id, "'encoder'", 2)
    assert [h.chunk_index for h in hits] == [2, 0, 3] or [h.chunk_index for h in hits] == [0, 2, 3]
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)
    ties = [h.chunk_index for h in hits if h.score == scores[-1]]
    assert ties == sorted(ties)
    assert [h.chunk_index for h in limited] == [h.chunk_index for h in hits][:2]


@pytest.mark.db
async def test_search_section_filter(session_factory: SessionFactory, clean_db: None) -> None:
    source_id = await add_source(
        session_factory,
        [("encoder in abstract", "Abstract"), ("encoder in body", "Methods")],
    )
    async with session_factory() as session:
        hits = await search_chunks(session, source_id, "'encoder'", 5, section="Methods")
        unknown = await search_chunks(session, uuid.uuid4(), "'encoder'", 5)
    assert [h.chunk_index for h in hits] == [1]
    assert unknown == []
