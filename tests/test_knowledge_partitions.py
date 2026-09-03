import pytest
from fastapi.testclient import TestClient
from exam_diagnosis.app import app
from exam_diagnosis.knowledge import (
    KnowledgePartitionNotFound, available_partitions, find_partition,
    load_partition, load_partitions, match_knowledge_points,
)
from conftest import SHIPPED_KNOWLEDGE_VERSION

MIDDLE_YAML = '''version: 0.2.0
stage: middle
subject: 数学
province: 广东
curriculum_year: 2022
academic_year_range: [初一, 初二, 初三]
curriculum: 义务教育数学课程标准（2022年版）
knowledge_points:
  - knowledge_id: middle.math.linear.equation
    name: 一元一次方程
    chapter: 方程
    keywords: [一元一次方程, 移项]
    confidence: 0.9
    parent: null
    dependencies: []
    grade_scope: 初一
    textbook_chapter: 七年级上册 第三章
'''


def write_partition(directory, name, text):
    path = directory / name
    path.write_text(text, encoding='utf-8')
    return path


def test_shipped_registry_exposes_high_math_guangdong_partition():
    partition = find_partition('high', '数学', '广东')
    assert partition and partition.version == SHIPPED_KNOWLEDGE_VERSION
    assert partition.describe()['curriculum'].startswith('普通高中数学课程标准')
    assert partition.key == ('high', '数学', '广东', 2017)


def test_matching_outside_any_partition_raises_instead_of_returning_empty():
    with pytest.raises(KnowledgePartitionNotFound) as raised:
        match_knowledge_points(stage='middle', subject='数学', province='广东', question_text='一元一次方程')
    assert raised.value.stage == 'middle'


def test_registry_reads_every_partition_in_configured_directory(tmp_path, monkeypatch):
    write_partition(tmp_path, 'middle_math_guangdong.yaml', MIDDLE_YAML)
    monkeypatch.setenv('EXAM_DIAGNOSIS_KNOWLEDGE_DIR', str(tmp_path))
    assert [item['stage'] for item in available_partitions()] == ['middle']
    matches = match_knowledge_points(stage='middle', subject='数学', province='广东', question_text='解一元一次方程')
    assert matches[0]['knowledge_id'] == 'middle.math.linear.equation'
    assert matches[0]['knowledge_base_version'] == '0.2.0'
    assert matches[0]['knowledge_partition'] == {'stage': 'middle', 'subject': '数学', 'province': '广东', 'curriculum_year': 2022}


def test_partitions_do_not_leak_knowledge_points_across_stages(tmp_path, monkeypatch):
    write_partition(tmp_path, 'middle_math_guangdong.yaml', MIDDLE_YAML)
    write_partition(tmp_path, 'high_math_guangdong.yaml', MIDDLE_YAML.replace('stage: middle', 'stage: high').replace('middle.math', 'high.math'))
    monkeypatch.setenv('EXAM_DIAGNOSIS_KNOWLEDGE_DIR', str(tmp_path))
    assert len(load_partitions()) == 2
    assert match_knowledge_points(stage='middle', subject='数学', province='广东', question_text='移项')[0]['knowledge_id'].startswith('middle.')
    assert match_knowledge_points(stage='high', subject='数学', province='广东', question_text='移项')[0]['knowledge_id'].startswith('high.')


def test_partition_resource_must_declare_its_scope(tmp_path):
    path = write_partition(tmp_path, 'no_scope.yaml', MIDDLE_YAML.replace('province: 广东\n', ''))
    with pytest.raises(ValueError, match='partition missing'):
        load_partition(path)


def test_duplicate_partition_scope_is_rejected(tmp_path, monkeypatch):
    write_partition(tmp_path, 'a.yaml', MIDDLE_YAML)
    write_partition(tmp_path, 'b.yaml', MIDDLE_YAML.replace('version: 0.2.0', 'version: 0.3.0'))
    monkeypatch.setenv('EXAM_DIAGNOSIS_KNOWLEDGE_DIR', str(tmp_path))
    with pytest.raises(ValueError, match='duplicate knowledge partition'):
        load_partitions()


