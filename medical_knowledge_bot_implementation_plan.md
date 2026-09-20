# Medical Knowledge Bot – Kế hoạch triển khai tổng thể

> **Cập nhật triển khai 20/09/2026:** Các quyết định ở mục 64–75 thay thế những đề xuất cũ có xung đột. PostgreSQL là nguồn chuẩn của nội dung/revision/review; Git lưu code/schema/prompt và snapshot. MVP triển khai theo một luồng hoàn chỉnh nhỏ, không bắt đầu bằng nhập hàng trăm card.

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

# 5. Source of Truth — quyết định đã chốt

**PostgreSQL là nơi có thẩm quyền ghi nội dung, revision và lịch sử kiểm duyệt.**

```text
Dashboard/API → PostgreSQL immutable revision → human review → publish
                                      └→ JSON/YAML snapshot → Git
```

- Git lưu mã nguồn, schema, prompt và snapshot có version; không đồng bộ nội dung hai chiều.
- Sửa card đã duyệt tạo revision mới ở trạng thái pending.
- Snapshot chứa revision, content hash và metadata duyệt; không chứa secrets hoặc dữ liệu bệnh nhân.
- Xuất snapshot chưa được triển khai trong P0; lịch sử DB là nguồn chuẩn ngay từ đầu.

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

Crossref không tìm thấy chưa chứng minh DOI không tồn tại. Kiểm tra registration agency/DOI resolution, phân biệt lỗi mạng, nguồn ở cơ quan khác và metadata thực sự không khớp. Chỉ flag/reject sau khi có lý do xác minh rõ ràng.

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

# 30. Trạng thái Knowledge Card

Khuyến nghị:

```text
IMPORTED
AI_GENERATED
PENDING
NEEDS_REVIEW
CONFLICT
VERIFIED
DEPRECATED
SUPERSEDED
REJECTED
```

Telegram public mode:

```text
VERIFIED only
```

Reviewer mode:

```text
VERIFIED
+
PENDING
+
CONFLICT
```

---

# 31. Telegram behavior

Nếu có VERIFIED card:

```text
🟢 VERIFIED

HẸP VAN ĐỘNG MẠCH CHỦ

Severe:
...

📚 Source
ESC ...
Page ...
Table ...

[Phân độ]
[Đo lường]
[Nguồn]
```

Nếu chưa có:

```text
🟡 CHƯA CÓ TRONG CSDL ĐÃ KIỂM DUYỆT

Topic đã được thêm vào danh sách cần bổ sung.

Nếu bật Reviewer mode:
có thể hiển thị candidate answer + sources,
nhưng phải đánh dấu rõ chưa kiểm duyệt.
```

---

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

Chỉ tính SHA256 sau khi chọn tài liệu để ingest và copy về cache. Không đọc toàn bộ thư viện để hash trong inventory đầu tiên.

Cần một worker quét file mới.

Nhiệm vụ:

```text
file discovered
 ↓
lightweight inventory (path / size / mtime)
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
```

Có thể thêm sau:

```text
scores
score_rules

guideline_families
source_relationships
```

---

# 48. MVP – thứ tự triển khai

> Danh mục dưới đây giữ để tham chiếu các thành phần. Thứ tự thực thi đã được thay bằng mục 74: schema + card chuẩn → vertical slice → kiểm thử → AI ingestion → mở rộng.

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
9. Telegram chỉ trả VERIFIED record.
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

> Thứ tự cũ bên dưới được thay thế bởi mục 74. Phát triển local được phép trong workspace hiện tại; chỉ triển khai VPS vào `/home/ubuntu/criteria` sau khi có kết nối. Không giả định máy local là VPS.

AI triển khai theo đúng thứ tự này:

## P0

1. Làm việc trực tiếp trong `/home/ubuntu/criteria`.
2. Kiểm tra `/home/ubuntu/rclone/papers/` đang accessible; **không tạo mount mới**.
3. Tạo repo structure.
4. Tạo config storage với `library_root=/home/ubuntu/rclone/papers`.
5. Tạo Docker Compose.
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

## P5

26. Missing-topic auto pipeline.
27. Priority by request_count.

## P6

28. Telegram Bot.

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


---

# 64. Quyết định triển khai bổ sung

MVP là công cụ tham khảo cho bác sĩ, không phải hệ thống tự chẩn đoán hoặc xử lý hồ sơ bệnh nhân. Tra cứu, calculator và khuyến nghị cá thể hóa là các mức chức năng riêng với tiêu chí nghiệm thu riêng.

Hai luồng độc lập:

1. Serving: query → tìm card đã xuất bản → template xác định → trả lời. Không cần gọi LLM nếu đã có card phù hợp.
2. Authoring: tìm nguồn → ingest → trích xuất → kiểm tra → review → publish. Chạy nền, không chặn Telegram.

# 65. Schema ngữ cảnh và kỹ thuật đo

Card bắt buộc có population, clinical_context, intended_use, prerequisites, exclusions, required_inputs, missing_data_policy, discordance_policy và limitations.

