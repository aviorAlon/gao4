"""Export → import round trip: the zip bundle, id remapping, and what happens to missing files."""
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

import exam_diagnosis.app as app_module
from exam_diagnosis import paths
from exam_diagnosis.transfer import (
    BUNDLE_DATA_NAME, BundleError, build_bundle, build_id_map, bundle_asset_name, prepare_import, read_bundle, remap,
)

SUBMISSION = {'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}

PAYLOAD = {
    'submission': {'id': 's-old', 'user_id': 'someone', 'subject': '数学'},
    'exams': [{'id': 'e-old', 'submission_id': 's-old', 'user_id': 'someone', 'draft_questions': [{'original_source': {'asset_id': 'a-old'}}]}],
    'assets': [{'id': 'a-old', 'exam_id': 'e-old', 'user_id': 'someone', 'filename': 'p.jpg', 'storage_path': '/old/machine/p.jpg'}],
    'pages': [{'id': 'p-old', 'exam_id': 'e-old', 'asset_id': 'a-old', 'user_id': 'someone'}],
    'question_records': [{'id': 'q-old', 'exam_id': 'e-old', 'user_id': 'someone'}],
    'analysis_runs': [{'id': 'r-old', 'submission_id': 's-old', 'user_id': 'someone', 'exam_ids': ['e-old'],
                       'report': {'analysis_run_id': 'r-old', 'source_question_ids': ['q-old'],
                                  'clusters': [{'question_ids': ['q-old']}], 'exam_versions': [{'exam_id': 'e-old'}],
                                  'verification_question_ids': ['v-old']}}],
    'verification_questions': [{'id': 'v-old', 'analysis_run_id': 'r-old', 'submission_id': 's-old', 'user_id': 'someone',
                                'knowledge_id': 'k1', 'source_question_ids': ['q-old']}],
    'mastery_records': [{'id': 's-old:k1:计算错误', 'submission_id': 's-old', 'user_id': 'someone', 'knowledge_id': 'k1',
                         'error_cluster': '计算错误', 'attempt_history': [{'verification_question_id': 'v-old'}]}],
    'versions': {'export_schema': '1.0.0', 'application': '0.1.0'},
}


def _sequential_ids():
    counter = iter(range(1000))
    return lambda: f'new-{next(counter)}'


# --- id remapping ------------------------------------------------------------------------------------


def test_every_record_id_gets_a_fresh_one():
    mapping = build_id_map(PAYLOAD, _sequential_ids())
    assert set(mapping) == {'s-old', 'e-old', 'a-old', 'p-old', 'q-old', 'r-old', 'v-old'}
    assert len(set(mapping.values())) == len(mapping)  # no duplicates
    assert 's-old:k1:计算错误' not in mapping  # composite, rebuilt not replaced


def test_remap_replaces_ids_at_every_depth_including_dict_keys():
    mapping = {'q-old': 'q-new'}
    assert remap({'a': ['q-old', {'b': 'q-old'}]}, mapping) == {'a': ['q-new', {'b': 'q-new'}]}
    assert remap({'q-old': 1}, mapping) == {'q-new': 1}
    assert remap('untouched', mapping) == 'untouched'
    assert remap(7, mapping) == 7 and remap(None, mapping) is None


def test_prepare_import_rewrites_every_cross_reference():
    prepared = prepare_import(PAYLOAD, 'importer', _sequential_ids())
    sid, eid = prepared['submission']['id'], prepared['exams'][0]['id']
    aid, qid = prepared['assets'][0]['id'], prepared['question_records'][0]['id']
    rid, vid = prepared['analysis_runs'][0]['id'], prepared['verification_questions'][0]['id']
    assert sid != 's-old' and eid != 'e-old'
    assert prepared['exams'][0]['submission_id'] == sid
    assert prepared['exams'][0]['draft_questions'][0]['original_source']['asset_id'] == aid
    assert prepared['pages'][0]['exam_id'] == eid and prepared['pages'][0]['asset_id'] == aid
    run = prepared['analysis_runs'][0]
    assert run['exam_ids'] == [eid]
    assert run['report']['analysis_run_id'] == rid and run['report']['source_question_ids'] == [qid]
    assert run['report']['clusters'][0]['question_ids'] == [qid]
    assert run['report']['exam_versions'][0]['exam_id'] == eid
    assert run['report']['verification_question_ids'] == [vid]
    assert prepared['verification_questions'][0]['source_question_ids'] == [qid]
    mastery = prepared['mastery_records'][0]
    assert mastery['id'] == f'{sid}:k1:计算错误' and mastery['submission_id'] == sid
    assert mastery['attempt_history'][0]['verification_question_id'] == vid
    # ownership transfers to whoever imported it
    assert all(item['user_id'] == 'importer' for group in ('exams', 'assets', 'pages', 'question_records', 'analysis_runs', 'verification_questions', 'mastery_records') for item in prepared[group])
    assert prepared['submission']['user_id'] == 'importer'


def test_prepare_import_rejects_a_payload_without_a_submission():
    with pytest.raises(BundleError, match='submission'):
        prepare_import({'exams': []}, 'u')
    with pytest.raises(BundleError):
        prepare_import({'submission': {'no': 'id'}}, 'u')


# --- bundles -----------------------------------------------------------------------------------------


def test_bundle_carries_files_and_records_the_ones_that_are_gone():
    data = build_bundle(PAYLOAD, lambda asset: b'image-bytes')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert BUNDLE_DATA_NAME in archive.namelist()
        assert archive.read(bundle_asset_name(PAYLOAD['assets'][0])) == b'image-bytes'
        manifest = json.loads(archive.read(BUNDLE_DATA_NAME))['bundle']
    assert manifest['assets'] == {'a-old': 'uploads/a-old.jpg'} and manifest['missing_assets'] == []

    gone = build_bundle(PAYLOAD, lambda asset: None)
    with zipfile.ZipFile(io.BytesIO(gone)) as archive:
        gone_manifest = json.loads(archive.read(BUNDLE_DATA_NAME))['bundle']
    # export still succeeds when a file is gone, it just records which one
    assert gone_manifest['missing_assets'] == ['a-old'] and gone_manifest['assets'] == {}


def test_read_bundle_accepts_zip_and_plain_json():
    payload, files = read_bundle(build_bundle(PAYLOAD, lambda a: b'bytes'))
    assert payload['submission']['id'] == 's-old' and files == {'a-old': b'bytes'}
    payload, files = read_bundle(json.dumps(PAYLOAD).encode('utf-8'))
    assert payload['submission']['id'] == 's-old' and files == {}


def test_read_bundle_rejects_junk_and_path_traversal():
    with pytest.raises(BundleError, match='JSON'):
        read_bundle(b'{not json')
    with pytest.raises(BundleError, match='zip'):
        read_bundle(b'\x00\x01 not a zip at all')
    with pytest.raises(BundleError, match=BUNDLE_DATA_NAME):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('something-else.txt', 'x')
        read_bundle(buffer.getvalue())
    # an entry pointing outside uploads/ is ignored rather than written anywhere
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr(BUNDLE_DATA_NAME, json.dumps({**PAYLOAD, 'bundle': {'assets': {'a-old': '../../evil.jpg'}}}))
        archive.writestr('../../evil.jpg', b'pwned')
    _, files = read_bundle(buffer.getvalue())
    assert files == {}


# --- API round trip ------------------------------------------------------------------------------------


def _seeded_submission(client):
    submission = client.post('/api/submissions', json=SUBMISSION).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    session = client.post(f"/api/exams/{exam['id']}/upload-sessions").json()
    client.post(f"/api/upload-sessions/{session['id']}/assets", files={'file': ('paper.txt', b'1. the original bytes', 'text/plain')})
    job = client.post(f"/api/upload-sessions/{session['id']}/finish").json()['job_id']
    client.post(f'/api/jobs/{job}/run')
    client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [{'question_number': '1', 'question_text': '求函数的定义域', 'is_wrong_question': True, 'original_question_available': True}]})
    run = client.post(f"/api/submissions/{submission['id']}/analysis-runs", json={'exam_ids': [exam['id']]}).json()
    client.post(f"/api/analysis-runs/{run['id']}/confirm-start")
    client.get(f"/api/analysis-runs/{run['id']}/report")
    return submission, exam


