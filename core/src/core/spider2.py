import json
import requests

SPIDER2_SNOW_JSONL_URL = (
    "https://raw.githubusercontent.com/xlang-ai/Spider2/main/"
    "spider2-snow/spider2-snow.jsonl"
)


def fetch_spider2_snow_sf_questions(
    url: str = SPIDER2_SNOW_JSONL_URL,
    timeout: float = 15.0,
) -> list[dict]:
    """Fetch Spider2-Snow JSONL benchmark questions from GitHub and return

    filtered questions whose instance_id starts with 'sf_'.
    """
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()

    sf_questions = []
    for line in resp.text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            q = json.loads(line)
        except json.JSONDecodeError:
            continue
        if q.get("instance_id", "").startswith("sf_"):
            sf_questions.append(q)

    return sf_questions
