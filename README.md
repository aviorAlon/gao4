# Exam Diagnosis MVP

当前是第一个可运行的垂直切片，基于 DeepTutor 的设计特点重新实现，未修改 DeepTutor 源码。

## 当前范围

- MVP 默认范围：高中 / 数学 / 广东
- 一个 Submission 只能有一个科目
- 一个 Submission 最多 10 次考试
- 支持图片/PDF作为上传资产
- 支持考试资料上传与基础状态记录
- 支持用户确认错题及对应原题
- 支持“确认并开始分析”前的 AnalysisRun 状态
- 当前使用内存存储，上传文件写入 `data/uploads/`；这是开发垂直切片，不是生产持久化实现。

## 运行测试

```bash
PYTHONPATH=. pytest -q
```

## 目录

```text
exam_diagnosis/
├── exam_diagnosis/app.py
└── tests/test_app.py
```

下一阶段应替换内存仓库为 SQLite/对象存储，并接入真正的异步解析 Job。