def test_zip_export_then_import_reproduces_the_whole_submission():
    client = TestClient(app_module.app)
    original, _ = _seeded_submission(client)
    bundle = client.get(f"/api/submissions/{original['id']}/export", params={'format': 'zip'})
    assert bundle.status_code == 200 and bundle.headers['content-type'] == 'application/zip'
    assert bundle.content[:2] == b'PK'

    result = client.post('/api/submissions/import', files={'file': ('backup.zip', bundle.content, 'application/zip')})
    assert result.status_code == 201, result.json()
    body = result.json()
    new_id = body['submission_id']
    assert new_id != original['id']  # additive, never overwrites the original
    assert body['imported']['exams'] == 1 and body['imported']['question_records'] == 1
    assert body['imported']['verification_questions'] >= 1 and body['imported']['mastery_records'] >= 1
    assert body['restored_files'] == 1 and body['missing_files'] == []
    assert body['source_versions']['export_schema'] == '1.0.0'

    # the original is untouched and the copy stands on its own
    assert client.get(f"/api/submissions/{original['id']}").status_code == 200
    imported = client.get(f'/api/submissions/{new_id}').json()
    assert imported['subject'] == '数学' and imported['user_id'] == 'local'
    exams = client.get(f'/api/submissions/{new_id}/exams').json()
    assert len(exams) == 1 and exams[0]['submission_id'] == new_id
    records = client.get(f"/api/exams/{exams[0]['id']}/question-records").json()['records']
    assert [r['question_text'] for r in records] == ['求函数的定义域']
    # the uploaded file really came along
    asset = client.get(f"/api/exams/{exams[0]['id']}/assets").json()['assets'][0]
    assert asset['file_missing'] is False
    assert paths.Path(asset['storage_path']).read_bytes() == b'1. the original bytes'
    # derived views work off the imported data without any further analysis
    assert client.get(f'/api/submissions/{new_id}/wrong-questions').json()['summary']['total'] == 1
    assert client.get(f'/api/submissions/{new_id}/trends').json()['exams'][0]['wrong_count'] == 1


