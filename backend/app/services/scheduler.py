"""
scheduler.py — APScheduler-based background cron runner for evaluation schedules.
Reads EvaluationSchedule rows from the DB on startup and registers cron jobs.
"""

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session, select

from app.db.engine import engine
from app.models.models import (
    EvalRun,
    EvaluationSchedule,
    GoldenQuestion,
    Table,
)

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(job_defaults={"coalesce": True, "max_instances": 1})


def _run_scheduled_evaluation(schedule_id: str):
    """Execute all tables in a schedule's scope."""
    from app.routers.orchestration import _run_full_pipeline

    with Session(engine) as session:
        schedule = session.get(EvaluationSchedule, schedule_id)
        if not schedule or not schedule.enabled:
            return

        # Determine table scope
        if schedule.table_scope:
            table_ids = schedule.table_scope
        else:
            # All tables in system
            tables = session.exec(select(Table)).all()
            table_ids = [t.id for t in tables]

        if not table_ids:
            logger.warning(
                f"[Scheduler] Schedule {schedule_id} has no tables to evaluate"
            )
            return

        # Create runs
        run_ids = []
        valid_table_ids = []
        for table_id in table_ids:
            table = session.get(Table, table_id)
            if not table:
                continue
            questions = session.exec(
                select(GoldenQuestion).where(GoldenQuestion.table_id == table_id)
            ).all()
            if not questions:
                logger.info(f"[Scheduler] Skipping {table_id} — no questions")
                continue

            run = EvalRun(table_id=table_id, triggered_by="scheduler")
            session.add(run)
            session.commit()
            session.refresh(run)
            run_ids.append(run.id)
            valid_table_ids.append(table_id)

        # Update schedule timestamps
        schedule.last_run_at = datetime.utcnow()
        session.add(schedule)
        session.commit()

    if valid_table_ids:
        logger.info(
            f"[Scheduler] Running {len(valid_table_ids)} tables for schedule {schedule_id}"
        )
        _run_full_pipeline(valid_table_ids, run_ids, triggered_by="scheduler")


def _register_schedule(schedule: EvaluationSchedule):
    """Parse cron expression and register APScheduler job."""
    try:
        parts = schedule.cron_expression.split()
        if len(parts) == 5:
            minute, hour, day, month, day_of_week = parts
        else:
            logger.warning(
                f"Invalid cron expression: {schedule.cron_expression} — falling back to daily 2am"
            )
            minute, hour, day, month, day_of_week = "0", "2", "*", "*", "*"

        trigger = CronTrigger(
            minute=minute, hour=hour, day=day, month=month, day_of_week=day_of_week
        )
        scheduler.add_job(
            _run_scheduled_evaluation,
            trigger=trigger,
            args=[schedule.id],
            id=f"eval_schedule_{schedule.id}",
            replace_existing=True,
        )
        logger.info(
            f"[Scheduler] Registered schedule {schedule.id} [{schedule.cron_expression}]"
        )
    except Exception as e:
        logger.error(f"[Scheduler] Failed to register schedule {schedule.id}: {e}")


def start_scheduler():
    """Load all enabled schedules from DB and start the scheduler."""
    with Session(engine) as session:
        schedules = session.exec(
            select(EvaluationSchedule).where(EvaluationSchedule.enabled)
        ).all()
        for s in schedules:
            _register_schedule(s)

    if not scheduler.running:
        scheduler.start()
        logger.info(f"[Scheduler] Started with {len(scheduler.get_jobs())} jobs")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[Scheduler] Stopped")


def reload_schedule(schedule_id: str):
    """Re-register a single schedule (call after create/update)."""
    with Session(engine) as session:
        schedule = session.get(EvaluationSchedule, schedule_id)
        if schedule and schedule.enabled:
            _register_schedule(schedule)
        else:
            job_id = f"eval_schedule_{schedule_id}"
            if scheduler.get_job(job_id):
                scheduler.remove_job(job_id)


def remove_schedule(schedule_id: str):
    job_id = f"eval_schedule_{schedule_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
