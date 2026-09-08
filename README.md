# DocAtlas

DocAtlas là app local để đọc, tìm kiếm toàn văn, quản lý và hỏi AI trên thư viện tài liệu viễn thông. Source hiện hỗ trợ hai định dạng:

- Ericsson Alex (`.alx`)
- ZTE eReader (`.zed` / `.ZED`)

App đọc trực tiếp nội dung HTML, bảng, ảnh và file đính kèm trong gói; không giải nén hoặc thay đổi tài liệu gốc.

## Chạy trên Windows

1. Cài Python 3.11+ và Node.js 22+.
2. Đặt thư viện mặc định tại `F:\Library\Ericsson_Alex` và `F:\Library\ZTE_Alex`, hoặc thêm thư mục khác từ giao diện.
3. Nhấp đúp `Start DocAtlas.cmd`.
4. Mở `http://127.0.0.1:8765` nếu trình duyệt chưa tự mở.

Lần đầu chạy, DocAtlas tự cài phần giao diện, quét hai thư mục và lập chỉ mục nền. Gói nhỏ được đưa lên trước để có thể đọc và tìm sớm. Chạy `Stop DocAtlas.ps1` để dừng máy chủ local.

Để thử nhanh đúng một gói nhỏ nhất của mỗi vendor:

```powershell
.\Start DocAtlas.ps1 -SampleOne
```

## Chức năng

- Đọc ALX và ZED ngay trong app, giữ liên kết, ảnh, bảng và file đính kèm trong gói.
- Search toàn văn bằng SQLite FTS5 trên tiêu đề, mã tài liệu và nội dung.
- Quản lý nhiều nguồn theo vendor; quét lại, theo dõi tiến độ và bỏ gói khỏi chỉ mục mà không xóa file gốc.
- Tải lại gói ALX/ZED gốc.
- Hỏi AI theo gói đang mở hoặc toàn thư viện, có trích dẫn quay lại đúng trang tài liệu.
- Tự động nhận các file mới trong thư mục ở lần quét tiếp theo.

## Kết nối AI

Trong **Kết nối AI**, chọn một trong các nhà cung cấp:

- Ollama local
- OpenAI / GPT
- Google Gemini
- Anthropic Claude
- API tương thích OpenAI (LM Studio, vLLM, LiteLLM hoặc gateway nội bộ)

Với OpenAI, Gemini và Claude, dán API key của tài khoản developer. Khóa được mã hóa bằng Windows DPAPI và chỉ lưu trong `.docatlas/secrets.json` trên máy hiện tại; khóa không được trả về giao diện, log hoặc Git. Có thể dùng biến môi trường `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY` hoặc `DOCATLAS_AI_KEY` thay cho việc lưu khóa.

Tài khoản ChatGPT/Claude web và gói thuê bao chat không tự động cấp quyền API cho ứng dụng bên thứ ba. Gemini hỗ trợ OAuth nếu tự cấu hình Google Cloud OAuth client, nhưng bản local mặc định dùng API key để việc cài đặt gọn và an toàn hơn.

Nếu chưa có mô hình, DocAtlas vẫn trả về các đoạn tài liệu gần nhất và trích dẫn để chức năng tra cứu không bị chặn.

## Dữ liệu local

Chỉ mục, tiến độ và cấu hình nằm trong `.docatlas/` và đã được loại khỏi Git. Xóa thư mục này để tạo lại toàn bộ chỉ mục; tài liệu gốc không bị ảnh hưởng.
