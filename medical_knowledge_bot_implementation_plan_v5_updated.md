# Medical Knowledge Bot – Kế hoạch triển khai tổng thể

> Mục tiêu: xây dựng một hệ thống kiến thức y khoa có cấu trúc, có nguồn dẫn chứng rõ ràng, dùng Telegram Bot làm giao diện tra cứu nhanh.  
> Trọng tâm không phải “AI tự trả lời y khoa”, mà là **AI hỗ trợ tìm nguồn, trích xuất và chuẩn hóa dữ liệu**; kiến thức được lưu thành database có version, evidence pointer và trạng thái kiểm duyệt.

---

# 0. Môi trường triển khai đã chốt

AI triển khai phải sử dụng đúng hạ tầng hiện tại:

```text
VPS project root:
/home/ubuntu/criteria

Google Drive medical library đã được rclone mount sẵn:
/home/ubuntu/rclone/papers/
```

Yêu cầu:

- Tận dụng mount rclone hiện có.
- Không tạo remote/mount rclone mới.
- Không sync toàn bộ thư viện về VPS.
- `/home/ubuntu/rclone/papers/` là **source library**.
- `/home/ubuntu/criteria/data/` là **processing workspace/cache**.
- File gốc vẫn được quản lý trên Google Drive.
- PostgreSQL giữ metadata và structured medical knowledge.

---


# 0.1. Domain và Reviewer Dashboard

Dự án sẽ tận dụng domain hiện có `bcanatomy` để làm giao diện quản trị và review.

Khuyến nghị dùng một subdomain riêng, ví dụ:

```text
criteria.bcanatomy.<TLD>
```

Reviewer Dashboard:

```text
https://criteria.bcanatomy.<TLD>/review
```

API:

```text
https://criteria.bcanatomy.<TLD>/api/
```

Telegram Bot và các client khác gọi cùng backend API này.

Nếu domain hiện đang đi qua Cloudflare Tunnel / reverse proxy thì tận dụng luôn kiến trúc đó, không mở trực tiếp port dịch vụ ra Internet.

Sơ đồ:

```text
Internet
   │
   ▼
criteria.bcanatomy.<TLD>
   │
   ▼
Cloudflare / Reverse Proxy
   │
   ▼
/home/ubuntu/criteria
   │
   ├── Reviewer Web
   ├── FastAPI
   ├── Worker
   └── Telegram Bot
```

Reviewer Dashboard dùng để xem:

- Missing topics đang được hỏi nhiều.
- Nguồn mà AI vừa tìm.
- PDF nguồn.
- DOI / PMID / metadata.
- Knowledge Card AI đã extract.
- Evidence Pointer.
- Kết quả verifier.
- Conflict giữa các nguồn.
- Trạng thái pending / verified / rejected.

Các thao tác bắt buộc:

```text
Approve
Edit
Reject
Need another source
Mark superseded
```

Màn hình review nên chia hai cột:

```text
┌──────────────────────────┬────────────────────────────┐
│ KNOWLEDGE CARD           │ SOURCE PDF                 │
│                          │                            │
│ Criteria / threshold     │ Page đúng evidence        │
│                          │                            │
│ Source metadata          │ Highlight đoạn liên quan  │
│ DOI / PMID               │                            │
│ Evidence Pointer         │                            │
│                          │                            │
│ [Approve] [Edit]         │ [Open full PDF]            │
│ [Reject]                 │ [Original] [Translated]    │
└──────────────────────────┴────────────────────────────┘
```

Khi bấm `View Evidence`:

- PDF viewer tự mở đúng page.
- Nếu có tọa độ text/span thì highlight đúng đoạn.
- Có nút mở bản gốc và bản dịch nếu cả hai tồn tại.
- Không cần public trực tiếp Google Drive.
- Backend serve PDF từ local cache; nếu chưa cache thì lấy file từ `/home/ubuntu/rclone/papers/` về cache trước.

Reviewer Dashboard không nên public hoàn toàn. Tối thiểu dùng một trong các cách:

```text
Cloudflare Access
hoặc
login/password riêng
hoặc
SSO nếu sau này có
```

Khuyến nghị ưu tiên Cloudflare Access nếu domain đang đi qua Cloudflare.

Public Telegram users không có quyền truy cập Reviewer Dashboard.

---

# 1. Ý tưởng cốt lõi

Xây dựng một “Medical Criteria / Clinical Reference Engine” phục vụ tra cứu nhanh:

- Tiêu chuẩn chẩn đoán.
- Tiêu chuẩn hình ảnh.
- Phân độ mức độ nặng.
- Classification.
- Clinical score.
- Measurement criteria.
- Decision rule.
- Management algorithm.
- Guideline summary ngắn gọn.
- Kiến thức trích từ guideline, bài báo và sách.

Telegram Bot chỉ là frontend đầu tiên.

Sau này cùng một database/API có thể dùng cho:

- Telegram.
- Web.
- Android/iOS.
- Radiology Atlas.
- Chrome extension.
- PACS/RIS integration.
- Internal medical knowledge assistant.

Nguyên tắc quan trọng:

> **AI không phải là nguồn kiến thức.**
>
> AI chỉ giúp:
> - hiểu câu hỏi,
> - tìm đúng tài liệu,
> - tìm đoạn liên quan,
> - trích dữ liệu,
> - dịch/chuẩn hóa,
> - kiểm tra chéo.
>
> Nguồn kiến thức thật là guideline / consensus / paper / textbook.

---

# 2. Vấn đề cần giải quyết

Nếu một người tự làm thủ công:

1. Tìm guideline.
2. Đọc hàng trăm trang.
3. Tìm bảng/tiêu chuẩn.
4. Tóm tắt.
5. Dịch.
6. Nhập database.
7. Kiểm tra lại.
8. Cập nhật khi guideline thay đổi.

=> Không khả thi khi muốn xây hàng trăm hoặc hàng nghìn topic.

Do đó phải xây một **Medical Knowledge Builder Pipeline** tự động hóa phần lớn công việc.

Con người chỉ tập trung vào phần giá trị nhất:

> **Review / Approve / Edit / Reject**

---

# 3. Kiến trúc tổng thể

```text
                         TELEGRAM BOT
                              │
                              ▼
                      Query Normalizer
                              │
                              ▼
                     Knowledge Database
                       │             │
                    FOUND         NOT FOUND
                       │             │
                       │             ▼
                       │       Missing Topic Queue
                       │             │
                       │             ▼
                       │        Source Finder
                       │             │
                       │     ┌───────┴────────┐
                       │     ▼                ▼
                       │  Internet       Local Library
                       │                 Drive / Papers
                       │     └───────┬────────┘
                       │             ▼
                       │       Source Registry
                       │             │
                       │             ▼
                       │       Document Parser
                       │             │
                       │             ▼
                       │       Relevant Chunks
                       │             │
                       │             ▼
                       │     Knowledge Extractor
                       │             │
                       │             ▼
                       │       Evidence Pointer
                       │             │
                       │             ▼
                       │         AI Verifier
                       │             │
                       │      ┌──────┴──────┐
                       │      │             │
                       │     PASS        CONFLICT
                       │      │             │
                       │      │       Stronger Model
                       │      │             │
                       │      └──────┬──────┘
                       │             ▼
                       │       PENDING REVIEW
                       │             │
                       │             ▼
                       │        HUMAN REVIEW
                       │             │
                       └────────► VERIFIED DB
```

---

# 4. Những thứ KHÔNG nên làm

Không xây chatbot theo kiểu:

```text
User
 ↓
LLM
 ↓
"Trả lời theo trí nhớ của model"
```

Không để LLM tự quyết định con số y khoa.

Không lấy câu trả lời Search Agent rồi ghi thẳng vào database.

Không coi “confidence 98%” của model là bằng chứng.

Không tự động chuyển record thành VERIFIED chỉ vì 2 model đồng ý.

Không dùng vector database làm source-of-truth.

Không gửi cả thư viện hàng nghìn file vào LLM mỗi lần hỏi.

---

# 5. Source of Truth

Nên có hai lớp:

## 5.1 Git / YAML / JSON

Dùng làm nguồn dữ liệu chuẩn có version history.

Ví dụ:

```text
knowledge/
├── cardiology/
├── radiology/
├── neurology/
├── gastroenterology/
├── nephrology/
├── emergency/
└── ...
```

Mỗi topic có thể là YAML/JSON riêng.

Ưu điểm:

- dễ diff,
- dễ review,
- dễ rollback,
- dễ version control,
- dễ import/export.

## 5.2 PostgreSQL

Dùng cho runtime:

- search nhanh,
- full-text search,
- alias,
- missing-topic counter,
- reviewer dashboard,
- Telegram API,
- user query statistics.

Pipeline:

```text
YAML/JSON
   ↓
build/import
   ↓
PostgreSQL
   ↓
Telegram/API
```

---

# 6. Các loại Knowledge Card

Không chỉ có “bệnh”.

Hỗ trợ tối thiểu:

```text
diagnostic_criteria
severity_grading
classification
clinical_score
imaging_guideline
measurement
management_algorithm
follow_up
red_flags
differential_diagnosis
reporting_system
```

Ví dụ:

| Topic | Type |
|---|---|
| Hẹp van động mạch chủ | severity_grading |
| Bosniak | classification |
| Lung-RADS | reporting_system |
| Fleischner | imaging_guideline |
| Wells PE | clinical_score |
| Tokyo cholecystitis | diagnostic_criteria |
| ARCO | classification |
| ASPECTS | clinical_score / imaging |
| LI-RADS | reporting_system |

