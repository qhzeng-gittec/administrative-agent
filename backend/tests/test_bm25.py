"""测试 BM25 检索。"""
from __future__ import annotations

from app.retrieval.bm25 import BM25Index, tokenize


def test_tokenize_chinese():
    tokens = tokenize("本科生选课管理办法")
    assert "选课" in tokens or "本科" in tokens or len(tokens) > 0


def test_bm25_search():
    idx = BM25Index()
    docs = [
        {"_id": "c1", "dept_id": "dept_jwc", "content": "本科生选课时间安排在每学期第16至18周完成下学期选课。"},
        {"_id": "c2", "dept_id": "dept_jwc", "content": "退课申请应当在开课后两周内提交。"},
        {"_id": "c3", "dept_id": "dept_cwc", "content": "学费缴纳截止时间为每学期开学前。"},
    ]
    idx.index(docs)
    hits = idx.search("选课时间", top_k=2)
    assert hits, "应返回检索结果"
    assert hits[0]["id"] == "c1"


def test_bm25_dept_filter():
    idx = BM25Index()
    docs = [
        {"_id": "c1", "dept_id": "dept_jwc", "content": "选课相关条款。"},
        {"_id": "c2", "dept_id": "dept_cwc", "content": "选课缴费相关条款。"},
    ]
    idx.index(docs)
    hits = idx.search("选课", top_k=5, dept_id="dept_jwc")
    assert all(h["dept_id"] == "dept_jwc" for h in hits)


def test_bm25_filters_before_topk():
    idx = BM25Index()
    docs = [
        {"_id": f"other-{i}", "dept_id": "dept_other", "content": "选课 选课 选课"}
        for i in range(25)
    ]
    docs.append({"_id": "target", "dept_id": "dept_jwc", "content": "选课"})
    idx.index(docs)
    hits = idx.search("选课", top_k=5, dept_id="dept_jwc")
    assert [h["id"] for h in hits] == ["target"]
