# Roadmap & TODOs

Here is the projected deployment scope for following iterations regarding structural reliability and automated benchmarking.

## 🔜 Next Steps

### 1. TTS-602: Schema Drift Detection (Airflow)
- **Problem**: Table formats in raw datalakes drift silently over time.
- **Action**: Schedule periodic Apache Airflow DAGs targeting upstream databases.
- **Workflow**:
  - Map current registered metadata.
  - Diff schema column changes.
  - Toggle table access state to `degraded` if continuous integrations fail.

### 2. TTS-302: Sandbox Evaluation Runner (Langfuse)
- **Problem**: Testing suites utilize mocked completion schemas.
- **Action**: Implement native scoring with specialized vector checkpoints.
- **Workflow**:
  - Connect prompts directly using Langfuse APIs.
  - Evaluate accuracy thresholds comprehensively over targeted queries.

### 1. Stability & Scaling
- **[MANDATORY] Alembic Migration**: Replace `migrate.py` with standard Alembic migrations for production schema management.
- **[INFRA] Docker Integration**: Add Trino (Coordinator/Worker) and Minio (S3 storage) to the Docker Compose stack.

### 2. Data Seeding
- **Real Data Injection**: Update seeding scripts to populate Minio/Trino with TPC-H or real production-representative datasets for testing the profiling engine.

### 3. Verification & Testing
- **E2E Validation**: Verify the full chain: Trino Data -> Profiling Engine -> Postgres Store -> LLM Context.
- **Join Detection**: Implement cross-table relationship discovery based on profiling statistics.