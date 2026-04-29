import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ThumbsUp, ThumbsDown, MessageSquare, X, Check } from "lucide-react";
import { feedbackApi } from "../../api/client";
import type { AuditQuery } from "../../types";

interface Props {
  query: AuditQuery;
  tableId?: string;
}

export function FeedbackWidget({ query, tableId }: Props) {
  const [voted, setVoted] = useState<"positive" | "negative" | null>(null);
  const [showComment, setShowComment] = useState(false);
  const [comment, setComment] = useState("");
  const [correction, setCorrection] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const mutation = useMutation({
    mutationFn: (rating: "positive" | "negative") =>
      feedbackApi.submit({
        user_id: "user-1",
        query_id: query.id,
        table_id: tableId,
        rating,
        comment: comment || undefined,
        suggested_correction: correction || undefined,
      }),
    onSuccess: () => { setSubmitted(true); setShowComment(false); },
  });

  if (submitted) {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 6, color: "var(--status-production)", fontSize: 12 }}>
        <Check size={14} /><span>Thanks for your feedback!</span>
      </div>
    );
  }

  const btnStyle = (active: boolean, activeColor: string) => ({
    display: "flex" as const, alignItems: "center" as const, gap: 4,
    padding: "4px 10px", borderRadius: 6, border: "1px solid",
    cursor: "pointer" as const, fontSize: 12, fontWeight: 600,
    borderColor: active ? activeColor : "var(--border)",
    background: active ? `${activeColor}18` : "transparent",
    color: active ? activeColor : "var(--text-muted)",
    transition: "all 0.15s",
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Was this helpful?</span>
        <button style={btnStyle(voted === "positive", "var(--status-production)")}
          onClick={() => { setVoted("positive"); mutation.mutate("positive"); }}
          disabled={mutation.isPending}>
          <ThumbsUp size={13} /> Yes
        </button>
        <button style={btnStyle(voted === "negative", "var(--status-degraded)")}
          onClick={() => { setVoted("negative"); setShowComment(true); }}
          disabled={mutation.isPending}>
          <ThumbsDown size={13} /> No
        </button>
        <button onClick={() => setShowComment(!showComment)}
          style={{ display: "flex", alignItems: "center", gap: 4, padding: "4px 10px", borderRadius: 6, border: "1px solid var(--border)", cursor: "pointer", fontSize: 12, color: "var(--text-muted)", background: "transparent" }}>
          <MessageSquare size={13} /> Comment
        </button>
      </div>

      {showComment && (
        <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 8, padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)" }}>Feedback</span>
            <button onClick={() => setShowComment(false)} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-muted)" }}><X size={14} /></button>
          </div>
          <textarea value={comment} onChange={(e) => setComment(e.target.value)}
            placeholder="What went wrong? (optional)"
            style={{ background: "var(--bg-base)", border: "1px solid var(--border)", borderRadius: 6, padding: "8px 10px", fontSize: 12, color: "var(--text)", resize: "vertical", minHeight: 60, fontFamily: "inherit" }} />
          <textarea value={correction} onChange={(e) => setCorrection(e.target.value)}
            placeholder="Suggest correction — paste expected SQL here (optional)"
            style={{ background: "var(--bg-base)", border: "1px solid var(--border)", borderRadius: 6, padding: "8px 10px", fontSize: 12, color: "var(--text)", resize: "vertical", minHeight: 50, fontFamily: "monospace" }} />
          <button className="btn btn--primary btn--sm" onClick={() => mutation.mutate(voted ?? "negative")} disabled={mutation.isPending}>
            {mutation.isPending ? "Submitting…" : "Submit Feedback"}
          </button>
        </div>
      )}
    </div>
  );
}
