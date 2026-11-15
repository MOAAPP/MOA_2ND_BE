from fastapi.testclient import TestClient
from unittest.mock import patch
from app.main import app
from app.services import state_store

def row(day, topic, correct):
    return dict(id=day.replace('-', '')+topic, user_id='S01', date=day, topic=topic,
                selection_reason='user_interest', source='rule_based', model=None,
                question='개념을 확인하는 금융 학습 문제입니다', options=['가','나','다','라'],
                answer=0, explanation='필수 결제를 먼저 확인해요.',
                answer_record={'choice':0 if correct else 1,'correct':correct})

def test_overview_uses_latest_answer_per_topic_and_does_not_call_ai(tmp_path, monkeypatch):
    monkeypatch.setenv('MOA_RUNTIME_DIR',str(tmp_path))
    for key in ('VERCEL','BLOB_STORE_ID','BLOB_READ_WRITE_TOKEN'):
        monkeypatch.delenv(key,raising=False)
    rows=[row('2026-11-02','saving',False),row('2026-11-03','credit',False),row('2026-11-04','saving',True)]
    state_store.put('learning/S01.json',{'quizzes':{r['date']:r for r in rows}})
    client=TestClient(app)
    with patch('app.services.learning_ai.structured_text') as ai:
        response=client.get('/api/v4/users/S01/learning-overview?day=2026-11-04')
    assert response.status_code==200
    body=response.json()
    assert body['attempt_count']==3 and body['streak_days']==3
    assert body['review_topic_count']==1
    topics={t['topic']:t for t in body['topics']}
    assert not topics['saving']['needs_review']
    assert topics['credit']['next_review_date']=='2026-11-04'
    assert topics['saving']['correct_count']==1
    assert not ai.called
    assert client.get('/api/v4/users/S02/learning-overview?day=2026-11-04').json()['attempt_count']==0
    earlier=client.get('/api/v4/users/S01/learning-overview?day=2026-11-02').json()
    assert earlier['attempt_count']==1 and earlier['review_topic_count']==1
    assert client.get('/api/v4/users/S01/learning-overview?day=2026-11-06').json()['streak_days']==0
    assert client.get('/api/v4/users/S99/learning-overview').status_code==404
    assert client.get('/api/v4/users/S01/learning-overview?day=2099-01-01').status_code==422
    schema=client.get('/openapi.json').json()
    assert 'LearningOverviewResponse' in schema['components']['schemas']

def test_hint_does_not_expose_answer_before_submission(tmp_path, monkeypatch):
    monkeypatch.setenv('MOA_RUNTIME_DIR',str(tmp_path))
    for key in ('VERCEL','BLOB_STORE_ID','BLOB_READ_WRITE_TOKEN','MOA_AI_API_KEY','OPENAI_API_KEY','MOA_AI_PROVIDER'):
        monkeypatch.delenv(key,raising=False)
    quiz=TestClient(app).get('/api/v4/users/S01/daily-quiz?day=2026-11-04').json()
    assert len(quiz['hint'])>10
    assert quiz['correct_answer'] is None and quiz['explanation'] is None