Logic lưu có cấu trúc: all/any/at_least/reference_only; hỗ trợ biểu thức lồng nhau trong giai đoạn calculator sau. Dữ liệu thiếu không tự chuyển thành false hoặc zero. Không suy ra phép AND từ một danh sách ngưỡng.

Measurement lưu modality, protocol, plane, landmarks, caliper_or_roi, quantity, unit, quality_requirements, pitfalls. Cách đo tách khỏi ngưỡng nhưng liên kết trong card. Hình minh họa phải có nguồn và quyền sử dụng.

Định danh hệ thống gồm guideline_family, guideline_module, modality, guideline_version; các module không bị coi là phiên bản thay thế nhau chỉ vì năm mới hơn. P0 chưa thực thi logic như calculator.

# 66. Revision, review và xuất bản

- Nội dung revision bất biến; mọi sửa đổi tạo revision mới.
- Tách origin (manual/ai_extracted/imported), review_status và lifecycle_status.
- Chỉ tài khoản reviewer/admin xuất bản; worker AI không có quyền xuất bản.
- Publish kiểm tra expected_revision trong transaction; từ chối duyệt bản cũ khi head đã đổi.
- Revision mới không tự thay revision đang được phục vụ.
- Thu hồi làm card biến mất khỏi serving ngay; sửa rồi tạo revision mới trước khi xuất bản lại.
- Audit lưu actor, thời điểm, revision, hành động, lý do. Diff nội dung được suy ra từ các revision bất biến.
- P0 dùng khóa riêng cho reader/reviewer/admin của một chủ sở hữu. Trước khi nhiều reviewer sử dụng cần danh tính riêng, token revocation/rotation và session authentication.
- Duyệt trích xuất đúng nguồn và duyệt đúng điều kiện áp dụng là hai xác nhận bắt buộc.

# 67. Nguồn, phiên bản, evidence và lưu giữ

Mô hình đích: source → document_version → source_file (gốc/dịch/HTML/PDF). P0 lưu document version bất biến có source_id và metadata của file gốc; chuẩn hóa các bảng source/source_file tiếp ở P1.

Mỗi claim có id và evidence_ids. Evidence chứa document_version_id, SHA256, pdf_page, printed_page, section/table/cell/footnote, quote, parser_version và bbox chuẩn hóa khi có.

- Hash dùng nhận diện, không thay thế lưu trữ.
- Tài liệu đã dùng để publish phải giữ được đúng bản nguồn bất biến theo quyền lưu trữ; bản này không chịu TTL của processing cache.
- Kho retained sources chỉ lưu tài liệu thực sự được dùng, không sync toàn bộ Drive.
- Reviewer phải mở trang nguồn thật, không chỉ đọc text OCR.
- P0 đăng ký metadata và attestation; tự động kiểm tra archive tồn tại/hash thực tế, viewer PDF và snapshot nguồn thuộc P1, là điều kiện trước khi dùng dữ liệu lâm sàng thật.

# 68. Chất lượng và vòng đời nguồn

Xếp hạng relevance/quần thể/module trước năm xuất bản, DOI/indexing. Tách điểm truy xuất kỹ thuật khỏi chất lượng guideline. Tham khảo AGREE II khi đánh giá guideline trọng yếu; chỉ lưu grade của nguồn nếu nguồn thực sự cung cấp.

Theo dõi new version, correction, erratum, retraction và partial supersession. Duy trì liên kết source → claims → revisions để xác định tác động. Hai guideline khác nhau được trình bày riêng; model mạnh chỉ phân tích, reviewer quyết định với lý do.

Crossref lookup thất bại được phân loại: timeout/rate limit/not found/other agency/metadata mismatch, không tự quy DOI là giả.

# 69. Retrieval và trả lời

- Exact ID/name/alias tiếng Việt có dấu/không dấu trước; trả danh sách nếu đa nghĩa.
- Từ viết tắt không mặc định ánh xạ duy nhất.
- Serving chỉ đọc revision đã xuất bản; trả tên nguồn, phiên bản card, phiên bản guideline và ngày duyệt.
- Renderer dùng template, không để LLM tự thêm ý vào nội dung đã duyệt.
- Search index/cache phải gắn revision; thu hồi phải vô hiệu hóa kết quả cũ.
- Source finder tận dụng local library nhưng vẫn kiểm tra cập nhật nguồn chính thức.

# 70. Job, chi phí và khả năng phục hồi

Job có idempotency key, retry/backoff giới hạn, timeout, dead-letter queue, trạng thái và quota. Khóa cache gồm topic/intent/schema_version/model/config/prompt_version/document_hash/chunk_hash. Chống chạy lặp cùng topic và giới hạn ngân sách theo ngày/topic.

OCR chạy với CPU/RAM/concurrency hạn chế; PDF lỗi không ảnh hưởng serving. Mount unavailable không đồng nghĩa source bị xóa. Inventory chỉ path/name/size/mtime; hash sau khi ingest.