def test_same_scope_under_two_course_schemes_are_distinct_partitions(tmp_path, monkeypatch):
    write_partition(tmp_path, 'old.yaml', MIDDLE_YAML)
    write_partition(tmp_path, 'new.yaml', MIDDLE_YAML.replace('curriculum_year: 2022', 'curriculum_year: 2011').replace('middle.math.linear.equation', 'middle.math.legacy.equation'))
    monkeypatch.setenv('EXAM_DIAGNOSIS_KNOWLEDGE_DIR', str(tmp_path))
    assert len(load_partitions()) == 2
    assert find_partition('middle', '数学', '广东', 2011).points[0]['knowledge_id'] == 'middle.math.legacy.equation'


def test_partition_without_curriculum_year_falls_back_to_the_newest_scheme(tmp_path, monkeypatch):
    write_partition(tmp_path, 'old.yaml', MIDDLE_YAML.replace('curriculum_year: 2022', 'curriculum_year: 2011').replace('middle.math.linear.equation', 'middle.math.legacy.equation'))
    write_partition(tmp_path, 'new.yaml', MIDDLE_YAML)
    monkeypatch.setenv('EXAM_DIAGNOSIS_KNOWLEDGE_DIR', str(tmp_path))
    assert find_partition('middle', '数学', '广东').curriculum_year == 2022


def test_partition_rejects_knowledge_points_outside_its_academic_year_range(tmp_path):
    path = write_partition(tmp_path, 'leaky.yaml', MIDDLE_YAML.replace('academic_year_range: [初一, 初二, 初三]', 'academic_year_range: [初二, 初三]'))
    with pytest.raises(ValueError, match='outside academic_year_range'):
        load_partition(path)


def test_submission_grade_must_sit_inside_the_partition_academic_year_range():
    client = TestClient(app)
    rejected = client.post('/api/submissions', json={'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '初三', 'academic_year_label': '25-26'})
    assert rejected.status_code == 422
    assert rejected.json()['error'] == 'grade_outside_partition_scope'
    assert rejected.json()['academic_year_range'] == ['高一', '高二', '高三']


def test_submission_with_unknown_curriculum_year_is_rejected():
    client = TestClient(app)
    rejected = client.post('/api/submissions', json={'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26', 'curriculum_year': 1999})
    assert rejected.status_code == 422
    assert rejected.json()['requested']['curriculum_year'] == 1999


def test_api_lists_partitions_and_rejects_submission_outside_them():
    client = TestClient(app)
    listing = client.get('/api/knowledge-partitions').json()['partitions']
    assert {'high', '数学', '广东'} <= {item['stage'] for item in listing} | {item['subject'] for item in listing} | {item['province'] for item in listing}
    rejected = client.post('/api/submissions', json={'stage': 'middle', 'subject': '数学', 'province': '广东', 'school_grade': '初二', 'academic_year_label': '25-26'})
    assert rejected.status_code == 422
    body = rejected.json()
    assert body['error'] == 'knowledge_partition_not_found'
    assert body['requested']['stage'] == 'middle' and body['available_partitions']


def test_submission_report_and_export_record_the_partition_they_used():
    client = TestClient(app)
    submission = client.post('/api/submissions', json={'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}).json()
    assert submission['knowledge_partition']['version'] == SHIPPED_KNOWLEDGE_VERSION
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '分区'}).json()
    client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [{'question_number': '1', 'question_text': '函数定义域', 'is_wrong_question': True, 'original_question_available': True, 'student_answer': '错答'}]})
    run = client.post(f"/api/submissions/{submission['id']}/analysis-runs", json={'exam_ids': [exam['id']]}).json()
    client.post(f"/api/analysis-runs/{run['id']}/confirm-start")
    report = client.get(f"/api/analysis-runs/{run['id']}/report").json()
    assert report['knowledge_base'] == submission['knowledge_partition']
    export = client.get(f"/api/submissions/{submission['id']}/export").json()
    assert export['knowledge_partition']['stage'] == 'high' and export['versions']['knowledge_base'] == SHIPPED_KNOWLEDGE_VERSION
