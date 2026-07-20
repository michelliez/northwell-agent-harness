"""Retrieval-backed documentation and schema-spelunking workflow.

Implementation is pending the RAG integration branch. The initial mock RAG
backend must implement the same retrieval contract as the eventual backend;
it must not reuse the fabricated data-catalog schemas.

Future flow:
    retrieve approved HTML chunks → bound context → answer → verify citations
"""
