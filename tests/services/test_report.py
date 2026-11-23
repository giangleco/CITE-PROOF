from uuid import UUID

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from citeproof.core.enums import ClaimStatus, EvidenceRelation, ReferenceStatus
from citeproof.core.errors import NotFoundError
from citeproof.core.settings import Settings
from citeproof.db.models import ClaimVerification, ReferenceCheck, User
from citeproof.schemas.report import MismatchView
from citeproof.services import report
from citeproof.services.sample import seed_sample
from citeproof.web.labels import FORBIDDEN_WORDS
from tests.services.helpers import new_user

SessionFactory = async_sessionmaker[AsyncSession]


async def _seeded(s: AsyncSession, settings: Settings) -> tuple[User, UUID]:
    user = await new_user(s)
    _, run_id = await seed_sample(s, settings, user_id=user.id)
    await s.commit()
    return user, run_id


@pytest.mark.db
async def test_overview_tallies(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user, run_id = await _seeded(s, settings)
        overview = await report.get_overview(s, user=user, run_id=run_id)
    claims = {t.key: t.count for t in overview.claim_tally}
    refs = {t.key: t.count for t in overview.ref_tally}
    assert claims == {
        "SUPPORTED": 1,
        "PARTIALLY_SUPPORTED": 1,
        "CONTRADICTED": 1,
        "UNSUPPORTED": 1,
        "UNVERIFIABLE": 2,
    }
    assert [t.key for t in overview.claim_tally] == [s.value for s in ClaimStatus]
    assert refs["MATCHED"] == 2 and all(
        refs[s.value] == 1 for s in ReferenceStatus if s != "MATCHED"
    )
    assert overview.header.is_sample and not overview.is_empty
    assert [w.code for w in overview.header.warnings] == ["SCANNED_PAGES"]


@pytest.mark.db
async def test_overview_todos_order_and_text(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user, run_id = await _seeded(s, settings)
        overview = await report.get_overview(s, user=user, run_id=run_id)
    kinds = [t.kind for t in overview.todos]
    assert kinds == ["flagged", "mismatch", "missing", "not_found", "lookup_failed"]
    assert overview.todos[0].title == "Xem lại 3 nhận định được gắn cờ nên tự kiểm tra"
    assert overview.todos[1].title == "Sửa thông tin của tài liệu [2]"
    assert "Năm: bài ghi 2018, bản ghi tìm thấy 2019" in overview.todos[1].detail
    assert overview.todos[0].href == f"/runs/{run_id}/claims?flag=1"


@pytest.mark.db
async def test_references_filter(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user, run_id = await _seeded(s, settings)
        _, items = await report.list_references(
            s, user=user, run_id=run_id, status=ReferenceStatus.METADATA_MISMATCH
        )
        _, all_items = await report.list_references(s, user=user, run_id=run_id)
    assert len(items) == 1 and items[0].index == 2
    assert items[0].mismatches[0] == MismatchView(field="year", in_document="2018", found="2019")
    assert items[0].doi == "10.18653/v1/N19-1423"
    assert [i.index for i in all_items] == [1, 2, 3, 4, 5, 6, 7]


@pytest.mark.db
async def test_mismatches_dict_default_is_empty(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user, run_id = await _seeded(s, settings)
        await s.execute(update(ReferenceCheck).values(mismatches={}))
        await s.commit()
        _, items = await report.list_references(s, user=user, run_id=run_id)
    assert all(i.mismatches == [] for i in items)


@pytest.mark.db
async def test_claims_filters(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user, run_id = await _seeded(s, settings)
        _, flagged = await report.list_claims(s, user=user, run_id=run_id, flagged=True)
        _, ref1 = await report.list_claims(s, user=user, run_id=run_id, ref_index=1)
        _, contra = await report.list_claims(
            s, user=user, run_id=run_id, status=ClaimStatus.CONTRADICTED
        )
        _, everything = await report.list_claims(s, user=user, run_id=run_id)
    assert len(flagged) == 3 and len(ref1) == 2 and len(contra) == 1
    assert [c.page for c in everything] == sorted(c.page for c in everything)


@pytest.mark.db
async def test_claim_sheet_evidence_and_span(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user, run_id = await _seeded(s, settings)
        _, rows = await report.list_claims(
            s, user=user, run_id=run_id, status=ClaimStatus.CONTRADICTED
        )
        sheet = await report.get_claim_sheet(
            s, user=user, run_id=run_id, claim_verification_id=rows[0].id
        )
    assert sheet.evidence[0].relation == EvidenceRelation.CONTRADICTS
    assert sheet.evidence[0].quote.startswith("BERT is designed")
    assert sheet.evidence[0].language == "en" and sheet.evidence[0].page == 1
    assert sheet.span in sheet.sentence and sheet.ref_index == 2
    assert sheet.section == "2.4 BERT và các biến thể"
    assert sheet.my_feedback is None


@pytest.mark.db
async def test_missing_sources(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user, run_id = await _seeded(s, settings)
        _, missing = await report.list_missing(s, user=user, run_id=run_id)
    assert [(m.ref_index, m.waiting_claims) for m in missing] == [(5, 1)]


@pytest.mark.db
async def test_markdown_has_sections_and_no_forbidden_words(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user, run_id = await _seeded(s, settings)
        text = await report.render_markdown(s, user=user, run_id=run_id)
    for heading in (
        "Tóm tắt",
        "Thông tin tài liệu và cảnh báo",
        "Thống kê",
        "Tài liệu tham khảo",
        "Nhận định",
        "Nhận định chưa kiểm chứng được",
        "Giới hạn",
        "Gợi ý hành động",
    ):
        assert f"## {heading}" in text
    assert "> BERT is designed to pre-train" in text
    for word in FORBIDDEN_WORDS:
        assert word not in text.lower()


@pytest.mark.db
async def test_foreign_run_not_found(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        _, run_id = await _seeded(s, settings)
        other = await new_user(s, "o@b.vn")
        with pytest.raises(NotFoundError):
            await report.get_overview(s, user=other, run_id=run_id)
        with pytest.raises(NotFoundError):
            await report.list_claims(s, user=other, run_id=run_id)
        cv = (await s.execute(select(ClaimVerification.id))).scalars().first()
        assert cv is not None
        with pytest.raises(NotFoundError):
            await report.get_claim_sheet(s, user=other, run_id=run_id, claim_verification_id=cv)


@pytest.mark.db
async def test_query_count_constant(
    session_factory: SessionFactory, engine: AsyncEngine, clean_db: None, settings: Settings
) -> None:
    statements: list[str] = []

    def count(*args: object) -> None:
        statements.append("x")

    async with session_factory() as s:
        user, run_id = await _seeded(s, settings)
        event.listen(engine.sync_engine, "before_cursor_execute", count)
        try:
            await report.list_claims(s, user=user, run_id=run_id)
            small = len(statements)
            await seed_sample(s, settings, user_id=user.id)  # thêm dữ liệu vào bảng
            await s.commit()
            statements.clear()
            await report.list_claims(s, user=user, run_id=run_id)
            await report.get_overview(s, user=user, run_id=run_id)
            big = len(statements)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", count)
    assert small <= 4 and big <= small + 8
