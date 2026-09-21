# Telegram công khai + AI OpenAI-compatible

## Cấu hình trên VPS

Giữ nguyên POSTGRES_PASSWORD và các token API đã có. Thêm vào `.env`:

```dotenv
COMPOSE_PROFILES=drive,bot
TELEGRAM_BOT_TOKEN=DIEN_TOKEN_TU_BOTFATHER
AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
AI_API_KEY=DIEN_API_KEY
AI_MODEL=DIEN_TEN_MODEL_DUNG_TREN_ENDPOINT_CUA_BAN
AI_JSON_MODE=true
AI_MAX_OUTPUT_TOKENS=12000
AI_DAILY_JOB_LIMIT=20
AI_CHAT_COOLDOWN_SECONDS=60
CRITERIA_DRIVE_ROOT=/home/ubuntu/rclone/papers/criteria_sources
```

`AI_BASE_URL` có thể là gateway Gemini của bạn (ví dụ `https://gateway.example/v1`).
Không thêm `/chat/completions`: worker tự thêm đường dẫn này. Endpoint phải hỗ trợ
HTTPS, Bearer API key, Chat Completions, messages system/user, `max_tokens`, và
trả `choices[0].message.content` chứa JSON. Nếu gateway không hỗ trợ
`response_format: {type: json_object}`, đặt `AI_JSON_MODE=false`; prompt vẫn yêu cầu
JSON và backend vẫn xác thực. Không gửi khóa API hoặc token Telegram vào chat.

Model không được tự chọn thay bạn. Điền đúng tên model mà nhà cung cấp chấp nhận.
Gemini sử dụng cấu hình OpenAI-compatible được mô tả tại:
https://ai.google.dev/gemini-api/docs/openai

Bot không có allowlist ID: mọi người có thể nhắn trực tiếp để tra cứu. Trong nhóm,
khả năng nhận tin phụ thuộc Privacy Mode của BotFather; dùng `/ask tên tiêu chuẩn`
hoặc nhắn riêng. Không đổi Privacy Mode tự động.

Giới hạn mặc định 20 chủ đề mới / 24 giờ cho toàn bot, một yêu cầu mới mỗi 60 giây
cho mỗi chat, tối đa 2 lần thử cho một job. Tra cứu tiêu chuẩn đã có không gọi AI.
Đây là giới hạn chi phí, không phải giới hạn quyền theo ID. Mỗi job thành công thông
thường có 4 lời gọi AI: phân loại/tạo truy vấn, chọn nguồn, trích xuất, tự kiểm tra.
Lần thử lại có thể phát sinh thêm lời gọi. Giới hạn token là theo từng lời gọi,
không phải cam kết tổng chi phí tiền tệ. Đặt thêm quota tại nhà cung cấp AI.

## Cập nhật image AMD64/ARM64 và bật bot

Mount rclone phải hoạt động và cho phép user trong container ghi `source_pdf`.
Bot chỉ mount writable thư mục này; không có quyền ghi cả thư viện hay audit_results.

```bash
cd /home/ubuntu/criteria
git pull --ff-only
docker compose pull
docker compose up -d postgres
docker compose run --rm migrate
docker compose run --rm bot python -m app.bot --check-config
# Tùy chọn: gọi AI một lần để kiểm tra endpoint (có thể tính phí):
docker compose run --rm bot python -m app.bot --check-ai
docker compose up -d --no-build api dashboard drive-sync bot
docker compose logs --tail=100 bot drive-sync
```

`--check-config` không gọi dịch vụ bên ngoài, không in token; kiểm tra cấu hình và
quyền ghi mount. `--check-ai` trả `AI_CONNECTION_OK` khi JSON test hợp lệ.
Nếu `.env` chưa bật COMPOSE_PROFILES, thêm `--profile drive --profile bot` sau
`docker compose` vào các lệnh trên.

Bot dùng long polling, không cần thêm port hay Cloudflare hostname. Nếu token đang
có webhook, worker dừng với lỗi rõ ràng thay vì tự xóa cấu hình bot cũ. Chỉ chạy một
worker cho một database/token; PostgreSQL advisory lock ngăn poller trùng trong
cùng database. Thay bot token trên database đang dùng cần quản lý lại offset và
notification queue; không tự đổi token trong một hàng đợi đang hoạt động.

## Luồng thực tế

1. `/start` hiển thị hướng dẫn. Người dùng gửi tên chủ đề hoặc `/ask chủ đề`.
2. Bot tìm tên/alias/mã chuẩn hóa trong các revision đang được công bố.
3. Có dữ liệu: trả nội dung, phạm vi áp dụng, nguồn và ba trạng thái AI/ChatGPT/bác sĩ.
4. Chưa có: lưu job bền vững vào PostgreSQL và báo đã nhận; người hỏi cùng chủ đề
   dùng chung job. `/status` xem yêu cầu gần nhất của chính chat đó.
