# Bộ cấu hình .agents — Coding Agent

Bộ cấu hình framework-agnostic (dùng được với LangChain, LangGraph, CrewAI, AutoGen,
hoặc agent tự viết) cho một **coding agent**. Chỉ cần "dịch" các file Markdown này
thành system prompt / config theo cú pháp của framework bạn chọn.

## Cấu trúc

```
.agents/
├── README.md                  ← file này
├── AGENTS.md                  ← "hiến pháp" của agent: vai trò, nguyên tắc cốt lõi
├── config.yaml                 ← cấu hình model, tool, giới hạn (điền theo framework)
├── rules/
│   ├── 01-coding-standards.md  ← chuẩn code, style, review
│   ├── 02-safety-guardrails.md ← giới hạn hành vi, việc không được làm
│   └── 03-git-conventions.md   ← quy ước commit, branch, PR
├── skills/
│   ├── code-review.md
│   ├── debugging.md
│   ├── testing.md
│   ├── refactoring.md
│   └── documentation.md
└── workflows/
    ├── feature-development.md
    ├── bug-fix.md
    └── pull-request.md
```

## Cách dùng theo từng framework

### LangChain / LangGraph
- Nạp nội dung `AGENTS.md` + `rules/*.md` làm `SystemMessage` gốc.
- Mỗi file trong `skills/` → một "tool description" hoặc một node riêng trong graph
  (ví dụ node `debugging` chỉ được kích hoạt khi state báo lỗi test).
- `workflows/*.md` → định nghĩa đồ thị trạng thái (StateGraph) hoặc chuỗi `Runnable`.

### CrewAI
- `AGENTS.md` → `backstory` + `goal` của Agent.
- Mỗi file `skills/*.md` → liệt kê trong `tools=[...]` hoặc mô tả trong `role`.
- Mỗi file `workflows/*.md` → một `Task` hoặc một `Process` (sequential/hierarchical).

### AutoGen
- `AGENTS.md` → `system_message` của từng `AssistantAgent`.
- `workflows/*.md` → kịch bản `GroupChat` (thứ tự agent nói, điều kiện dừng).

### Tự viết agent loop
- Ghép `AGENTS.md` + toàn bộ `rules/*.md` thành system prompt.
- Dùng `skills/*.md` làm few-shot / hướng dẫn khi agent cần thực hiện tác vụ tương ứng.
- Dùng `workflows/*.md` làm state machine hoặc checklist agent tự chấm.

## Gợi ý tuỳ biến
1. Đổi ngôn ngữ lập trình / stack cụ thể trong `01-coding-standards.md`.
2. Thêm/bớt guardrail trong `02-safety-guardrails.md` theo mức độ tự chủ bạn cho phép
   (ví dụ agent có được tự `git push` không, có được tự chạy migration DB không).
3. Với agent đa tác nhân (multi-agent), tách `AGENTS.md` thành nhiều file theo vai trò
   (planner, coder, reviewer, tester) và để mỗi role chỉ đọc phần liên quan.
