# 脚本

| 脚本 | 用途 |
|---|---|
| `seed_data.py` | 幂等种子数据：部门 / 术语表 / 校历 / 默认 Rules&Hooks / 3 条可执行基线 Skill |
| `seed_demo_data.py` | 可选演示扩展：模拟文档、审核单、badcase 和部门 Skill；不属于正常启动必需步骤 |
| `doctor.py` | 检查 DeepSeek、中转站、Embedding/Reranker 等模型连接 |
| `ingest_department_files.py` | 导入 `department_files` 示例文档 |
| `loop_worker.py` | 旧版定时 Worker（兼容保留；生产使用 `async_worker.py`） |
| `async_worker.py` | Redis Stream Worker：文档入库、反馈唤醒、Loop |
| `evaluate_rag.py` | 真实部门文档评测：Recall@5 / MRR / 引用正确率 / 答案一致性 |
| `migrate_memory.py` | 旧四层大文档记忆迁移到事实平面 + 五个记忆平面 |
| `wait_for_deps.py` | 启动前等待 MongoDB/Redis 就绪（Docker） |

用法：

```bash
python -m scripts.seed_data
python -m scripts.ingest_department_files --base ../department_files
python -m scripts.migrate_memory
python -m scripts.async_worker
python -m scripts.evaluate_rag --output evaluation-report.json
STORAGE_MODE=memory EMBEDDING_PROVIDER=hash python -m scripts.evaluate_rag --ingest-base ../department_files
```