---

# 7. Schema Knowledge Card đề xuất

Ví dụ:

```yaml
id: aortic_stenosis_severity

topic_id: aortic_stenosis

name_vi: Hẹp van động mạch chủ
name_en: Aortic stenosis

aliases:
  - hẹp van chủ
  - AS
  - aortic valve stenosis

specialties:
  - cardiology
  - echocardiography

type: severity_grading

content:
  severe:
    vmax:
      operator: ">="
      value: 4.0
      unit: "m/s"

    mean_gradient:
      operator: ">="
      value: 40
      unit: "mmHg"

    ava:
      operator: "<="
      value: 1.0
      unit: "cm2"

sources:
  - source_id: SRC-000123
    role: current_guideline

evidence:
  - source_id: SRC-000123
    page: 41
    section: "Aortic stenosis"
    table: 5
    evidence_span: "..."

review:
  status: pending
  reviewed_by: null
  reviewed_at: null

version:
  card_version: "1.0"
  created_at: "..."
  updated_at: "..."
```

---

# 8. Evidence Pointer – thành phần bắt buộc

Mỗi claim quan trọng phải truy ngược được về nguồn.

Không chỉ lưu:

```text
page = 41
```

Nên lưu:

```json
{
  "source_id": "SRC-000123",

  "location": {
    "page": 41,
    "section": "Aortic stenosis",
    "subsection": "Assessment of severity",
    "table": "Table 5",
    "figure": null,
    "paragraph": 3
  },

  "evidence_span": "...",

  "source": {
    "title": "...",
    "organization": "ESC",
    "year": 2025,
    "doi": "...",
    "pmid": "...",
    "url": "..."
  },

  "document_sha256": "..."
}
```

## Vì sao lưu SHA256?

Một URL có thể trỏ tới PDF mới hơn trong tương lai.

SHA256 giúp biết chính xác:

> Card này được tạo từ phiên bản file nào.

---

# 9. Source Registry

Tạo bảng `sources`.

Các trường nên có:

```text
id
title
title_normalized
authors
organization
journal
publication_year

doi
pmid
pmcid
isbn

source_type
language

url
official_url
pdf_path

is_official
is_pubmed_indexed

canonical_for
source_role

retrieved_at
document_sha256

parser_status
metadata_status
```

`source_type`:

```text
guideline
consensus
position_statement
systematic_review
original_study
review
textbook
chapter
website
dataset
```

`source_role`:

```text
canonical_source
current_guideline
original_definition
supporting_source
historical_source
```

---

# 10. Ưu tiên nguồn y khoa

Source Finder phải ưu tiên:

1. Official current clinical guideline.
2. Official professional society guideline.
3. Consensus / position statement.
4. Original publication định nghĩa tiêu chuẩn / classification.
5. Peer-reviewed guideline update.
6. Systematic review.
7. Major textbook.
8. Review article.
9. Các nguồn secondary khác.

Hạn chế:

- blog,
- SEO website,
- Wikipedia,
- trang tư vấn bệnh nhân,
- bài không có nguồn rõ.

Không phải lúc nào nguồn mới nhất cũng thay thế nguồn cũ.

Ví dụ cần lưu đồng thời:

```text
Canonical source:
Original consensus 2019

Current guideline:
Guideline 2025

Supporting review:
Review 2026
```

---

# 11. Source Finder Agent

Nhiệm vụ:

> “Tôi nên đọc tài liệu nào?”

KHÔNG phải:

> “Tiêu chuẩn là gì?”

---

## 11.1 Input

Ví dụ user hỏi:

```text
Tiêu chuẩn siêu âm hẹp động mạch thận
```

Query Normalizer chuyển thành:

```json
{
  "topic": "renal artery stenosis",
  "intent": "diagnostic_criteria",
  "modality": "Doppler ultrasound",
  "original_query": "Tiêu chuẩn siêu âm hẹp động mạch thận"
}
```

---

## 11.2 Query Builder

LLM tạo nhiều search query tiếng Anh.

Ví dụ:

```text
renal artery stenosis Doppler ultrasound diagnostic criteria guideline

renal artery stenosis duplex ultrasound velocity criteria consensus

renal artery stenosis PSV RAR guideline

renal artery stenosis ultrasound society guideline
```

Có thể thêm:

```text
site:pubmed.ncbi.nlm.nih.gov ...
site:acr.org ...
site:aium.org ...
site:vascular.org ...
```

Không chỉ dùng 1 query.

Khuyến nghị:

```text
4–8 search queries / missing topic
```

---

# 12. Công cụ Source Finder

V1:

```text
Gemini + Google Search grounding
Crossref
```

V2:

```text
PubMed E-utilities
```

V3:

```text
Europe PMC
Local library search
Official society crawler
```

---

# 13. PubMed

LLM không được tự “bịa PMID”.

LLM chỉ tạo query.

Backend gọi PubMed thật:

```text
AI
 ↓
Generate PubMed Query
 ↓
NCBI ESearch
 ↓
PMID list
 ↓
ESummary / EFetch
 ↓
Metadata
```

Sau đó lưu metadata vào `sources`.

---

# 14. Crossref

Nếu AI hoặc PubMed phát hiện DOI:

```text
10.xxxx/yyyy
```

Backend gọi Crossref để xác minh:

```text
DOI
 ↓
Crossref
 ↓
exists?
```

Nếu DOI không tồn tại hoặc metadata không khớp:

```text
reject / flag
```

Không tin DOI do LLM tự sinh.

---

# 15. Source Ranking

Không cần dùng model mạnh để xếp nguồn.

Backend có thể dùng rule.

Ví dụ:

```python
score = 0

if official_society:
    score += 40

if source_type == "guideline":
    score += 30

if has_doi:
    score += 10

if pubmed_indexed:
    score += 10

if recent:
    score += 10
```

Lưu ý:

> Đây là điểm ưu tiên kỹ thuật để chọn tài liệu cho pipeline,
> không phải grading chất lượng bằng chứng y khoa.

---

# 16. Kho tài liệu cá nhân

Hệ thống cần ingest:

- guideline PDF,
- bài báo gốc,
- bài báo đã dịch,
- sách,
- chapter,
- tài liệu tham khảo khác.

Mỗi tài liệu phải có metadata.

Ví dụ:

```text
SOURCE-00872

Title
Authors
Year
DOI
PMID
Journal

Original PDF
Vietnamese translated PDF

Pages
Language
Source type
```

---

# 17. Cách dùng bài báo dịch

Bản dịch rất hữu ích để:

- tìm keyword tiếng Việt,
- search semantic tiếng Việt,
- đọc nhanh,
- phát hiện đoạn liên quan.

Nhưng:

> Evidence pointer nên ưu tiên trỏ về bản gốc.

Pipeline:

```text
Vietnamese translated paper
          ↓
find relevant section
          ↓
map to original paper
          ↓
extract evidence from ORIGINAL
          ↓
create Knowledge Card
```

Nếu không có bản gốc thì có thể dùng bản dịch với trạng thái cảnh báo riêng.

---

# 18. Sách

Sách có thể dùng làm:

```text
supporting_source
reference_source
```

Nếu có guideline chính thức:

```text
Guideline = canonical/current source

Textbook = supporting source
```

Metadata sách nên lưu:

```text
title
edition
year
authors/editors
chapter
page
isbn
```

---

# 19. MD2SKILL

MD2SKILL dùng như nguồn bootstrap.

Pipeline:

```text
MD2SKILL
   ↓
Importer
   ↓
Normalize schema
   ↓
Translate / normalize Vietnamese
   ↓
Map source
   ↓
PENDING
   ↓
Human review
   ↓
VERIFIED
```

Không import rồi mặc định VERIFIED.

Mục tiêu:

- có ngay hàng trăm candidate cards,
- giảm thời gian nhập database,
- học schema của các clinical skills hiện có.

---

# 20. Document Processing Pipeline

Không gửi nguyên guideline 400 trang cho model mỗi lần hỏi.

Pipeline:

```text
PDF
 ↓
Parser
 ↓
Heading / paragraph / table extraction
 ↓
Chunking
 ↓
BM25 / Full-text / Embedding
 ↓
Relevant chunks
 ↓
LLM
```

Parser có thể dùng:

- Docling.
- MinerU.
- PyMuPDF.
- pdftotext.
- parser khác tùy tài liệu.

OCR chỉ dùng cho tài liệu scan.

---

# 21. Relevant Chunk Retrieval

Ví dụ user hỏi:

```text
Bosniak IIF follow-up
```

Không gửi cả sách.

Chỉ lấy:

```text
Bosniak classification
Follow-up
Management
Relevant table
```

Lợi ích:

- ít token,
- nhanh,
- ít hallucination,
- evidence pointer chính xác.

---

# 22. Knowledge Extractor

Nhiệm vụ:

> “Tài liệu này nói tiêu chuẩn gì?”

Input:

- topic,
- intent,
- selected source,
- relevant chunks.

Output bắt buộc:

```json
{
  "topic": "...",
  "type": "...",

  "criteria": [],

  "evidence": [
    {
      "page": 12,
      "section": "...",
      "table": "...",
      "evidence_span": "..."
    }
  ],

  "status": "pending"
}
```

Không cho output văn xuôi tự do làm dữ liệu chính.

---

# 23. Numeric / Logic Validator

Y khoa đặc biệt dễ lỗi ở:

```text
< vs <=
> vs >=
5 mm vs 6 mm
2/3 vs 3/3
AND vs OR
mg vs µg
cm vs mm
```

