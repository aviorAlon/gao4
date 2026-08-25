"""Public knowledge registry, partitioned by stage, subject and province."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import yaml

DEFAULT_KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / 'data' / 'knowledge'
KNOWLEDGE_PATH = DEFAULT_KNOWLEDGE_DIR / 'high_math_guangdong_v1.yaml'
POINT_FIELDS = {'knowledge_id','name','chapter','keywords','confidence','parent','dependencies','grade_scope','textbook_chapter'}
PARTITION_FIELDS = ('stage', 'subject', 'province', 'curriculum_year', 'academic_year_range')

PartitionKey = tuple[str, str, str, int]
_PARTITION_CACHE: dict[Path, tuple[tuple, dict[PartitionKey, 'KnowledgePartition']]] = {}


class KnowledgePartitionNotFound(LookupError):
    """Raised when no public knowledge partition covers the requested scope."""

    def __init__(self, stage: str, subject: str, province: str, curriculum_year: int | None = None):
        self.stage, self.subject, self.province, self.curriculum_year = stage, subject, province, curriculum_year
        super().__init__(f'no knowledge partition for {stage}/{subject}/{province}/{curriculum_year or "latest"}')


@dataclass(frozen=True)
class KnowledgePartition:
    stage: str
    subject: str
    province: str
    curriculum_year: int
    academic_year_range: tuple[str, ...]
    version: str
    curriculum: str | None
    path: Path
    points: tuple[dict[str, Any], ...]

    @property
    def key(self) -> PartitionKey:
        return (self.stage, self.subject, self.province, self.curriculum_year)

    def covers_grade(self, school_grade: str) -> bool:
        return school_grade in self.academic_year_range

    def describe(self) -> dict[str, Any]:
        return {
            'stage': self.stage, 'subject': self.subject, 'province': self.province,
            'curriculum_year': self.curriculum_year, 'academic_year_range': list(self.academic_year_range),
            'version': self.version, 'curriculum': self.curriculum,
            'knowledge_point_count': len(self.points), 'resource': self.path.name,
        }


def knowledge_dir() -> Path:
    return Path(os.getenv('EXAM_DIAGNOSIS_KNOWLEDGE_DIR') or DEFAULT_KNOWLEDGE_DIR)


def _validate_payload(payload: Any) -> tuple[str, list[dict[str, Any]]]:
    if not isinstance(payload, dict) or not isinstance(payload.get('version'), str) or not payload['version']:
        raise ValueError('knowledge schema: version required')
    points = payload.get('knowledge_points')
    if not isinstance(points, list) or not points:
        raise ValueError('knowledge schema: knowledge_points required')
    ids = set()
    for point in points:
        missing = POINT_FIELDS - set(point)
        if missing:
            raise ValueError(f'knowledge schema missing: {sorted(missing)}')
        if point['knowledge_id'] in ids:
            raise ValueError('knowledge schema duplicate knowledge_id')
        ids.add(point['knowledge_id'])
    for point in points:
        for dependency in point['dependencies']:
            if dependency not in ids:
                raise ValueError(f'knowledge dependency not found: {dependency}')
    return payload['version'], points


def load_versioned_knowledge(path: str | Path = KNOWLEDGE_PATH) -> tuple[str, list[dict[str, Any]]]:
    return _validate_payload(yaml.safe_load(Path(path).read_text(encoding='utf-8')))


def load_partition(path: str | Path) -> KnowledgePartition:
    path = Path(path)
    payload = yaml.safe_load(path.read_text(encoding='utf-8'))
    version, points = _validate_payload(payload)
    missing = [field for field in PARTITION_FIELDS if not payload.get(field)]
    if missing:
        raise ValueError(f'knowledge partition missing: {missing} in {path.name}')
    if not isinstance(payload['academic_year_range'], list):
        raise ValueError(f'knowledge partition academic_year_range must be a list in {path.name}')
    covered = set(payload['academic_year_range'])
    outside = sorted({point['grade_scope'] for point in points} - covered)
    if outside:
        raise ValueError(f'knowledge points outside academic_year_range {outside} in {path.name}')
    return KnowledgePartition(
        stage=payload['stage'], subject=payload['subject'], province=payload['province'],
        curriculum_year=int(payload['curriculum_year']),
        academic_year_range=tuple(payload['academic_year_range']),
        version=version, curriculum=payload.get('curriculum'), path=path,
        points=tuple(points),
    )


def load_partitions(directory: str | Path | None = None) -> dict[PartitionKey, KnowledgePartition]:
    root = (Path(directory) if directory is not None else knowledge_dir()).resolve()
    resources = sorted(root.glob('*.yaml')) + sorted(root.glob('*.yml')) if root.is_dir() else []
    signature = tuple((item.name, item.stat().st_mtime_ns, item.stat().st_size) for item in resources)
    cached = _PARTITION_CACHE.get(root)
    if cached and cached[0] == signature:
        return cached[1]
    partitions: dict[tuple[str, str, str], KnowledgePartition] = {}
    for resource in resources:
        partition = load_partition(resource)
        if partition.key in partitions:
            raise ValueError(f'duplicate knowledge partition {partition.key} in {resource.name}')
        partitions[partition.key] = partition
    _PARTITION_CACHE[root] = (signature, partitions)
    return partitions


def available_partitions(directory: str | Path | None = None) -> list[dict[str, Any]]:
    return [partition.describe() for partition in load_partitions(directory).values()]


def find_partition(stage: str, subject: str, province: str, curriculum_year: int | None = None, directory: str | Path | None = None) -> KnowledgePartition | None:
    """Resolve a partition; without an explicit curriculum_year the newest course scheme wins."""
    candidates = [item for item in load_partitions(directory).values() if (item.stage, item.subject, item.province) == (stage, subject, province)]
    if curriculum_year is not None:
        candidates = [item for item in candidates if item.curriculum_year == curriculum_year]
    return max(candidates, key=lambda item: item.curriculum_year, default=None)


def require_partition(stage: str, subject: str, province: str, curriculum_year: int | None = None, directory: str | Path | None = None) -> KnowledgePartition:
    partition = find_partition(stage, subject, province, curriculum_year, directory)
    if partition is None:
        raise KnowledgePartitionNotFound(stage, subject, province, curriculum_year)
    return partition


def curriculum_path(knowledge_id: str, partition: KnowledgePartition | None = None) -> list[dict[str, Any]]:
    if partition is None:
        partition = next((item for item in load_partitions().values() if any(point['knowledge_id'] == knowledge_id for point in item.points)), None)
        if partition is None:
            return []
    by_id = {item['knowledge_id']: item for item in partition.points}
    if knowledge_id not in by_id:
        return []
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()

    def visit(item_id: str):
        if item_id in seen or item_id not in by_id:
            return
        seen.add(item_id)
        for dependency in by_id[item_id]['dependencies']:
            visit(dependency)
        ordered.append(by_id[item_id])

    visit(knowledge_id)
    return ordered


def match_knowledge_points(*, stage: str, subject: str, province: str, question_text: str, curriculum_year: int | None = None) -> list[dict[str, Any]]:
    partition = require_partition(stage, subject, province, curriculum_year)
    matches = []
    for item in partition.points:
        hits = [keyword for keyword in item.get('keywords', []) if keyword in question_text]
        if not hits:
            continue
        matches.append({
            'knowledge_id': item['knowledge_id'], 'name': item['name'],
            'chapter': item['chapter'], 'confidence': item['confidence'],
            'matched_keywords': hits, 'source': 'public_registry',
            'knowledge_base_version': partition.version,
            'knowledge_partition': {'stage': partition.stage, 'subject': partition.subject, 'province': partition.province, 'curriculum_year': partition.curriculum_year},
            'parent': item['parent'], 'dependencies': item['dependencies'],
            'grade_scope': item['grade_scope'], 'textbook_chapter': item['textbook_chapter'],
            'curriculum_path': [entry['knowledge_id'] for entry in curriculum_path(item['knowledge_id'], partition)],
        })
    return sorted(matches, key=lambda item: item['confidence'], reverse=True)
