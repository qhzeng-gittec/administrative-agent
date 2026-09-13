# 数据与检索层（L4）

BM25 关键词 + 向量语义 + RRF 融合 + 重排序。

## 文件

- `bm25.py` —— jieba BM25；多 Pod 的 `mongo` 模式从共享 active chunks 无状态计算
- `vector_store.py` —— 向量存储抽象（内存 / Mongo 共享 / Chroma；规模化可切 Milvus）
- `reranker.py` —— 重排序（默认启发式，可选 cross-encoder）
- `hybrid.py` —— 混合检索（RRF 倒数排名融合）

## 检索流程

1. BM25 召回 top `BM25_TOP`
2. 向量 cosine 召回 top `VECTOR_TOP`
3. RRF 融合
4. 重排序（可选 reranker）
5. 返回 top `HYBRID_TOPK`
6. 从 MongoDB 回填完整 chunk，并再次验证文档 active 状态
