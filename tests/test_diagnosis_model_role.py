from exam_diagnosis.diagnosis import diagnose_records


class FakeClient:
    """Stub for llm.ChatClient — no network, returns a scripted answer per call."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def complete_json(self, system, user):
        self.calls.append((system, user))
        if not self.answers:
            return None
        return self.answers.pop(0)


RECORD = {
    'id': 'q1', 'question_text': '函数定义域', 'is_wrong_question': True,
    'knowledge_points': [{'knowledge_id': 'k.domain', 'name': '定义域', 'chapter': '函数'}],
    'correction': {'teacher_comment': '写错了'},  # deliberately doesn't hit any keyword rule
}


def test_model_diagnosis_used_when_client_configured_and_valid():
    client = FakeClient([{'root_cause': '审题错误', 'explanation': '把定义域看成值域', 'avoidance_advice': '审题时圈出问的是什么'}])
    result = diagnose_records([RECORD], client=client)
    assert result['root_cause_counts'] == {'审题错误': 1}
    assert result['clusters'][0]['avoidance_advice'] == '审题时圈出问的是什么'
    assert result['wrong_question_collection'][0]['explanation'] == '把定义域看成值域'
    assert len(client.calls) == 1  # cached — not called twice for the same record


def test_falls_back_to_keyword_rule_when_model_returns_none():
    client = FakeClient([None])  # simulates network failure / bad response
    result = diagnose_records([RECORD], client=client)
    assert result['root_cause_counts'] == {'待人工确认': 1}  # keyword rule's default, comment matches nothing


def test_falls_back_to_keyword_rule_when_model_response_is_malformed():
    client = FakeClient([{'root_cause': 'not-a-real-category', 'explanation': 'x', 'avoidance_advice': 'y'}])
    result = diagnose_records([RECORD], client=client)
    assert result['root_cause_counts'] == {'待人工确认': 1}


def test_no_client_keeps_original_keyword_behavior():
    records = [{**RECORD, 'correction': {'teacher_comment': '漏写分母不为零'}}]
    result = diagnose_records(records)
    assert result['root_cause_counts'] == {'条件遗漏': 1}


def test_verify_client_generates_a_real_variant_question():
    verify_client = FakeClient([{'question_text': '已知f(x)=1/(x-2)，求其定义域。', 'answer': '需要 x-2≠0，故定义域为 x≠2，即 (-∞,2)∪(2,+∞)。'}])
    result = diagnose_records([RECORD], verify_client=verify_client)
    q = result['verification_questions'][0]
    assert q['question_text'] == '已知f(x)=1/(x-2)，求其定义域。'
    assert q['answer'].startswith('需要 x-2≠0')
    assert len(verify_client.calls) == 1


def test_verify_falls_back_to_template_when_client_returns_none():
    result = diagnose_records([RECORD], verify_client=FakeClient([None]))
    q = result['verification_questions'][0]
    assert q['question_text'] == "针对知识点 k.domain，完成一道变式题并说明关键步骤。"


def test_verify_falls_back_to_template_when_response_missing_fields():
    result = diagnose_records([RECORD], verify_client=FakeClient([{'question_text': '', 'answer': 'x'}]))
    q = result['verification_questions'][0]
    assert q['question_text'] == "针对知识点 k.domain，完成一道变式题并说明关键步骤。"


def test_no_verify_client_keeps_template_behavior():
    result = diagnose_records([RECORD])
    q = result['verification_questions'][0]
    assert q['question_text'] == "针对知识点 k.domain，完成一道变式题并说明关键步骤。"
    assert q['answer'] == "围绕 k.domain 的定义、条件和计算步骤逐项作答。"