def test_json_import_keeps_the_records_but_marks_assets_missing():
    client = TestClient(app_module.app)
    original, _ = _seeded_submission(client)
    snapshot = client.get(f"/api/submissions/{original['id']}/export").json()
    result = client.post('/api/submissions/import', files={'file': ('backup.json', json.dumps(snapshot).encode('utf-8'), 'application/json')})
    assert result.status_code == 201
    body = result.json()
    assert body['restored_files'] == 0 and len(body['missing_files']) == 1
    exams = client.get(f"/api/submissions/{body['submission_id']}/exams").json()
    asset = client.get(f"/api/exams/{exams[0]['id']}/assets").json()['assets'][0]
    assert asset['file_missing'] is True and asset['storage_path'] == ''  # no path pointing at nothing


def test_importing_the_same_bundle_twice_creates_two_independent_copies():
    client = TestClient(app_module.app)
    original, _ = _seeded_submission(client)
    bundle = client.get(f"/api/submissions/{original['id']}/export", params={'format': 'zip'}).content
    first = client.post('/api/submissions/import', files={'file': ('b.zip', bundle, 'application/zip')}).json()
    second = client.post('/api/submissions/import', files={'file': ('b.zip', bundle, 'application/zip')}).json()
    assert first['submission_id'] != second['submission_id']
    assert len(client.get('/api/submissions').json()) == 3  # original + two copies


def test_import_transfers_ownership_and_export_rejects_bad_formats():
    client = TestClient(app_module.app)
    original, _ = _seeded_submission(client)
    bundle = client.get(f"/api/submissions/{original['id']}/export", params={'format': 'zip'}).content
    other = TestClient(app_module.app, headers={'X-User-Id': 'other-person'})
    imported = other.post('/api/submissions/import', files={'file': ('b.zip', bundle, 'application/zip')}).json()
    assert other.get(f"/api/submissions/{imported['submission_id']}").status_code == 200
    assert client.get(f"/api/submissions/{imported['submission_id']}").status_code == 404  # now theirs, not ours
    assert client.get(f"/api/submissions/{original['id']}/export", params={'format': 'tar'}).status_code == 422


def test_import_rejects_files_that_are_not_exports():
    client = TestClient(app_module.app)
    bad = client.post('/api/submissions/import', files={'file': ('x.json', b'{"hello": 1}', 'application/json')})
    assert bad.status_code == 422 and bad.json()['error'] == 'invalid_bundle'
    junk = client.post('/api/submissions/import', files={'file': ('x.bin', b'\x00not-a-bundle', 'application/octet-stream')})
    assert junk.status_code == 422 and 'zip' in junk.json()['detail']
