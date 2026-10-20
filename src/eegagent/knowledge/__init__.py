"""Knowledge layer: notes + papers + graph, hybrid retrieval with graph expansion.

Public API (also re-exported by eegagent.worker.knowledge for backward compatibility):
    index() / reload()            singleton KnowledgeIndex
    KnowledgeIndex.search(...)    -> {"chunks", "facts", "entities"}
    KnowledgeGraph                entities / relations / neighbors / path
    tokenize(), knowledge_dir()
"""

from .chunking import Chunk, chunk_markdown, knowledge_dir, split_frontmatter
from .graph import KnowledgeGraph
from .index import KnowledgeIndex, index, reload, search_knowledge
from .tokenize import tokenize

__all__ = ["Chunk", "KnowledgeGraph", "KnowledgeIndex", "chunk_markdown", "index", "knowledge_dir", "reload",
           "search_knowledge", "split_frontmatter", "tokenize"]