Các threshold nên tách thành field riêng:

```json
{
  "parameter": "Vmax",
  "operator": ">=",
  "value": 4.0,
  "unit": "m/s"
}
```

Sau đó validator so trực tiếp với evidence.

Ví dụ:

```text
SOURCE:
Vmax ≥ 4.0 m/s

DATABASE:
operator = ≥
value = 4.0
unit = m/s

=> MATCH
```

---

# 24. AI Verifier

Verifier KHÔNG được dùng trí nhớ y khoa để sửa.

Prompt logic:

```text
Verify every extracted field against the supplied evidence only.

For each field return:
- supported
- contradicted
- not_found

Check specifically:
- numeric value
- unit
- > / >= / < / <=
- AND / OR logic
- population
- exceptions
- modality
- disease stage

Do not use outside medical knowledge.
```

---

# 25. Model Routing

Không dùng model mạnh cho tất cả.

## Model rẻ / nhanh

Dùng cho:

- normalize query,
- aliases,
- specialty classification,
- metadata cleanup,
- translation,
- JSON formatting,
- deduplication,
- simple extraction,
- source query generation.

## Model trung bình

Dùng cho:

- relevant-section detection,
- guideline extraction,
- table interpretation,
- evidence mapping.

## Model mạnh

Chỉ dùng khi:

```text
AMBIGUOUS
CONFLICT
COMPLEX TABLE
MULTIPLE GUIDELINES
FAILED VALIDATION
```

---

# 26. Gợi ý với các model hiện có

Thiết kế model-agnostic, nhưng có thể dùng:

```text
Gemini Flash / Flash-class model
    ↓
source discovery + document extraction

OpenAI lightweight model
    ↓
cross-check / structured verification

Gemini Pro / GPT strong reasoning model
    ↓
conflict resolution / difficult cases
```

Quan trọng:

> Không phụ thuộc cứng vào tên một model.

Tạo config:

```yaml
models:
  query_router: ...
  source_finder: ...
  extractor: ...
  verifier: ...
  conflict_resolver: ...
```

để dễ thay model về sau.

---

# 27. Missing Knowledge Pipeline

Đây là thành phần rất quan trọng.

Nếu user hỏi topic chưa có:

```text
QUERY
 ↓
Knowledge DB
 ↓
NOT FOUND
 ↓
Missing Topics
```

Record:

```json
{
  "topic": "renal artery stenosis",
  "intent": "diagnostic_criteria",
  "modality": "Doppler ultrasound",
  "original_query": "Tiêu chuẩn siêu âm hẹp động mạch thận",
  "request_count": 1,
  "status": "missing"
}
```

Nếu người khác hỏi lại:

```text
request_count += 1
```

---

# 28. Ưu tiên theo nhu cầu thực tế

Dashboard:

```text
Missing Topic                   Requests
------------------------------------------------
Renal artery stenosis Doppler      14
Pulmonary hypertension             11
Aortic regurgitation                8
Rare syndrome X                     1
```

Ưu tiên xây Knowledge Card dựa trên:

```text
request_count
+
clinical importance
+
source availability
+
review readiness
```

Nhờ đó database phát triển theo nhu cầu thật.

---

# 29. Missing Topic Auto-Enrichment

Khi topic mới xuất hiện:

```text
Missing Topic
 ↓
Source Finder
 ↓
Sources found
 ↓
Document ingest
 ↓
Relevant chunk retrieval
 ↓
Knowledge Extractor
 ↓
Evidence pointer
 ↓
Verifier
 ↓
PENDING REVIEW
```

Khi bạn vào Dashboard:

```text
🔥 REQUESTED 14 TIMES

Renal artery stenosis Doppler

Sources found:      ✅
Extraction:         ✅
Evidence pointers:  ✅
Cross-check:        ✅

[Review]
```

Bạn chỉ cần:

```text
Approve
Edit
Reject
```

---


# 30. Mô hình trạng thái kiểm chứng nhiều lớp

Không dùng một trạng thái chung kiểu `PENDING`. Mỗi Knowledge Card có 3 lớp kiểm chứng độc lập và luôn hiển thị cho người dùng:

```text
🟡 Gemini / Models
🟣 ChatGPT Audit
🟢 Doctor Review
```

## 🟡 Gemini / Models

Sau khi pipeline tự động đã tìm nguồn, extract Knowledge Card, tạo Evidence Pointer, chạy numeric/logic validator và verifier nội bộ:

```text
GEMINI_VERIFIED
GEMINI_CONFLICT
GEMINI_FAILED
```

Hiển thị ví dụ:

```text
🟡✓ Gemini
🟡! Gemini conflict
```

`GEMINI_VERIFIED` không đồng nghĩa bác sĩ đã duyệt.

## 🟣 ChatGPT Audit

Mặc định:

```text
GPT_UNVERIFIED
```

Hiển thị:

```text
🟣— ChatGPT
```

Sau khi ChatGPT Web audit thành công:

```text
GPT_VERIFIED
```

Hiển thị:

```text
🟣✓ ChatGPT
```

Nếu phát hiện mâu thuẫn:

```text
GPT_CONFLICT
```

Hiển thị:

```text
🟣! ChatGPT
```

Nếu card đã được gửi sang ChatGPT nhưng ChatGPT không đủ bằng chứng để kết luận, không được quay về `GPT_UNVERIFIED`. Dùng trạng thái riêng:

```text
GPT_INDETERMINATE
```

Hiển thị:

```text
🟣? ChatGPT
```

Ý nghĩa: **đã audit nhưng chưa thể xác nhận hoặc bác bỏ**.

Các nguyên nhân điển hình:

- evidence không đủ,
- PDF không đọc được,
- bảng quá phức tạp,
- source không chứa claim,
- nhiều nguồn mâu thuẫn,
- không xác định được population / exception,
- auditor trả về AMBIGUOUS / NOT_ENOUGH_EVIDENCE.

## 🟢 Doctor Review

Mặc định:

```text
DOCTOR_UNVERIFIED
```

Hiển thị:

```text
🟢— Doctor
```

Sau khi bác sĩ duyệt:

```text
DOCTOR_VERIFIED
```

Hiển thị:

```text
🟢✓ Doctor
```

Các trạng thái khác:

```text
DOCTOR_NEEDS_EDIT
DOCTOR_REJECTED
```

## Không gộp ba lớp thành một boolean `verified`

Nên lưu riêng:

```json
{
  "verification": {
    "models": {
      "status": "GEMINI_VERIFIED",
      "verified_at": "...",
      "model": "...",
      "prompt_version": "..."
    },
    "chatgpt": {
      "status": "GPT_UNVERIFIED",
      "audited_at": null,
      "audit_id": null
    },
    "doctor": {
      "status": "DOCTOR_UNVERIFIED",
      "reviewed_at": null,
      "reviewed_by": null
    }
  }
}
```

Ví dụ card mới:

```text
🟡✓ G3.8-Fl
🟣— GPT-Sol
🟢— Doctor
```

Sau GPT audit:

```text
🟡✓ G3.8-Fl
🟣✓ GPT-Sol
🟢— Doctor
```

Sau bác sĩ duyệt:

```text
🟡✓ G3.8-Fl
🟣✓ GPT-Sol
🟢✓ Doctor
```

Nếu GPT không kết luận được:

```text
🟡✓ G3.8-Fl
🔴(?) GPT-Sol
🟢— Doctor
```

## Quy ước ký hiệu

```text
🟡 = automated model verification
🟣 = ChatGPT independent audit
🟢 = Doctor / human medical review
🔴 = trạng thái cần chú ý

✓ = verified
— = unverified / chưa chạy
🔴(?) = indeterminate / đã audit nhưng chưa đủ bằng chứng
🔴! = conflict
🔴× = rejected / failed
```

Không dùng phần trăm "độ tin cậy" do model tự sinh.

## Hiển thị khi tra cứu

Telegram/Web luôn hiển thị đủ ba lớp:

```text
HẸP ĐỘNG MẠCH THẬN – DOPPLER

🟡✓ G3.8-Fl
🟣✓ GPT-Sol
🟢— Doctor

PSV ...
RAR ...

📚 Source:
...
```

Nếu GPT chưa audit:

```text
🟡✓ G3.8-Fl
🟣— GPT-Sol
🟢— Doctor
```

Nếu GPT đã audit nhưng không thể kết luận:

```text
🟡✓ G3.8-Fl
🔴(?) GPT-Sol
🟢— Doctor
```

Người đọc tự đánh giá mức độ tin tưởng dựa trên các lớp kiểm chứng đã hoàn thành.

## Reviewer Dashboard

Dashboard phải có bộ lọc:

```text
Gemini verified
GPT unverified
GPT verified
GPT indeterminate
GPT conflict
Doctor unverified
Doctor verified
Doctor rejected
```

Các queue chính:

```text
Needs GPT Audit
GPT Indeterminate
GPT Conflict
Needs Doctor Review
Fully Verified
```

## Database fields đề xuất

```text
model_verification_status
model_verified_at
model_name
model_prompt_version

gpt_audit_status
gpt_audited_at
gpt_audit_model
gpt_audit_notes
gpt_audit_package_id

doctor_review_status
doctor_reviewed_at
doctor_reviewed_by
doctor_review_notes
```

Enum:

