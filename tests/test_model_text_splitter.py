from exam_diagnosis.parser import ModelTextSplitter, QUESTION_NUMBER_RE


class FakeClient:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def complete_json(self, system, user):
        self.calls.append((system, user))
        return self.answer


REAL_PAPER_EXCERPT = (
    "2025 普通高等学校招生考试(新高考II)\n一、单选题\n"
    "1. 样本数据2, 8, 14, 16, 20 的平均数为\n(\n)\n(A) 8\n(B) 9\n(C) 12\n(D) 18\n"
    "2. 已知z = 1 + i, 则\n1\nz −1 =\n(\n)\n(A) −i\n(B) i\n"
)

ESSAY_WITH_SUBPARTS = "15. 已知函数f(x) = cos(2x + φ).\n(1) 求φ;\n(2) 设函数g(x) = f(x)，求单调区间.\n"


def test_question_number_regex_matches_bare_dot_numbering():
    # This is the exact format the real 2025 national paper uses — the bug this fixes.
    matches = [m.group(1) for m in QUESTION_NUMBER_RE.finditer(REAL_PAPER_EXCERPT)]
    assert matches == ['1', '2']


def test_question_number_regex_does_not_split_essay_subparts():
    # "(1)"/"(2)" inside one essay question must not become separate top-level questions.
    matches = [m.group(1) for m in QUESTION_NUMBER_RE.finditer(ESSAY_WITH_SUBPARTS)]
    assert matches == ['15']


def test_question_number_regex_ignores_decimals():
    assert list(QUESTION_NUMBER_RE.finditer('周长为2.5厘米')) == []


def test_question_number_regex_does_not_cross_lines_for_a_bare_fraction_denominator():
    # Regression: a fraction like "1/2." split across PDF-extraction lines as "...= 1" / "2." /
    # "(1) 求φ;" must not be read as "question 2, text: (1) 求φ;" — \s* silently matching \n was
    # exactly this bug the first time around.
    text = "15. 已知函数f (x) = cos (2x + φ), f (0) = 1\n2.\n(1) 求φ;\n(2) 设函数g (x)\n"
    matches = [m.group(1) for m in QUESTION_NUMBER_RE.finditer(text)]
    assert matches == ['15']


def test_model_split_used_when_client_configured():
    client = FakeClient({
        'subject': '数学',
        'questions': [
            {'question_number': '1', 'question_text': '样本数据2, 8, 14, 16, 20 的平均数为', 'confidence': 0.95},
            {'question_number': '2', 'question_text': '已知z = 1 + i, 则 1/(z-1) =', 'confidence': 0.9},
        ],
    })
    result = ModelTextSplitter(client).parse_page('paper.pdf', REAL_PAPER_EXCERPT, 1)
    assert [q.question_number for q in result.questions] == ['1', '2']
    assert result.subject == '数学'
    assert result.review_required is False  # a successful model split doesn't demand review
    assert len(client.calls) == 1


def test_falls_back_to_regex_and_flags_review_when_no_client():
    result = ModelTextSplitter(None).parse_page('paper.pdf', REAL_PAPER_EXCERPT, 1)
    assert [q.question_number for q in result.questions] == ['1', '2']  # broadened regex still finds these
    assert result.review_required is True  # but the fallback path is never trusted silently
    assert result.warning


def test_falls_back_to_regex_and_flags_review_when_model_response_malformed():
    client = FakeClient({'subject': '数学'})  # missing "questions" key
    result = ModelTextSplitter(client).parse_page('paper.pdf', REAL_PAPER_EXCERPT, 1)
    assert result.review_required is True
    assert [q.question_number for q in result.questions] == ['1', '2']


def test_zero_questions_from_model_still_flags_review():
    client = FakeClient({'subject': '数学', 'questions': []})
    result = ModelTextSplitter(client).parse_page('paper.pdf', REAL_PAPER_EXCERPT, 1)
    assert result.review_required is True
