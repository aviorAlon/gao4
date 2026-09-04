"""User-maintained knowledge bases layered over the public registry, and semantic (model) matching."""
import io

import pytest
import yaml
from fastapi.testclient import TestClient

import exam_diagnosis.app as app_module
from exam_diagnosis import knowledge
from exam_diagnosis.knowledge import (
    KnowledgePartition, find_partition, load_public_partitions, match_knowledge_points,
    merge_points, model_match_knowledge_ids, user_partition_from_record, validate_points,
)

SCOPE = {'stage': 'high', 'subject': '数学', 'province': '广东', 'curriculum_year': 2017}
SUBMISSION = {'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}


def _point(knowledge_id, name, keywords, **extra):
    return {'knowledge_id': knowledge_id, 'name': name, 'chapter': extra.pop('chapter', '向量'), 'keywords': keywords, 'confidence': extra.pop('confidence', 0.9),
            'parent': extra.pop('parent', None), 'dependencies': extra.pop('dependencies', []), 'grade_scope': extra.pop('grade_scope', '高二'), 'textbook_chapter': extra.pop('textbook_chapter', '必修第二册'), **extra}


# Deliberately a topic the shipped registry has no point (and no keyword) for.
VECTOR = _point('user.math.vector.dot', '极坐标与参数方程', ['极坐标', '参数方程'])
CUSTOM_QUESTION = '将极坐标方程 ρ=2cosθ 化为直角坐标方程'


class FakeClient:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def complete_json(self, system, user):
        self.calls.append((system, user))
        return self.answer


# --- registry layering (pure knowledge.py) ------------------------------------------------------------


def test_public_registry_is_unchanged_without_a_user():
    partition = find_partition('high', '数学', '广东')
    assert partition.source == 'public' and partition.describe()['source'] == 'public'
    assert 'id' not in partition.describe()
    assert all('origin' not in point for point in partition.points)


def test_user_partition_extending_public_merges_and_overrides_by_id():
    public = load_public_partitions()
    base = public[('high', '数学', '广东', 2017)]
    override = {**_point('high.math.function.domain', '函数定义域（自定义）', ['定义域', '自变量取值范围'], chapter='函数', grade_scope='高一')}
    partition = user_partition_from_record({'id': 'kb1', 'user_id': 'u', **SCOPE, 'extends_public': True, 'name': '我的数学库', 'knowledge_points': [VECTOR, override]}, public)
    assert partition.source == 'user' and partition.extends_public and partition.base_version == base.version
    assert len(partition.points) == len(base.points) + 1
    by_id = {p['knowledge_id']: p for p in partition.points}
    assert by_id['user.math.vector.dot']['origin'] == 'user'
    assert by_id['high.math.function.domain']['name'] == '函数定义域（自定义）' and by_id['high.math.function.domain']['origin'] == 'user'
    assert by_id['high.math.sets.logic']['origin'] == 'public'
    assert partition.academic_year_range == base.academic_year_range  # inherited
    described = partition.describe()
    assert described['id'] == 'kb1' and described['name'] == '我的数学库' and described['user_point_count'] == 2 and described['resource'] is None


def test_standalone_user_partition_for_a_scope_the_product_lacks():
    record = {'id': 'kb2', 'user_id': 'u', 'stage': 'high', 'subject': '物理', 'province': '广东', 'curriculum_year': 2017, 'extends_public': False,
              'academic_year_range': ['高一', '高二'], 'knowledge_points': [_point('user.physics.kinematics', '匀变速直线运动', ['加速度', '位移'], chapter='运动学', grade_scope='高一')]}
    partition = user_partition_from_record(record, load_public_partitions())
    assert partition.source == 'user' and not partition.extends_public and partition.version == '1.0.0'
    assert [p['knowledge_id'] for p in partition.points] == ['user.physics.kinematics']


def test_user_partition_validation_errors():
    public = load_public_partitions()
    with pytest.raises(ValueError, match='public partition not found'):
        user_partition_from_record({**SCOPE, 'subject': '化学', 'extends_public': True, 'knowledge_points': []}, public)
    with pytest.raises(ValueError, match='academic_year_range'):
        user_partition_from_record({**SCOPE, 'extends_public': False, 'knowledge_points': [VECTOR]}, public)
    with pytest.raises(ValueError, match='dependency not found'):
        user_partition_from_record({**SCOPE, 'extends_public': True, 'knowledge_points': [_point('x', 'x', [], dependencies=['nope'])]}, public)
    with pytest.raises(ValueError, match='outside academic_year_range'):
        user_partition_from_record({**SCOPE, 'extends_public': True, 'knowledge_points': [_point('x', 'x', [], grade_scope='初三')]}, public)
    with pytest.raises(ValueError, match='duplicate'):
        validate_points([VECTOR, VECTOR])
    with pytest.raises(ValueError, match='confidence'):
        validate_points([_point('x', 'x', [], confidence=1.5)])
    # depending on a public id is fine when extending
    user_partition_from_record({**SCOPE, 'extends_public': True, 'knowledge_points': [_point('x', 'x', [], dependencies=['high.math.sets.logic'])]}, public)


def test_merge_points_keeps_public_order_and_tags_origin():
    merged = merge_points([{'knowledge_id': 'a'}, {'knowledge_id': 'b'}], [{'knowledge_id': 'b', 'name': 'B2'}, {'knowledge_id': 'c'}])
    assert [(p['knowledge_id'], p['origin']) for p in merged] == [('a', 'public'), ('b', 'user'), ('c', 'user')]
    assert merged[1]['name'] == 'B2'


def test_provider_overlay_resolves_user_partition_for_that_user_only(monkeypatch):
    records = {'u1': [{'id': 'kb', 'user_id': 'u1', **SCOPE, 'extends_public': True, 'knowledge_points': [VECTOR]}]}
    monkeypatch.setattr(knowledge, '_USER_PARTITION_PROVIDER', lambda uid: records.get(uid, []))
    assert find_partition('high', '数学', '广东', user_id='u1').source == 'user'
    assert find_partition('high', '数学', '广东', user_id='u2').source == 'public'
    assert find_partition('high', '数学', '广东').source == 'public'
    listed = knowledge.available_partitions(user_id='u1')
    assert [p['source'] for p in listed if p['subject'] == '数学' and p['stage'] == 'high'] == ['user']


def test_invalid_stored_record_is_skipped_not_fatal(monkeypatch):
    monkeypatch.setattr(knowledge, '_USER_PARTITION_PROVIDER', lambda uid: [{'id': 'bad', 'user_id': uid, **SCOPE, 'extends_public': True, 'knowledge_points': [_point('x', 'x', [], dependencies=['gone'])]}])
    assert find_partition('high', '数学', '广东', user_id='u1').source == 'public'


# --- matching --------------------------------------------------------------------------------------


def test_keyword_matching_sees_user_points(monkeypatch):
    monkeypatch.setattr(knowledge, '_USER_PARTITION_PROVIDER', lambda uid: [{'id': 'kb', 'user_id': uid, **SCOPE, 'extends_public': True, 'knowledge_points': [VECTOR]}])
    matches = match_knowledge_points(stage='high', subject='数学', province='广东', question_text=CUSTOM_QUESTION, user_id='u1')
    assert [m['knowledge_id'] for m in matches] == ['user.math.vector.dot']
    assert matches[0]['source'] == 'user_knowledge_base' and matches[0]['knowledge_base_source'] == 'user' and matches[0]['match_method'] == 'keyword'
    assert matches[0]['matched_keywords'] == ['极坐标']
    assert match_knowledge_points(stage='high', subject='数学', province='广东', question_text=CUSTOM_QUESTION) == []  # public only: no such point


def test_model_match_validates_ids_and_shape():
    partition = find_partition('high', '数学', '广东')
    client = FakeClient({'knowledge_ids': ['high.math.function.domain', 'made.up', 'high.math.function.domain'], 'confidence': 0.85})
    picked = model_match_knowledge_ids(client, partition, '求 f(x)=1/(x-2) 中 x 的取值范围')
    assert picked == [{'knowledge_id': 'high.math.function.domain', 'confidence': 0.85}]
    system, user = client.calls[0]
    assert 'knowledge_ids' in system and 'high.math.function.domain' in user and '取值范围' in user
    assert model_match_knowledge_ids(FakeClient(None), partition, 'q') is None
    assert model_match_knowledge_ids(FakeClient({'knowledge_ids': 'nope'}), partition, 'q') is None
    assert model_match_knowledge_ids(FakeClient({'knowledge_ids': [], 'confidence': 2}), partition, 'q') is None
    assert model_match_knowledge_ids(FakeClient({'knowledge_ids': ['high.math.function.domain']}), partition, 'q') == [{'knowledge_id': 'high.math.function.domain', 'confidence': 0.7}]
    assert model_match_knowledge_ids(FakeClient({'knowledge_ids': ['x']}), partition, '   ') is None


def test_model_matches_merge_with_keyword_matches():
    # question mentions 定义域 (keyword hit) but assesses monotonicity only by meaning
    client = FakeClient({'knowledge_ids': ['high.math.function.domain', 'high.math.function.monotonicity'], 'confidence': 0.8})
    matches = match_knowledge_points(stage='high', subject='数学', province='广东', question_text='求函数的定义域，并判断在该区间上是增是减', client=client)
    by_id = {m['knowledge_id']: m for m in matches}
    assert by_id['high.math.function.domain']['match_method'] == 'keyword+model' and by_id['high.math.function.domain']['model_confidence'] == 0.8
    mono = by_id['high.math.function.monotonicity']
    assert mono['match_method'] == 'model' and mono['matched_keywords'] == [] and mono['confidence'] == 0.8
    # model failure → exactly the keyword result
    assert [m['knowledge_id'] for m in match_knowledge_points(stage='high', subject='数学', province='广东', question_text='求函数的定义域', client=FakeClient(None))] == ['high.math.function.domain']


# --- API ---------------------------------------------------------------------------------------------


def _create_kb(client, **overrides):
    body = {**SCOPE, 'extends_public': True, 'name': '我的数学库', 'knowledge_points': [VECTOR], **overrides}
    response = client.post('/api/knowledge-bases', json=body)
    assert response.status_code == 201, response.json()
    return response.json()


def test_knowledge_base_crud_and_resolution():
    client = TestClient(app_module.app)
    created = _create_kb(client)
    assert created['resolved']['source'] == 'user' and created['resolved']['user_point_count'] == 1 and created['problem'] is None
    kb_id = created['id']
    listed = client.get('/api/knowledge-bases').json()['knowledge_bases']
    assert [kb['id'] for kb in listed] == [kb_id]
    partitions = client.get('/api/knowledge-partitions').json()['partitions']
    mine = [p for p in partitions if p['stage'] == 'high' and p['subject'] == '数学']
    assert len(mine) == 1 and mine[0]['source'] == 'user' and mine[0]['id'] == kb_id
    points = client.get('/api/knowledge-partitions/points', params=SCOPE).json()
    assert points['partition']['source'] == 'user'
    assert {p['knowledge_id']: p['origin'] for p in points['points']}['user.math.vector.dot'] == 'user'
    assert any(p['origin'] == 'public' for p in points['points'])
    # add / override / delete points
    added = client.post(f'/api/knowledge-bases/{kb_id}/points', json={**_point('user.math.vector.basis', '平面向量基本定理', ['基底']), 'dependencies': ['user.math.vector.dot']}).json()
    assert added['resolved']['user_point_count'] == 2
    blocked = client.delete(f'/api/knowledge-bases/{kb_id}/points/user.math.vector.dot')
    assert blocked.status_code == 422 and 'dependency not found' in blocked.json()['detail']
    assert client.delete(f'/api/knowledge-bases/{kb_id}/points/user.math.vector.basis').json()['resolved']['user_point_count'] == 1
    assert client.delete(f'/api/knowledge-bases/{kb_id}/points/nope').status_code == 404
    replaced = client.put(f'/api/knowledge-bases/{kb_id}', json={**SCOPE, 'extends_public': True, 'name': '改名', 'knowledge_points': []}).json()
    assert replaced['name'] == '改名' and replaced['resolved']['user_point_count'] == 0
    assert client.delete(f'/api/knowledge-bases/{kb_id}').json() == {'deleted': kb_id}
    assert client.get(f'/api/knowledge-bases/{kb_id}').status_code == 404
    assert client.get('/api/knowledge-partitions/points', params=SCOPE).json()['partition']['source'] == 'public'


def test_knowledge_base_validation_and_scope_conflicts():
    client = TestClient(app_module.app)
    bad = client.post('/api/knowledge-bases', json={**SCOPE, 'subject': '化学', 'extends_public': True, 'knowledge_points': []})
    assert bad.status_code == 422 and bad.json()['error'] == 'public_partition_not_found'
    bad = client.post('/api/knowledge-bases', json={**SCOPE, 'extends_public': True, 'knowledge_points': [_point('x', 'x', [], dependencies=['gone'])]})
    assert bad.status_code == 422 and bad.json()['error'] == 'invalid_knowledge_base' and 'gone' in bad.json()['detail']
    _create_kb(client)
    duplicate = client.post('/api/knowledge-bases', json={**SCOPE, 'extends_public': True, 'knowledge_points': []})
    assert duplicate.status_code == 409 and duplicate.json() == {'error': 'knowledge_base_exists_for_scope'}
    assert client.get('/api/knowledge-partitions/points', params={**SCOPE, 'subject': '化学'}).status_code == 422


def test_knowledge_bases_are_private_to_their_owner():
    alice = TestClient(app_module.app, headers={'X-User-Id': 'alice'})
    bob = TestClient(app_module.app, headers={'X-User-Id': 'bob'})
    kb_id = _create_kb(alice)['id']
    assert bob.get('/api/knowledge-bases').json()['knowledge_bases'] == []
    assert bob.get(f'/api/knowledge-bases/{kb_id}').status_code == 404
    assert bob.delete(f'/api/knowledge-bases/{kb_id}').status_code == 404
    assert bob.get('/api/knowledge-partitions/points', params=SCOPE).json()['partition']['source'] == 'public'
    assert alice.get('/api/knowledge-partitions/points', params=SCOPE).json()['partition']['source'] == 'user'


def test_import_and_export_round_trip():
    client = TestClient(app_module.app)
    payload = {**SCOPE, 'extends_public': True, 'name': '导入的库', 'version': '2.0.0', 'knowledge_points': [VECTOR]}
    imported = client.post('/api/knowledge-bases/import', files={'file': ('kb.yaml', yaml.safe_dump(payload, allow_unicode=True).encode('utf-8'), 'application/x-yaml')})
    assert imported.status_code == 201, imported.json()
    kb_id = imported.json()['id']
    assert imported.json()['name'] == '导入的库' and imported.json()['resolved']['version'] == '2.0.0'
    exported = client.get(f'/api/knowledge-bases/{kb_id}/export')
    assert exported.status_code == 200 and exported.headers['content-disposition'].endswith('.yaml"')
    round_trip = yaml.safe_load(exported.text)
    assert round_trip['knowledge_points'] == [VECTOR] and round_trip['extends_public'] is True and round_trip['version'] == '2.0.0'
    # a standalone file in the public schema (no extends_public) imports as a standalone base
    standalone = {'stage': 'high', 'subject': '物理', 'province': '广东', 'curriculum_year': 2017, 'academic_year_range': ['高一'], 'version': '1.0.0',
                  'knowledge_points': [_point('user.physics.kinematics', '匀变速直线运动', ['加速度'], chapter='运动学', grade_scope='高一')]}
    response = client.post('/api/knowledge-bases/import', files={'file': ('physics.yaml', yaml.safe_dump(standalone, allow_unicode=True).encode('utf-8'), 'application/x-yaml')})
    assert response.status_code == 201 and response.json()['extends_public'] is False
    assert client.post('/api/knowledge-bases/import', files={'file': ('bad.yaml', b'- just\n- a list', 'application/x-yaml')}).status_code == 422
    assert client.post('/api/knowledge-bases/import', files={'file': ('bad.yaml', b'stage: [unclosed', 'application/x-yaml')}).status_code == 422


def test_pipeline_uses_user_points_and_match_role(monkeypatch):
    client = TestClient(app_module.app)
    _create_kb(client)
    fake = FakeClient({'knowledge_ids': ['high.math.function.monotonicity'], 'confidence': 0.9})
    seen = []
    monkeypatch.setattr(app_module, 'guarded_client_from_env', lambda role, user_id, **ctx: seen.append((role, user_id, ctx)) or fake)
    submission = client.post('/api/submissions', json=SUBMISSION).json()
    assert submission['knowledge_partition']['source'] == 'user'
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [{'question_number': '1', 'question_text': CUSTOM_QUESTION, 'is_wrong_question': True, 'original_question_available': True}]})
    records = client.get(f"/api/exams/{exam['id']}/question-records").json()['records']
    mapped = client.post(f"/api/question-records/{records[0]['id']}/knowledge-mapping").json()['knowledge_points']
    methods = {m['knowledge_id']: m['match_method'] for m in mapped}
    assert methods == {'user.math.vector.dot': 'keyword', 'high.math.function.monotonicity': 'model'}
    assert seen and seen[-1][0] == 'match' and seen[-1][1] == 'local' and seen[-1][2]['record_id'] == records[0]['id']
