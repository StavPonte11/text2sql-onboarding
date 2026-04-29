import type { TableStatus } from "../../types";

const LABELS: Record<TableStatus, string> = {
  draft: "Draft",
  sandbox: "Sandbox",
  verified: "Verified",
  production: "Production",
  degraded: "Degraded",
};

interface Props { status: TableStatus }

export function StatusBadge({ status }: Props) {
  return (
    <span className={`badge badge--${status}`}>
      {LABELS[status]}
    </span>
  );
}
