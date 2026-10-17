# 53 — Intermediate Representation

Separar DSX del motor.

```text
DSX → Parser → IR → Analyzer/Planner/Generator
```

Modelos:

```python
@dataclass
class DataStageProject:
    name: str
    jobs: list
    sequences: list
    metadata: dict

@dataclass
class DataStageJob:
    name: str
    job_type: str
    stages: list
    links: list
    parameters: list
    source_file: str

@dataclass
class DataStageStage:
    name: str
    stage_type: str
    properties: dict
    input_links: list
    output_links: list
    evidence: list

@dataclass
class DataStageLink:
    name: str
    source_stage: str
    target_stage: str
    columns: list
    expressions: dict
```

También:
- DataStageSequence
- DataStageParameter
- DataStageConnection
- SourceEvidence

Persistir IR para reproducibilidad, UI, retry y debugging.