```text
model_verification_status:
  GEMINI_VERIFIED
  GEMINI_CONFLICT
  GEMINI_FAILED

gpt_audit_status:
  GPT_UNVERIFIED
  GPT_VERIFIED
  GPT_CONFLICT
  GPT_INDETERMINATE

doctor_review_status:
  DOCTOR_UNVERIFIED
  DOCTOR_VERIFIED
  DOCTOR_NEEDS_EDIT
  DOCTOR_REJECTED
```




# 30.10. Kiến trúc lưu trữ đơn giản hóa: KHÔNG đồng bộ hai chiều

Để tránh rối và lỗi sync, dự án **không dùng mô hình PostgreSQL ↔ Google Drive đồng bộ hai chiều**.

Quy tắc chính thức:

```text
Google Drive
=
nơi giữ FILE GỐC / PDF GỐC

PostgreSQL
=
nơi giữ DATABASE / STATUS / METADATA / VERIFICATION

Local VPS filesystem
=
CACHE / PROCESSING WORKSPACE
```

Ba lớp này có vai trò khác nhau và không cạnh tranh làm source-of-truth.

## Google Drive

Giữ tài liệu gốc lâu dài:

```text
guideline PDF
paper PDF
translated PDF
books
source documents
```

Nguồn hiện có:

```text
/home/ubuntu/rclone/papers/
```

Nguồn mới do Source Finder tải:

```text
/home/ubuntu/rclone/papers/criteria_sources/
```

Drive là source-of-truth cho **binary document files**.

## PostgreSQL

Là source-of-truth duy nhất cho:

```text
topics
knowledge cards
evidence pointers
metadata
PDF status
Gemini/model verification
ChatGPT audit status
Doctor review status
missing topics
query logs
audit history
```

Dashboard bcanatomy và Telegram đều đọc từ PostgreSQL.

## Local VPS

Chỉ dùng làm cache/workspace:

```text
/home/ubuntu/criteria/data/cache/
/home/ubuntu/criteria/data/parsed/
/home/ubuntu/criteria/data/chunks/
/home/ubuntu/criteria/data/indexes/
/home/ubuntu/criteria/data/temp/
```

Local cache có thể xóa và rebuild.

Mất local cache không được làm mất dữ liệu thật.

---

# 30.11. PDF AI tìm được

Khi Source Finder tìm được PDF hợp pháp/công khai:

```text
Internet
   ↓
download tạm về VPS
   ↓
validate PDF
   ↓
extract metadata
   ↓
DOI / PMID verification
   ↓
SHA256
   ↓
deduplicate
   ↓
copy/upload vào Google Drive
```

File tạm:

```text
/home/ubuntu/criteria/data/cache/downloads/
```

File dài hạn:

```text
/home/ubuntu/rclone/papers/criteria_sources/
```

Sau khi ghi Drive thành công:

```text
PostgreSQL:
pdf_status = PDF_AVAILABLE
```

Không cần giữ PDF lâu dài trên local.

---

# 30.12. PDF Status

Chỉ hiện:

```text
📄✓ PDF
```

khi:

- file tồn tại trên Drive,
- PDF mở được,
- SHA256 đã tính,
- source record đã link đúng.

Các trạng thái:

```text
PDF_AVAILABLE
PDF_UNAVAILABLE
PDF_PAYWALLED
PDF_INVALID
PDF_DOWNLOAD_FAILED
PDF_UPLOAD_PENDING
```

Hiển thị:

```text
📄✓  PDF available
📄—  PDF unavailable
📄🔒 PDF paywalled
📄!  PDF invalid
📄×  PDF download failed
```

Không đánh dấu `PDF_AVAILABLE` chỉ vì có URL.

---

# 30.13. Không mirror verification state thường xuyên lên Drive

Không bắt buộc duy trì liên tục:

```text
verification.json
knowledge_card.json
evidence.json
```

trên Drive cho mọi card.

Lý do:

- dễ lệch version với PostgreSQL,
- dễ phát sinh sync conflict,
- tăng complexity,
- ChatGPT Web chỉ cần snapshot khi audit.

Do đó:

```text
PostgreSQL
=
live state

Drive
=
source documents
+
audit snapshot khi cần
```

---

# 30.14. Prepare GPT Audit = tạo snapshot một chiều

Khi user bấm:

```text
Prepare GPT Audit
```

backend lấy:

```text
Knowledge Card từ PostgreSQL
+
Evidence Pointer từ PostgreSQL
+
Verification state từ PostgreSQL
+
PDF từ Google Drive
```

và tạo một **Audit Package snapshot**.

Ví dụ:

```text
/home/ubuntu/rclone/papers/criteria_audit/
└── KC-000184/
    ├── audit_manifest.md
    └── source.pdf
```

Nếu ChatGPT có thể truy cập source PDF ở thư mục khác một cách ổn định, có thể không duplicate PDF.

Nếu không ổn định, copy PDF vào audit folder.

Audit package không phải source-of-truth.

Nó chỉ là:

```text
EXPORT SNAPSHOT
```

---

# 30.15. audit_manifest.md

Đây là file chính ChatGPT Web đọc.

Ví dụ:

```text
# AUDIT PACKAGE

Card ID: KC-000184

Topic:
Renal artery stenosis – Doppler ultrasound

## Current state

📄✓ PDF
🟡✓ G3.8-Fl
🟣— GPT-Sol
🟢— Doctor

## Candidate Knowledge Card

- PSV ...
- RAR ...
- ...

## Evidence Pointer

Source ID: SRC-000184
Page: 18
Table: Table 3
Section: Duplex ultrasound

## Source metadata

Title: ...
DOI: ...
PMID: ...
Year: ...

## GPT Audit Task

Audit strictly against source.pdf.

Check:
- numeric values
- units
- > >= < <=
- AND / OR
- population
- modality
- exceptions
- evidence location

Return one of:

GPT_VERIFIED
GPT_CONFLICT
GPT_INDETERMINATE
```

Mục tiêu:

> ChatGPT chỉ cần đọc `audit_manifest.md` + `source.pdf`.

---

# 30.16. Audit Package không phải database

Quan hệ chỉ một chiều:

```text
PostgreSQL
    │
    │ export
    ▼
audit_manifest.md
```

KHÔNG làm:

```text
PostgreSQL ⇄ Drive JSON
```

Manifest cũ không quan trọng.

Nếu dữ liệu thay đổi:

```text
Prepare GPT Audit
      ↓
regenerate manifest
```

Có thể overwrite package cũ hoặc tạo revision mới.

---

# 30.17. GPT Audit Result

ChatGPT không sửa Knowledge Card trực tiếp.

GPT chỉ trả kết quả audit chuẩn.

Ví dụ JSON:

```json
{
  "card_id": "KC-000184",
  "model": "GPT-Sol",
  "overall": "GPT_VERIFIED",
  "claims": [
    {
      "field": "PSV",
      "result": "SUPPORTED"
    },
    {
      "field": "RAR",
      "result": "SUPPORTED"
    }
  ],
  "notes": ""
}
```

Nếu không kết luận được:

```json
{
  "card_id": "KC-000184",
  "model": "GPT-Sol",
  "overall": "GPT_INDETERMINATE",
  "reason": "The cited source does not clearly define the target population."
}
```

Nếu conflict:

```json
{
  "card_id": "KC-000184",
  "model": "GPT-Sol",
  "overall": "GPT_CONFLICT",
  "conflicts": [
    {
      "field": "PSV",
      "database_value": "...",
      "source_value": "..."
    }
  ]
}
```

---

# 30.18. Import GPT Audit về PostgreSQL

V1 nên đơn giản:

```text
ChatGPT Web
   ↓
copy JSON result
   ↓
paste vào Reviewer Dashboard
   ↓
backend validate
   ↓
update PostgreSQL
```

Audit Importer phải kiểm tra:

- `card_id`,
- schema,
- enum hợp lệ,
- model,
- timestamp,
- duplicate audit.

Sau đó cập nhật:

```text
gpt_audit_status
gpt_audited_at
gpt_audit_model
gpt_audit_notes
```

Không bắt buộc ChatGPT ghi file vào Drive.

---

# 30.19. Doctor Review

Doctor review chỉ cập nhật PostgreSQL.

Ví dụ:

```text
DOCTOR_UNVERIFIED
       ↓
Approve
       ↓
DOCTOR_VERIFIED
```

Không cần sync doctor status sang Drive theo thời gian thực.

Khi lần sau tạo GPT Audit Package, `audit_manifest.md` sẽ lấy trạng thái Doctor mới nhất từ PostgreSQL.

---

# 30.20. Dashboard bcanatomy

Dashboard luôn đọc trực tiếp PostgreSQL.

Ví dụ:

```text
Hẹp động mạch thận – Doppler

📄✓ PDF

🟡✓ G3.8-Fl
🟣— GPT-Sol
🟢— Doctor

[View Card]
[View Evidence]
[Open PDF]
[Prepare GPT Audit]
```

Sau import audit:

```text
📄✓ PDF

🟡✓ G3.8-Fl
🟣✓ GPT-Sol
🟢— Doctor
```

Nếu GPT không kết luận:

```text
📄✓ PDF

🟡✓ G3.8-Fl
🔴(?) GPT-Sol
🟢— Doctor
```

---

# 30.21. Disaster Recovery

Nếu mất local cache:

```text
rebuild từ Drive + PostgreSQL
```

Nếu mất Audit Package:

```text
regenerate từ PostgreSQL + Drive PDF
```

Audit package không cần backup riêng vì có thể tạo lại.

---

# 30.22. Quy tắc tuyệt đối để tránh sync conflict

