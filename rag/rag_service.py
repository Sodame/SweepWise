from pathlib import Path
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from model.factory import chat_model
from rag.vector_store import VectorStoreService
from rag.reranker import reranker
from utils.config_handler import chroma_conf
from utils.prompt_loader import load_rag_prompts
from utils.logger_handler import logger
from utils.language import language_prompt


class RagSummarizeService:
    def __init__(self, language='zh'):
        self.language = language
        self.vector_store = VectorStoreService(language)
        self.retriever = self.vector_store.get_retriever()
        prompt = load_rag_prompts().replace('仅用中文回答', '使用指定的回复语言回答') + language_prompt({'language': language})
        self.chain = PromptTemplate.from_template(prompt) | chat_model | StrOutputParser()

    def retrieve(self, query):
        from rag.knowledge_storage import root_path, lock, recover, revision
        root = root_path()
        with lock(root, self.language):
            recover(root, self.language, self)
            current = revision(root, self.language)
            if current != getattr(self, '_knowledge_revision', ''):
                self.retriever.invalidate()
                self._knowledge_revision = current
            docs = self.retriever.invoke(query)
        warning = ''
        try:
            docs = reranker.rerank(query, docs, top_k=chroma_conf.get('k', 3))
        except Exception:
            logger.exception('BGE 重排序不可用，使用混合检索排序')
            warning = 'BGE 重排序暂不可用，本次使用 BM25 与向量混合检索结果。'
            docs = docs[:chroma_conf.get('k', 3)]
        return docs, warning

    def retriever_docs(self, query):
        return self.retrieve(query)[0]

    @staticmethod
    def sources(docs):
        return [{'title': Path(str(doc.metadata.get('source', '知识库'))).name,
                 'page': doc.metadata.get('page'), 'preview': doc.page_content[:260],
                 'language': doc.metadata.get('language'),
                 'rerank_score': doc.metadata.get('rerank_score')} for doc in docs]

    @staticmethod
    def context(docs, language='zh'):
        label = 'Source' if language == 'en' else '资料'
        return '\n\n'.join(f"[{label} {i}: {Path(str(doc.metadata.get('source', 'knowledge'))).name}]\n{doc.page_content}" for i, doc in enumerate(docs, 1))

    def rag_summarize(self, query):
        docs, _ = self.retrieve(query)
        if not docs:
            return 'No matching knowledge documents. Please import the English corpus.' if self.language == 'en' else '知识库暂无相关资料，请先导入知识文档。'
        return self.chain.invoke({'input': query, 'context': self.context(docs, self.language)})
