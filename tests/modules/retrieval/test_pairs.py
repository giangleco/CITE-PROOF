"""Cặp claim-reference và chọn nguồn (spec §7.7; T-501)."""

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import ContentLevel, SourceOrigin, StageName
from citeproof.db.models import ReferenceCheck, VerificationRun
from citeproof.modules.retrieval.pairs import choose_sources, load_pairs
from citeproof.pipeline.config import RunScope, load_run_info
from citeproof.pipeline.runs import create_run
from tests.modules.retrieval.helpers import ClaimSpec, add_claims, add_source
from tests.modules.sources.test_fetch_stage import _cite, _work
from tests.modules.sources.test_index_stage import _entries
from tests.services.helpers import new_user

SessionFactory = async_sessionmaker[AsyncSession]


@pytest.mark.db
async def test_load_pairs_includes_unresolved(
    session_factory: SessionFactory, clean_db: None
) -> None:
    work_id = await _work(session_factory, "Paper One")
    _, run_id = await _cite(session_factory, [work_id, work_id])
    e0, e1 = await _entries(session_factory, run_id)
    c0, c1, c2 = await add_claims(
        session_factory,
        run_id,
        [
            ClaimSpec("Câu thứ nhất", links=(None,)),
            ClaimSpec("Câu thứ hai", links=(e1, e0), is_claim=False),
            ClaimSpec("Câu thứ ba", language="en", links=(e0,)),
        ],
    )
    async with session_factory() as s:
        info = await load_run_info(s, run_id)
        pairs = await load_pairs(s, info)
    assert [(p.claim_id, p.reference_entry_id) for p in pairs] == [
        (c0, None),
        (c1, e0),
        (c1, e1),
        (c2, e0),
    ]
    assert [p.position for p in pairs] == [0, 1, 2, 3]
    assert pairs[0].claim_text == "Câu thứ nhất" and pairs[0].language == "vi"
    assert pairs[0].sentence_text == "Câu thứ nhất [1]."
    assert [p.is_claim for p in pairs] == [True, False, False, True]


@pytest.mark.db
async def test_load_pairs_respects_scope(session_factory: SessionFactory, clean_db: None) -> None:
    work_id = await _work(session_factory, "Paper One")
    _, run_id = await _cite(session_factory, [work_id, work_id], scope=[1])
    e0, e1 = await _entries(session_factory, run_id)
    await add_claims(
        session_factory,
        run_id,
        [ClaimSpec("a b", links=(None, e0, e1))],
    )
    async with session_factory() as s:
        info = await load_run_info(s, run_id)
        pairs = await load_pairs(s, info)
    assert [p.reference_entry_id for p in pairs] == [e1]


@pytest.mark.db
async def test_choose_sources_priority(session_factory: SessionFactory, clean_db: None) -> None:
    work_id = await _work(session_factory, "Paper One")
    user, run_id = await _cite(session_factory, [work_id])
    [entry] = await _entries(session_factory, run_id)
    abstract = await add_source(
        session_factory, ["abstract text"], work_id=work_id, level=ContentLevel.ABSTRACT
    )
    open_full = await add_source(session_factory, ["open one", "open two"], work_id=work_id)
    async with session_factory() as s:
        info = await load_run_info(s, run_id)
        choice = await choose_sources(s, info, entry)
        none_choice = await choose_sources(s, info, None)
    assert choice.full_text is not None and choice.abstract is not None
    assert choice.full_text.source_file_id == open_full
    assert choice.full_text.origin is SourceOrigin.OPEN_ACCESS
    assert choice.full_text.chunk_count == 2 and choice.full_text.language == "en"
    assert choice.abstract.source_file_id == abstract
    assert (none_choice.full_text, none_choice.abstract) == (None, None)

    # Tệp tải lên của người khác bị bỏ qua; của chính người dùng thắng, mới nhất thắng.
    async with session_factory() as s:
        other = await new_user(s, email="other@b.vn")
    await add_source(session_factory, ["theirs"], owner_user_id=other.id, reference_entry_id=entry)
    old = await add_source(
        session_factory, ["mine old"], owner_user_id=user.id, reference_entry_id=entry
    )
    new = await add_source(
        session_factory, ["mine new"], owner_user_id=user.id, reference_entry_id=entry
    )
    async with session_factory() as s:
        choice = await choose_sources(s, info, entry)
    assert choice.full_text is not None
    assert choice.full_text.source_file_id == new != old
    assert choice.full_text.origin is SourceOrigin.USER_UPLOAD
    assert choice.abstract is not None and choice.abstract.source_file_id == abstract


@pytest.mark.db
async def test_choose_sources_language_and_empty(
    session_factory: SessionFactory, clean_db: None
) -> None:
    work_id = await _work(session_factory, "Paper One")
    _, run_id = await _cite(session_factory, [work_id])
    [entry] = await _entries(session_factory, run_id)
    empty = await add_source(session_factory, [], work_id=work_id)
    async with session_factory() as s:
        info = await load_run_info(s, run_id)
        choice = await choose_sources(s, info, entry)
    assert choice.full_text is not None
    assert choice.full_text.source_file_id == empty
    assert (choice.full_text.chunk_count, choice.full_text.language) == (0, None)


@pytest.mark.db
async def test_choose_sources_uses_parent_run_work(
    session_factory: SessionFactory, clean_db: None
) -> None:
    # Lần chạy con bắt đầu từ S6 không có reference_checks: work lấy từ lần chạy cha.
    work_id = await _work(session_factory, "Paper One")
    user, parent_id = await _cite(session_factory, [work_id])
    [entry] = await _entries(session_factory, parent_id)
    open_full = await add_source(session_factory, ["open text"], work_id=work_id)
    async with session_factory() as s:
        parent = await load_run_info(s, parent_id)
        child_id = await create_run(
            s,
            document_id=parent.document_id,
            user_id=user.id,
            config={
                "variant": "V2",
                "scope": RunScope(
                    kind="upload", reference_entry_ids=[entry], from_stage=StageName.S6
                ).model_dump(mode="json"),
            },
        )
        await s.execute(
            update(VerificationRun)
            .where(VerificationRun.id == child_id)
            .values(parent_run_id=parent_id)
        )
        await s.commit()
        child = await load_run_info(s, child_id)
        choice = await choose_sources(s, child, entry)
        assert child.parent_run_id == parent_id
        # Dòng của chính lần chạy con thắng dòng của lần chạy cha.
        other_work = await _work(session_factory, "Paper Two")
        s.add(
            ReferenceCheck(
                run_id=child_id,
                reference_entry_id=entry,
                status="MATCHED",
                work_id=other_work,
                sources_searched=[],
            )
        )
        await s.commit()
        own = await choose_sources(s, child, entry)
    assert choice.full_text is not None and choice.full_text.source_file_id == open_full
    assert own.full_text is None
