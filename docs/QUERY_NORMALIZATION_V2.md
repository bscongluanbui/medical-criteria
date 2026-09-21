# Query normalization core v2 — triển khai

## Phạm vi

Bốn intent tự động: overview, diagnostic_criteria, imaging_diagnostic_criteria,
diagnostic_features. Các loại card cũ vẫn đọc được để giữ revision/audit bất biến;
router không tự chạy phân độ, điều trị, theo dõi hoặc tính điểm.

## Hoạt động

1. Chuẩn hóa Unicode, dấu tiếng Việt, khoảng trắng, 2 lá/hai lá và typo đã biết.
   Không đổi dấu so sánh, giá trị thập phân hoặc đoán viết tắt đa nghĩa.
2. Tách intent và modality; exact alias → fuzzy ngưỡng cao và khoảng cách ứng viên
   → tìm tên đầy đủ theo token → giải nghĩa viết tắt có ngữ cảnh.
3. MS đơn lẻ yêu cầu làm rõ; MS MRI và MS siêu âm được phân tuyến theo ngữ cảnh.
4. Nếu deterministic chưa giải quyết được, worker gọi model AI_ROUTER_MODEL
   (để trống dùng AI_MODEL). Endpoint/key dùng chung client OpenAI-compatible.
   Router chỉ chọn topic tồn tại; JSON sai, model bịa ID hoặc confidence thấp không
   dẫn đến tạo card. Không dùng confidence như độ chính xác y khoa.
5. Lookup topic + intent + modality. Overview liệt kê nội dung và nút Telegram.
   Các nút chỉ trỏ bản đang công bố; callback kiểm tra lại revision khi bấm.
6. Thiếu card: khóa hàng đợi SHA256(topic|intent|modality), không khóa theo typo.
   Các card đang có vẫn có nút xem trong lúc chờ nội dung thiếu.
7. Research pipeline yêu cầu loại card/modality đúng routing; features phải có
   strength=supportive và logic=reference_only. Không biến dấu hiệu gợi ý thành
   tiêu chuẩn chẩn đoán chính thức.
8. Alias từ model được lưu candidate, không tự áp dụng. Admin duyệt qua dashboard;
   alias đa nghĩa/xung đột bị chặn. Query analytics đếm truy vấn chuẩn hóa.

## Migration và dữ liệu cũ

Migration chỉ thêm topics, topic_aliases, topic_card_bindings, alias_candidates,
query_events, telegram_menus; không viết lại revision hoặc hash audit cũ.
Vocabulary ban đầu lấy tên/alias trong tài liệu yêu cầu, không chứa tiêu chí y khoa.
Tên bệnh của card cũ khớp chính xác vocabulary được ánh xạ qua bảng binding; các
card khác giữ topic_id cũ. Không tự gộp các topic trùng khác hoặc suy đoán bệnh từ
alias không rõ. Bootstrap chạy idempotent lúc migrate và bot khởi động.

## Cấu hình và triển khai

```dotenv
AI_ROUTER_MODEL=
```

```bash
cd /home/ubuntu/criteria
git pull --ff-only
docker compose --profile bot --profile drive pull
docker compose run --rm migrate
docker compose --profile bot --profile drive up -d --no-build api dashboard drive-sync bot
```

Dashboard đăng nhập có Tra cứu theo chủ đề chuẩn, Alias đề xuất và thống kê truy vấn.
Trang web công khai vẫn giới hạn sáu card; không mở thêm API để lộ toàn bộ thư viện.

## Những giới hạn cần biết

- Token search hiện dùng ứng dụng, chưa là index PostgreSQL full-text/pg_trgm.
- Model fallback chỉ nhận tối đa 200 topic/600 alias; cần indexed retrieval khi kho lớn.
- Topic hoàn toàn mới/chưa chắc chắn đi vào needs_review/QUERY_NEEDS_CLARIFICATION,
  không tự tạo topic từ trí nhớ model. Danh mục được bootstrap từ tên trong yêu cầu
  và card đã đăng ký; chưa có màn hình quản trị/gộp topic chuyên dụng.
- Chưa có ngữ cảnh hội thoại cho “bệnh đó”; người dùng cần ghi lại tên bệnh.
- Phần public web không được mở rộng beyond sáu card đã chọn. Nút topic nằm ở
  Telegram và tra cứu dashboard đăng nhập.
- Request/alias thống kê có thể chứa câu người dùng nhập; chỉ dùng chủ đề kiến thức,
  không nhập thông tin bệnh nhân. Chưa có chính sách tự xóa log theo thời hạn.
- Router có thể phát sinh thêm một AI call trên đường fallback; hạn mức job hiện có
  vẫn áp dụng. Chưa thêm index fuzzy hoặc tăng độ ưu tiên theo request_count.

## Rollback

Dừng bot trước khi đổi về image cũ. Giữ nguyên database/volume và các bảng bổ sung;
không xóa revision mới. Bản cũ không có UI/routing mới. Xem artifacts/query-v2 để
kiểm tra reverse patch trên một bản sao source code riêng, không chạy trên DB VPS.
