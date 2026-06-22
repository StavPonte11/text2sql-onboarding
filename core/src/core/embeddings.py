import json
import logging
import urllib.request

logger = logging.getLogger(__name__)

EXPECTED_EMBEDDING_DIM = 768

def get_embedding(
    text: str,
    embedder_url: str,
    embedder_model: str,
    embedder_key: str | None = None
) -> list[float] | None:
    data = json.dumps({"model": embedder_model, "input": text}).encode()
    headers = {"Content-Type": "application/json"}
    if embedder_key:
        headers["Authorization"] = f"Bearer {embedder_key}"
        
    req = urllib.request.Request(embedder_url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            resp_data = json.loads(res.read().decode())["data"][0]
            embedding = resp_data.get("embedding")
            if not embedding or len(embedding) != EXPECTED_EMBEDDING_DIM:
                raise ValueError(
                    f"Embedder returned embedding of length {len(embedding) if embedding else 'None'}, "
                    f"expected {EXPECTED_EMBEDDING_DIM}"
                )
            return embedding
    except Exception as e:
        logger.warning(f"Failed to generate embedding: {e}")
    return None
