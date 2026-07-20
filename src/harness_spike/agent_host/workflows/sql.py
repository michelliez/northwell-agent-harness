"""Future retrieval-backed SQL workflow.

Implementation is pending the RAG integration branch. It will consume
``retrieve_documentation`` and ``resolve_schema_evidence`` from
``agent_host.retrieval`` and must validate SQL against a cited
``SchemaSnapshot``.

The current fabricated catalog → SQL chain lives in ``legacy_mock.py`` and is
a deletion target once this workflow is enabled. This module must never import
``mcp_servers.data_catalog.TABLES`` or legacy catalog result models.
"""
