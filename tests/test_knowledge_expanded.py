from pathlib import Path
import pytest
from exam_diagnosis.knowledge import load_versioned_knowledge, curriculum_path


def test_guangdong_math_registry_covers_required_domains_and_schema():
    version, points = load_versioned_knowledge(Path('data/knowledge/high_math_guangdong_v1.yaml'))
    assert version == '1.1.0'
    chapters = {p['chapter'] for p in points}
    required = {'集合与逻辑','函数','三角函数','平面向量','数列','不等式','立体几何','解析几何','概率统计','导数'}
    assert required <= chapters
    for point in points:
        assert {'knowledge_id','name','chapter','keywords','confidence','parent','dependencies','grade_scope','textbook_chapter'} <= point.keys()


def test_schema_validation_rejects_missing_dependency_target(tmp_path):
    path=tmp_path/'bad.yaml'; path.write_text('version: 1.0.0\nknowledge_points:\n  - knowledge_id: x\n    name: X\n    chapter: X\n    keywords: [x]\n    confidence: 1\n    parent: null\n    dependencies: [missing]\n    grade_scope: 高一\n    textbook_chapter: 第一章\n',encoding='utf-8')
    with pytest.raises(ValueError,match='dependency'):
        load_versioned_knowledge(path)


def test_curriculum_path_includes_dependencies_and_parent():
    path=curriculum_path('high.math.derivative.application')
    assert path[-1]['knowledge_id']=='high.math.derivative.application'
    assert any(item['knowledge_id']=='high.math.function.monotonicity' for item in path)
