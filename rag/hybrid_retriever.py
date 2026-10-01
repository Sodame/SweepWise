"""Chinese-aware BM25 + dense retrieval fused by weighted reciprocal rank."""
import hashlib
import re
import threading
import time
from langchain_core.documents import Document
from rank_bm25 import BM25Okapi


def tokenize(text):
    tokens = re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]', text.lower())
    for run in re.findall(r'[\u4e00-\u9fff]+', text):
        tokens.extend(run[i:i+2] for i in range(len(run)-1))
    return tokens or ['<empty>']


def document_key(doc):
    return hashlib.sha256((str(doc.metadata.get('source', '')) + ':' + str(doc.metadata.get('page', '')) + ':' + doc.page_content).encode()).hexdigest()


def fuse_results(dense, sparse, weights=(0.5, 0.5), limit=12):
    scores, documents = {}, {}
    for result, weight in zip((dense, sparse), weights):
        seen = set()
        for rank, doc in enumerate(result, 1):
            key = document_key(doc)
            if key in seen:
                continue
            seen.add(key)
            documents[key] = doc
            scores[key] = scores.get(key, 0) + weight / (60 + rank)
    return [Document(page_content=documents[key].page_content, metadata={**documents[key].metadata, 'rrf_score': scores[key]}) for key in sorted(scores, key=scores.get, reverse=True)[:limit]]


class HybridRetriever:
    def __init__(self, store, candidate_k=12):
        self.store = store
        self.candidate_k = candidate_k
        self._lock = threading.Lock()
        self._refreshed = 0
        self._documents = []
        self._bm25 = None

    def invalidate(self):
        with self._lock:
            self._refreshed = 0

    def _snapshot(self):
        with self._lock:
            if time.monotonic() - self._refreshed > 60:
                corpus = self.store.get(include=['documents', 'metadatas'])
                texts = corpus.get('documents') or []
                metadata = corpus.get('metadatas') or [{}] * len(texts)
                self._documents = [Document(page_content=text, metadata=meta or {}) for text, meta in zip(texts, metadata) if text and text.strip()]
                self._bm25 = BM25Okapi([tokenize(doc.page_content) for doc in self._documents]) if self._documents else None
                self._refreshed = time.monotonic()
            return self._documents, self._bm25

    def invoke(self, query):
        documents, bm25 = self._snapshot()
        if not documents:
            return []
        dense = self.store.similarity_search(query, k=self.candidate_k)
        tokens = tokenize(query)
        scores = bm25.get_scores(tokens)
        matches = [i for i, doc in enumerate(documents) if set(tokens).intersection(tokenize(doc.page_content))]
        matches.sort(key=lambda i: float(scores[i]), reverse=True)
        sparse = [documents[i] for i in matches[:self.candidate_k]]
        weights = (0.7, 0.3) if len(query) > 50 else (0.3, 0.7) if len(query) < 20 else (0.5, 0.5)
        return fuse_results(dense, sparse, weights, self.candidate_k)
