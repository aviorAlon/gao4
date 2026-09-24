"""Spaced repetition for mastery records: when should this come back?

A mastery record already tracks *whether* a knowledge point's error is resolved (not_started →
in_progress → provisionally_resolved → resolved). What it never had is *when to ask again*, so a
student who passed a verification question once was never prompted a second time and the loop
quietly ended there. This module adds the schedule.

The ladder is deliberately short and conventional (1/3/7/16/35 days). This is not a claim to have
tuned an optimal forgetting curve for this subject — it's a defensible default that spreads
reviews out as they succeed, and `REVIEW_INTERVALS_DAYS` is one tuple to change if evidence ever
says otherwise.

Records created before scheduling existed have no `due_at`. They are treated as due now rather
than as "never scheduled": they exist because the student got something wrong, so surfacing them
is the honest default and no data migration is needed.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable

REVIEW_INTERVALS_DAYS = (1, 3, 7, 16, 35)
# A failed attempt doesn't reset to zero-and-tomorrow only — it goes back to the first rung, so
# the point is asked again soon and has to climb the ladder from the bottom again.
FAILURE_STAGE = 0
# Status set by the existing state machine that means "no more scheduling needed".
RESOLVED_STATUS = 'resolved'

RESULT_NOTE = '复习间隔是一个通用的默认梯度（1/3/7/16/35 天），不是针对本学科调过的遗忘曲线。'


def _iso(moment: datetime) -> str:
    return moment.isoformat() + 'Z'


def parse_due(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.rstrip('Z'))
    except ValueError:
        return None


def initial_schedule(now: datetime | None = None) -> dict[str, Any]:
    """A brand-new mastery record is due immediately: the student just got this wrong."""
    now = now or datetime.utcnow()
    return {'review_stage': 0, 'due_at': _iso(now), 'last_reviewed_at': None, 'interval_days': 0}


def schedule_after_attempt(record: dict[str, Any], passed: bool, now: datetime | None = None) -> dict[str, Any]:
    """Advance (or reset) the schedule after an attempt. Mutates and returns `record`.

    A pass moves one rung up the ladder; a failure drops back to the bottom. Once a record has
    climbed past the last rung *and* the status machine calls it resolved, it stops being
    scheduled at all — `due_at` becomes None, which the plan reports as 已巩固.
    """
    now = now or datetime.utcnow()
    stage = int(record.get('review_stage') or 0)
    stage = stage + 1 if passed else FAILURE_STAGE
    record['review_stage'] = stage
    record['last_reviewed_at'] = _iso(now)
    if passed and stage > len(REVIEW_INTERVALS_DAYS) and record.get('status') == RESOLVED_STATUS:
        record['due_at'] = None
        record['interval_days'] = None
        return record
    # `review_stage` counts consecutive passes, so the rung to wait out after the Nth pass is
    # the Nth interval — index N-1. Stage 0 (a fresh record or one that just failed) waits out
    # the first rung too.
    interval = REVIEW_INTERVALS_DAYS[min(max(stage - 1, 0), len(REVIEW_INTERVALS_DAYS) - 1)]
    record['interval_days'] = interval
    record['due_at'] = _iso(now + timedelta(days=interval))
    return record


def due_state(record: dict[str, Any], now: datetime | None = None) -> tuple[str, int | None]:
    """('due' | 'upcoming' | 'completed', days until due — negative when overdue)."""
    now = now or datetime.utcnow()
    if record.get('status') == RESOLVED_STATUS and 'due_at' in record and record['due_at'] is None:
        return 'completed', None
    due = parse_due(record.get('due_at'))
    if due is None:
        # Never scheduled (predates this feature, or a record written by an older version):
        # it exists because something was wrong, so ask now rather than losing it.
        return 'due', 0
    delta_days = (due.date() - now.date()).days
    return ('upcoming' if due > now else 'due'), delta_days


def build_review_plan(
    records: Iterable[dict[str, Any]],
    questions_by_knowledge: dict[tuple[str, str], dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Group a user's mastery records into what to do now, what is coming, and what is done.

    `questions_by_knowledge` maps (submission_id, knowledge_id) to the verification question that
    should be practised, so the plan can link straight to it instead of making the caller hunt.
    """
    now = now or datetime.utcnow()
    questions_by_knowledge = questions_by_knowledge or {}
    buckets: dict[str, list[dict[str, Any]]] = {'due': [], 'upcoming': [], 'completed': []}
    for record in records:
        state, days = due_state(record, now)
        question = questions_by_knowledge.get((record.get('submission_id'), record.get('knowledge_id')))
        buckets[state].append({
            'mastery_id': record.get('id'),
            'submission_id': record.get('submission_id'),
            'knowledge_id': record.get('knowledge_id'),
            'error_cluster': record.get('error_cluster'),
            'status': record.get('status'),
            'review_stage': int(record.get('review_stage') or 0),
            'interval_days': record.get('interval_days'),
            'due_at': record.get('due_at'),
            'last_reviewed_at': record.get('last_reviewed_at'),
            'attempt_count': len(record.get('attempt_history') or []),
            'days_until_due': days,
            'overdue_days': -days if days is not None and days < 0 else 0,
            'verification_question_id': (question or {}).get('id'),
            'analysis_run_id': (question or {}).get('analysis_run_id'),
            'question_text': (question or {}).get('question_text'),
        })
    # Most overdue first: the thing that has waited longest is the thing to do now.
    buckets['due'].sort(key=lambda item: (item['days_until_due'] if item['days_until_due'] is not None else 0, item['knowledge_id'] or ''))
    buckets['upcoming'].sort(key=lambda item: (item['due_at'] or '', item['knowledge_id'] or ''))
    buckets['completed'].sort(key=lambda item: (item['last_reviewed_at'] or '', item['knowledge_id'] or ''))
    return {
        'due': buckets['due'],
        'upcoming': buckets['upcoming'],
        'completed': buckets['completed'],
        'summary': {
            'due_count': len(buckets['due']),
            'overdue_count': sum(1 for item in buckets['due'] if item['overdue_days'] > 0),
            'upcoming_count': len(buckets['upcoming']),
            'completed_count': len(buckets['completed']),
            'next_due_at': buckets['upcoming'][0]['due_at'] if buckets['upcoming'] else None,
        },
        'intervals_days': list(REVIEW_INTERVALS_DAYS),
        'note': RESULT_NOTE,
    }