# 71. Telegram, phân quyền và dữ liệu

Bot private/allowlist trước. Reader chỉ tra cứu; reviewer/admin tách quyền ở API. Webhook dùng secret_token; dedup update_id trước tạo job/tăng counter. Telegram là frontend API, không giữ logic y khoa.

Secrets nằm ngoài Git/log. Query log mặc định chỉ thông tin cần thiết, không giữ định danh bệnh nhân. Evidence link có phân quyền; không công khai Drive PDF. Tài liệu tải về là dữ liệu, không là chỉ dẫn cho agent. Worker không có quyền đọc secrets không cần thiết, chạy shell từ tài liệu hoặc tự duyệt.

# 72. Bộ chuẩn và tiêu chí nghiệm thu

Xây 10–15 card chuẩn do bác sĩ kiểm tra trước mở rộng. Có bản gốc, evidence theo claim và bộ câu hỏi tiếng Việt/tiếng Anh. Bao gồm dữ liệu thiếu, sai modality, sai quần thể, alias đa nghĩa, bảng có footnote và guideline xung đột.

Kiểm thử: schema, giá trị/đơn vị/toán tử, logic, điều kiện áp dụng, page evidence, phân quyền, stale approval, thu hồi, retry, mất mount, API lỗi và restore backup.

Calculator tương lai phải test ngay dưới/bằng/trên ngưỡng, đơn vị và input không hợp lệ. Hai model đồng ý không thay thế gold set.

Gate lâm sàng: 100% claim trọng yếu có evidence mở được; không còn lỗi nghiêm trọng đã biết trên gold set; không lộ pending/withdrawn; calculator vượt test biên; restore thành công. Đây là tiêu chí nghiệm thu dự án, không bảo đảm độ chính xác tuyệt đối ngoài bộ test.

# 73. Vận hành VPS

Docker Compose đơn giản, PostgreSQL + API + migration; worker/bot thêm khi có chức năng. API bind loopback, reverse proxy TLS hoặc SSH tunnel khi cần. PostgreSQL không mở port public. Library mount hiện hữu chỉ đọc, không tạo/sửa mount tự động.

Backup DB + retained sources + cấu hình cần thiết, mã hóa và lưu ngoài VPS; secrets quản lý riêng. Chốt RPO/RTO trước pilot (mục tiêu ban đầu: RPO 24 giờ, RTO 4 giờ, cần đo thực tế). Kiểm tra restore vào môi trường tách biệt. Có healthcheck, disk alert, lỗi job, latency, chi phí và hàng đợi review. Schema upgrade dùng migration; không hạ schema phá dữ liệu để rollback ứng dụng.

# 74. Thứ tự thực thi thay thế mục 48/61

1. P0: schema có context/measurement; revision, source version, claim evidence; workflow API; audit, permission, test synthetic; cập nhật plan và runbook.
2. P1: retained source ingestion + viewer/reviewer UI; 10–15 card chuẩn lâm sàng do bác sĩ duyệt; template renderer và Telegram mỏng; PostgreSQL integration/concurrency tests và backup/restore.
3. P2: parser/chunker + extractor/verifier được đánh giá trên gold set; giữ approval thủ công.
4. P3: incremental scanner, PubMed/Crossref ngay trong source discovery, missing-topic jobs, cost controls; MD2SKILL importer sau xác minh repo/license/provenance.
5. P4: tăng 50–100 topic; guideline monitoring; calculator chỉ khi bộ test riêng hoàn chỉnh.

Không tạo card lâm sàng VERIFIED từ dữ liệu giả lập. Fixture phần mềm nằm trong tests, không tự seed vào production. Đếm card chỉ là chỉ số phụ; theo dõi thời gian review/card, tỷ lệ claim có bằng chứng, lỗi critical, độ chính xác retrieval và chi phí/card đã duyệt.

# 75. Nguồn tham khảo cho bổ sung

- ACR LI-RADS: https://www.acr.org/Clinical-Resources/Clinical-Tools-and-Reference/Reporting-and-Data-Systems/LI-RADS
- AGREE II: https://agreetrust.org/wp-content/uploads/2013/06/AGREE_II_Users_Manual_and_23-item_Instrument_ENGLISH.pdf
- WHO, AI for health / LMM governance: https://www.who.int/publications/i/item/9789240084759
- Crossref REST API: https://www.crossref.org/documentation/retrieve-metadata/rest-api/
- Telegram Bot API: https://core.telegram.org/bots/api
- FastAPI security: https://fastapi.tiangolo.com/reference/security/
- PostgreSQL SELECT locking: https://www.postgresql.org/docs/current/sql-select.html
- Psycopg transactions: https://www.psycopg.org/psycopg3/docs/basic/transactions.html

Các quyết định kiến trúc, quota và tiêu chí nghiệm thu là quyết định của dự án; không phải khuyến cáo lâm sàng từ các nguồn trên.