1. Không dùng sync hai chiều giữa PostgreSQL và Drive metadata.
2. Không coi JSON/MD trên Drive là live database.
3. Không để ChatGPT Web sửa Knowledge Card trực tiếp.
4. PDF gốc chỉ có một bản canonical trên Drive.
5. Verification/status chỉ có một bản canonical trong PostgreSQL.
6. Local filesystem chỉ là cache.
7. Audit package luôn có thể xóa và tạo lại.
8. Mọi trạng thái hiển thị trên Telegram/Web phải lấy từ PostgreSQL.

---

# 30.23. Kiến trúc cuối cùng

```text
                    GOOGLE DRIVE
                         │
                      PDF gốc
                         │
                         ▼
                    rclone mount
                         │
                         ▼
┌─────────────────────────────────────┐
│                VPS                  │
│                                     │
│ PostgreSQL        Local cache       │
│     │                 │             │
│     │            parser/OCR         │
│     │            chunks/index       │
│     │                               │
│     └──── Criteria Backend          │
│                  │                  │
└──────────────────┼──────────────────┘
                   │
          criteria.bcanatomy
             │              │
             ▼              ▼
          Telegram       Dashboard
```

Khi cần ChatGPT audit:

```text
PostgreSQL
    +
Drive PDF
    ↓
Generate Audit Package
    ↓
Google Drive / criteria_audit
    ↓
ChatGPT Web
```

Không có sync hai chiều.


# 30.26. Hiển thị tên model trực tiếp trên badge

Mỗi lớp AI verification phải lưu và hiển thị **model cụ thể đã thực hiện tác vụ**, không chỉ ghi chung chung `Gemini` hoặc `ChatGPT`.

Mục tiêu:

> Người đọc biết card đã được kiểm tra bởi model nào và có thể tự đánh giá mức độ tin tưởng.

Không cần hiển thị tên model đầy đủ dài dòng. Dùng `short_code`.

Ví dụ quy ước:

```text
Gemini 3.8 Flash      → G3.8-Fl
Gemini Pro            → G-Pro
GPT-5.6 Sol           → GPT-Sol
GPT-5.6 Luna          → GPT-Luna
Astra                 → Astra
```

Các model mới sau này chỉ cần bổ sung vào config, không hard-code trong UI.

Ví dụ config:

```yaml
model_labels:
  gemini-3.8-flash: "G3.8-Fl"
  gemini-pro: "G-Pro"
  gpt-5.6-sol: "GPT-Sol"
  gpt-5.6-luna: "GPT-Luna"
  astra: "Astra"
```

Database phải lưu cả:

```text
model_provider
model_id
model_display_name
model_short_code
model_version
```

Không chỉ lưu `model_name`.

Ví dụ:

```json
{
  "models": [
    {
      "provider": "google",
      "model_id": "gemini-3.8-flash",
      "short_code": "G3.8-Fl",
      "status": "VERIFIED",
      "verified_at": "..."
    },
    {
      "provider": "openai",
      "model_id": "gpt-5.6-sol",
      "short_code": "GPT-Sol",
      "status": "VERIFIED",
      "verified_at": "..."
    }
  ]
}
```

Nếu nhiều model cùng kiểm tra một card, UI được phép hiển thị nhiều badge:

```text
🟡✓ G3.8-Fl
🟡✓ Astra
🟣✓ GPT-Sol
🟢— Doctor
```

Hoặc dạng compact:

```text
🟡 G3.8-Fl ✓
🟡 Astra ✓
🟣 GPT-Sol ✓
🟢 Doctor —
```

Không được gộp nhiều model thành một nhãn chung nếu database biết model cụ thể.

---

# 30.27. Quy tắc màu và ký hiệu trạng thái

Màu phải được thiết kế để người dùng **không thể nhìn thoáng qua rồi nhầm trạng thái chưa xác định với verified**.

Quy ước:

```text
🟡 vàng  = automated model verification
🟣 tím   = ChatGPT independent audit
🟢 xanh  = Doctor review
🔴 đỏ    = indeterminate / conflict / cần chú ý
```

### Verified

Hiển thị badge theo màu lớp + dấu check:

```text
🟡✓ G3.8-Fl
🟣✓ GPT-Sol
🟢✓ Doctor
```

### Unverified / chưa chạy

Dùng dấu gạch hoặc trạng thái trung tính:

```text
🟣— GPT-Sol
🟢— Doctor
```

Không dùng dấu check mờ.

### GPT đã audit nhưng không thể kết luận

**Không dùng `🟣?` đơn thuần.**

Phải dùng một badge cảnh báo riêng có:

- nền đỏ,
- hình tròn,
- dấu `?` màu trắng hoặc tương phản cao,
- text rõ `Indeterminate` hoặc `Chưa kết luận`.

Concept:

```text
🔴(?) GPT-Sol
```

UI web thực tế:

```text
[ ? ] GPT-Sol
```

trong đó:

```text
background: red
shape: circle
symbol: ?
```

Có thể render:

```html
<span class="status-indeterminate">?</span>
<span>GPT-Sol</span>
```

CSS concept:

```css
.status-indeterminate {
  display: inline-flex;
  align-items: center;
  justify-content: center;

  width: 20px;
  height: 20px;
  border-radius: 50%;

  background: #d32f2f;
  color: white;
  font-weight: 700;
}
```

Mục tiêu:

> `GPT_INDETERMINATE` phải nổi bật như một cảnh báo, không giống một badge đã verify.

### Conflict

Cũng dùng màu đỏ nhưng ký hiệu:

```text
🔴! G3.8-Fl
🔴! GPT-Sol
```

### Failed / rejected

```text
🔴× Model
```

Doctor rejected có thể hiển thị:

```text
🔴× Doctor
```

---

# 30.28. Ví dụ hiển thị cuối cùng trên Telegram/Web

Card mới do Gemini tạo:

```text
HẸP ĐỘNG MẠCH THẬN – DOPPLER

📄✓ PDF

🟡✓ G3.8-Fl
🟣— GPT-Sol
🟢— Doctor
```

Card đã được GPT audit:

```text
📄✓ PDF

🟡✓ G3.8-Fl
🟣✓ GPT-Sol
🟢— Doctor
```

Card đã qua hai automated layers và bác sĩ:

```text
📄✓ PDF

🟡✓ G3.8-Fl
🟣✓ GPT-Sol
🟢✓ Doctor
```

Card GPT đã audit nhưng không kết luận được:

```text
📄✓ PDF

🟡✓ G3.8-Fl
🔴(?) GPT-Sol
🟢— Doctor
```

Kèm text:

```text
GPT-Sol: đã audit nhưng chưa đủ bằng chứng để kết luận.
```

Card có nhiều model verification:

```text
📄✓ PDF

🟡✓ G3.8-Fl
🟡✓ Astra
🟣✓ GPT-Sol
🟢— Doctor
```

---

# 30.29. Tooltip / chi tiết verification

Trên Reviewer Dashboard và Web, mỗi badge nên có tooltip hoặc click để xem chi tiết.

Ví dụ click:

```text
🟡✓ G3.8-Fl
```

hiện:

```text
Model: Gemini 3.8 Flash
Provider: Google
Task: Knowledge extraction verification
Prompt: verifier_v3
Verified at: ...
Evidence: SRC-000184 / Page 18 / Table 3
```

Click:

```text
🔴(?) GPT-Sol
```

hiện:

```text
Model: GPT-5.6 Sol
Audit status: INDETERMINATE

Reason:
The cited source does not clearly define the target population.

Audited at: ...
Audit file:
audits/gpt_audit.json
```

Như vậy phần badge ngắn gọn nhưng toàn bộ audit trail vẫn xem được.

---

# 30.30. Schema verification hỗ trợ nhiều model

Không nên giả định chỉ có đúng một Gemini verifier.

Schema nên hỗ trợ array:

```json
{
  "verification": {
    "models": [
      {
        "provider": "google",
        "model_id": "gemini-3.8-flash",
        "short_code": "G3.8-Fl",
        "status": "VERIFIED"
      },
      {
        "provider": "astra",
        "model_id": "astra",
        "short_code": "Astra",
        "status": "VERIFIED"
      }
    ],

    "chatgpt": {
      "provider": "openai",
      "model_id": "gpt-5.6-sol",
      "short_code": "GPT-Sol",
      "status": "GPT_UNVERIFIED"
    },

    "doctor": {
      "status": "DOCTOR_UNVERIFIED"
    }
  }
}
```

Điều này cho phép sau này:

```text
Gemini Flash
Gemini Pro
GPT Sol
GPT Luna
Astra
Claude
hoặc model khác
```

đều có thể được thêm vào mà không thay đổi schema.


# 31. Telegram behavior

Telegram/Web phải luôn hiển thị ba badge verification của mỗi card.

Ví dụ:

```text
HẸP VAN ĐỘNG MẠCH CHỦ

🟡✓ G3.8-Fl
🟣✓ GPT-Sol
🟢— Doctor

Severe:
...

📚 Source
ESC ...
Page ...
Table ...

[Phân độ]
[Đo lường]
[Nguồn]
[Evidence]
```

Nếu chưa có Knowledge Card:

```text
🟡 Chưa có dữ liệu trong Knowledge Database

Topic đã được thêm vào Missing Topics
và Source Finder sẽ tìm tài liệu.
```

Nếu có conflict:

```text
⚠ Có mâu thuẫn trong quá trình kiểm chứng.
```

Người dùng có thể mở `[Chi tiết kiểm chứng]` để xem từng lớp và lý do.


# 32. Search trong Knowledge DB

Ưu tiên theo thứ tự:

