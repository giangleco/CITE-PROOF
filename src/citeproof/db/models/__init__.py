"""Các model ORM; import module model tại đây để đăng ký lên Base.metadata."""

from citeproof.db.models.citations import Citation, CitationLink, Claim
from citeproof.db.models.documents import Document
from citeproof.db.models.feedback import Feedback
from citeproof.db.models.references import ReferenceCheck, ReferenceEntry, Work
from citeproof.db.models.runs import ClaimVerification, Evidence, VerificationRun
from citeproof.db.models.sources import SourceChunk, SourceFile
from citeproof.db.models.users import User

__all__ = [
    "Citation",
    "CitationLink",
    "Claim",
    "ClaimVerification",
    "Document",
    "Evidence",
    "Feedback",
    "ReferenceCheck",
    "ReferenceEntry",
    "SourceChunk",
    "SourceFile",
    "User",
    "VerificationRun",
    "Work",
]
