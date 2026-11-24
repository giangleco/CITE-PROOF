"""Mặc định dùng chung cho `Settings` và `RunConfig` (spec §6.2, §8.2, §15).

Một nguồn duy nhất: `Settings` lấy mặc định ở đây khi không đặt biến môi trường;
`RunConfig.load` điền khóa thiếu của cấu hình đã lưu cũng từ đây (không từ
`Settings`), nên lần chạy cũ tiếp tục sau lỗi không đổi model. Các bảng là chỉ đọc;
nơi dùng phải sao chép (`dict(...)`) trước khi sửa.
"""

from collections.abc import Mapping
from types import MappingProxyType

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"

# Theo tác vụ (khóa là giá trị `LLMTask`): W1-W4 dùng Haiku, effort thấp; W5, A1, A2 dùng
# Sonnet, effort cao.
LLM_MODELS: Mapping[str, str] = MappingProxyType(
    {
        "reference.parse": HAIKU,
        "claim.extract": HAIKU,
        "claim.translate": HAIKU,
        "claim.decompose": HAIKU,
        "verdict.judge": SONNET,
        "verdict.judge_closed_book": SONNET,
        "agent.reference_resolver": SONNET,
        "agent.evidence_investigator": SONNET,
    }
)
LLM_EFFORT: Mapping[str, str] = MappingProxyType(
    {
        "reference.parse": "low",
        "claim.extract": "low",
        "claim.translate": "low",
        "claim.decompose": "low",
        "verdict.judge": "high",
        "verdict.judge_closed_book": "high",
        "agent.reference_resolver": "high",
        "agent.evidence_investigator": "high",
    }
)
LLM_MAX_TOKENS: Mapping[str, int] = MappingProxyType(
    {
        "reference.parse": 8000,
        "claim.extract": 4000,
        "claim.translate": 2000,
        "claim.decompose": 2000,
        "verdict.judge": 16000,
        "verdict.judge_closed_book": 16000,
        "agent.reference_resolver": 16000,
        "agent.evidence_investigator": 16000,
    }
)
# USD trên 1 triệu token (đầu vào, đầu ra), khóa theo model.
LLM_PRICES: Mapping[str, tuple[float, float]] = MappingProxyType(
    {
        HAIKU: (0.10, 0.50),
        SONNET: (2.0, 10.0),
        OPUS: (4.0, 20.0),
    }
)

# Giới hạn một lần chạy (QĐ-32) và số chunk truy xuất mỗi cặp (`[ĐX]`).
RUN_MAX_CLAIMS = 150
RUN_MAX_COST_USD = 5.0
RETRIEVAL_K = 6

# Số yêu cầu mỗi giây theo nguồn metadata (spec §7.3, `[ĐX]`). `Settings` lấy làm mặc định;
# gateway dùng lại cho nguồn thiếu khi biến môi trường chỉ đặt một phần bảng.
METADATA_RATE_LIMITS: Mapping[str, float] = MappingProxyType(
    {"crossref": 5.0, "openalex": 10.0, "semantic_scholar": 0.3}
)