```text
1. exact ID
2. alias
3. normalized term
4. PostgreSQL full-text
5. fuzzy search
6. embedding
7. LLM query router
```

Không dùng LLM nếu exact/fuzzy search đã giải quyết được.

---

# 33. Alias System

Ví dụ:

```yaml
topic_id: aortic_stenosis

aliases:
  - hẹp van chủ
  - hẹp van động mạch chủ
  - AS
  - aortic stenosis
  - aortic valve stenosis
```

Người dùng có thể hỏi:

```text
hep van chu
AS
hẹp chủ
```

vẫn map đúng topic.

---

# 34. Clinical Score

Score/calculator phải deterministic.

Ví dụ:

```text
Wells PE
CHA2DS2-VASc
HAS-BLED
CURB-65
MELD
Child-Pugh
BISAP
HEART
```

LLM chỉ hiểu input.

Calculator backend tự tính.

Không yêu cầu LLM cộng điểm nếu có thể code rule.

---

# 35. Hạ tầng lưu trữ thực tế của dự án

## Storage rule — bản chốt

```text
Drive PDF        = canonical document
PostgreSQL       = canonical metadata/status
Local filesystem = disposable cache
Audit package    = disposable snapshot for ChatGPT
```

Không có sync hai chiều giữa Drive và PostgreSQL.



## Quyết định đã chốt

Project sẽ chạy trực tiếp trên VPS với:

```text
Repo / application root:
/home/ubuntu/criteria

Kho tài liệu Google Drive đã được mount sẵn bằng rclone:
/home/ubuntu/rclone/papers/
```

**Không tạo thêm rclone mount mới.**

Hệ thống phải tận dụng trực tiếp mount hiện có:

```text
/home/ubuntu/rclone/papers/
```

để truy cập:

- guideline,
- bài báo gốc,
- bài báo đã dịch,
- sách,
- tài liệu y khoa khác đã có sẵn trên Drive.

Kiến trúc:

```text
GOOGLE DRIVE
      │
      │ rclone mount đã hoạt động
      ▼
/home/ubuntu/rclone/papers/
      │
      │ chỉ đọc / lấy file khi cần
      ▼
/home/ubuntu/criteria/data/cache/
      │
      ├─ PDF tạm để parser xử lý
      ├─ OCR tạm
      ├─ extracted text
      ├─ chunks
      └─ processing artifacts
```

Google Drive vẫn là **kho tài liệu gốc lâu dài**.

VPS local là **workspace/cache xử lý**.

PostgreSQL là nơi giữ:

- metadata,
- topic,
- Knowledge Card,
- evidence pointer,
- trạng thái review,
- missing topics,
- query log.

---

# 36. Không tải toàn bộ kho Drive về VPS

Kho tài liệu đã mount qua rclone, vì vậy **không được chạy sync/download toàn bộ `/home/ubuntu/rclone/papers/` về local**.

Chỉ copy/cache file đang cần xử lý.

Quy trình:

```text
/home/ubuntu/rclone/papers/<source.pdf>
             │
             ▼
Metadata scanner phát hiện file
             │
             ▼
Tính metadata cơ bản / xác định source
             │
             ▼
Khi thực sự cần parse
             │
             ▼
copy/cache file về local
             │
             ▼
/home/ubuntu/criteria/data/cache/documents/<sha256>.pdf
             │
             ▼
Parser / OCR / extraction
             │
             ▼
parsed text + chunks + index
```

Sau khi xử lý xong:

- PDF local có thể xóa theo cache policy.
- Parsed text/chunks/index có thể giữ lại.
- File gốc vẫn nằm trên Drive.

---

# 37. Vì sao vẫn cần local cache dù Drive đã mount bằng rclone?

Không nên parse/OCR file lớn trực tiếp nhiều lần qua rclone mount vì các công cụ thường:

- seek file nhiều lần,
- đọc random access,
- tạo temporary files,
- chạy song song,
- đọc lại cùng PDF nhiều lần.

Điều này có thể:

- tăng latency,
- chậm hơn local disk,
- tạo nhiều request tới Drive,
- dễ gặp timeout/rate limit khi xử lý hàng loạt.

Do đó:

```text
rclone mount
    ↓
copy file cần xử lý về cache local
    ↓
parse/OCR/index trên local
```

Lưu ý:

> Mount `/home/ubuntu/rclone/papers/` là **source library**, không phải processing workspace.

---

# 38. Cấu trúc repo thực tế trên VPS

Project root:

```text
/home/ubuntu/criteria/
```

Đề xuất:

```text
/home/ubuntu/criteria/
│
├── app/
│   ├── api/
│   ├── models/
│   ├── services/
│   ├── workers/
│   ├── source_finder/
│   ├── extractor/
│   ├── verifier/
│   └── telegram/
│
├── reviewer/
│
├── scripts/
│   ├── scan_library.py
│   ├── import_md2skill.py
│   ├── rebuild_index.py
│   └── metadata_enrich.py
│
├── config/
│   ├── settings.yaml
│   └── prompts/
│
├── knowledge/
│   ├── cardiology/
│   ├── radiology/
│   └── ...
│
├── data/
│   ├── cache/
│   │   └── documents/
│   ├── parsed/
│   ├── chunks/
│   ├── ocr/
│   ├── indexes/
│   └── temp/
│
├── logs/
│
├── tests/
│
├── docker-compose.yml
├── .env
└── README.md
```

---

# 39. Cấu hình storage phải dùng đúng path hiện tại

AI triển khai phải tạo cấu hình thay vì hard-code path rải rác trong source code.

Ví dụ:

```yaml
project:
  root: "/home/ubuntu/criteria"

storage:
  library_root: "/home/ubuntu/rclone/papers"
  cache_root: "/home/ubuntu/criteria/data/cache"
  parsed_root: "/home/ubuntu/criteria/data/parsed"
  chunks_root: "/home/ubuntu/criteria/data/chunks"
  ocr_root: "/home/ubuntu/criteria/data/ocr"
  indexes_root: "/home/ubuntu/criteria/data/indexes"
  temp_root: "/home/ubuntu/criteria/data/temp"

web:
  base_url: "https://criteria.bcanatomy.<TLD>"
  review_path: "/review"
  api_path: "/api"
```

Hoặc `.env`:

```bash
CRITERIA_ROOT=/home/ubuntu/criteria
MEDICAL_LIBRARY_ROOT=/home/ubuntu/rclone/papers
CACHE_ROOT=/home/ubuntu/criteria/data/cache
```

Quy tắc:

1. **Không tạo rclone remote/mount mới.**
2. **Không sửa service rclone hiện có nếu không cần.**
3. Trước khi scan chỉ kiểm tra `/home/ubuntu/rclone/papers/` có accessible hay không.
4. Nếu mount tạm thời unavailable, worker phải báo lỗi và retry sau, không xóa metadata/source trong database.
5. Không ghi file xử lý tạm vào thư mục rclone mount.
6. Không rename/move tài liệu trên Drive trong giai đoạn MVP.
7. Source Registry chỉ lưu đường dẫn tương đối so với `library_root` khi có thể.

Ví dụ:

```text
library_root:
/home/ubuntu/rclone/papers

relative_path:
Cardiology/ESC/2025_ESC_Valvular_Guideline.pdf
```

Thay vì phụ thuộc cứng vào absolute path trong database.

---

# 39.1. Source Library Scanner

Scanner cần quét:

```text
/home/ubuntu/rclone/papers/
```

nhưng phải làm theo kiểu incremental.

Không parse toàn bộ PDF ngay trong lần scan đầu.

### Pass 1 – Inventory

Chỉ lấy nhanh:

```text
relative path
filename
extension
size
mtime
```

và tạo `source_file`.

### Pass 2 – Metadata

Cho file mới/chưa xử lý:

```text
PDF metadata
DOI detector
PMID detector
ISBN detector
title detector
year detector
```

### Pass 3 – Deep processing

Chỉ chạy khi:

- tài liệu được Source Finder chọn,
- topic đang cần,
- admin yêu cầu ingest,
- hoặc chạy batch background có kiểm soát.

Khi đó:

```text
rclone mounted file
      ↓
local cache
      ↓
SHA256
      ↓
parser/OCR
      ↓
chunks/index
```

---

# 39.2. Tận dụng kho tài liệu hiện có trước khi tìm Internet

Source Finder phải tìm theo thứ tự:

```text
1. VERIFIED Knowledge Database
2. Existing library: /home/ubuntu/rclone/papers/
3. PubMed / Crossref / Europe PMC
4. Google Search / official society websites
```

Điều này giúp:

- tận dụng bài báo/sách bạn đã thu thập,
- giảm search API,
- giảm token,
- tránh tải lại tài liệu đã có,
- ưu tiên bản PDF/bản dịch bạn đã lưu.

Nếu tìm thấy tài liệu local phù hợp:

```text
LOCAL_LIBRARY_HIT
```

thì Source Finder vẫn có thể tìm Internet để kiểm tra:

- có guideline mới hơn hay không,
- metadata chính xác,
- DOI/PMID,
- source chính thức.

---

# 39.3. Bài báo gốc và bài dịch trong kho hiện tại

Scanner cần cố gắng ghép các cặp:

```text
original paper
↕
translated paper
```

Ưu tiên matching theo:

```text
DOI
PMID
normalized title
year
manual mapping
```

Nếu bản dịch được tìm thấy trước:

```text
translated PDF
      ↓
search / semantic retrieval
      ↓
locate relevant content
      ↓
map back to original PDF nếu có
      ↓
Evidence Pointer ưu tiên ORIGINAL
```

