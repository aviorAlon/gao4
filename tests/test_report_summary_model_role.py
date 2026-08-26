from exam_diagnosis.reporting import generate_report_summary, render_html, render_markdown


class FakeClient:
    def __init__(self, answer):
        self.answer = answer

    def complete_json(self, system, user):
        return self.answer


REPORT = {'wrong_question_count': 2, 'root_cause_counts': {'条件遗漏': 2}, 'review_order': [], 'quality_flags': [],
           'clusters': [], 'knowledge_tree': {}, 'wrong_question_collection': [], 'verification_questions': []}


def test_summary_included_when_model_responds():
    client = FakeClient({'summary': '本次共 2 道错题，主要是条件遗漏。'})
    summary = generate_report_summary(client, REPORT)
    assert summary == '本次共 2 道错题，主要是条件遗漏。'
    report = {**REPORT, 'summary': summary}
    assert '概述' in render_markdown(report)
    assert '概述' in render_html(report)


def test_summary_none_when_model_response_malformed():
    client = FakeClient({'not_summary': 'oops'})
    assert generate_report_summary(client, REPORT) is None


def test_summary_none_when_client_returns_none():
    client = FakeClient(None)
    assert generate_report_summary(client, REPORT) is None


def test_no_summary_section_when_absent():
    assert '概述' not in render_markdown(REPORT)
    assert '概述' not in render_html(REPORT)
