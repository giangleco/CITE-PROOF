# Thiết kế pipeline lõi S1–S9 và agent A1/A2 — CiteProof

| Thuộc tính | Giá trị |
|---|---|
| Ngày | 24/11/2026 |
| Trạng thái | Chờ duyệt |
| Nhánh | `feat/core-pipeline` |
| Task | T-201 (phần còn lại), T-203…T-209, T-301…T-304, T-401…T-406, T-408, T-501…T-506, T-601/T-602 (dữ liệu thật), T-606, T-608, T-704 |
| ADR đi kèm | ADR-004 (GROBID), ADR-006 (lớp LLM), ADR-007 (embedding); ADR-010 chuyển Accepted ở task AgentRunner |
| Nguồn yêu cầu | `04` §4.3–§4.8, `07` §4.4–§4.10, `07a` (toàn bộ), `08` §4.2–§4.5, `09` §4.4–§4.6, `10` §4.2, `12` §4.6–§4.8, `13` §A, §B, `14` |
| Kế hoạch | `docs/superpowers/plans/2026-11-24-core-pipeline.md` |

## 1. Mục đích

M1 đã có hàng đợi job, `LLMClient`, `http_safe`; web UI v1 đã đọc các bảng kết quả. Các giai đoạn S1–S9 vẫn là no-op. Tài liệu này thiết kế **toàn bộ phần mềm còn lại của MVP** trong một lượt (theo yêu cầu của người dùng; tối ưu sau): parse, tra cứu tài liệu tham khảo, trích dẫn và nhận định, lấy và lập chỉ mục nguồn, truy xuất, phán quyết, kiểm chứng lại, tải nguồn, harness đánh giá, triển khai pilot, giới hạn tần suất, và hai agent A1, A2.

Nguyên tắc giữ nguyên từ `07a`: pipeline tất định là xương sống; **LLM đề xuất, code quyết định**; mọi trạng thái cuối do quy tắc tất định gán; hệ thống vẫn chạy hết khi không có khóa LLM.

## 2. Phạm vi

**Trong phạm vi:** T-209 gia hạn lease; S1 (PDF, DOCX, văn bản; ngôn ngữ, bảng mã cũ, trang scan, giới hạn số trang); S2 (tách và parse danh mục, W1); S3 (Crossref, OpenAlex, Semantic Scholar; cache, giới hạn tốc độ, ngắt mạch; so khớp; `LOOKUP_FAILED`; kiểm tra URL; cảnh báo ngoài lĩnh vực); S4 (marker, liên kết, claim, W2); S5 (bản mở); S6 (chia đoạn, chỉ mục); S7 (truy xuất, W3); S8 (W5, validator, quy tắc trạng thái, V0–V3); S9; tải nguồn trong tab "Thiếu toàn văn" (FR-32/33); kiểm chứng lại một phần (FR-34) và tra lại `LOOKUP_FAILED`; harness đánh giá trên `citeproof_eval` với bộ dữ liệu tổng hợp nhỏ; cấu hình triển khai pilot (TLS, sao lưu); giới hạn số yêu cầu mỗi phút; `AgentRunner`, A1, A2 (V4, `V2+A2`).

**Ngoài phạm vi:** việc nghiên cứu và con người (T-003, gán nhãn T-E*, spike, pilot, chạy thực nghiệm M7, bài báo); embedding (ADR-007); backend lưu trữ S3 (QĐ-29); xuất PDF; OCR trang scan; chuyển đổi bảng mã TCVN3/VNI sang Unicode.

## 3. Quyết định

### 3.1 Đã chốt trước (người dùng)

| ID | Quyết định | Lý do một dòng |
|---|---|---|
| QĐ-01 | LLM là Anthropic Claude qua SDK `anthropic` chính thức, cài thành provider sau `LLMClient` (giữ cache, ghi `llm_calls`, phát lại). Model theo tác vụ, cấu hình được: W1–W4 `claude-haiku-5-5`, W5/A1/A2 `claude-sonnet-5-5`. Khóa `CITEPROOF_ANTHROPIC_API_KEY`. Không có khóa: W1, W2 bỏ qua; S8 ghi `UNVERIFIABLE`/`PROCESSING_ERROR`; lần chạy có cảnh báo `LLM_NOT_CONFIGURED` và vẫn hoàn tất. Test chỉ dùng provider kịch bản | Giữ một chỗ cho cache và chi phí; hệ thống dùng được khi chưa có ngân sách |
| QĐ-02 | Truy xuất bằng full-text PostgreSQL (`source_chunks.tsv`, `ts_rank_cd`) trong một nguồn; không embedding ở v1; giữ protocol `Embedder` + `NoEmbedder`. X2 qua W3 khi có LLM, ngược lại X1 | Bớt một model phải vận hành; tất định; chờ SPK-04 |
| QĐ-03 | PDF: GROBID `processFulltextDocument` (TEI có tọa độ) là chính; `pypdfium2` dự phòng (không dùng PyMuPDF vì AGPL). DOCX bằng `python-docx`. XML parse bằng `defusedxml`. Văn bản đã parse lưu JSON (`documents.parsed_key`) có đoạn, mục, trang, vị trí ký tự | Giấy phép và vị trí cho S4 |
| QĐ-04 | Chuỗi reference thô qua GROBID `processCitationList`; dự phòng rule (DOI/URL/ISBN/năm, tiêu đề trong ngoặc kép, khối tác giả); W1 chỉ khi có LLM. Loại tài liệu phân bằng rule (+W1) | Rẻ, tất định trước |
| QĐ-05 | Ngôn ngữ bằng heuristic không phụ thuộc thư viện (tỷ lệ dấu tiếng Việt); phát hiện TCVN3/VNI và trang scan → `parse_warnings` theo DESIGN.md §8.6; `OUT_OF_DOMAIN` khi có heuristic rẻ; giới hạn `max_pages` ở S1 là `InputError` | Không thêm phụ thuộc nặng |
| QĐ-06 | Connector Crossref, OpenAlex, Semantic Scholar (httpx, timeout, User-Agent chung không tên người dùng, `CITEPROOF_CONTACT_EMAIL` tùy chọn cho polite pool); `metadata_cache` có TTL; giới hạn tốc độ và ngắt mạch theo nguồn; so khớp tất định bằng `rapidfuzz`; trạng thái theo `04` §4.5.2; `works` upsert (dòng `sample` được ghi đè); test bằng JSON đã ghi | Trung thực về "chưa tìm thấy" (FR-14) |
| QĐ-07 | Bản mở: OpenAlex `best_oa_location`, arXiv, ACL Anthology; tải chỉ qua `http_safe`; lưu thành `source_files` `OPEN_ACCESS` dùng chung; Unpaywall chỉ khi có email liên hệ | SSRF (SR-11), điều khoản Unpaywall |
| QĐ-08 | Marker theo rule: số (`[1]`, `[1–3]`, `[1, 2]`) và tác giả–năm, gồm tên Việt (họ trước); tách câu theo rule vi/en; claim tất định, W2 tinh chỉnh khi có LLM; span luôn là chuỗi con của câu | `07a` TK-14 |
| QĐ-09 | W5 với Verdict v2, validator tất định (quote nguyên văn, bằng chứng tối thiểu `08` §4.4, quy tắc abstract `04` §4.5.1, cờ `04` §4.5.4, hạ mức `07` §4.6); biến thể theo `13` | Kiểm soát hallucination |
| QĐ-10 | `pipeline/demo.py` giữ lại cho local, thành lớp bọc quanh stage thật (chờ, giả lập lỗi khi config có `demo`) | Thử được tiến độ trên stage thật |
| QĐ-11 | Bộ golden (T-208) sinh một lần bằng script dev có commit, rồi commit thành fixture; gồm tiếng Việt, nhiều kiểu trích dẫn (cả kiểu khóa luận Việt Nam) và một mẫu TCVN3; test cần GROBID dùng TEI đã ghi, chạy thật qua marker `grobid`; ngưỡng G-5 vẫn `[ĐX]` | CI không cần GROBID |
| QĐ-12 | Giữ quy ước: enum TEXT + CHECK (`enum_check`), Alembic 0007+, mypy strict, ruff, không log nội dung, báo cáo dùng số truy vấn cố định, nhãn tiếng Việt chỉ từ `web/labels.py`, lỗi lưu qua `describe_error` | Nhất quán với M1 và web UI |

### 3.2 Do người viết spec quyết định