Nếu chưa tìm thấy bản gốc:

```text
translation_only = true
```

và record phải giữ trạng thái review phù hợp.

---

# 39.4. Cache policy đề xuất

Không để cache tăng vô hạn.

Ví dụ cấu hình:

```yaml
cache:
  keep_original_pdf_days: 14
  keep_parsed_text: true
  keep_chunks: true
  keep_indexes: true
```

Có thể có cleanup worker:

```text
PDF cache quá hạn
+
không đang được job sử dụng
+
đã parse thành công
      ↓
delete local cached PDF
```

Không bao giờ xóa source trên:

```text
/home/ubuntu/rclone/papers/
```

từ cleanup worker.

---

# 40. Metadata Scanner cho kho tài liệu

Cần một worker quét file mới.

Nhiệm vụ:

```text
file discovered
 ↓
SHA256
 ↓
extract filename metadata
 ↓
PDF metadata
 ↓
DOI detector
 ↓
PMID detector
 ↓
Crossref / PubMed lookup
 ↓
source registry
```

Các metadata cần tìm:

```text
DOI
PMID
PMCID
ISBN
title
authors
journal
year
volume
issue
pages
publisher
```

Nếu không xác định được:

```text
metadata_status = needs_review
```

---

# 41. Ghép paper gốc và paper dịch

Có thể dùng:

```text
DOI
PMID
title similarity
SHA256 mapping
manual link
```

Schema:

```text
source_id
original_file_id
translated_file_id
translation_language
translation_method
```

---

# 42. Deduplication

Cùng một paper có thể nằm nhiều nơi.

Ưu tiên dedup bằng:

```text
DOI
PMID
ISBN + chapter
SHA256
normalized title + year
```

Không dựa chỉ vào filename.

---

# 43. Reviewer Dashboard

Đây nên là phần được làm trước Telegram.

Dashboard cần:

## Queue

```text
Pending
Conflict
Missing high priority
Recently imported
Recently updated guideline
```

## Review screen

Hiển thị:

```text
Knowledge Card
│
├─ extracted criteria
├─ Vietnamese content
├─ original evidence
├─ source PDF page
├─ metadata
├─ verifier result
└─ conflicts
```

Buttons:

```text
Approve
Edit
Reject
Need another source
Mark superseded
```

---

# 44. Audit Trail

Mỗi thay đổi nên lưu:

```text
who
when
old value
new value
reason
source
```

Ví dụ:

```text
AVA:
< 1.0
→
≤ 1.0

Reason:
Corrected against Table 5
```

---

# 45. Guideline Update Tracking

Mỗi source có:

```text
organization
guideline family
publication year
version
```

Nếu tìm được version mới:

```text
old guideline
     ↓
NEW VERSION DETECTED
     ↓
identify affected cards
     ↓
re-extract
     ↓
compare
     ↓
review queue
```

Không tự ghi đè record cũ.

Dùng:

```text
SUPERSEDED
```

---

# 46. API Layer

Backend nên có API riêng.

Ví dụ:

```text
GET /topics/search
GET /topics/{id}
GET /cards/{id}
GET /sources/{id}

POST /missing-topic

GET /review/pending
POST /review/{id}/approve
POST /review/{id}/reject

POST /cards/{id}/prepare-gpt-audit
GET  /cards/{id}/audit-package
POST /cards/{id}/import-gpt-audit   # paste/import GPT audit result
```

Telegram chỉ gọi API.

---

# 47. Database Tables tối thiểu

```text
topics
topic_aliases

knowledge_cards
knowledge_card_versions

sources
source_files

evidence_pointers

missing_topics
query_log

review_queue
review_history

document_chunks

audit_packages
audit_results
```

Có thể thêm sau:

```text
scores
score_rules

guideline_families
source_relationships
```

---


### Verification model metadata

Các bảng verification/audit phải lưu:

```text
model_provider
model_id
model_display_name
model_short_code
model_version
prompt_version
status
verified_at
```


# 48. MVP – thứ tự triển khai

KHÔNG bắt đầu bằng Telegram Bot.

## Phase 1 – Core schema

Làm:

```text
topics
sources
knowledge_cards
evidence_pointers
missing_topics
```

Chốt schema trước.

---

## Phase 2 – MD2SKILL importer

```text
MD2SKILL
 ↓
parse
 ↓
normalize
 ↓
database
 ↓
PENDING
```

Mục tiêu:

- test schema,
- có dữ liệu bootstrap.

---

## Phase 3 – Personal Library ingestion

Kết nối:

```text
Google Drive
```

Làm:

```text
scan files
metadata extraction
SHA256
DOI/PMID lookup
original/translation linking
```

---

## Phase 4 – Source Finder

V1:

```text
Gemini Search
+
Crossref
```

Sau đó thêm:

```text
PubMed
Europe PMC
```

---

## Phase 5 – Document Parser

```text
PDF
 ↓
text
 ↓
sections
 ↓
tables
 ↓
chunks
```

---

## Phase 6 – Knowledge Extractor

Relevant chunks:

```text
↓
structured JSON
↓
evidence pointer
```

---

## Phase 7 – Verifier

Check:

```text
numbers
operators
units
logic
population
exceptions
```

---

## Phase 8 – Reviewer Dashboard

Đây là nơi human verify.

---

## Phase 9 – Missing Topic Pipeline

```text
NOT FOUND
 ↓
missing
 ↓
count
 ↓
source finder
 ↓
candidate card
```

---

## Phase 10 – Telegram Bot

Cuối cùng mới làm:

```text
search
display card
inline buttons
missing-topic submission
```

---

# 49. MVP scope

Không cần toàn bộ y khoa ngay.

Bắt đầu:

```text
Radiology
+
Cardiology
```

Khoảng:

```text
50–100 VERIFIED topics
```

Ví dụ:

```text
Lung-RADS
Fleischner
Bosniak
LI-RADS
PI-RADS
TI-RADS
ASPECTS
Fazekas
ARCO
Atlanta pancreatitis
Tokyo cholecystitis

Aortic stenosis
Aortic regurgitation
Mitral stenosis
Mitral regurgitation
Pulmonary hypertension
Renal artery stenosis
Carotid stenosis
...
```

Sau đó database tự mở rộng từ Missing Topics.

---

# 50. Chỉ số thành công của MVP

MVP được coi là thành công nếu:

1. Import được MD2SKILL candidate data.
2. Scan được thư viện Drive.
3. Tự nhận metadata DOI / PMID / year.
4. Tìm được source cho missing topic.
5. Extract được structured criteria.
6. Evidence pointer mở đúng vị trí nguồn.
7. Numeric validator phát hiện lỗi threshold.
8. Human có thể approve/reject.
9. Telegram/Web luôn hiển thị 3 lớp verification: Gemini / ChatGPT / Doctor.
10. Missing topic tự tăng request_count.

---

# 51. Prompt – Source Finder

```text
You are a medical literature source discovery agent.

Your task is NOT to answer the medical question.

Your task is to locate authoritative primary sources that contain
diagnostic criteria, classification systems, measurement thresholds,
severity grading, clinical decision rules, reporting systems,
or management algorithms.

Search priority:

1. Current official clinical practice guideline
2. Official professional society guideline / consensus
3. Original publication defining the criteria or classification
4. Peer-reviewed guideline update
5. Systematic review
6. Major medical textbook when primary guidance is unavailable

Prefer:
- professional medical societies
- PubMed indexed publications
- official journal publications
- primary sources
- current applicable guideline versions

Avoid:
- blogs
- commercial medical summaries
- patient education pages
- Wikipedia
- SEO articles
- unsourced summaries

Do NOT generate diagnostic criteria from memory.

Return sources only.

For every source return:
- title
- authors
- organization
- year
- journal
- DOI if verified
- PMID if verified
- official URL
- source type
- reason this source is relevant
- expected section/table containing the criteria
```

---

# 52. Prompt – Knowledge Extractor

```text
You are a medical knowledge extraction agent.

Use ONLY the supplied source text.

Extract structured clinical knowledge relevant to the requested topic.

Do NOT use external medical knowledge.
Do NOT fill missing information from memory.
Do NOT change numeric thresholds.
Preserve:
- >, >=, <, <=
- units
- AND/OR logic
- exceptions
- population
- imaging modality
- disease stage

Every extracted claim must contain an evidence pointer.

If information is not explicitly present in the supplied text,
mark it as NOT_FOUND.

Output valid structured JSON only.
```

---

# 53. Prompt – Verifier

```text
You are a medical evidence verification agent.

Compare the extracted record with the supplied source evidence.

Do NOT use outside medical knowledge.

For every field return one of:

SUPPORTED
CONTRADICTED
NOT_FOUND

Check:

- numeric value
- comparison operator
- unit
- AND / OR logic
- population
- modality
- disease stage
- exclusions
- exceptions

Any mismatch in numeric value, operator, unit or logical condition
must be marked CONTRADICTED.
```

---

# 54. Chiến lược token/cost

Tiết kiệm token bằng:

```text
exact search first
full-text search
BM25
metadata filtering
relevant chunk retrieval
```

Sau đó mới gọi LLM.

Không làm:

```text
400-page PDF
 ↓
LLM every query
```

Nên làm:

```text
400-page PDF
 ↓
parse once
 ↓
index once
 ↓
retrieve 3–10 relevant chunks
 ↓
LLM
```

---

# 55. Cache kết quả AI

Không gọi lại model nếu input giống nhau.

