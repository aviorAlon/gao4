"""Knowledge registry: public partitions (YAML files shipped with the product, partitioned by
stage / subject / province / curriculum year) plus per-user partitions maintained through the API.

Storage of user partitions belongs to app.py (they live in the same SQLite as everything else);
this module only knows how to ask for them (`set_user_partition_provider`) and how to fold them
into resolution: a user's partition for a scope takes precedence over the public one for the same
scope, and may either replace it outright or extend it (public points as the base, the user's
points added on top / overriding by knowledge_id).
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
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

# app.py plugs in "give me this user's raw knowledge-base records" here; None means public only.
_USER_PARTITION_PROVIDER: Callable[[str], list[dict[str, Any]]] | None = None


def set_user_partition_provider(provider: Callable[[str], list[dict[str, Any]]] | None) -> None:
    global _USER_PARTITION_PROVIDER
    _USER_PARTITION_PROVIDER = provider


class KnowledgePartitionNotFound(LookupError):
    """Raised when no knowledge partition (public or the user's own) covers the requested scope."""

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
    path: Path | None
    points: tuple[dict[str, Any], ...]
    source: str = 'public'  # 'public' | 'user'
    partition_id: str | None = None  # user partitions: the record id
    owner_id: str | None = None
    name: str | None = None
    extends_public: bool = False
    base_version: str | None = None  # version of the public partition a user partition extends

    @property
    def key(self) -> PartitionKey:
        return (self.stage, self.subject, self.province, self.curriculum_year)

    def covers_grade(self, school_grade: str) -> bool:
        return school_grade in self.academic_year_range

    def describe(self) -> dict[str, Any]:
        described = {
            'stage': self.stage, 'subject': self.subject, 'province': self.province,
            'curriculum_year': self.curriculum_year, 'academic_year_range': list(self.academic_year_range),
            'version': self.version, 'curriculum': self.curriculum,
            'knowledge_point_count': len(self.points), 'resource': self.path.name if self.path else None,
            'source': self.source,
        }
        if self.source == 'user':
            described.update({'id': self.partition_id, 'name': self.name, 'extends_public': self.extends_public, 'base_version': self.base_version,
                              'user_point_count': sum(1 for point in self.points if point.get('origin') == 'user')})
        return described


def knowledge_dir() -> Path:
    return Path(os.getenv('EXAM_DIAGNOSIS_KNOWLEDGE_DIR') or DEFAULT_KNOWLEDGE_DIR)


def validate_points(points: Any, *, known_ids: set[str] | frozenset[str] = frozenset(), allow_empty: bool = False) -> list[dict[str, Any]]:
    """Schema-check a list of knowledge points. `known_ids` are ids that exist outside this list
    (the public base a user partition extends) and may be referenced as parent/dependencies."""
    if not isinstance(points, list) or (not points and not allow_empty):
        raise ValueError('knowledge schema: knowledge_points required')
    ids: set[str] = set()
    for point in points:
        if not isinstance(point, dict):
            raise ValueError('knowledge schema: each knowledge point must be a mapping')
        missing = POINT_FIELDS - set(point)
        if missing:
            raise ValueError(f'knowledge schema missing: {sorted(missing)}')
        if not isinstance(point['knowledge_id'], str) or not point['knowledge_id'].strip():
            raise ValueError('knowledge schema: knowledge_id must be a non-empty string')
        if not isinstance(point['keywords'], list) or not isinstance(point['dependencies'], list):
            raise ValueError(f"knowledge schema: keywords/dependencies must be lists ({point['knowledge_id']})")
        if isinstance(point['confidence'], bool) or not isinstance(point['confidence'], (int, float)) or not 0 <= point['confidence'] <= 1:
            raise ValueError(f"knowledge schema: confidence must be a number in [0, 1] ({point['knowledge_id']})")
        if point['knowledge_id'] in ids:
            raise ValueError('knowledge schema duplicate knowledge_id')
        ids.add(point['knowledge_id'])
    # `parent` is a grouping label (the shipped registry uses ids like high.math.function that
    # have no point of their own), so only dependencies must resolve.
    resolvable = ids | set(known_ids)
    for point in points:
        for dependency in point['dependencies']:
            if dependency not in resolvable:
                raise ValueError(f'knowledge dependency not found: {dependency}')
    return points


def _validate_payload(payload: Any) -> tuple[str, list[dict[str, Any]]]:
    if not isinstance(payload, dict) or not isinstance(payload.get('version'), str) or not payload['version']:
        raise ValueError('knowledge schema: version required')
    return payload['version'], validate_points(payload.get('knowledge_points'))


def load_versioned_knowledge(path: str | Path = KNOWLEDGE_PATH) -> tuple[str, list[dict[str, Any]]]:
    return _validate_payload(yaml.safe_load(Path(path).read_text(encoding='utf-8')))


def _check_grade_scope(points: list[dict[str, Any]], academic_year_range: tuple[str, ...], where: str) -> None:
    outside = sorted({point['grade_scope'] for point in points} - set(academic_year_range))
    if outside:
        raise ValueError(f'knowledge points outside academic_year_range {outside} in {where}')


def load_partition(path: str | Path) -> KnowledgePartition:
    path = Path(path)
    payload = yaml.safe_load(path.read_text(encoding='utf-8'))
    version, points = _validate_payload(payload)
    missing = [field for field in PARTITION_FIELDS if not payload.get(field)]
    if missing:
        raise ValueError(f'knowledge partition missing: {missing} in {path.name}')
    if not isinstance(payload['academic_year_range'], list):
        raise ValueError(f'knowledge partition academic_year_range must be a list in {path.name}')
    _check_grade_scope(points, tuple(payload['academic_year_range']), path.name)
    return KnowledgePartition(
        stage=payload['stage'], subject=payload['subject'], province=payload['province'],
        curriculum_year=int(payload['curriculum_year']),
        academic_year_range=tuple(payload['academic_year_range']),
        version=version, curriculum=payload.get('curriculum'), path=path,
        points=tuple(points),
    )


def load_public_partitions(directory: str | Path | None = None) -> dict[PartitionKey, KnowledgePartition]:
    root = (Path(directory) if directory is not None else knowledge_dir()).resolve()
    resources = sorted(root.glob('*.yaml')) + sorted(root.glob('*.yml')) if root.is_dir() else []
    signature = tuple((item.name, item.stat().st_mtime_ns, item.stat().st_size) for item in resources)
    cached = _PARTITION_CACHE.get(root)
    if cached and cached[0] == signature:
        return cached[1]
    partitions: dict[PartitionKey, KnowledgePartition] = {}
    for resource in resources:
        partition = load_partition(resource)
        if partition.key in partitions:
            raise ValueError(f'duplicate knowledge partition {partition.key} in {resource.name}')
        partitions[partition.key] = partition
    _PARTITION_CACHE[root] = (signature, partitions)
    return partitions


def merge_points(base_points: tuple[dict[str, Any], ...] | list[dict[str, Any]], user_points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Public base first, then the user's points — a user point with a public knowledge_id
    replaces that public point (keeping its position), everything else is appended. Every point
    is tagged with its origin so matches can say which knowledge base they came from."""
    merged: dict[str, dict[str, Any]] = {point['knowledge_id']: {**point, 'origin': 'public'} for point in base_points}
    for point in user_points:
        merged[point['knowledge_id']] = {**point, 'origin': 'user'}
    return list(merged.values())


def user_partition_from_record(record: dict[str, Any], public: dict[PartitionKey, KnowledgePartition] | None = None) -> KnowledgePartition:
    """Build (and fully validate) a user partition from its stored/submitted record. Raises
    ValueError with a human-readable reason on any schema problem — the API turns that into 422."""
    if public is None:
        public = load_public_partitions()
    missing = [field for field in ('stage', 'subject', 'province', 'curriculum_year') if not record.get(field)]
    if missing:
        raise ValueError(f'knowledge partition missing: {missing}')
    key: PartitionKey = (record['stage'], record['subject'], record['province'], int(record['curriculum_year']))
    base = public.get(key) if record.get('extends_public') else None
    if record.get('extends_public') and base is None:
        raise ValueError(f'public partition not found for {key[0]}/{key[1]}/{key[2]}/{key[3]}')
    user_points = validate_points(record.get('knowledge_points') or [], known_ids={point['knowledge_id'] for point in base.points} if base else frozenset(), allow_empty=True)
    academic_year_range = tuple(record.get('academic_year_range') or (base.academic_year_range if base else ()))
    if not academic_year_range:
        raise ValueError('knowledge partition missing: academic_year_range')
    where = record.get('name') or record.get('id') or 'user partition'
    _check_grade_scope(user_points, academic_year_range, where)
    points = merge_points(base.points if base else (), user_points)
    return KnowledgePartition(
        stage=key[0], subject=key[1], province=key[2], curriculum_year=key[3],
        academic_year_range=academic_year_range,
        version=record.get('version') or (base.version if base else '1.0.0'),
        curriculum=record.get('curriculum') or (base.curriculum if base else None),
        path=None, points=tuple(points), source='user',
        partition_id=record.get('id'), owner_id=record.get('user_id'), name=record.get('name') or None,
        extends_public=bool(base), base_version=base.version if base else None,
    )


def user_partitions(user_id: str | None, public: dict[PartitionKey, KnowledgePartition] | None = None) -> list[KnowledgePartition]:
    if not user_id or _USER_PARTITION_PROVIDER is None:
        return []
    if public is None:
        public = load_public_partitions()
    partitions = []
    for record in _USER_PARTITION_PROVIDER(user_id):
        try:
            partitions.append(user_partition_from_record(record, public))
        except ValueError:
            # A record that no longer validates (e.g. the public base it extended was updated and
            # dropped an id it depended on) is skipped rather than taking the whole registry down;
            # the management endpoints report the problem when the user opens it.
            continue
    return partitions


def load_partitions(directory: str | Path | None = None, user_id: str | None = None) -> dict[PartitionKey, KnowledgePartition]:
    """Effective registry for a user: public partitions, overlaid by the user's own for any scope
    both cover. Without a user_id this is the public registry alone."""
    public = load_public_partitions(directory)
    if not user_id:
        return public
    effective = dict(public)
    for partition in user_partitions(user_id, public):
        effective[partition.key] = partition
    return effective


def available_partitions(directory: str | Path | None = None, user_id: str | None = None) -> list[dict[str, Any]]:
    return [partition.describe() for partition in load_partitions(directory, user_id).values()]


def find_partition(stage: str, subject: str, province: str, curriculum_year: int | None = None, directory: str | Path | None = None, user_id: str | None = None) -> KnowledgePartition | None:
    """Resolve a partition; without an explicit curriculum_year the newest course scheme wins."""
    candidates = [item for item in load_partitions(directory, user_id).values() if (item.stage, item.subject, item.province) == (stage, subject, province)]
    if curriculum_year is not None:
        candidates = [item for item in candidates if item.curriculum_year == curriculum_year]
    return max(candidates, key=lambda item: item.curriculum_year, default=None)


def require_partition(stage: str, subject: str, province: str, curriculum_year: int | None = None, directory: str | Path | None = None, user_id: str | None = None) -> KnowledgePartition:
    partition = find_partition(stage, subject, province, curriculum_year, directory, user_id)
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


# --- matching questions to knowledge points ----------------------------------------------------

MODEL_MATCH_MAX_CANDIDATES = 400


def model_match_knowledge_ids(client: Any, partition: KnowledgePartition, question_text: str) -> list[dict[str, Any]] | None:
    """Ask the `match` model role which of the partition's points a question assesses.

    Keyword matching only fires when a question literally contains a listed keyword; a question
    that says "求 f(x) 的取值范围" never mentions 值域. The model sees the whole candidate list
    (id / name / chapter / keywords) and the question and picks by meaning. Returns
    [{'knowledge_id', 'confidence'}] restricted to ids that actually exist, or None on any
    failure or malformed answer — callers fall back to keywords, never to a guess."""
    candidates = list(partition.points)[:MODEL_MATCH_MAX_CANDIDATES]
    if not candidates or not question_text.strip():
        return None
    system = (
        '你是学科知识点标注助手。给定一道题和候选知识点列表，判断这道题主要考查候选列表中的哪些知识点'
        '（按语义判断，不要求题干出现关键词；通常 1-3 个，宁缺毋滥，候选里没有合适的就返回空列表）。'
        '只能使用候选列表里的 knowledge_id。严格只返回 JSON，不要输出其它文字：'
        '{"knowledge_ids": [string], "confidence": number}，confidence 是 0 到 1 之间的总体把握。'
    )
    payload = {
        'question_text': question_text,
        'candidates': [{'knowledge_id': p['knowledge_id'], 'name': p['name'], 'chapter': p['chapter'], 'keywords': p.get('keywords', [])} for p in candidates],
    }
    result = client.complete_json(system, json.dumps(payload, ensure_ascii=False))
    if not isinstance(result, dict) or not isinstance(result.get('knowledge_ids'), list):
        return None
    confidence = result.get('confidence', 0.7)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        return None
    known = {p['knowledge_id'] for p in candidates}
    picked: list[dict[str, Any]] = []
    for knowledge_id in result['knowledge_ids']:
        if isinstance(knowledge_id, str) and knowledge_id in known and all(item['knowledge_id'] != knowledge_id for item in picked):
            picked.append({'knowledge_id': knowledge_id, 'confidence': round(float(confidence), 4)})
    return picked


def _match_entry(item: dict[str, Any], partition: KnowledgePartition, *, hits: list[str], confidence: float, method: str) -> dict[str, Any]:
    return {
        'knowledge_id': item['knowledge_id'], 'name': item['name'],
        'chapter': item['chapter'], 'confidence': confidence,
        'matched_keywords': hits, 'match_method': method,
        'source': 'user_knowledge_base' if item.get('origin') == 'user' else 'public_registry',
        'knowledge_base_version': partition.version,
        'knowledge_base_source': partition.source,
        'knowledge_partition': {'stage': partition.stage, 'subject': partition.subject, 'province': partition.province, 'curriculum_year': partition.curriculum_year},
        'parent': item['parent'], 'dependencies': item['dependencies'],
        'grade_scope': item['grade_scope'], 'textbook_chapter': item['textbook_chapter'],
        'curriculum_path': [entry['knowledge_id'] for entry in curriculum_path(item['knowledge_id'], partition)],
    }


def match_knowledge_points(*, stage: str, subject: str, province: str, question_text: str, curriculum_year: int | None = None, user_id: str | None = None, client: Any = None) -> list[dict[str, Any]]:
    """Keyword matches always; when a `match` model client is given, its semantic picks are
    merged in (a point found both ways is reported as such, one found only by the model is added
    with no matched keywords). The model never removes a keyword hit — an honest keyword match is
    evidence the model's judgement shouldn't silently erase."""
    partition = require_partition(stage, subject, province, curriculum_year, user_id=user_id)
    by_id = {item['knowledge_id']: item for item in partition.points}
    matches: dict[str, dict[str, Any]] = {}
    for item in partition.points:
        hits = [keyword for keyword in item.get('keywords', []) if keyword in question_text]
        if hits:
            matches[item['knowledge_id']] = _match_entry(item, partition, hits=hits, confidence=item['confidence'], method='keyword')
    if client is not None:
        for picked in model_match_knowledge_ids(client, partition, question_text) or []:
            existing = matches.get(picked['knowledge_id'])
            if existing is not None:
                existing['match_method'] = 'keyword+model'
                existing['model_confidence'] = picked['confidence']
            else:
                item = by_id[picked['knowledge_id']]
                entry = _match_entry(item, partition, hits=[], confidence=min(item['confidence'], picked['confidence']), method='model')
                entry['model_confidence'] = picked['confidence']
                matches[picked['knowledge_id']] = entry
    return sorted(matches.values(), key=lambda item: item['confidence'], reverse=True)