| ID | Quyết định | Lý do một dòng |
|---|---|---|
| QĐ-13 | Kết quả cấp **tài liệu** (JSON đã parse, `reference_entries`, `citations`, `citation_links`, `claims`) tạo một lần; lần chạy sau dùng lại, S1/S2/S4 bỏ qua khi đã có | Tạo lại `reference_entries` sẽ xóa cascade kết quả của các lần chạy cũ; parse tất định và có cache |
| QĐ-14 | `claim_verifications.reference_entry_id` cho phép NULL chỉ khi lý do là `REFERENCE_UNRESOLVED`; khóa duy nhất đổi thành (`run_id`, `claim_id`, `reference_entry_id`, `verifier`) `NULLS NOT DISTINCT` | FR-40 đánh giá từng cặp claim–tài liệu; marker không liên kết được vẫn phải có dòng |
| QĐ-15 | Thêm `claims.is_claim` (mặc định `true`) | W2 trả `is_claim=false` → `NOT_A_CLAIM` |
| QĐ-16 | Span tất định: đoạn liền mạch của câu từ cuối marker trước (hoặc đầu câu) đến đầu marker hiện tại, bỏ dấu câu và khoảng trắng hai đầu; dưới 3 từ thì lấy cả câu bỏ marker ở đầu và cuối. Marker nằm giữa đoạn được giữ nguyên | Giải mâu thuẫn QĐ-01 ("câu bỏ marker") với QĐ-08 (chuỗi con) |
| QĐ-17 | Abstract là một `source_file` `OPEN_ACCESS`, `content_level = ABSTRACT`, văn bản lưu trong storage | `evidence.source_chunk_id` bắt buộc |
| QĐ-18 | Enum `AccessStatus` = `OPEN_FULL_TEXT`, `OPEN_ABSTRACT`, `USER_PROVIDED` (khớp bài mẫu), thêm CHECK | v1.0 §9.3 chưa có trong repo; ánh xạ lại khi có |
| QĐ-19 | `WEB`, `THESIS`, `OTHER` không có DOI → `UNSUPPORTED_TYPE` không tra cứu (WEB vẫn kiểm tra URL); có DOI thì luôn tra | Độ phủ kém, "chưa tìm thấy" sẽ gây hiểu lầm |
| QĐ-20 | v1 không báo lệch `venue` | Tên viết tắt (NeurIPS…) gây báo lệch sai |
| QĐ-21 | Lease dùng lại `job_lock_timeout_seconds` làm độ dài lease (mặc định 120 s), heartbeat 30 s; stage nhận `StageContext`; mọi ghi theo mục đi qua `fenced_session` (khóa `FOR KEY SHARE` dòng job theo `locked_at`) | Rào ghi kết quả bằng token sẵn có |
| QĐ-22 | GROBID không truy cập được → `RetryableError`; ở lần thử cuối dùng `pypdfium2` và cảnh báo `PDF_TEXT_FALLBACK`. GROBID trả lỗi xử lý tài liệu → dự phòng ngay | Kết quả cấp tài liệu bất biến nên tránh parse kém vì sự cố tạm thời |
| QĐ-23 | S4 phát hiện marker bằng rule cho mọi loại đầu vào, không dùng `<ref>` của GROBID | Một đường mã, test được không cần GROBID |
| QĐ-24 | Truy vấn lexical: token của claim bỏ stopword vi/en, tối đa 32 từ, nối OR (`to_tsquery('simple', …)`); gộp các biến thể truy vấn bằng RRF (k=60); nguồn toàn văn mà không có chunk nào khớp → `UNVERIFIABLE`/`EVIDENCE_INCONCLUSIVE` không gọi LLM; mặc định `cross_lingual = X2` | Không có embedding thì claim tiếng Việt cần bản dịch để tìm trên nguồn tiếng Anh |
| QĐ-25 | Bản dịch W3 không có cột riêng; lấy lại từ `llm_cache` (namespace tài liệu) khi cần | Đã lưu để kiểm tra lại, xóa theo tài liệu |
| QĐ-26 | V0 (chỉ admin và đánh giá) được miễn bằng chứng tối thiểu: giữ trạng thái LLM, `content_level_used = NONE`, luôn gắn cờ | V0 đo mức "đoán"; áp quy tắc thì V0 luôn ra `UNVERIFIABLE` |
| QĐ-27 | Hạ mức do kiểm tra bằng chứng → `UNVERIFIABLE`/`EVIDENCE_INCONCLUSIVE`, gắn cờ; hạ mức do quy tắc abstract → `ABSTRACT_ONLY_INSUFFICIENT`, không gắn cờ | `04` §4.5.4 gắn cờ cho cơ chế 2 của `07` §4.6, không cho cơ chế 3 |
| QĐ-28 | Báo cáo chỉ hiện dòng có `verifier` bằng biến thể cuối của lần chạy; `V2+A2` ghi dòng `V2` (trung gian) và dòng `V2+A2` (cuối) cho mọi cặp | Một kết quả mỗi cặp trên giao diện; giữ V2 để so sánh |
| QĐ-29 | Pilot dùng lưu trữ local trên volume Docker, sao lưu hằng ngày cùng DB; backend S3 hoãn | Một máy chủ (`14` §4.2 phương án A); ít dịch vụ |
| QĐ-30 | Kiểm chứng lại một phần: job của giai đoạn bỏ qua ghi `DONE` với `payload {"skipped": true}`; S9 chép kết quả ngoài phạm vi từ lần chạy cha; không tính vào hạn mức; tải nguồn và kiểm chứng lại là hai bước | Báo cáo của lần chạy con đầy đủ; tải nhiều tệp rồi chạy một lần |
| QĐ-31 | Tab "Thiếu toàn văn" liệt kê cả tài liệu có claim `REFERENCE_NOT_FOUND` | Luận văn trong nước chưa tìm thấy metadata nhưng người dùng có PDF |
| QĐ-32 | Vượt `max_claims` hoặc `max_cost_usd` → các cặp còn lại `UNVERIFIABLE`/`PROCESSING_ERROR`, cảnh báo `CLAIM_LIMIT_REACHED`/`COST_LIMIT_REACHED`; mặc định 150 cặp, 5 USD `[ĐX]` | Theo `07a` §4.3.5; con số chờ Q-01 |
| QĐ-33 | Prompt nằm trong gói: `src/citeproof/prompts/<task>/<version>.md`, mô tả công cụ `src/citeproof/prompts/tools/<catalog>/<version>.yaml` | Image Docker và gói cài đặt tự mang prompt |
| QĐ-34 | Gói mới `citeproof.parsing` (mô hình `ParsedDocument`, parser text/DOCX/PDF/TEI, heuristic ngôn ngữ) dùng chung cho S1 và S6 | Giữ quy tắc phụ thuộc module của `07` §4.4 |
| QĐ-35 | Câu giải thích cho kết quả tất định nằm ở `modules/verification/explanations.py` (vi và en); giải thích theo tiếng Anh khi bài là `en`, còn lại tiếng Việt | Đây là dữ liệu lưu, không phải nhãn giao diện; FR-45 |
| QĐ-36 | Gọi Sonnet ở worker dùng fallback phía server khi model từ chối (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`), bật mặc định, tắt được; ghi model thực sự trả lời vào `llm_calls.model` và `claim_verifications.model`. Agent không dùng fallback | Hướng dẫn SDK; giữ truy vết model |
| QĐ-37 | `effort` mặc định: W1–W4 `low`, W5/A1/A2 `high`; không gửi `temperature`, `top_p`, `top_k`, không ép `tool_choice`; không đặt `thinking` (adaptive mặc định trên Haiku 5.5, Sonnet 5.5) | Quy tắc `12` §4.8 mục 8 và API hiện hành |
| QĐ-38 | PDF golden sinh bằng `reportlab` với font DejaVuSans của hệ thống; DOCX bằng `python-docx` | Có tiếng Việt, giấy phép tự do; script chỉ chạy khi tạo lại fixture |
| QĐ-39 | Giới hạn tần suất trong tiến trình, cửa sổ 60 giây theo IP (sau `ProxyHeadersMiddleware`) | Pilot chạy một tiến trình app; không thêm Redis (ADR-003) |
| QĐ-40 | A1 không tìm trên arXiv; `meta_search` dùng ba nguồn của S3 | arXiv đã có trong OpenAlex, Semantic Scholar |
| QĐ-41 | `OUT_OF_DOMAIN`: ≥5 tài liệu đã khớp có lĩnh vực OpenAlex mà tỷ lệ "Computer Science" < 25% → `out_of_domain`; đủ dữ liệu mà ≥25% → `in_scope`; còn lại `unknown`. Thêm cột `works.field` | Heuristic rẻ đúng `07a` §4.2 |
| QĐ-42 | Tra bản lưu trữ archive.org (FR-15) tắt mặc định (`web_archive_lookup`) | Màn hình đồng ý chưa nêu archive.org |
| QĐ-43 | Màn hình đồng ý nêu thêm nguồn tải bản mở (OpenAlex, Unpaywall, arXiv, ACL Anthology, trang nhà xuất bản); tăng `consent_text_updated_at` | FR-63 |
| QĐ-44 | Đánh giá: nguồn trong DB `citeproof_eval` là `OPEN_ACCESS`; dòng JSONL có thêm trường tùy chọn `source_path` hoặc `source_text` | Không cần mạng khi đã có nguồn |
| QĐ-45 | `agent_runs` được ghi ngay khi agent bắt đầu với `status = ERROR`, cập nhật khi xong | `llm_calls.agent_run_id` cần dòng có trước; tiến trình chết để lại trạng thái đúng |
| QĐ-46 | Cấu hình lần chạy (`RunConfig`) được "đóng băng" khi tạo: model, effort, phiên bản prompt và bộ công cụ ghi vào `verification_runs.config` | FR-81, tiếp tục sau lỗi không đổi model |
| QĐ-47 | A1, A2 tắt mặc định; admin bật qua cấu hình lần chạy hoặc cấu hình đánh giá | TK-03, T-407 chưa có dữ liệu |
| QĐ-48 | Thêm V3 (W4 + quy tắc gộp `07a` §4.4.3) | `13` §B.6 đặt V3 trong so sánh chính; `07` §4.7 và `07a` §4.4.3 định nghĩa cụ thể |

## 4. Mâu thuẫn tìm thấy và cách xử lý

| Chỗ | Mâu thuẫn | Xử lý |
|---|---|---|
| `07` §4.2, `07a` §4.2, T-203 | Ghi "PyMuPDF dự phòng" | QĐ-03: `pypdfium2`; ADR-004 ghi lại; plan cập nhật `07`, task-tracking |
| `07` §4.5 S6–S7, `07a` TK-19, `13` §B.6 | Hybrid BM25 + vector, RRF | QĐ-02: chỉ lexical ở v1, `score_vector = NULL`; ablation "chỉ vector/hybrid" chưa chạy được đến khi xét lại ADR-007 |
| QĐ-01 với QĐ-08, `08` `claims` | "Claim = câu bỏ marker" với "span là chuỗi con" | QĐ-16 |
| `08` §4.4, FR-41 với V0 | V0 không có bằng chứng | QĐ-26 |
| `08` `claim_verifications` | `reference_entry_id` NOT NULL và khóa (`run_id`, `claim_id`, `verifier`) không chứa được `REFERENCE_UNRESOLVED` và claim trích nhiều nguồn | QĐ-14 (migration 0009) |
| `14` §4.1 | Pilot dùng storage S3-compatible | QĐ-29; cần người dùng xác nhận |
| `07a` §4.6.2 `meta_search` | Liệt kê arXiv | QĐ-40; mô tả công cụ v1 bỏ arXiv |
| `12` §4.1 | `prompts/` ở gốc repo; `tests/unit`, `tests/integration` | QĐ-33; test giữ cấu trúc hiện có `tests/<module>/` |
| `07a` §4.4.1 | W2 hạng "vừa", W5 hạng "mạnh" | QĐ-01 của người dùng là quyết định cuối: W2 dùng Haiku, W5 dùng Sonnet |
| `07a` §4.11 | Ví dụ config bật A1 | QĐ-47 |

## 5. Cấu trúc mã

```
src/citeproof/
  core/domain.py                 ParsedReference và model miền dùng chung
  parsing/                       model.py (ParsedDocument), text.py, docx.py, pdf.py, tei.py,
                                 language.py, encoding.py
  integrations/grobid.py         GrobidClient
  integrations/metadata/         models.py (WorkRecord), base.py (MetadataConnector), crossref.py,
                                 openalex.py, semantic_scholar.py, ratelimit.py, breaker.py,
                                 cache.py (MetadataCache), gateway.py (MetadataGateway),
                                 abstracts.py (bỏ thẻ JATS, dựng abstract OpenAlex; connector cần)
  integrations/oa/               AccessOption, arxiv.py, acl.py, openalex_oa.py, unpaywall.py
  llm/providers/anthropic.py     AnthropicProvider
  llm/schema.py                  strict_output_schema
  llm/factory.py                 make_llm_client
  llm/tools.py, llm/agent_runner.py
  modules/documents/stage.py     S1
  modules/references/            normalize.py, sections.py, rules.py, classify.py, llm_parse.py (W1),
                                 stage_extract.py (S2), matcher.py, cascade.py, works.py,
                                 web_check.py, domain.py, stage_check.py (S3), tools.py, resolver_agent.py (A1)
  modules/citations/             sentences.py, markers.py, linking.py, claims.py, llm_extract.py (W2), stage.py (S4)
  modules/sources/               fetch.py, stage_fetch.py (S5), chunking.py,
                                 parse_source.py, stage_index.py (S6), title_match.py
  modules/retrieval/             embedding.py, lexical.py, fusion.py, translate.py (W3), pairs.py,
                                 retriever.py, stage.py (S7), tools.py (công cụ A2)
  modules/verification/          verdict.py, quotes.py, rules.py, explanations.py, context.py, judge.py,
                                 precheck.py, decompose.py (W4), aggregation.py,
                                 verifiers/{base,v0,v1,v2,v3,v4_agentic,escalation}.py, stage.py (S8)
  modules/reports/stage.py       S9
  pipeline/                      config.py (RunConfig, RunInfo), deps.py, registry.py, demo.py (lớp bọc)
  services/sources.py, services/reverify.py
  evaluation/                    dataset.py, importer.py, runner.py, metrics.py, __main__.py
  prompts/                       <task>/v1.md, tools/{meta,source}/v1.yaml
  web/ratelimit.py
```

Phụ thuộc giữa module theo `07` §4.4: `references`, `sources` dùng `integrations`; `verification` dùng `retrieval`; chỉ `pipeline` ghép mọi thứ; `services` chỉ gọi `pipeline.runs` và các hàm công khai của module.

## 6. Khung pipeline

### 6.1 Lease (T-209)

- Cột mới trong `jobs`: `heartbeat_at`, `lease_expires_at`, `started_at`, `finished_at`. `claim_next` đặt `locked_at = now()`, `lease_expires_at = now() + lease`, `started_at = now()`. Job bị coi là hết hạn khi `lease_expires_at < now()`. `locked_at` vẫn là token fencing.
- Worker chạy stage trong một task và một vòng heartbeat song song (`renew_lease` mỗi `job_heartbeat_seconds`). Gia hạn thất bại (lease đã mất) → hủy task stage, log `stage_lease_lost`, không ghi kết quả.
- `PipelineStage.run(ctx: StageContext)`; `StageContext` gồm `run_id`, `lease` (`ClaimedJob`), `max_attempts`, `attempt`, `is_last_attempt`.
- Mọi ghi theo mục dùng `fenced_session(session_factory, lease)`: mở giao dịch, `SELECT … FOR KEY SHARE` dòng job với điều kiện `status = RUNNING AND locked_at = lease.locked_at`, không thấy thì ném `LeaseLostError`. `FOR KEY SHARE` (không phải `FOR SHARE`) vì vẫn xung đột với `FOR UPDATE` của claim và đánh hết hạn nhưng không chặn UPDATE heartbeat của chính job. Giao dịch rào phải ngắn: không gọi mạng, GROBID hay LLM bên trong; việc chặn luồng hoặc nặng CPU chạy qua `asyncio.to_thread`. `renew_lease` tính hạn mới từ `clock_timestamp()`.
- `mark_done`/`mark_failed` ghi `finished_at`; trang "Chi tiết kỹ thuật" hiện thời lượng từng bước.

### 6.2 Cấu hình lần chạy

`RunConfig` (Pydantic) đọc từ `verification_runs.config`, thiếu khóa thì lấy mặc định (lần chạy cũ chỉ có `{"variant": "V2"}`):

```yaml
variant: V2                  # V0 | V1 | V2 | V3 | V4 | V2+A2
cross_lingual: X2            # X1 | X2
retrieval_k: 6
agents:
  reference_resolver:    {enabled: false, max_steps: 6, max_context_tokens: 10000, max_total_input_tokens: 40000, max_wall_seconds: 120}
  evidence_investigator: {enabled: false, max_steps: 8, max_context_tokens: 20000, max_total_input_tokens: 90000, max_wall_seconds: 180}
models / effort / max_tokens / prompts: {<task>: …}   # đóng băng khi tạo lần chạy (QĐ-46)
tool_catalogs: {meta: v1, source: v1}
limits: {max_claims: 150, max_cost_usd: 5.0}
demo: null                   # chỉ local
scope: null                  # lần chạy con: {kind: upload|lookup_retry, reference_entry_ids: [...], from_stage: S6|S3}
```

`RunConfig.task(name) -> TaskSpec(model, effort, max_tokens, prompt_version)`. `RunInfo` gồm `run_id`, `document_id`, `user_id`, `parent_run_id`, `config`, `document_language` và `in_scope(reference_entry_id)`.

### 6.3 Phụ thuộc và registry

`PipelineDeps`: `session_factory`, `storage`, `settings`, `llm: LLMClient | None`, `prompts: PromptLoader`, `http: httpx.AsyncClient` (chỉ cho host API cố định), `safe_http: SafeHttpClient` (mọi URL từ người dùng hoặc metadata), `grobid: GrobidClient | None`, `metadata: Callable[[], MetadataGateway]` (một gateway mới mỗi lần stage chạy, để bộ ngắt mạch theo lần chạy), `embedder: Embedder`, `agents: AgentRunner | None`. `open_deps(settings, session_factory)` là async context manager đóng client khi xong. `build_registry(deps)` trả stage thật; ở `env=local` bọc từng stage bằng `DemoStage` (QĐ-10). `python -m citeproof.pipeline` dùng `build_registry` ở mọi môi trường.

`complete_stage` bỏ cảnh báo trùng khi gộp vào `verification_runs.warnings`.

### 6.4 Đơn vị công việc và tiếp tục sau lỗi

| Giai đoạn | Đơn vị | Dấu "đã xong" | Ghi |
|---|---|---|---|
| S1 | tài liệu | `documents.parsed_key` khác NULL | một giao dịch |
| S2 | tài liệu | có `reference_entries` | một giao dịch cho mọi mục |
| S3 | reference entry | có `reference_checks` (run, entry) | từng mục |
| S4 | tài liệu | có `citations` | một giao dịch |
| S5 | work | có `source_files` FULL_TEXT/ABSTRACT cho work | từng work |
| S6 | source file | có `source_chunks` | từng file |
| S7 | cặp claim–nguồn | có `retrieval_results` (run, claim, source) | từng cặp |
| S8 | cặp claim–reference | có `claim_verifications` (run, claim, ref, verifier) | từng cặp |
| S9 | lần chạy | — | một giao dịch |

Lỗi một mục: ghi trạng thái lỗi của mục (`LOOKUP_FAILED`, `PROCESSING_ERROR`) rồi tiếp tục. Lỗi cấp giai đoạn (DB, storage, GROBID hết lượt) làm stage lỗi; `RetryableError` được job runner thử lại với backoff; `InputError` dừng lần chạy với thông điệp tiếng Việt (không chứa nội dung tài liệu).

## 7. Từng giai đoạn

### 7.1 S1 — Nhận và parse (T-201, T-203–T-206)

- **Vào:** `documents` (kind, storage_key). **Ra:** `documents/<uid>/<docid>/parsed.json` (`ParsedDocument`), `documents.parsed_key`, `language`, `page_count`, `parse_warnings`.
- **TEXT:** JSON `{"body", "references"}`; đoạn văn tách theo dòng trống; danh mục tách theo dòng thành `references_raw`.
- **DOCX:** từ chối khi có `word/vbaProject.bin` (`DOCX_MACRO_NOT_ALLOWED`) hoặc tổng dung lượng giải nén > 200 MB (`FILE_TOO_LARGE`); tiêu đề mục theo style `Heading*`/`Title`; không có số trang.
- **PDF:** đếm trang bằng `pypdfium2`; > `max_pages` → `InputError` `PAGE_LIMIT_EXCEEDED`; PDF mã hóa → `PDF_ENCRYPTED`. GROBID `processFulltextDocument` với `segmentSentences=1`, `teiCoordinates=p,s,head,ref,biblStruct,figure`, `includeRawCitations=1`, `consolidateHeader=0`, `consolidateCitations=0`. TEI → đoạn (mục từ `<head>`, trang từ tọa độ đầu tiên), chú thích hình/bảng (`region` FIGURE/TABLE), abstract (mục "Abstract"), `listBibl` → `references_raw` có trường đã parse. QĐ-22 cho dự phòng.
- **Cảnh báo:** trang có < 25 ký tự văn bản → `{"code": "SCANNED_PAGES", "pages": [...]}` (số trang từ 1); `{"code": "LEGACY_ENCODING", "encoding": "TCVN3"|"VNI"}`; `{"code": "PDF_TEXT_FALLBACK"}`. Không trích được chữ nào → `InputError` `NO_TEXT_EXTRACTED`.
- **Ngôn ngữ:** theo đoạn; `vi` khi tỷ lệ chữ có dấu tiếng Việt ≥ 8%, `en` khi < 1% và có từ chức năng tiếng Anh; tài liệu `mixed` khi mỗi ngôn ngữ chiếm ≥ 20% số ký tự; còn lại `unknown`.
- **Không LLM.**

### 7.2 S2 — Tách và parse tài liệu tham khảo (T-207)

- **Vào:** `ParsedDocument`. **Ra:** `reference_entries` (`raw_text` nguyên văn, `parsed`, `ref_type`, `parse_method`, `doi_normalized`, `order_index`).
- Nguồn chuỗi thô: `references_raw` (TEXT, GROBID) hoặc khối sau tiêu đề cuối cùng khớp `tài liệu tham khảo|danh mục tài liệu tham khảo|references|bibliography|works cited` (không phân biệt dấu, hoa thường) tới tiêu đề "Phụ lục/Appendix". Tách mục theo nhãn `[n]`/`n.`; dòng không có nhãn nối vào mục trước khi các mục khác có nhãn.
- Parse: kết quả có cấu trúc của GROBID → `processCitationList` cho chuỗi chưa có cấu trúc → rule → W1 (lô ≤10, chỉ cho mục chưa có tiêu đề và DOI). Validator W1: trường phải xuất hiện trong chuỗi gốc (tiêu đề `partial_ratio` ≥ 90 sau chuẩn hóa, năm và DOI là chuỗi con, họ tác giả có mặt), trường không đạt bị bỏ.
- `parsed`: `title`, `authors` (chuỗi như trong bài), `year`, `venue`, `doi`, `isbn`, `url`, `label`. DOI chuẩn hóa: chữ thường, bỏ `https://doi.org/`, `doi:`, dấu câu cuối; phải khớp `^10\.\d{4,9}/\S+$`.
- Loại tài liệu bằng rule (arXiv → PREPRINT; Proceedings/Hội nghị/Workshop → CONFERENCE; Luận văn/Luận án/Khóa luận/thesis → THESIS; chỉ có URL + "truy cập/accessed" → WEB; ISBN/NXB/Press → BOOK; có tập/số trang → ARTICLE; còn lại UNKNOWN); W1 ghi đè khi được gọi.
- Không có danh mục → `{"code": "NO_REFERENCE_SECTION"}`, S2 vẫn xong (S3, S4 không có việc).

### 7.3 S3 — Kiểm tra tài liệu tham khảo (T-301–T-304)

- **Vào:** `reference_entries` (lọc theo `scope`). **Ra:** `reference_checks`, `works`, `documents.domain_flag` (+ cảnh báo `OUT_OF_DOMAIN`).
- Thứ tự trạng thái: không có tiêu đề và DOI → `PARSE_FAILED`; `WEB`/`THESIS`/`OTHER` không DOI → `UNSUPPORTED_TYPE` (WEB: `url_alive` qua `http_safe`); có DOI → Crossref theo DOI, rồi OpenAlex theo DOI; không DOI hoặc không thấy → tìm Crossref (bibliographic), OpenAlex, Semantic Scholar theo thứ tự, dừng ở nguồn đầu tiên có ứng viên được chấp nhận.
- **So khớp** (`rapidfuzz` trên `title_norm`): chấp nhận khi DOI trùng, hoặc `token_set_ratio` ≥ 90, hoặc ≥ 75 cùng họ tác giả đầu và năm lệch ≤ 1. Họ tác giả: phần trước dấu phẩy; nếu không, từ đầu khi là họ Việt phổ biến (bỏ dấu), ngược lại từ cuối. Lệch: `title` (< 95), `year`, `authors` (họ tác giả đầu khác), `doi` (bài có DOI khác). Hai ứng viên đầu chênh < 5 điểm → `needs_user_review`. `match_score` ∈ [0, 1].
- `mismatches` là danh sách `{"field", "in_document", "found"}` (`08` §4.4a). `sources_searched` chỉ chứa nguồn tra thành công; `sources_failed` gồm nguồn lỗi hoặc bị ngắt mạch. Không nguồn nào thành công → `LOOKUP_FAILED`, không bao giờ `NOT_FOUND_IN_SEARCHED_SOURCES` với danh sách rỗng.
- **Hạ tầng:** `MetadataCache` khóa `sha256` của JSON chuẩn hóa `{"op", "args"}`, không lưu chuỗi truy vấn; TTL 30 ngày (`ok`), 7 ngày (`not_found`); lỗi không cache; DB đánh giá không TTL. Giới hạn tốc độ theo nguồn (crossref 5/s, openalex 10/s, semantic_scholar 0,3/s, mặc định `[ĐX]`); mỗi lời gọi thử lại tối đa 2 lần khi 429/5xx (theo `Retry-After` ≤ 10 s); ngắt mạch sau 3 lỗi liên tiếp trong một lần chạy. User-Agent `CiteProof/0.1 (+citation checker)`, thêm `mailto:<contact_email>` khi cấu hình; OpenAlex thêm tham số `mailto`.
- `works` upsert theo DOI → OpenAlex ID → S2 ID → arXiv ID (không phiên bản); dòng `metadata_source = "sample"` bị ghi đè; dòng thật được làm mới khi `retrieved_at` cũ hơn TTL. Lưu `abstract`, `field` (lĩnh vực OpenAlex).
- Đồng thời `reference_concurrency = 4`; mỗi mục commit riêng nên kết quả hiện sớm (NFR-03).
- DOI trùng nhưng bài có tiêu đề với điểm tiêu đề (đã gấp dấu) < 60: vẫn liên kết work của DOI, trạng thái `METADATA_MISMATCH` với trường lệch `title` và `needs_user_review`.
- Tiêu đề ngắn hoặc tiêu đề con: khi tiêu đề của bài hay của ứng viên dưới 4 từ sau gấp dấu, hoặc ứng viên có ít hơn 60% số từ của bài, chấp nhận qua điểm tiêu đề (không có DOI trùng) còn cần năm trùng hoặc cùng họ tác giả đầu; không đạt thì ứng viên không được chấp nhận (chuỗi tra tiếp tục). Ứng viên được chấp nhận xếp theo ít trường lệch hơn rồi điểm.
- Chi tiết khi xây dựng (Task 9): bài không có tiêu đề thì không tìm kiếm (truy vấn rỗng không gọi API nên không được tính là đã tra); nguồn có ít nhất một lần tra thành công trong mục nằm trong `sources_searched` (nguồn của bản ghi được chấp nhận luôn có mặt), nguồn có một lần lỗi nằm trong `sources_failed` (một nguồn có thể ở cả hai); ứng viên thứ hai chỉ làm "sát điểm" khi cũng được chấp nhận và không lệch nhiều trường hơn ứng viên đầu (bản preprint lệch năm không làm bản chính thức thành mơ hồ); họ tác giả của bản ghi so cả họ đoán lại từ tên đầy đủ khi phần tên (`given`) có từ hai từ trở lên (OpenAlex/Semantic Scholar đoán họ là từ cuối của tên hiển thị: "Nguyễn Văn An" đoán lại thành "Nguyễn", "Le Song" thì không); lỗi ở bước leo thang giữ kết quả chuỗi tra cứu và gắn cờ; lần chạy con (có `scope`) không đổi `domain_flag`.

### 7.4 S4 — Trích dẫn, liên kết, claim (T-401, T-402)

- **Vào:** `ParsedDocument`, `reference_entries`. **Ra:** `citations` (marker, câu, trang, mục, vị trí trong văn bản đã parse), `citation_links`, `claims`.
- Bỏ đoạn thuộc khối danh mục (cùng hàm của S2), tiêu đề và chú thích. Tách câu theo `[.!?…]` + khoảng trắng + chữ hoa/số/ngoặc, trừ viết tắt (`et al.`, `e.g.`, `i.e.`, `Fig.`, `Eq.`, `vs.`, `cs.`, `TS.`, `ThS.`, `PGS.`, `GS.`, `tr.`, `pp.`, `Vol.`, `No.`, `v.v.`) và số thập phân.
- Marker số `[…]` chỉ chứa số, dấu phẩy, chấm phẩy, gạch nối; mở rộng khoảng (tối đa 50 số). Được kèm vị trí trang sau số tài liệu (phán quyết của người điều phối, Task 10): `[1, tr. 25]`, `[1, trang 25]`, `[1, tr.25-30]`, `[1, p. 25]`, `[1, pp. 25–30]`, `[1: 25]`, `[1, tr. 25; 3, tr. 7]`; chỉ số tài liệu được liên kết (số trang thì không), `marker_text` giữ cả ngoặc; trang không có số tài liệu (`[tr. 25]`) không phải marker. Tinh chỉnh của người xây dựng, đã được người điều phối xác nhận: vị trí trang (`tr.`, `trang`, `p.`, `pp.`) chỉ hợp lệ ngay sau một số tài liệu đơn (không sau khoảng, không hai vị trí trang liền nhau như `[1, tr. 25, tr. 30]`); trong một nhóm, số trơn sau vị trí trang là trang (`[1, tr. 25, 30]` → 1), còn số đứng ngay trước một vị trí trang là số tài liệu (`[1, tr. 25, 3, tr. 7]` → 1, 3); dạng `n: trang` luôn là số tài liệu kèm trang (`[1: 25, 3: 7]` → 1, 3). Hạn chế đã biết: tỉ lệ trong ngoặc vuông như `[1:2]` bị đọc là trích dẫn tài liệu 1, vì trùng dạng `[n: trang]` đã được phán quyết. Marker tác giả–năm: ngoặc tròn chứa các phần `Tên[ et al.| và cs.| & Tên], YYYY[a-z]` phân tách bằng `;`, và dạng tường thuật `Tên (YYYY)`. Năm 1800–2099.
- Liên kết số: theo `parsed.label` nếu danh mục có nhãn, ngược lại `order_index + 1`; số ngoài khoảng → link NULL. Liên kết tác giả–năm: khớp duy nhất họ tên đầy đủ + năm, rồi họ + năm (bỏ dấu, chữ thường, xét hậu tố a/b); nhiều ứng viên → NULL (`REFERENCE_UNRESOLVED`, TK-14).
- Mỗi marker một `citations`, mỗi tài liệu được trích một `citation_links`, một `claims` cho mỗi marker (span theo QĐ-16; `context_text` = câu trước + câu sau; `language` của câu; `extraction_method = "rule"`).
- **W2** (khi có LLM): một lời gọi mỗi đoạn có marker, marker đánh ID `m1…`; span hợp lệ (`claim_text == sentence[span_start:span_end]`) thay span rule, `extraction_method = "llm:claim.extract/v1"`; span sai giữ span rule (không thử lại, `07a` §4.3.5); `is_claim = false` → `claims.is_claim = false`.

### 7.5 S5 — Truy cập và lấy toàn văn (T-403)

- **Vào:** `reference_checks` có `work_id` của lần chạy. **Ra:** `source_files` `OPEN_ACCESS` dùng chung.
- Abstract (Crossref JATS bỏ thẻ; OpenAlex dựng lại từ `abstract_inverted_index`) → `sources/open/<sha256>.abstract.json`, `content_level = ABSTRACT`, `access_status = OPEN_ABSTRACT`.
- Toàn văn, thử theo thứ tự: arXiv (`https://export.arxiv.org/pdf/<id>`), ACL Anthology (DOI `10.18653/v1/<id>` → `https://aclanthology.org/<id>.pdf`, CC BY 4.0), OpenAlex `best_oa_location.pdf_url` (+`license`), Unpaywall (`url_for_pdf`, chỉ khi có email). Tải qua `SafeHttpClient` (≤ `max_source_bytes`, phải bắt đầu `%PDF-`, không bị cắt) → `sources/open/<sha256>.pdf`, `FULL_TEXT`, `OPEN_FULL_TEXT`. Index duy nhất (`work_id`, `sha256`) cho `OPEN_ACCESS`.
- Lỗi tải một nguồn chỉ bỏ qua nguồn đó; không có bản nào thì không có dòng (S8 ra `NO_ACCESSIBLE_TEXT`).

### 7.6 S6 — Chia đoạn và lập chỉ mục (T-405)

- **Vào:** `source_files` liên quan đến lần chạy (bản mở của các work, bản `USER_UPLOAD` của các reference trong tài liệu). **Ra:** `source_chunks`, `<storage_key>.parsed.json`.
- PDF nguồn parse như S1 (không giới hạn trang của bài, dùng `max_source_pages = 200`). Chunk trong từng mục, 250–400 token (ước lượng `ceil(số từ × 1,3)`), chồng lấn một câu; chú thích hình/bảng là chunk riêng; abstract là chunk riêng `section = "Abstract"`. `chunk.text == parsed.text()[char_start:char_end]`. `language` theo heuristic; `embedding` do `Embedder` (`NoEmbedder` → NULL).

### 7.7 S7 — Truy xuất (T-501)

- **Vào:** các cặp claim–reference trong phạm vi, nguồn được chọn. **Ra:** `retrieval_results`.
- Chọn nguồn của cặp: `USER_UPLOAD` của đúng reference và đúng chủ sở hữu (mới nhất) → `OPEN_ACCESS` FULL_TEXT của work → ABSTRACT. Không bao giờ truy vấn ngoài `source_file_id` đã chọn.
- Biến thể truy vấn `original`; thêm `translated` khi `cross_lingual = X2`, có LLM và claim không phải tiếng Anh (W3; bản dịch làm rơi một số có trong claim gốc thì bỏ). Mỗi biến thể top-k theo `ts_rank_cd` rồi `chunk_index`; gộp RRF; ghi mỗi dòng với `rank` của biến thể và `score_fused`. V0 không truy xuất; V1 lấy mọi chunk của nguồn ABSTRACT.

### 7.8 S8 — Phán quyết (T-502–T-504)

Tiền kiểm tất định (không gọi LLM), theo thứ tự: `is_claim = false` → `NOT_A_CLAIM`; link NULL → `REFERENCE_UNRESOLVED`; reference `NOT_FOUND_IN_SEARCHED_SOURCES` và không có tệp tải lên → `REFERENCE_NOT_FOUND`; reference `LOOKUP_FAILED` → `PROCESSING_ERROR`; không có nguồn → `NO_ACCESSIBLE_TEXT`; nguồn toàn văn không có chunk → `SOURCE_PARSE_FAILED`; ngôn ngữ nguồn ngoài vi/en (chữ không phải Latin chiếm > 50%) → `SOURCE_LANGUAGE_UNSUPPORTED`; không có LLM → `PROCESSING_ERROR` + `LLM_NOT_CONFIGURED`; V2/V3 có nguồn toàn văn mà không có chunk nào được truy xuất → `EVIDENCE_INCONCLUSIVE` (gắn cờ).

Biến thể: **V0** W5 với prompt closed-book (`verdict.judge_closed_book`), QĐ-26. **V1** W5 trên chunk abstract. **V2** W5 trên top-k toàn văn; không có toàn văn thì như V1. **V3** W4 tách ≤4 ý; mỗi ý truy xuất (`query_variant = subclaim:s1…`) và W5; gộp theo bảng `07a` §4.4.3; W4 lỗi thì coi cả claim là một ý. **V4**, **V2+A2** ở §9.

Validator (`apply_rules`), áp sau mọi Verdict:

1. Chunk ID ngắn không có trong context → bỏ mục bằng chứng.
2. Quote phải xuất hiện nguyên văn trong chunk sau chuẩn hóa NFC và gộp khoảng trắng; vị trí tính trên văn bản gốc của chunk; không khớp → bỏ.
3. Bằng chứng tối thiểu theo `08` §4.4; thiếu → `UNVERIFIABLE`/`EVIDENCE_INCONCLUSIVE`, `downgraded_by_rule` = `QUOTE_NOT_VERBATIM` (khi có mục bị bỏ) hoặc `EVIDENCE_REQUIRED`, gắn cờ.
4. `content_level_used = ABSTRACT` và `UNSUPPORTED` → `ABSTRACT_ONLY_INSUFFICIENT`, `downgraded_by_rule = ABSTRACT_RULE`.
5. `abstain_cause`: `TEXT_UNREADABLE` → `SOURCE_PARSE_FAILED`; `CONTEXT_INCONCLUSIVE` hoặc NULL → `EVIDENCE_INCONCLUSIVE`. `abstain_cause` đi với trạng thái khác `UNVERIFIABLE` là lỗi schema (thử lại một lần).
6. Cờ `needs_user_review`: `PARTIALLY_SUPPORTED`, `CONTRADICTED`; kết luận dựa trên abstract; bằng chứng ở vùng TABLE/FIGURE; tệp tải lên `title_match = MISMATCH`; hạ mức ở bước 3; `UNSUPPORTED` của V2 (TK-13); `EVIDENCE_INCONCLUSIVE`; V0.

Giới hạn: tối đa `limits.max_claims` cặp theo thứ tự trong bài; trước mỗi cặp cần LLM, cộng `llm_calls.cost_estimate` của lần chạy, ≥ `max_cost_usd` thì dừng gọi LLM (QĐ-32). Đồng thời `llm_concurrency = 4`. Ghi `model` (model trả lời thực), `prompt_version`, `schema_version = verdict/2`, `verifier`.

### 7.9 S9 — Hoàn tất (T-601, T-602)

Lần chạy con: chép từ lần chạy cha `reference_checks` của reference ngoài phạm vi và `claim_verifications` + `evidence` của cặp chưa có trong lần chạy con. Cập nhật `verification_runs.cost_estimate` = tổng `llm_calls.cost_estimate`. Báo cáo vẫn tạo khi xem.

Thay đổi tầng báo cáo (dữ liệu thật): lọc theo verifier cuối (QĐ-28); outer join reference cho `REFERENCE_UNRESOLVED` (`ClaimRow.ref_index: int | None`); tab tài liệu tham khảo đánh dấu mục không được trích lần nào (FR-21); tab "Thiếu toàn văn" theo QĐ-31. Số truy vấn mỗi hàm vẫn cố định.

## 8. LLM

### 8.1 Provider (ADR-006)

`AnthropicProvider` (SDK `anthropic>=2.13,<3`, `AsyncAnthropic`) cài `LLMProvider`:

- Worker: `messages.create(model, max_tokens, system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}], messages, output_config={"format": {"type": "json_schema", "schema": strict_output_schema(schema)}, "effort": effort})`. Sonnet/Opus + `anthropic_refusal_fallback` → `beta.messages.create(..., betas=["server-side-fallback-2026-07-01"], fallbacks="default")`. Văn bản lấy từ các khối `text`, bỏ khối `thinking`. `stop_reason` `refusal`/`max_tokens` → `LLMClient` coi là đầu ra hỏng.
- `strict_output_schema` bỏ `minLength`, `maxLength`, `pattern`, `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`, `multipleOf`, `minItems`, `maxItems`, `uniqueItems`, `default`, `format` ngoài danh sách hỗ trợ; đặt `additionalProperties: false` cho mọi object. `LLMClient` vẫn validate đủ ràng buộc bằng Pydantic và gửi lại `SCHEMA_INVALID` một lần.
- Lượt agent: `messages.create(..., tools=[{name, description, input_schema, "strict": True}])`, không `tool_choice`; nội dung phản hồi (`response.to_dict()["content"]`, bỏ khóa có giá trị NULL) được gửi lại nguyên vẹn, kể cả khối `thinking`; hội thoại chỉ nối thêm.
- Lỗi: `RateLimitError`, `APIConnectionError`, `APIStatusError` ≥ 500 → `RetryableError`; 4xx khác → `ExternalServiceError`; thông điệp chỉ gồm tên lớp và mã HTTP.
- `ProviderResponse.model` = model thực sự trả lời; `effort` nằm trong khóa cache khi khác NULL (khóa cũ không đổi khi không có effort).
- `make_llm_client(settings, session_factory, mode)` trả `None` khi không có khóa.

### 8.2 Tác vụ

| Tác vụ | Ai | Model mặc định | effort | max_tokens | Schema |
|---|---|---|---|---|---|
| `reference.parse` | W1 | `claude-haiku-5-5` | low | 8000 | `parsed_reference_batch/1` |
| `claim.extract` | W2 | `claude-haiku-5-5` | low | 4000 | `claim_extraction_batch/1` |
| `claim.translate` | W3 | `claude-haiku-5-5` | low | 2000 | `translated_claim/1` |
| `claim.decompose` | W4 | `claude-haiku-5-5` | low | 2000 | `subclaims/1` |
| `verdict.judge`, `verdict.judge_closed_book` | W5 | `claude-sonnet-5-5` | high | 16000 | `verdict/2` |
| `agent.reference_resolver` | A1 | `claude-sonnet-5-5` | high | 16000/lượt | `submit_resolution` |
| `agent.evidence_investigator` | A2 | `claude-sonnet-5-5` | high | 16000/lượt | `verdict/2` |

Prompt bằng tiếng Anh, phiên bản `v1`, theo khung `07a` §4.8.3: phần cố định trước, dữ liệu trong `render_data_block(..., untrusted=True)`, lời nhắc ngôn ngữ đầu ra ở cuối. Giá mặc định `llm_prices` (USD/1M token): Haiku 5.5 0,10/0,50; Sonnet 5.5 2/10; Opus 5.5 4/20.

## 9. Agent

**`AgentRunner`** (T-406, ADR-010): vòng lặp gọi `complete_turn` → chạy các lời gọi công cụ song song → trả mọi `tool_result` trong một lượt; dừng khi `submit_*` hợp lệ, hết ngân sách (`max_steps` không tính submit, `max_context_tokens` theo `input_tokens` một lượt, `max_total_input_tokens`, `max_wall_seconds`) hoặc lỗi. Còn một bước → thêm "You have one call left: submit now with what you have." vào lượt kết quả công cụ. Model dừng mà chưa nộp → nhắc một lần → `NO_SUBMIT`. Submit sai nghiệp vụ → trả `is_error` kèm `fix`, tối đa `max_submit_attempts = 2`, sau đó nhận bản cuối và để validator tất định hạ mức. Cache từng lượt bằng `agent_step_cache_key` (hội thoại gồm system prompt và danh sách công cụ). Ghi `agent_runs` (QĐ-45) và `llm_calls` với `agent_run_id`, `step_index`. Kết quả công cụ cắt ở giới hạn token ghi trong mô tả và báo rõ đã cắt.

**A1** (T-408): chạy khi bật, có LLM, và (a) cascade ra `NOT_FOUND_IN_SEARCHED_SOURCES`, (b) `PARSE_FAILED`, hoặc (c) ứng viên sát điểm; không chạy cho `WEB` hay khi mọi nguồn đang ngắt mạch. Công cụ `meta_search`, `meta_get`, `submit_resolution` theo `07a` §4.6.2 (bỏ arXiv); ID ứng viên `cand_N` theo phiên. Hậu xử lý theo `07a` §4.5.2: so ứng viên với trường parse gốc, hoặc với `raw_text` khi `PARSE_FAILED`; tiêu đề lệch nhưng tác giả, năm, venue khớp → `METADATA_MISMATCH` (`title`) + `needs_user_review`; không đạt → giữ trạng thái cascade. Ghi `resolved_by = agent`, `agent_run_id`. Agent hết ngân sách → giữ trạng thái cascade, gắn cờ.

**A2** (T-704): phạm vi khóa bằng closure (`source_file_id` của cặp); `source_outline`, `source_search` (lexical trong nguồn, k ≤ 8, lọc mục), `source_read` (1–3 chunk, `neighbors`), `submit_verdict` (Verdict v2). Kiểm tra khi nộp: `UNKNOWN_CHUNK_ID`, `CHUNK_NOT_READ`, `QUOTE_NOT_IN_CHUNK`, `EVIDENCE_REQUIRED`; sau đó `apply_rules` như W5. **V4**: A2 cho mọi cặp có toàn văn; lỗi → `PROCESSING_ERROR`, gắn cờ. **V2+A2**: W5 trước; A2 chỉ khi W5 ra `UNSUPPORTED`/`PARTIALLY_SUPPORTED` trên `FULL_TEXT`; A2 nhận trạng thái và chunk đã dùng của W5, không nhận lời giải thích; dòng cuối `V2+A2` có `escalated_from`; A2 lỗi → giữ kết quả W5, gắn cờ. Admin chọn được V3, V4, `V2+A2`.

## 10. Tải nguồn và kiểm chứng lại

- **Tải lên** (FR-32/33, T-404): trên tab "Thiếu toàn văn", mỗi tài liệu có ô tải PDF (`POST /runs/{run_id}/missing/{reference_entry_id}/upload`, CSRF). Kiểm tra: chữ ký `%PDF-`, `max_upload_bytes`, `max_pages`, tài liệu thuộc đúng lần chạy và người dùng (khác → 404). Lưu `documents/<uid>/<docid>/sources/<source_file_id>.pdf`; `USER_UPLOAD`, `owner_user_id`, `reference_entry_id`, `USER_PROVIDED`, `FULL_TEXT`. Khớp tiêu đề: văn bản hai trang đầu và tiêu đề trong metadata PDF so với tiêu đề reference (hoặc work) bằng `partial_ratio` ≥ 85 → `MATCH`; không có tiêu đề để so → `UNKNOWN`; còn lại `MISMATCH` kèm cảnh báo và nút "Xóa tệp này".
- **Kiểm chứng lại** (FR-34, T-505): nút "Kiểm chứng lại các tài liệu đã tải" tạo lần chạy con (`parent_run_id`, `scope.kind = upload`, bắt đầu S6). Nút "Tra lại các tài liệu lỗi kết nối" trên tab tài liệu tham khảo tạo lần chạy con `lookup_retry` bắt đầu S3. Một tài liệu chỉ có một lần chạy hoạt động; không tính hạn mức (QĐ-30).

## 11. Đánh giá (T-506)

- `python -m citeproof.evaluation run --dataset <jsonl> --config <yaml> --mode read_write|replay|fresh [--split dev] [--out benchmark/results] [--dump]` và `run-refs` cho D5. Chỉ chạy trên DB có tên kết thúc `_eval` hoặc `_test`.
- Nhập D1–D4 thành dòng thật dưới người dùng hệ thống `eval@citeproof.local`: một tài liệu TEXT cho mỗi tập, reference + work + `reference_checks` `MATCHED`, nguồn `OPEN_ACCESS`, citation, claim; lần chạy có job S1–S5 `skipped`, rồi chạy S6–S8 bằng đúng mã sản phẩm. D5: một tài liệu TEXT chứa các chuỗi reference, chạy S2–S3.
- Chỉ số: Macro-F1, F1 từng lớp, ma trận nhầm lẫn, khoảng tin cậy bootstrap (1000 lần, seed 0); Recall@k và MRR khi có `evidence_spans` (chunk liên quan khi chứa nguyên văn một span sau chuẩn hóa); tỷ lệ quote bị loại; chi phí và độ trễ từ `llm_calls`; D5: accuracy, tỷ lệ tài liệu thật bị báo chưa tìm thấy.
- Kết quả `benchmark/results/<YYYY-MM-DD>-<config>/`: `report.json`, `report.md`, `config.yaml`, `manifest.json` (commit, hash tập dữ liệu, model, prompt, chế độ cache, ngày), `dump.sql.gz` + `sha256` khi `--dump`. Namespace cache `eval:<dataset>@<hash>`.
- Bộ dữ liệu tổng hợp nhỏ trong `tests/fixtures/eval/` (6 claim, 2 nguồn, có một claim tiếng Việt) và cấu hình mẫu `benchmark/configs/`.

## 12. Triển khai và giới hạn tần suất

- **T-608:** middleware ASGI, mặc định 120 yêu cầu/phút/IP và 10 lần tải lên/phút/IP; bỏ qua `/static/` và `/api/v1/health`; vượt → 429 (trang lỗi hoặc JSON `RATE_LIMITED`) kèm `Retry-After`.
- **T-606:** `docker-compose.prod.yml`: `caddy` (TLS tự động, cổng 80/443, địa chỉ cố định trong mạng `172.30.0.0/24` để đặt `CITEPROOF_TRUSTED_PROXIES`), `app`, `worker`, `grobid` (không mở cổng, giới hạn RAM 4 GB `[ĐX]`), `db` (không mở cổng), `migrate`, `backup` (hằng ngày: `pg_dump -Fc` DB sản phẩm và DB đánh giá, nén thư mục storage, giữ 14 ngày). `.env.production.example` liệt kê mọi biến; `scripts/backup.sh`, `scripts/restore.sh`, `scripts/smoke.py` (E2E smoke); `14` thêm mục vận hành.

## 13. Hợp đồng dữ liệu

- **`parsed.json`** (`parsed/1`): `source` (`text|docx|grobid|pdfium`), `page_count`, `language`, `paragraphs[]` {`index`, `text`, `section`, `page`, `char_start`, `char_end`, `region`, `kind` (`body|heading|caption`), `language`}, `references_raw[]` {`raw`, `label`, `page`, `parsed`}. `text()` = các đoạn nối bằng `"\n\n"`; mọi offset tính trên chuỗi này.
- **`reference_entries.parsed`**: `title`, `authors`, `year`, `venue`, `doi`, `isbn`, `url`, `label`.
- **`reference_checks.mismatches`**, **`verification_runs.warnings`**, **`documents.parse_warnings`**: theo `08` §4.4a.
- **Mã cảnh báo mới** (thêm vào DESIGN.md §8.6 và `labels.WARNINGS` cùng lúc):
  - `LLM_NOT_CONFIGURED`: "Chưa cấu hình mô hình ngôn ngữ, nên các nhận định chưa được đối chiếu; phần tra cứu tài liệu tham khảo vẫn chạy bình thường."
  - `CLAIM_LIMIT_REACHED`: "Bài có nhiều nhận định hơn giới hạn của một lần kiểm tra; các nhận định vượt giới hạn chưa được đối chiếu."
  - `COST_LIMIT_REACHED`: "Lần kiểm tra đã chạm hạn mức chi phí; các nhận định còn lại chưa được đối chiếu."
  - `PDF_TEXT_FALLBACK`: "Tệp PDF được đọc bằng chế độ dự phòng; tên mục và vị trí trong bài có thể chưa chính xác."
  - `REFERENCE_PARSE_DEGRADED` (S2 dùng rule vì GROBID hoặc W1 lỗi): "Một số tài liệu tham khảo được tách bằng luật đơn giản vì dịch vụ phân tích tạm thời không khả dụng; thông tin có thể chưa đầy đủ."
- **Nhãn mới:** khớp tiêu đề (`MATCH` "Tên bài trong tệp khớp với tài liệu tham khảo.", `MISMATCH` "Tên bài trong tệp có vẻ không khớp với tài liệu tham khảo. Kiểm tra lại trước khi dùng.", `UNKNOWN` "Chưa kiểm tra được tên bài trong tệp."); "Không được trích lần nào trong bài."; "[chưa xác định]" cho claim chưa liên kết; nút "Tải lên", "Xóa tệp này", "Kiểm chứng lại các tài liệu đã tải", "Tra lại các tài liệu lỗi kết nối".
- **`retrieval_results.query_variant`**: `original`, `translated`, `subclaim:s<n>`.
- **`agent_runs.steps[]`**: `index`, `tool`, `args`, `result_preview` (≤ 2 000 ký tự), `error_code`, `input_tokens`, `output_tokens`, `cache_hit`.

## 14. Thay đổi lược đồ

| Migration | Nội dung |
|---|---|
| 0007 `job_leases` | `jobs`: `heartbeat_at`, `lease_expires_at`, `started_at`, `finished_at`; index (`status`, `lease_expires_at`); điền `lease_expires_at` cho job RUNNING sẵn có |
| 0008 `works_field` | `works.field TEXT NULL` |
| 0009 `claim_pairs` | `claims.is_claim BOOL NOT NULL DEFAULT true`; `claim_verifications.reference_entry_id` NULL được + CHECK `reference_entry_id IS NOT NULL OR unverifiable_reason IS NOT DISTINCT FROM 'REFERENCE_UNRESOLVED'` (so sánh an toàn với NULL: với `=`, lý do NULL cho biểu thức NULL và CHECK vẫn đạt); thay khóa duy nhất (QĐ-14) |
| 0010 `source_access` | CHECK `access_status` theo QĐ-18; index duy nhất một phần (`work_id`, `sha256`) WHERE `origin = 'OPEN_ACCESS'` |

## 15. Cấu hình mới (`Settings`, `.env.example`)

`job_lock_timeout_seconds` (độ dài lease, mặc định 120), `job_heartbeat_seconds` (30); `anthropic_api_key` (SecretStr), `anthropic_timeout_seconds` (120), `anthropic_max_retries` (2), `anthropic_refusal_fallback` (true), `llm_models`, `llm_effort`, `llm_max_tokens`, `llm_prices` (mặc định §8.2), `llm_concurrency` (4); `grobid_url` (`http://localhost:8070`), `grobid_timeout_seconds` (300), `max_pages` (300), `max_source_pages` (200), `max_source_bytes` (30 MB); `contact_email`, `semantic_scholar_api_key`, `metadata_rate_limits`, `metadata_breaker_threshold` (3), `metadata_cache_ttl_days` (30), `metadata_not_found_ttl_days` (7), `metadata_timeout_seconds` (15), `reference_concurrency` (4), `web_archive_lookup` (false); `run_max_claims` (150), `run_max_cost_usd` (5.0), `retrieval_k` (6); `rate_limit_per_minute` (120), `rate_limit_uploads_per_minute` (10). Mọi giá trị số là `[ĐX]`.

## 16. Rủi ro

| Rủi ro | Giảm thiểu |
|---|---|
| Không có embedding: claim tiếng Việt khó tìm trên nguồn tiếng Anh khi không có LLM | X2 mặc định; không có chunk khớp thì trả `EVIDENCE_INCONCLUSIVE`, không đoán; ADR-007 xem lại sau SPK-04 |
| Heuristic marker, tách câu, bảng mã sai trên bài thật | Bộ golden; ngưỡng là hằng số đặt tên; tinh chỉnh sau pilot |
| GROBID chậm hoặc hết RAM với bài 300 trang | Lease gia hạn; timeout 300 s; giới hạn RAM container; dự phòng `pypdfium2` |
| API metadata đổi định dạng hoặc giới hạn tốc độ | Fixture đã ghi; parse phòng thủ (bản ghi hỏng bị bỏ); ngắt mạch; `LOOKUP_FAILED` trung thực |
| Fallback phía server làm model trả lời khác model yêu cầu | Ghi model thực vào `llm_calls` và `claim_verifications`; tắt được bằng cấu hình |
| Giới hạn tần suất trong tiến trình không chia sẻ khi chạy nhiều tiến trình | Pilot chạy một tiến trình app; ghi rõ trong `14` |
| Phạm vi lớn trong một nhánh | Mỗi task tự xanh; thứ tự cắt giảm `11` §4.5 (A2, A1, V3, FR-34) vẫn áp được vì chúng là các task cuối |

## 17. Để lại cho người xây dựng

Nội dung prompt và ví dụ (tiếng Anh, chỉnh trên tập dev); danh sách stopword và viết tắt; tên lớp CSS và chia partial; cấu trúc nội bộ từng module khi chữ ký trong plan đã cố định; chọn ngưỡng trong khoảng `[ĐX]` nếu golden cho thấy cần.

## 18. Cần người dùng quyết

1. Q-01: `run_max_cost_usd` (mặc định 5 USD) và việc dùng Sonnet cho W5.
2. Q-03: đọc chính sách dữ liệu của Anthropic (ZDR hay không), đặt `CITEPROOF_LLM_PROVIDER_NAME`, `CITEPROOF_LLM_PROVIDER_POLICY_URL`.
3. Fallback phía server cho Sonnet (QĐ-36) bật mặc định; tắt nếu cần mọi kết quả từ đúng một model.
4. QĐ-29: hoãn storage S3 cho pilot.
5. QĐ-42, QĐ-43: bật archive.org hay không; nội dung màn hình đồng ý mới.
6. `CITEPROOF_CONTACT_EMAIL` cho polite pool (email của người vận hành, không phải người dùng).
7. Tên miền và máy chủ pilot cho Caddy.