5. AI chuyển chủ đề kiến thức chung thành truy vấn khoa học. Yêu cầu cá nhân hóa
   theo bệnh nhân hoặc ngoài phạm vi được dừng ở trạng thái cần xem xét.
6. Backend tìm nguồn thực trên Europe PMC, AI chọn tối đa ba ứng viên. Backend tải
   PDF từ PMC Cloud chính thức, không tải URL do mô hình tự bịa.
7. Kiểm tra license, trạng thái thu hồi, checksum PMC, PDF header; parse trong
   subprocess giới hạn thời gian/bộ nhớ; lưu PDF theo SHA256 vào `source_pdf`.
8. AI trích xuất card theo schema. Backend kiểm tra nguồn, hash, số trang và sự
   hiện diện nguyên văn của trích dẫn trên trang đã dẫn. AI kiểm tra lại claim và
   điều kiện áp dụng trong một lời gọi riêng; đây vẫn là **AI tự kiểm tra**, không
   phải audit độc lập hoặc chứng nhận y khoa.
9. Chỉ khi các kiểm tra đạt: lưu nguồn/revision/provenance, công bố AI sơ bộ, tạo
   gói audit; worker Drive đưa gói vào `audit_packages`.
10. Bot gửi kết quả cho các chat đang chờ. Nếu nguồn thiếu hoặc không đọc được,
    gửi trạng thái thất bại/cần xem xét, không tự điền ngưỡng từ trí nhớ.
11. Bạn audit qua ChatGPT Web và lưu JSON vào `audit_results` như trước. Doctor
    audit tiếp tục ở dashboard. Mọi thay đổi nội dung tạo revision riêng.

Dashboard có mục **Hàng đợi Telegram / AI** để xem trạng thái và mã lỗi. Bấm Làm mới
để lấy trạng thái mới. Nguồn và card được tạo tự động xuất hiện trong danh sách biên tập.
Trang công khai vẫn giữ tối đa sáu card do admin chọn; bot đọc tất cả card đã công bố.

## Phạm vi tìm nguồn của bản đầu

- Tìm kiếm độc lập bằng Europe PMC + PMC Cloud, không giả định gateway có Google
  Search grounding. Điều này giữ tương thích với endpoint OpenAI Chat Completions.
- Nguồn tự động được giới hạn ở PDF có license CC BY, CC BY-SA hoặc CC0, không phải
  toàn bộ guideline trên internet. Không vượt paywall; không tự đọc toàn bộ thư viện
  Drive. Chưa có OCR hoặc kiểm định bảng/hình bằng thị giác.
- Parser giới hạn 30 MB/PDF, 150 trang, 180.000 ký tự/tài liệu, timeout 45 giây.
  Tài liệu vượt giới hạn, thiếu PDF hoặc cần OCR có thể dẫn đến needs_review.
- Không đảm bảo nguồn tìm được là guideline mới nhất. Phiên bản nguồn và trạng thái
  phải luôn được hiển thị; nguồn PMC/NLM không hàm ý NLM xác nhận nội dung do AI tạo.
- Bản đầu tự bổ sung **theo yêu cầu thiếu dữ liệu**, chưa tự cập nhật định kỳ mọi
  guideline đã lưu. Lỗi kỹ thuật được thử lại hai lần; needs_review được giữ để xem
  xét, không lặp vô hạn hoặc phát sinh chi phí âm thầm.
- Việc gửi Telegram có ngữ nghĩa at-least-once: crash sau gửi nhưng trước ghi nhận
  có thể tạo tin trùng. Tin lỗi có backoff và tối đa 10 lần gửi; không chặn các chat khác.
- Chỉ gửi chủ đề kiến thức chung; tên chủ đề được gửi cho nhà cung cấp AI và dịch vụ
  tìm nguồn. Không đưa thông tin bệnh nhân vào yêu cầu bot.

## Nguồn kỹ thuật đã đối chiếu

- OpenAI Chat Completions: https://developers.openai.com/api/reference/resources/chat
- Gemini compatibility: https://ai.google.dev/gemini-api/docs/openai
- Telegram long polling: https://core.telegram.org/bots/api#getupdates
- PMC Cloud: https://pmc.ncbi.nlm.nih.gov/tools/pmcaws/
- PMC dataset schema: https://pmc-oa-opendata.s3.amazonaws.com/README.txt

PMC OA Web Service cũ đã ngừng hoạt động từ tháng 8/2026; bản này dùng metadata và
PDF từ PMC Cloud mới. File PDF thật đã được tải thử và parse mà không đưa vào
database sản xuất. Các test AI/Telegram dùng giả lập, không dùng khóa thật.

## Dừng / rollback

```bash
docker compose stop bot
```

API/dashboard/Drive audit tiếp tục hoạt động. Muốn rollback image, dùng digest hoặc
tag `sha-...` đã ghi trước khi cập nhật rồi recreate các service. Migration chỉ thêm
research_jobs, telegram_updates, telegram_state; không xóa dữ liệu cũ. Không dùng
`docker compose down -v`.
