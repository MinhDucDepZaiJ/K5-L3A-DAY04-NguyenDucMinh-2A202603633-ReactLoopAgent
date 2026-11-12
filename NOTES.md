## 1. Case được chọn

- **ID case:** H7
- **Điểm ở lần chạy 1:** 32/100
- **Vấn đề:** Agent kết luận Cloudflare Spectrum hoạt động bình thường dù truy vấn Cloudflare bị timeout.
- **Kết quả mong đợi:** Agent phải nói không xác minh được trạng thái Cloudflare khi thiếu dữ liệu hợp lệ.

## 2. Bằng chứng trong trace

Theo kết quả đánh giá H7, phần GitHub Actions được xử lý đúng và có dẫn nguồn. Tuy nhiên, phần Cloudflare Spectrum đưa ra kết luận operational trái với yêu cầu khi tool bị timeout.

**Nguyên nhân giả định:** Agent chưa tuân thủ đầy đủ nguyên tắc tách biệt bằng chứng theo provider và chưa xử lý đúng tình huống thiếu dữ liệu.

Cần đối chiếu lại các sự kiện tool trong trace H7 để xác định chính xác bước dẫn đến kết luận sai.

## 3. Thay đổi đã thực hiện

- **File:** `agent.py`
- **Vị trí:** `SYSTEM_PROMPT`
- **Thay đổi:** Bổ sung quy tắc không suy diễn trạng thái khi tool timeout; tách biệt bằng chứng GitHub và Cloudflare; phân biệt snapshot và trạng thái hiện tại; không suy diễn khi incident thiếu dữ liệu hoặc bị truncated.
- **Lý do:** Giảm các kết luận không có bằng chứng và cải thiện độ chính xác của agent.

**Kỳ vọng:** Agent sẽ trả lời thận trọng hơn khi tool lỗi và chỉ đưa ra kết luận có nguồn xác minh.

**Trạng thái:** Chưa xác minh bằng unit test và eval lần hai.

## 4. So sánh hai lần chạy

| Chỉ số | `eval_before.json` | `eval_after.json` |
|---|---|---|
| Điểm tổng | 87.55	 | 93.36 |
| Điểm case đã chọn | 32 | 97 |
| Số model calls | 2 | 2 |
| Token H7 | 1,385 | 1,584 |
| Model calls toàn bộ | 71 | 70 |
| Model toàn bộ | 94,917 |98,684 |

- Lỗi đã giải quyết: H7 không còn kết luận Cloudflare operational khi tool timeout. Điểm tăng 65.
- Lỗi còn lại hoặc mới xuất hiện: A5 vẫn đạt 41 điểm do giới hạn dữ liệu lịch sử; H1 giảm còn 78 điểm do truy xuất incident chưa đầy đủ.

## 5. Giới hạn

LLM Judge: Điểm đánh giá có tính chủ quan và có thể thay đổi giữa các lần chạy.

Snapshot: Chỉ phản ánh dữ liệu tại thời điểm thu thập, không đảm bảo đầy đủ lịch sử hoặc trạng thái hiện tại.

Số lần chạy: Mỗi phiên bản chỉ được đánh giá một lần nên chưa thể khẳng định mức cải thiện ổn định.
