# Chẩn đoán lỗi research và thử lại

Lỗi cũ `ValidationError`/`SourceError` chỉ ghi tên lớp; không đủ để xác định trường
hoặc nguồn lỗi. Không suy đoán nguồn lỗi từ độ đơn giản của tên bệnh.

Bản sửa lưu stage, schema field/type (không lưu input model), mã lỗi nguồn cụ thể,
source_failures theo PMCID, và tối đa 10 failure_history gần nhất trong provenance.
ValidationError khi trích card được sửa JSON đúng một lần với cùng PDF/schema;
không tìm nguồn lại ngay, không bỏ evidence validator, không điền từ trí nhớ model.
Repair vẫn sai thì needs_review/CARD_SCHEMA_INVALID_AFTER_REPAIR.
Mỗi lần repair có thể thêm một lời gọi AI và chi phí tương ứng.

Nguồn hiện vẫn Europe PMC + PMC PDF mở có license được chấp nhận; không phải toàn
bộ guideline. Chủ đề phổ biến không đảm bảo pipeline tìm/tải được nguồn phù hợp.
Không đổi license hay hạ tiêu chuẩn bằng chứng trong bản sửa này.

Sau khi pull/recreate bot, xem job lỗi (không in query hay token):

```bash
docker compose exec bot python -m app.job_admin inspect 7bc90e5afd48
```

Chạy lại đúng job, giữ lịch sử, reset giới hạn thử cho lần do operator yêu cầu:

```bash
docker compose exec bot python -m app.job_admin retry 7bc90e5afd48
docker compose logs --tail=100 bot
```

Chỉ retry failed/needs_review, không reset job đang chạy hoặc đã có card. Đây là
lệnh operator trong container, không phải quyền của người dùng Telegram công khai.
Kết quả có thể được gửi lại cho những chat đã yêu cầu job này.

Dữ liệu chẩn đoán chi tiết chỉ bắt đầu được ghi sau bản sửa; job lịch sử chỉ có
ValidationError/SourceError sẽ không tự phục hồi được chi tiết đã bị bỏ mất.
