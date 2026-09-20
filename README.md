# Medical Criteria — P0

Nền tảng cơ sở tri thức tham khảo y khoa có nguồn và bác sĩ kiểm duyệt.

## Quick start — Docker trên VPS

```bash
git clone https://github.com/bscongluanbui/medical-criteria.git /home/ubuntu/criteria
cd /home/ubuntu/criteria
python3 scripts/init_env.py
docker compose up --build --wait --wait-timeout 120
curl --fail http://127.0.0.1:8001/health/live
```

Yêu cầu Docker Engine + Docker Compose v2 và Python 3 để tạo `.env`. Script tạo password/token ngẫu nhiên, không in giá trị và không ghi đè `.env` đã tồn tại. `.env` bị loại khỏi Git và Docker build context. Nếu môi trường đã có `.env` cũ, đối chiếu các biến với `.env.example`.

API có healthcheck kiểm tra readiness/database; chạy non-root với filesystem chỉ đọc. PostgreSQL lưu trong named volume và không mở cổng ra host. API mặc định bind `127.0.0.1:8001`; cần SSH tunnel/reverse proxy TLS khi truy cập từ bên ngoài. Cổng nội bộ container vẫn là `8000`; đổi cổng host bằng `API_PORT` trong `.env`, rồi chạy `docker compose up -d api`. `.env` cũ không có biến này tự dùng `8001`. Không chạy `down --volumes` trên môi trường chứa dữ liệu cần giữ.

```bash
docker compose ps                          # Trạng thái/health
docker compose logs --tail=100 -f api      # Log API
docker compose stop                       # Dừng, giữ dữ liệu
docker compose start                      # Chạy lại
git pull --ff-only
docker compose up --build --wait          # Cập nhật code/image
```

PostgreSQL password trong `.env` được dùng khi khởi tạo volume lần đầu. Đổi biến này sau đó không tự đổi password trong DB; cần quy trình rotate riêng. `APP_VERSION` dùng tag image local, không phải registry release. Schema migration hiện là initial installer; không dùng nâng cấp phá dữ liệu tự động.

### Kiểm thử toàn bộ bằng Docker

```bash
docker compose --profile test run --build --rm test
docker compose --profile test stop postgres-test
```

Profile test dùng PostgreSQL riêng `criteria_test` trên tmpfs, không dùng volume production. Bao gồm workflow tests với SQLite và PostgreSQL tests: migration chạy lặp, trigger bất biến, cạnh tranh sửa/duyệt và persistence qua session. Test sẽ skip nhóm PostgreSQL khi chạy pytest trực tiếp mà không có `TEST_DATABASE_URL`; Docker profile truyền biến này tự động. GitHub Actions chạy test, build và smoke test runtime trên mỗi push/PR.

## Đã triển khai

- Pydantic schema: applicability, measurement, logic, claim-level evidence.
- SQLAlchemy models cho PostgreSQL: document versions, immutable card revisions, publication pointer, audit events.
- FastAPI: tạo revision, pending queue, publish, withdraw, lịch sử, tra cứu tên/alias có dấu hoặc không dấu.
- Quyền reader/reviewer/admin; optimistic concurrency cho sửa revision và row lock khi publish trên PostgreSQL.
- PostgreSQL initial migration có trigger chặn UPDATE/DELETE ở các bảng bất biến.
- Docker Compose: PostgreSQL + migration + API, API chỉ bind loopback.
- Test bằng fixture giả lập, không chứa tiêu chuẩn lâm sàng để sử dụng cho bệnh nhân.

## Trạng thái kiểm chứng

API/workflow được test local qua FastAPI TestClient và SQLite cô lập. SQLite không chứng minh hành vi row-lock, trigger, migration hoặc concurrent transaction trên PostgreSQL. Các bài PostgreSQL được thực thi bằng Docker test profile/CI; xem kết quả GitHub Actions cho commit đang dùng. Chưa triển khai VPS, kết nối Telegram, seed dữ liệu lâm sàng hoặc gọi AI.

P0 kiểm tra cấu trúc, liên kết, hash khai báo và số trang. Chưa xác minh file archive thực tế hoặc nội dung trích dẫn đúng với PDF. Hai xác nhận của reviewer là attestation thủ công, không phải chứng nhận tự động. Phải hoàn thành ingest/viewer và gold-set review trước sử dụng lâm sàng.

## Chạy kiểm thử (Windows PowerShell)

