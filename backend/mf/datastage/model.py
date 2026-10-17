"""Intermediate representation (doc 53). Later stages read this, never the DSX."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class SourceEvidence:
    source_file: str
    object_name: str
    record_type: str
    property_name: str
    raw_value: str
    line: int = 0


@dataclass
class DataStageParameter:
    name: str
    param_type: str            # DataStage ParamType code ("13" = parameter set)
    default: str | None = None
    prompt: str | None = None
    is_set: bool = False


@dataclass
class DataStageLink:
    name: str
    source_stage: str
    target_stage: str
    columns: list = field(default_factory=list)       # [{name, derivation?, key, nullable, sqlType}]
    expressions: dict = field(default_factory=dict)   # column -> derivation (Transformer outputs)
    properties: dict = field(default_factory=dict)    # pin properties (dataset, file, ...)
    constraint: str | None = None                     # Transformer output constraint
    trigger: dict | None = None                       # sequence links: {type, expression}


@dataclass
class DataStageStage:
    name: str
    stage_type: str
    record_type: str
    properties: dict = field(default_factory=dict)    # flattened, XML connector properties as Usage/SQL/... paths
    input_links: list = field(default_factory=list)
    output_links: list = field(default_factory=list)
    stage_vars: list = field(default_factory=list)    # Transformer: [{name, expression}]
    evidence: list = field(default_factory=list)


@dataclass
class DataStageJob:
    name: str
    job_type: str                                     # PARALLEL | SEQUENCE
    stages: list = field(default_factory=list)
    links: list = field(default_factory=list)
    parameters: list = field(default_factory=list)
    source_file: str = ''
    description: str = ''
    line: int = 0
    warnings: list = field(default_factory=list)

    def stage(self, name):
        return next((s for s in self.stages if s.name == name), None)


@dataclass
class DataStageProject:
    name: str
    jobs: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)      # header per file, versions, parse status, warnings

    @property
    def sequences(self):
        return [j for j in self.jobs if j.job_type == 'SEQUENCE']

    def job(self, name):
        return next((j for j in self.jobs if j.name == name), None)

    def to_dict(self) -> dict:
        return asdict(self) | {'sequences': [j.name for j in self.sequences]}

    @staticmethod
    def from_dict(d: dict) -> 'DataStageProject':
        jobs = []
        for j in d['jobs']:
            stages = [DataStageStage(**(s | {'evidence': [SourceEvidence(**e) for e in s['evidence']]})) for s in j['stages']]
            jobs.append(DataStageJob(**(j | {'stages': stages, 'links': [DataStageLink(**x) for x in j['links']],
                                             'parameters': [DataStageParameter(**p) for p in j['parameters']]})))
        return DataStageProject(name=d['name'], jobs=jobs, metadata=d['metadata'])
