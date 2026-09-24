import os
from pathlib import Path
from exam_diagnosis.knowledge import match_knowledge_points, load_versioned_knowledge
from conftest import SHIPPED_KNOWLEDGE_VERSION


def test_matching_uses_yaml_resource_version_not_only_python_constant():
    version, points = load_versioned_knowledge(Path('data/knowledge/high_math_guangdong_v1.yaml'))
    assert version == SHIPPED_KNOWLEDGE_VERSION
    assert any(p['knowledge_id'] == 'high.math.function.domain' for p in points)
    matches = match_knowledge_points(stage='high', subject='数学', province='广东', question_text='函数定义域')
    assert matches[0]['knowledge_id'] == 'high.math.function.domain'
    assert matches[0]['knowledge_base_version'] == SHIPPED_KNOWLEDGE_VERSION
