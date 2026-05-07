"""RAG pipeline — semantic search over Malcolm field documentation.

Uses Ollama for embeddings and OpenSearch k-NN for vector storage.
Ingests field descriptions from Malcolm's /mapi/fields endpoint and
makes them searchable by natural language queries.
"""

import logging

import httpx

from .connections import (
    OPENSEARCH_URL as _OS_URL, OPENSEARCH_AUTH as _OS_AUTH, OPENSEARCH_VERIFY_SSL as _OS_VERIFY,
    OLLAMA_URL as _OLLAMA_URL, OLLAMA_EMBED_MODEL as _EMBED_MODEL,
    MALCOLM_API_VERIFY_SSL as _API_VERIFY,
)

logger = logging.getLogger(__name__)

# dimension depends on the model (llama3.1:8b is 4096)
_EMBED_DIM = 4096

_RAG_INDEX = 'malcolm_knowledge'


async def get_embedding(text, client=None):
    """Get embedding vector from Ollama."""
    should_close = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
    try:
        resp = await client.post(
            f"{_OLLAMA_URL}/api/embeddings",
            json={"model": _EMBED_MODEL, "prompt": text},
        )
        resp.raise_for_status()
        return resp.json()["embedding"]
    finally:
        if should_close:
            await client.aclose()


async def create_knowledge_index():
    """Create the k-NN index for Malcolm knowledge."""
    body = {
        "settings": {
            "index": {
                "knn": True,
                "knn.algo_param.ef_search": 100,
            },
            "number_of_shards": 1,
            "number_of_replicas": 0,
        },
        "mappings": {
            "properties": {
                "text": {"type": "text"},
                "field_name": {"type": "keyword"},
                "category": {"type": "keyword"},
                "source": {"type": "keyword"},
                "embedding": {
                    "type": "knn_vector",
                    "dimension": _EMBED_DIM,
                    "method": {
                        "name": "hnsw",
                        "space_type": "l2",
                        "engine": "lucene",
                    },
                },
            },
        },
    }

    async with httpx.AsyncClient(verify=_OS_VERIFY, auth=_OS_AUTH, timeout=15.0) as client:
        # Delete if exists
        await client.delete(f"{_OS_URL}/{_RAG_INDEX}")
        resp = await client.put(
            f"{_OS_URL}/{_RAG_INDEX}",
            json=body,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        logger.info("Created knowledge index: %s", _RAG_INDEX)
        return resp.json()


async def ingest_field_descriptions(malcolm_api_url, malcolm_auth=None,
                                    include_embeddings=False):
    """Ingest field descriptions from Malcolm's /mapi/fields endpoint.

    Args:
        malcolm_api_url: Malcolm API base URL (e.g. http://api:5000)
        malcolm_auth: (user, pass) tuple for Malcolm API
        include_embeddings: Whether to generate vector embeddings (slow)
    """
    async with httpx.AsyncClient(verify=_API_VERIFY, auth=malcolm_auth, timeout=30.0) as api_client:
        resp = await api_client.get(f"{malcolm_api_url}/mapi/fields")
        resp.raise_for_status()
        fields_data = resp.json()

    fields = fields_data.get("fields", [])
    if isinstance(fields, dict):
        field_list = []
        for name, info in fields.items():
            if isinstance(info, dict):
                field_list.append({"name": name, **info})
            else:
                field_list.append({"name": name, "type": str(info)})
        fields = field_list

    logger.info("Ingesting %d field descriptions (embeddings=%s)", len(fields), include_embeddings)

    embed_client = httpx.AsyncClient(timeout=60.0) if include_embeddings else None
    os_client = httpx.AsyncClient(verify=_OS_VERIFY, auth=_OS_AUTH, timeout=15.0)

    # Bulk ingest for speed
    bulk_body = []
    ingested = 0
    try:
        for field in fields:
            name = field.get("name", field.get("field", ""))
            if not name:
                continue

            field_type = field.get("type", "")
            description = field.get("description", "")
            text = f"Field: {name}"
            if field_type:
                text += f" (type: {field_type})"
            if description:
                text += f" — {description}"
            else:
                parts = name.replace(".", " ").replace("_", " ").split()
                text += f" — {' '.join(parts)}"

            doc = {
                "text": text,
                "field_name": name,
                "category": "field",
                "source": "mapi_fields",
            }

            if include_embeddings and embed_client:
                doc["embedding"] = await get_embedding(text, embed_client)

            # Bulk API format: action line + doc line
            bulk_body.append('{"index":{}}')
            import json
            bulk_body.append(json.dumps(doc))
            ingested += 1

            # Flush every 200 docs
            if len(bulk_body) >= 400:
                await _flush_bulk(os_client, bulk_body)
                bulk_body.clear()
                logger.info("Ingested %d/%d fields", ingested, len(fields))

        # Final flush
        if bulk_body:
            await _flush_bulk(os_client, bulk_body)

    finally:
        if embed_client:
            await embed_client.aclose()
        await os_client.aclose()

    # Refresh
    async with httpx.AsyncClient(verify=_OS_VERIFY, auth=_OS_AUTH, timeout=15.0) as client:
        await client.post(f"{_OS_URL}/{_RAG_INDEX}/_refresh")

    logger.info("Ingested %d field descriptions into %s", ingested, _RAG_INDEX)
    return ingested


async def _flush_bulk(client, bulk_lines):
    """Send a bulk index request."""
    body = "\n".join(bulk_lines) + "\n"
    resp = await client.post(
        f"{_OS_URL}/{_RAG_INDEX}/_bulk",
        content=body,
        headers={"Content-Type": "application/x-ndjson"},
    )
    resp.raise_for_status()


async def search_knowledge(query, k=5, method="text"):
    """Search the knowledge base using text matching or semantic search.

    Args:
        query: Natural language query
        k: Number of results to return
        method: Search method — 'text' (BM25, default) or 'vector' (k-NN)

    Returns:
        List of matching documents with scores
    """
    if method == "vector":
        embedding = await get_embedding(query)
        body = {
            "size": k,
            "query": {
                "knn": {
                    "embedding": {"vector": embedding, "k": k},
                },
            },
            "_source": ["text", "field_name", "category", "source"],
        }
    else:
        # BM25 text search — works reliably without a dedicated embedding model
        body = {
            "size": k,
            "query": {
                "bool": {
                    "should": [
                        {"match": {"text": {"query": query, "boost": 2}}},
                        {"match": {"field_name": {"query": query, "boost": 3}}},
                    ],
                },
            },
            "_source": ["text", "field_name", "category", "source"],
        }

    async with httpx.AsyncClient(verify=_OS_VERIFY, auth=_OS_AUTH, timeout=15.0) as client:
        resp = await client.post(
            f"{_OS_URL}/{_RAG_INDEX}/_search",
            json=body,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()

    return [
        {
            "text": hit["_source"]["text"],
            "field_name": hit["_source"].get("field_name", ""),
            "score": hit["_score"],
        }
        for hit in data.get("hits", {}).get("hits", [])
    ]


async def index_exists():
    """Check if the knowledge index exists."""
    async with httpx.AsyncClient(verify=_OS_VERIFY, auth=_OS_AUTH, timeout=5.0) as client:
        resp = await client.head(f"{_OS_URL}/{_RAG_INDEX}")
        return resp.status_code == 200
