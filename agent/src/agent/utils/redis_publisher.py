import json
import logging
from agent.config import settings

logger = logging.getLogger(__name__)

_sync_redis = None

def get_sync_redis():
    global _sync_redis
    if _sync_redis is None:
        import redis
        _sync_redis = redis.from_url(
            settings.REDIS_URL,
            socket_timeout=2,
            socket_connect_timeout=2,
        )
    return _sync_redis

async def publish_node_event(thread_id: str, node: str, status: str = "active"):
    """
    Publish an execution path event to Redis so the backend can stream it via SSE.
    """
    if not thread_id:
        return
        
    try:
        from python_core_utils.redis import get_redis_client
        redis_client = get_redis_client()
        if redis_client:
            payload = json.dumps({"thread_id": thread_id, "node": node, "status": status})
            await redis_client.publish(f"agent_stream:{thread_id}", payload)
    except Exception as e:
        logger.warning(f"Failed to publish node event to Redis: {e}")

def publish_node_event_sync(thread_id: str, node: str, status: str = "active"):
    if not thread_id:
        return
    try:
        r = get_sync_redis()
        payload = json.dumps({"thread_id": thread_id, "node": node, "status": status})
        r.publish(f"agent_stream:{thread_id}", payload)
    except Exception as e:
        logger.warning(f"Failed to publish sync node event to Redis: {e}")