Cache theo:

```text
model
prompt_version
document_sha256
chunk_hash
task_type
```

Ví dụ:

```text
extractor_v3
+
SHA256(document)
+
chunk 48
```

Nếu đã xử lý:

```text
reuse result
```

---

# 56. Prompt Versioning

Mỗi extraction lưu:

```text
model_name
model_version
prompt_name
prompt_version
timestamp
```

Khi thay prompt:

```text
extractor_v1
extractor_v2
extractor_v3
```

Dễ audit và re-process.

---

# 57. Logging

Cần log:

```text
query
normalized topic
database hit/miss
source search queries
sources found
source selected
document version
chunks used
model
prompt version
token usage
extraction result
verification result
review decision
```

---

# 58. Quy tắc an toàn dữ liệu y khoa

Knowledge database nên rõ trạng thái:

```text
VERIFIED
PENDING
AI_GENERATED
```

Không trộn lẫn.

Telegram mặc định:

```text
VERIFIED ONLY
```

Nếu hiển thị pending trong Reviewer mode:

```text
⚠ Chưa kiểm duyệt
```

---

# 59. Copyright / Licensing

Không tự động public toàn bộ nội dung sách hoặc guideline có bản quyền.

Database nên lưu:

- metadata,
- citation,
- evidence pointer,
- phần trích ngắn cần thiết,
- structured facts,
- link/file nội bộ.

File PDF gốc có thể nằm trong private Drive.

Nếu sau này mở bot công khai:

> cần rà lại license của từng nguồn.

---

# 60. Kiến trúc triển khai ban đầu đề xuất

Domain/reverse proxy:

```text
criteria.bcanatomy.<TLD>
        │
        ├── /review  → reviewer-web
        └── /api     → FastAPI
```

Không expose trực tiếp PostgreSQL hoặc Redis ra Internet.


Toàn bộ project đặt tại:

```text
/home/ubuntu/criteria
```

Không tạo project ở path khác nếu không có yêu cầu mới.

```text
/home/ubuntu/criteria
│
└── Docker Compose
      │
      ├── api
      │     FastAPI
      │
      ├── worker
      │     Celery / RQ / Dramatiq
      │
      ├── postgres
      │
      ├── redis
      │
      ├── telegram-bot
      │
      └── reviewer-web
```

Bind mount/read access tới thư viện hiện có:

```text
/home/ubuntu/rclone/papers/
```

Worker phải coi path này là source library đã được hệ thống bên ngoài mount sẵn.

Có thể đơn giản hơn trong V1:

```text
FastAPI
PostgreSQL
background worker
simple reviewer UI
```

Không cần microservice quá sớm.

---

# 61. Priority cho AI bắt đầu code

AI triển khai theo đúng thứ tự này:

## P0

1. Làm việc trực tiếp trong `/home/ubuntu/criteria`.
2. Kiểm tra `/home/ubuntu/rclone/papers/` đang accessible; **không tạo mount mới**.
3. Chuẩn bị subdomain `criteria.bcanatomy.<TLD>` cho Reviewer Dashboard và API.
4. Tạo repo structure.
5. Tạo config storage với `library_root=/home/ubuntu/rclone/papers`.
6. Tạo Docker Compose.
6. PostgreSQL schema.
7. Source model.
8. Topic model.
9. Knowledge Card model.
10. Evidence Pointer model.
11. Missing Topic model.

## P1

9. MD2SKILL importer.
10. Local/Drive document scanner.
11. SHA256.
12. Metadata extractor.
13. DOI/PMID detection.
14. Crossref lookup.

## P2

15. Document parser.
16. Chunker.
17. Full-text search.
18. Relevant chunk retrieval.

## P3

19. Source Finder.
20. Knowledge Extractor.
21. Verifier.
22. Numeric validator.

## P4

23. Reviewer Dashboard.
24. Approve/Edit/Reject.
25. Audit history.
26. Drive Audit Package exporter.
27. `knowledge_card.json` / `evidence.json` / `verification.json`.
28. `audit_manifest.md`.
29. PDF status tracking.
30. GPT audit import workflow.

## P5

31. Missing-topic auto pipeline.
32. Priority by request_count.

## P6

33. Telegram Bot.

---

# 62. Quyết định lưu trữ cuối cùng

**Tài liệu y khoa không cần tải toàn bộ về local vĩnh viễn.**

Khuyến nghị:

```text
Google Drive
=
long-term document storage / source of files

VPS local
=
processing cache + parsed data + indexes
```

Cụ thể:

```text
Drive:
- PDF gốc
- PDF dịch
- sách
- guideline

PostgreSQL:
- metadata
- knowledge cards
- evidence pointer
- review status

VPS local:
- file đang parse
- extracted text
- OCR
- chunks
- indexes
- temporary files
```

Khi cần xử lý một PDF:

```text
Drive
 ↓
copy/cache local
 ↓
parse
 ↓
index
 ↓
keep parsed/index
 ↓
optional remove cached PDF
```

Đây là phương án cân bằng tốt nhất giữa:

- dung lượng VPS,
- tốc độ,
- an toàn dữ liệu,
- backup,
- khả năng mở rộng.

---

# 63. Triết lý cuối cùng của dự án

```text
AI tìm
AI đọc
AI trích
AI kiểm tra

NHƯNG

Nguồn quyết định sự thật
+
Con người quyết định VERIFIED
```

Database phải có khả năng trả lời:

> “Thông tin này lấy từ đâu?”

và dẫn ngay tới:

```text
document
page
section
table
evidence
version
```

Nếu làm được điều đó, Telegram Bot chỉ còn là lớp giao diện phía trên một **Medical Knowledge Engine có thể tái sử dụng lâu dài**.


## 64. Quyết định chốt sau review — ưu tiên hơn các mục mâu thuẫn phía trên

- PostgreSQL là nguồn dữ liệu chuẩn; Drive lưu tài liệu và snapshot audit, không đồng bộ database hai chiều.
- Công bố và kiểm định là hai trục riêng. Bản Gemini có bằng chứng và qua kiểm tra cấu trúc được công bố sơ bộ, không cần chờ bác sĩ. Không gọi bản này là đã được bác sĩ duyệt.
- Gemini: ghi nhận đã tạo và tên model tự khai báo; ChatGPT: chưa audit / đạt / mâu thuẫn / chưa đủ bằng chứng; bác sĩ: chưa duyệt / đã duyệt. Mỗi trạng thái gắn với revision cụ thể.
- ChatGPT Web do người quản lý chủ động audit định kỳ qua Google Drive. Bản đầu tải snapshot JSON và PDF lên Drive, rồi nhập JSON kết quả qua dashboard. Không giả định plugin tự ghi trả database.
- Snapshot bất biến có package ID, card ID, revision, content SHA256, source hashes và schema kết quả; không ghi đè gói đang audit.
- Backend kiểm tra đủ claim, evidence ID, ngữ cảnh và kết luận tổng hợp. Import lặp cùng kết quả là idempotent; khác kết quả trên cùng package bị từ chối. Audit cũ chỉ cập nhật revision cũ.
- Audit không tự sửa nội dung. Đề xuất sửa được lưu trong ghi chú; biên tập tạo revision mới, không kế thừa audit/duyệt.
- Bản có mâu thuẫn bị ẩn khỏi câu trả lời công khai; sửa thành revision mới trước khi công bố lại. Bản Gemini mới không tự thay bản bác sĩ đã duyệt.
- Web tiếp tục chọn tối đa sáu card công khai. API trả trạng thái kiểm định cùng nội dung; Telegram phải hiển thị các trạng thái khi được tích hợp.
- Release hiện tại: dashboard/backend công bố sơ bộ, snapshot audit, import kết quả, image GHCR và Compose pull. Telegram → Gemini, tải/kiểm chứng PDF và upload Drive chưa được tích hợp trong release này.

### 64.1. Bổ sung triển khai: trao đổi audit qua Drive mount

- Dùng thư mục `criteria_sources/source_pdf`, `criteria_sources/audit_packages`, `criteria_sources/audit_results` trên mount rclone hiện có; không tạo mount mới.
- Service Docker `drive-sync` (profile `drive`) tự tạo gói cho revision `ai_extracted` chưa có package; xuất cả package được tạo thủ công từ dashboard.
- Người quản lý audit định kỳ bằng ChatGPT Web rồi lưu JSON theo tên `PACKAGE_ID.json` trực tiếp trong `audit_results`. Plugin không có quyền ghi thì người quản lý tải JSON lên thư mục này.
- Worker đọc kết quả mỗi 60 giây; độ trễ thực tế còn phụ thuộc cache/upload của rclone. `READY.json` chỉ xác nhận hoàn tất ghi trên mount cục bộ, không xác nhận Google Drive đã nhận file.
- Worker chỉ có quyền ghi `audit_packages`; hai thư mục còn lại được mount chỉ đọc. Không xóa hoặc di chuyển file của người dùng.
- Khi tự import kết luận GPT_VERIFIED, worker yêu cầu PDF gốc có header PDF và SHA256 khớp nguồn đã đăng ký. Kiểm tra file không đồng nghĩa xác nhận nội dung y khoa.
- Kết quả JSON chưa hoàn tất, sai tên, sai hash/revision hoặc thiếu claim được giữ nguyên và thử lại ở chu kỳ sau; kết quả giống nhau không tạo audit trùng.
- PostgreSQL giữ toàn bộ package và kết quả bất biến. Phần Telegram → Gemini và tải PDF từ internet vẫn là bước tích hợp tiếp theo.
