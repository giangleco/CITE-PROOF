from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import delete, event, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from citeproof.core.enums import (
    ClaimStatus,
    EvidenceRelation,
    ReferenceStatus,
    UnverifiableReason,
    Verifier,
)
from citeproof.core.errors import NotFoundError
from citeproof.core.settings import Settings
from citeproof.db.models import (
    ClaimVerification,
    ReferenceCheck,
    ReferenceEntry,
    User,
    VerificationRun,
)
from citeproof.schemas.report import MismatchView
from citeproof.services import report
from citeproof.services.sample import seed_sample
from citeproof.web.labels import FORBIDDEN_WORDS, UNCITED_REFERENCE, UNRESOLVED_REF
from tests.services.helpers import (
    UNRESOLVED_SENTENCE,
    add_claim_verification,
    new_user,
    run_document_id,
)

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
    assert items[0].doi == "10.18653/v1/n19-1423"
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
    # QĐ-31: tài liệu có nhận định `REFERENCE_NOT_FOUND` ([4]) cũng được liệt kê.
    assert [(m.ref_index, m.waiting_claims) for m in missing] == [(4, 1), (5, 1)]


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


# --- Dữ liệu thật (Task 18: QĐ-28, QĐ-31, FR-21) --------------------------------------------


@pytest.mark.db
async def test_report_filters_final_verifier(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user, run_id = await _seeded(s, settings)
        await s.execute(
            update(VerificationRun)
            .where(VerificationRun.id == run_id)
            .values(config={"variant": Verifier.V2_A2.value})
        )
        entry_id = await s.scalar(
            select(ReferenceEntry.id).where(
                ReferenceEntry.document_id == await run_document_id(s, run_id),
                ReferenceEntry.order_index == 0,
            )
        )
        final = [
            await add_claim_verification(
                s,
                run_id,
                entry_id=entry_id,
                sentence=f"Nhận định cuối số {i} [1].",
                status=ClaimStatus.SUPPORTED,
                reason=None,
                verifier=Verifier.V2_A2,
                page=40 + i,
            )
            for i in range(2)
        ]
        await s.commit()
        overview = await report.get_overview(s, user=user, run_id=run_id)
        _, rows = await report.list_claims(s, user=user, run_id=run_id)
        _, missing = await report.list_missing(s, user=user, run_id=run_id)
        markdown = await report.render_markdown(s, user=user, run_id=run_id)
        sheet = await report.get_claim_sheet(
            s, user=user, run_id=run_id, claim_verification_id=final[0]
        )
        v2_id = (
            (
                await s.execute(
                    select(ClaimVerification.id).where(
                        ClaimVerification.run_id == run_id,
                        ClaimVerification.verifier == Verifier.V2.value,
                    )
                )
            )
            .scalars()
            .first()
        )
        assert v2_id is not None
        with pytest.raises(NotFoundError):
            await report.get_claim_sheet(s, user=user, run_id=run_id, claim_verification_id=v2_id)
    assert sorted(r.id for r in rows) == sorted(final)
    assert {t.key: t.count for t in overview.claim_tally} == {"SUPPORTED": 2}
    assert missing == [] and overview.missing_count == 0
    assert sheet.verifier == "V2+A2"
    assert "BERT chỉ sử dụng ngữ cảnh bên trái" not in markdown


@pytest.mark.db
async def test_unresolved_claim_listed(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user, run_id = await _seeded(s, settings)
        cv_id = await add_claim_verification(s, run_id, entry_id=None, sentence=UNRESOLVED_SENTENCE)
        await s.commit()
        overview = await report.get_overview(s, user=user, run_id=run_id)
        _, rows = await report.list_claims(s, user=user, run_id=run_id)
        _, unverifiable = await report.list_claims(
            s, user=user, run_id=run_id, status=ClaimStatus.UNVERIFIABLE
        )
        _, ref1 = await report.list_claims(s, user=user, run_id=run_id, ref_index=1)
        sheet = await report.get_claim_sheet(
            s, user=user, run_id=run_id, claim_verification_id=cv_id
        )
        markdown = await report.render_markdown(s, user=user, run_id=run_id)
    row = next(r for r in rows if r.id == cv_id)
    assert row.ref_index is None and len(rows) == 7
    assert {t.key: t.count for t in overview.claim_tally}["UNVERIFIABLE"] == 3
    assert sum(t.count for t in overview.claim_tally) == len(rows)
    assert len(unverifiable) == 3 and cv_id not in {r.id for r in ref1}
    assert sheet.ref_index is None and sheet.ref_raw is None
    assert sheet.unverifiable_reason == UnverifiableReason.REFERENCE_UNRESOLVED
    assert f"tài liệu {UNRESOLVED_REF}: {UNRESOLVED_SENTENCE}" in markdown


@pytest.mark.db
async def test_uncited_reference_marked(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user, run_id = await _seeded(s, settings)
        _, refs = await report.list_references(s, user=user, run_id=run_id)
        markdown = await report.render_markdown(s, user=user, run_id=run_id)
    # Bài mẫu: [1] được trích hai lần, [6] và [7] không có trích dẫn nào.
    assert [r.cited_count for r in refs] == [2, 1, 1, 1, 1, 0, 0]
    assert markdown.count(UNCITED_REFERENCE) == 2
    assert f"6. {refs[5].raw_text}" in markdown


@pytest.mark.db
async def test_missing_includes_not_found(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user, run_id = await _seeded(s, settings)
        _, missing = await report.list_missing(s, user=user, run_id=run_id)
        overview = await report.get_overview(s, user=user, run_id=run_id)
    assert [(m.ref_index, m.waiting_claims, m.reason) for m in missing] == [
        (4, 1, UnverifiableReason.REFERENCE_NOT_FOUND),
        (5, 1, UnverifiableReason.NO_ACCESSIBLE_TEXT),
    ]
    assert overview.missing_count == len(missing)


@pytest.mark.db
async def test_report_query_count_constant(
    session_factory: SessionFactory, engine: AsyncEngine, clean_db: None, settings: Settings
) -> None:
    statements: list[str] = []

    def count(*args: object) -> None:
        statements.append("x")

    async def measure(s: AsyncSession, user: User, run_id: UUID) -> dict[str, int]:
        _, rows = await report.list_claims(s, user=user, run_id=run_id)
        calls: dict[str, Any] = {
            "overview": lambda: report.get_overview(s, user=user, run_id=run_id),
            "claims": lambda: report.list_claims(s, user=user, run_id=run_id),
            "references": lambda: report.list_references(s, user=user, run_id=run_id),
            "missing": lambda: report.list_missing(s, user=user, run_id=run_id),
            "sheet": lambda: report.get_claim_sheet(
                s, user=user, run_id=run_id, claim_verification_id=rows[0].id
            ),
            "markdown": lambda: report.render_markdown(s, user=user, run_id=run_id),
        }
        result = {}
        for name, call in calls.items():
            statements.clear()
            await call()
            result[name] = len(statements)
        return result

    async with session_factory() as s:
        user, run_id = await _seeded(s, settings)
        await s.execute(delete(ClaimVerification).where(ClaimVerification.run_id == run_id))
        await add_claim_verification(s, run_id, entry_id=None, sentence=UNRESOLVED_SENTENCE, page=1)
        await s.commit()
        event.listen(engine.sync_engine, "before_cursor_execute", count)
        try:
            one = await measure(s, user, run_id)
            for i in range(19):
                await add_claim_verification(
                    s, run_id, entry_id=None, sentence=f"Câu {i} [9].", page=2 + i
                )
            await s.commit()
            twenty = await measure(s, user, run_id)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", count)
        _, rows = await report.list_claims(s, user=user, run_id=run_id)
    assert len(rows) == 20
    assert one == twenty


@pytest.mark.db
async def test_foreign_user_gets_not_found_everywhere(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        _, run_id = await _seeded(s, settings)
        other = await new_user(s, "khac@b.vn")
        for call in (report.list_references, report.list_missing, report.render_markdown):
            with pytest.raises(NotFoundError):
                await call(s, user=other, run_id=run_id)
