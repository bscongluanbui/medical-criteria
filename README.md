# Medical Criteria — P0

Nền tảng cơ sở tri thức tham khảo y khoa có nguồn và bác sĩ kiểm duyệt.

## Dashboard + Cloudflare Tunnel (cổng 3500)

Dashboard có trang công khai `/`, đăng nhập `/login` và khu vực biên tập `/dashboard`.
Khách chỉ xem tối đa 6 card được admin chọn; bản nháp và nội dung chưa chọn không xuất hiện.
Ban đầu thư viện trống, không tự tạo tiêu chuẩn y khoa giả. Đã có màn hình nguồn,
card, editor tên/claim/ngưỡng + JSON đầy đủ, duyệt/thu hồi, lịch sử và chọn card công khai.
PDF hiện mở bằng link nguồn chính thức; upload/viewer PDF nội bộ sẽ được bổ sung sau.

### Cập nhật VPS hiện có

```bash
cd /home/ubuntu/criteria
git pull --ff-only
docker compose build
docker compose run --rm migrate
docker compose up -d --wait --wait-timeout 120
docker compose exec dashboard python -m app.manage create-user YOUR_EMAIL --role admin
```

Thay `YOUR_EMAIL` bằng email đăng nhập. Nhập mật khẩu hai lần trong terminal (ít nhất
12 ký tự). Không có mật khẩu mặc định hoặc đăng ký công khai. Mật khẩu không nằm trong
Git, log hay JavaScript. Migration bổ sung các bảng account/session/public slots vào DB
hiện có; không xóa các card hoặc nguồn cũ. Backup DB trước khi nâng cấp VPS.

Các biến mặc định (thêm vào `.env` nếu cần đổi):

```dotenv
DASHBOARD_PORT=3500
DASHBOARD_ORIGIN=https://criteria.bcanatomy.site
```

Cookie đăng nhập Secure/HttpOnly, session lưu hash trong DB và hết hạn sau 8 giờ.
POST yêu cầu đúng Origin và CSRF token (riêng login kiểm tra Origin). HTTPS qua tunnel
là đường truy cập đăng nhập được hỗ trợ; HTTP loopback chỉ dùng smoke test.
Giới hạn 5 lần thử/email và 100 lần toàn hệ thống trong 15 phút; có thể gây khóa tạm
khi bị tấn công, nên bổ sung rate limit/Turnstile tại Cloudflare khi mở rộng.

### Đích Cloudflare Tunnel

- `cloudflared` chạy trực tiếp trên VPS hoặc Docker host network: `http://127.0.0.1:3500`.
- `cloudflared` chạy trong container: nối vào network `medical-criteria_default`,
  cấu hình service là `http://dashboard:3500`. `localhost` trong container không phải host.
  Khai báo network này lâu dài trong stack của tunnel; lệnh kết nối dưới đây chỉ áp dụng
  cho container hiện tại:

```bash
docker network connect medical-criteria_default YOUR_CLOUDFLARED_CONTAINER
```

Hostname route: `criteria.bcanatomy.site`. Giữ nguyên tunnel/token hiện có; repository
không tạo tunnel mới. Dashboard chỉ bind loopback trên host; không mở cổng 3500 public.
Nếu muốn trang chủ công khai, không đặt Cloudflare Access chặn toàn hostname. App đã
bảo vệ backend biên tập bằng đăng nhập. Tắt cache Cloudflare cho `/web/*`, `/dashboard`
và `/public/*` (đặc biệt nếu có Cache Everything); ứng dụng gửi `Cache-Control: no-store`.

### Tài khoản và công khai nội dung

```bash
docker compose exec dashboard python -m app.manage create-user REVIEWER_EMAIL --role reviewer
docker compose exec dashboard python -m app.manage reset-password YOUR_EMAIL
docker compose exec dashboard python -m app.manage disable-user REVIEWER_EMAIL
```

Reset/disable thu hồi session hiện tại. Reviewer được tạo/sửa/duyệt/thu hồi card;
admin thêm quyền đăng ký nguồn và chọn 6 vị trí công khai. Audit ghi email từng người.
Chọn public gắn với revision cụ thể: khi xuất bản revision mới, bản public cũ tự ẩn
cho đến khi admin chọn lại. Thu hồi cũng ẩn ngay. Public response không chứa evidence
quote nguyên văn, hash, đường dẫn archive hoặc danh tính reviewer.

Kiểm tra từ VPS:

```bash
curl --fail http://127.0.0.1:3500/health/live
curl --fail http://127.0.0.1:3500/public/cards
```

Không dùng `docker compose down --volumes` để cập nhật. Nếu rollback ứng dụng, checkout
commit trước và build lại; giữ nguyên DB/volume, không xóa các bảng mới để hạ phiên bản.

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
