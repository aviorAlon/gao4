from pathlib import Path
from exam_diagnosis.knowledge import load_versioned_knowledge


def test_versioned_knowledge_resource_loads():
    version, points = load_versioned_knowledge(Path('data/knowledge/high_math_guangdong_v1.yaml'))
    assert version == '1.1.0'
    assert any(point['knowledge_id'] == 'high.math.function.domain' for point in points)
