import logging

from sqlmodel import Session, select

from core.db.engine import engine
from core.models.models import ColumnProfile, CrossTableProfile

logger = logging.getLogger(__name__)


def discover_joins_for_table(table_id: str):
    """
    Finds potential joins between the given table and all other profiled tables.
    Uses basic heuristics on column names and types.
    """
    with Session(engine) as session:
        # Get columns of the source table
        source_cols = session.exec(
            select(ColumnProfile).where(ColumnProfile.table_id == table_id)
        ).all()
        if not source_cols:
            return []

        # Get all other columns not belonging to this table
        target_cols = session.exec(
            select(ColumnProfile).where(ColumnProfile.table_id != table_id)
        ).all()

        # Group target columns by table
        targets_by_table = {}
        for tc in target_cols:
            targets_by_table.setdefault(tc.table_id, []).append(tc)

        suggestions = []
        for target_id, t_cols in targets_by_table.items():
            common = []
            for sc in source_cols:
                for tc in t_cols:
                    if (
                        sc.column_name == tc.column_name
                        and sc.data_type == tc.data_type
                    ):
                        common.append(sc.column_name)
                    elif (
                        sc.column_name.endswith("id")
                        and sc.column_name == tc.column_name
                    ):
                        common.append(sc.column_name)
                    elif sc.column_name == "custkey" and tc.column_name == "custkey":
                        common.append(sc.column_name)
                    elif sc.column_name == "orderkey" and tc.column_name == "orderkey":
                        common.append(sc.column_name)

            # unique
            common = list(set(common))
            if common:
                match_strength = (
                    "strong"
                    if len(common) > 1 or any("key" in c or "id" in c for c in common)
                    else "weak"
                )
                suggestion = f"source.{common[0]} = target.{common[0]}"

                existing = session.exec(
                    select(CrossTableProfile).where(
                        CrossTableProfile.source_table_id == table_id,
                        CrossTableProfile.target_table_id == target_id,
                    )
                ).first()

                if existing:
                    existing.common_columns = common
                    existing.match_strength = match_strength
                    existing.join_suggestion = suggestion
                    session.add(existing)
                    suggestions.append(existing)
                else:
                    new_cross = CrossTableProfile(
                        source_table_id=table_id,
                        target_table_id=target_id,
                        join_suggestion=suggestion,
                        match_strength=match_strength,
                        common_columns=common,
                    )
                    session.add(new_cross)
                    suggestions.append(new_cross)

        session.commit()
        for s in suggestions:
            session.refresh(s)
        return suggestions
