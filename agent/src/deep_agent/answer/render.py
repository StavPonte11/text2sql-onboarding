"""
The user-facing answer: Hebrew explanation, assumptions, a table built from Trino's rows (never from
model text), warnings, and the SQL.
"""

from typing import Any


def _cell(value: Any) -> str:
    return "" if value is None else str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(final_result: dict[str, Any]) -> str:
    """User-facing Hebrew answer: explanation, table from real rows, SQL block."""
    answer = final_result.get("answer") or {}
    if final_result.get("status") == "clarification":
        return answer.get("needs_clarification") or answer.get("explanation_he", "")
    parts = [answer.get("explanation_he", "")]
    if answer.get("assumptions"):
        parts.append("**הנחות:**\n" + "\n".join(f"- {a}" for a in answer["assumptions"]))

    result = final_result.get("result")
    if result:
        header = "| " + " | ".join(_cell(c) for c in result["columns"]) + " |"
        sep = "|" + "---|" * len(result["columns"])
        body = ["| " + " | ".join(_cell(v) for v in row) + " |" for row in result["rows"]]
        parts.append("\n".join([header, sep, *body]))
        if result.get("truncated"):
            parts.append(f"_מוצגות {result['row_count']} השורות הראשונות בלבד._")
    elif final_result.get("status") == "no_answer":
        reason = final_result.get("error")
        parts.append("⚠️ לא התקבלה תשובה סופית" + (f": {reason}" if reason else "."))
    elif final_result.get("error"):
        parts.append(f"⚠️ לא ניתן היה להריץ את השאילתה הסופית: {final_result['error']}")

    review = final_result.get("semantic_validation") or {}
    if review.get("verdict") in ("failed", "disputed") and review.get("issues"):
        title = "בדיקה עצמאית מצאה בעיות בתשובה" if review["verdict"] == "failed" else "בדיקה עצמאית העלתה הסתייגות"
        parts.append(f"⚠️ **{title}:**\n" + "\n".join(f"- {i['problem']}" for i in review["issues"]))
        if review["verdict"] == "disputed" and review.get("response"):
            parts.append(f"**תגובת הסוכן:** {review['response']}")

    if final_result.get("sql"):
        parts.append(f"```sql\n{final_result['sql']}\n```")
    return "\n\n".join(p for p in parts if p)
