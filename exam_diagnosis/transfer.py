"""Take your data with you: export a submission as a portable bundle and import it back.

`GET /api/submissions/{id}/export` has always produced a JSON snapshot, but there was no way in —
so the data could leave and never return. A new machine, a reinstall, or moving between the web
deployment and the desktop app all meant starting over. This module is the other half.

Two shapes:
- **JSON** — the existing snapshot, metadata only. Uploaded files are not in it, so an import
  from JSON marks every asset `file_missing` rather than pointing at a path that isn't there.
- **ZIP bundle** — the same JSON as `data.json` plus the actual uploaded files under `uploads/`.
  This is the one that actually moves a student's work.

Import always **remaps every id**. Preserving ids would be simpler, but then importing a bundle
onto the machine it came from would silently overwrite the live records instead of landing beside
them. Remapping makes import purely additive: it can never destroy what's already there.

Ids are remapped by walking the whole payload and replacing any string that is a known old id.
That's deliberate rather than enumerating each cross-reference by hand — reports alone reference
question ids from four different nesting depths, and a missed path would produce a bundle that
imports "successfully" while quietly pointing at nothing. Ids are UUIDs, so a value colliding with
unrelated content isn't a practical concern; mastery ids are `submission:knowledge:cause`
composites and are rebuilt explicitly.
"""
from __future__ import annotations

import io
import json
import posixpath
import zipfile
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

BUNDLE_DATA_NAME = 'data.json'
BUNDLE_UPLOADS_PREFIX = 'uploads/'
BUNDLE_SCHEMA = '1.0.0'
# Collections in the export whose top-level `id` identifies a record.
ID_COLLECTIONS = ('exams', 'assets', 'pages', 'question_records', 'analysis_runs', 'verification_questions')
MAX_BUNDLE_BYTES = 200 * 1024 * 1024


class BundleError(ValueError):
    """The bundle can't be read or doesn't contain what an export should."""


def _new_id() -> str:
    return str(uuid4())


def build_id_map(payload: dict[str, Any], new_id: Callable[[], str] = _new_id) -> dict[str, str]:
    """Every old id in the export mapped to a fresh one. Mastery ids are excluded: they are
    composites rebuilt from the new submission id after the remap."""
    mapping: dict[str, str] = {}
    submission_id = (payload.get('submission') or {}).get('id')
    if submission_id:
        mapping[submission_id] = new_id()
    for collection in ID_COLLECTIONS:
        for item in payload.get(collection) or []:
            old = item.get('id')
            if isinstance(old, str) and old and old not in mapping:
                mapping[old] = new_id()
    return mapping


def remap(value: Any, mapping: dict[str, str]) -> Any:
    """Deep copy with every known id replaced, wherever it appears — including as a dict key,
    which is how some report sections index things by question id."""
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [remap(item, mapping) for item in value]
    if isinstance(value, dict):
        return {mapping.get(key, key) if isinstance(key, str) else key: remap(item, mapping) for key, item in value.items()}
    return value


def prepare_import(payload: dict[str, Any], user_id: str, new_id: Callable[[], str] = _new_id) -> dict[str, Any]:
    """Turn an exported payload into records ready to be written, owned by `user_id`.

    Returns {'submission', 'exams', 'assets', 'pages', 'question_records', 'analysis_runs',
    'verification_questions', 'mastery_records', 'id_map'}. Nothing is written here — the caller
    decides that, and can validate first.
    """
    submission = payload.get('submission')
    if not isinstance(submission, dict) or not submission.get('id'):
        raise BundleError('导入文件里没有 submission，可能不是本应用导出的数据')
    mapping = build_id_map(payload, new_id)
    remapped = remap(payload, mapping)
    result: dict[str, Any] = {'id_map': mapping}
    result['submission'] = {**remapped['submission'], 'user_id': user_id}
    for collection in ID_COLLECTIONS:
        result[collection] = [{**item, 'user_id': user_id} for item in remapped.get(collection) or []]
    # Mastery ids are `submission_id:knowledge_id:error_cluster`; the submission id inside them
    # changed, so they're rebuilt rather than string-replaced.
    new_submission_id = result['submission']['id']
    mastery = []
    for item in remapped.get('mastery_records') or []:
        record = {**item, 'user_id': user_id, 'submission_id': new_submission_id}
        record['id'] = f"{new_submission_id}:{item.get('knowledge_id')}:{item.get('error_cluster')}"
        mastery.append(record)
    result['mastery_records'] = mastery
    return result


# --- bundles ---------------------------------------------------------------------------------------


def bundle_asset_name(asset: dict[str, Any]) -> str:
    """Where an asset's file lives inside the bundle: keyed by the asset's own id, so a bundle
    never depends on the original machine's directory layout or on filenames being unique."""
    suffix = Path(str(asset.get('filename') or '')).suffix
    return f"{BUNDLE_UPLOADS_PREFIX}{asset.get('id')}{suffix}"


def build_bundle(payload: dict[str, Any], read_file: Callable[[dict[str, Any]], bytes | None]) -> bytes:
    """Zip the JSON snapshot together with each asset's bytes. `read_file` returns None for an
    asset whose file is gone; the bundle records that instead of failing the whole export."""
    buffer = io.BytesIO()
    manifest = {'schema': BUNDLE_SCHEMA, 'assets': {}, 'missing_assets': []}
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for asset in payload.get('assets') or []:
            content = read_file(asset)
            if content is None:
                manifest['missing_assets'].append(asset.get('id'))
                continue
            name = bundle_asset_name(asset)
            archive.writestr(name, content)
            manifest['assets'][asset.get('id')] = name
        archive.writestr(BUNDLE_DATA_NAME, json.dumps({**payload, 'bundle': manifest}, ensure_ascii=False, indent=2))
    return buffer.getvalue()


def read_bundle(data: bytes) -> tuple[dict[str, Any], dict[str, bytes]]:
    """(payload, {old_asset_id: file bytes}). Accepts a raw JSON export too, in which case there
    are no files — the caller marks those assets missing."""
    stripped = data.lstrip()
    if stripped[:1] == b'{':
        try:
            payload = json.loads(data.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BundleError(f'JSON 无法解析：{exc}') from None
        if not isinstance(payload, dict):
            raise BundleError('JSON 顶层必须是一个对象')
        return payload, {}
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise BundleError('既不是 JSON 也不是有效的 zip 备份包') from None
    with archive:
        try:
            payload = json.loads(archive.read(BUNDLE_DATA_NAME).decode('utf-8'))
        except KeyError:
            raise BundleError(f'备份包里没有 {BUNDLE_DATA_NAME}') from None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BundleError(f'备份包里的 {BUNDLE_DATA_NAME} 无法解析：{exc}') from None
        if not isinstance(payload, dict):
            raise BundleError(f'{BUNDLE_DATA_NAME} 顶层必须是一个对象')
        names = {name for name in archive.namelist()}
        files: dict[str, bytes] = {}
        for asset in payload.get('assets') or []:
            name = (payload.get('bundle') or {}).get('assets', {}).get(asset.get('id')) or bundle_asset_name(asset)
            # Refuse anything that would escape the uploads prefix — a zip is attacker-shaped
            # input even when it usually came from us.
            if name in names and posixpath.normpath(name).startswith(BUNDLE_UPLOADS_PREFIX) and '..' not in name:
                files[asset.get('id')] = archive.read(name)
    return payload, files