```powershell
cd 'E:\coding\Medical Criteria'
python -m venv .venv
python -m pip --python .venv\Scripts\python.exe install -r requirements.txt
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m scripts.export_schema
```

Nếu ensurepip của Python hệ thống lỗi, dùng `python -m pip --python ...` như trên với venv đã tạo. Các package nằm trong `.venv`, không cài global. requirements.txt khóa các phiên bản đã test local; cần xác minh lại trên Linux trong bước build image.

## Triển khai lên VPS khi có kết nối

Project root: `/home/ubuntu/criteria`. Giữ nguyên mount `/home/ubuntu/rclone/papers`; P0 chưa đọc thư viện và không sửa mount.

```bash
cd /home/ubuntu/criteria
python3 scripts/init_env.py
docker compose config --quiet
docker compose up --build -d
docker compose ps
curl --fail http://127.0.0.1:8001/health/live
```

Giá trị POSTGRES_PASSWORD nên dùng hex để an toàn trong connection URL. API key gắn với một vai trò cho chủ sở hữu trong P0; trước nhiều người sử dụng cần individual identities/token revocation. Trước expose mạng: TLS, access control, rate limits, request-size limits, tài khoản DB runtime tối thiểu thay vì schema owner, backup/restore và PostgreSQL integration tests.

`config/storage.json` mô tả cấu hình ingestion P1; P0 chưa có scanner/worker nên chưa mount library vào container. Không đặt `.env` thật vào repository.

## API

Mọi endpoint nghiệp vụ dùng `Authorization: Bearer <token>`; không log token.

| Endpoint | Quyền |
|---|---|
| GET /health/live | Liveness public |
| GET /health/ready | reader trở lên |
| GET /topics/search?q=... | reader trở lên |
| GET /cards/{id} | reader trở lên, chỉ published |
| POST /sources | admin |
| POST /cards/{id}/revisions | reviewer/admin |
| GET /review/pending | reviewer/admin |
| GET /review/{id}/history | reviewer/admin |
| POST /review/{id}/publish | reviewer/admin |
| POST /review/{id}/withdraw | reviewer/admin |

Tạo revision truyền `{expected_revision: 0, card: ...}` lần đầu; lần sau truyền head revision đang xem. Publish truyền expected_revision, reason, evidence_checked=true, applicability_checked=true. HTTP 409 yêu cầu tải lại revision. Bản nháp mới giữ nguyên bản published cũ cho tới khi reviewer duyệt; withdrawal ẩn ngay và chỉ được xuất bản lại dưới revision mới.

Search P0 là exact normalized match; giữ tất cả kết quả alias đa nghĩa. Chưa có fuzzy/full-text, pagination, missing-topic queue hoặc calculator. JSON Schema ở `config/knowledge-card.schema.json`. Fixture trong tests không được nạp production.

## Bước tiếp theo

1. Theo dõi PostgreSQL integration tests trong Docker/CI; mở rộng restart/restore và race edit-vs-publish; thêm migrations có version khi schema thay đổi.
2. Ingest PDF nguồn bất biến với hash thực tế; viewer mở đúng trang và vùng evidence; reviewer UI.
3. Bác sĩ chọn/duyệt 10–15 card chuẩn và bộ test lâm sàng.
4. Template renderer + bot Telegram private (allowlist, update dedup).
5. Backup + restore thực tế rồi mới pilot; sau đó AI extraction và missing-topic jobs.

## Tài liệu và khôi phục

- Kế hoạch: `medical_knowledge_bot_implementation_plan.md`, mục 64–75 là quyết định mới.
- Lịch sử thay đổi mã nguồn được lưu trong Git. Chọn commit đã kiểm thử để build lại khi cần khôi phục ứng dụng; dữ liệu PostgreSQL cần backup/restore riêng.
- Bản gốc kế hoạch, diff và báo cáo thử nghiệm local trong `artifacts/`, `VERIFICATION.txt` và các helper rollback chỉ lưu ở workspace phát triển, không đưa lên repository công khai.

## Nguồn kỹ thuật

- [FastAPI security](https://fastapi.tiangolo.com/reference/security/)
- [PostgreSQL locking](https://www.postgresql.org/docs/current/sql-select.html)
- [Psycopg transaction semantics](https://www.psycopg.org/psycopg3/docs/basic/transactions.html)

Kế hoạch chứa nguồn ACR/AGREE II/WHO cho định hướng tri thức y khoa. P0 không khẳng định một ngưỡng chẩn đoán y khoa nào.
