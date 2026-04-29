import uuid
import time
import random
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlmodel import Session, select
from app.db.engine import get_session, engine
from app.models.models import (
    EvalRun, EvalRunRead, EvalResult, EvalResultRead,
    GoldenQuestion, Table, EvalStatus
)

router = APIRouter(tags=["evaluation"])

def background_eval_task(run_id: str):
    time.sleep(5)  # Simulate long-running eval
    with Session(engine) as session:
        run = session.get(EvalRun, run_id)
        if run:
            results = session.exec(select(EvalResult).where(EvalResult.run_id == run_id)).all()
            total_score = 0
            for r in results:
                # Mock random score
                r.score = random.choice([0.0, 0.5, 1.0])
                r.status = "pass" if r.score >= 0.5 else "fail"
                r.error_type = None if r.status == "pass" else random.choice(["syntax_error", "hallucination", "schema_mismatch"])
                session.add(r)
                total_score += r.score
            
            run.score = total_score / len(results) if results else 0.0
            run.status = EvalStatus.completed
            session.add(run)
            session.commit()

@router.post("/tables/{table_id}/eval/run", response_model=EvalRunRead, status_code=201)
def trigger_eval_run(table_id: str, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    questions = session.exec(
        select(GoldenQuestion).where(GoldenQuestion.table_id == table_id)
    ).all()

    run = EvalRun(table_id=table_id, status=EvalStatus.running, score=0.0)
    session.add(run)
    session.flush()

    # Stub results — real evaluation is external
    results = []
    for q in questions:
        result = EvalResult(
            run_id=run.id,
            question_id=q.id,
            score=0.0,
            status="fail",
            error_type="not_evaluated",
        )
        session.add(result)
        results.append(result)

    session.commit()
    session.refresh(run)
    
    background_tasks.add_task(background_eval_task, run.id)
    
    return run


@router.get("/eval/runs/all", response_model=list[EvalRunRead])
def get_all_eval_runs(session: Session = Depends(get_session)):
    return session.exec(
        select(EvalRun).order_by(EvalRun.created_at.desc()).limit(100)
    ).all()


@router.get("/tables/{table_id}/eval/runs", response_model=list[EvalRunRead])
def get_table_eval_runs(table_id: str, session: Session = Depends(get_session)):
    return session.exec(
        select(EvalRun).where(EvalRun.table_id == table_id).order_by(EvalRun.created_at.desc())
    ).all()


@router.get("/eval/{run_id}", response_model=EvalRunRead)
def get_eval_run(run_id: str, session: Session = Depends(get_session)):
    run = session.get(EvalRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Eval run not found")
    return run


@router.get("/eval/{run_id}/results", response_model=list[EvalResultRead])
def get_eval_results(run_id: str, session: Session = Depends(get_session)):
    return session.exec(
        select(EvalResult).where(EvalResult.run_id == run_id)
    ).all()
