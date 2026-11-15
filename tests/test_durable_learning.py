from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.services import state_store

@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setenv('MOA_RUNTIME_DIR',str(tmp_path))
    for name in ('BLOB_STORE_ID','BLOB_READ_WRITE_TOKEN','MOA_AI_API_KEY','OPENAI_API_KEY','MOA_AI_PROVIDER','VERCEL'):
        monkeypatch.delenv(name,raising=False)

def test_quiz_survives_new_clients_and_answer_is_not_lost():
    quiz=TestClient(app).get('/api/v4/users/S01/daily-quiz?day=2026-11-04').json()
    assert TestClient(app).get('/api/v4/users/S01/daily-quiz?day=2026-11-04').json()['id']==quiz['id']
    answer=TestClient(app).post('/api/v4/users/S01/daily-quiz/answer?day=2026-11-04',json={'quiz_id':quiz['id'],'choice':0})
    assert answer.status_code==200,answer.text
    history=TestClient(app).get('/api/v4/users/S01/learning-history?day=2026-11-04').json()
    assert history['attempt_count']==1
    assert history['items'][0]['id']==quiz['id']
    assert TestClient(app).get('/api/v4/users/S02/learning-history?day=2026-11-04').json()['attempt_count']==0

def test_compare_and_swap_rejects_a_stale_writer():
    state_store.put('tests/state.json',{'count':0})
    _,old=state_store.read('tests/state.json')
    state_store.put('tests/state.json',{'count':1})
    with pytest.raises(state_store.StateConflict):
        state_store.write('tests/state.json',{'count':99},old)
    assert state_store.get('tests/state.json')['count']==1


def test_fresh_home_concurrent_requests_initialize_storage_safely():
    paths = ['/api/v4/users/S01/daily-quiz?day=2026-11-15',
             '/api/v4/users/S01/learning-interests?day=2026-11-15',
             '/api/v4/users/S01/learning-history?day=2026-11-15']
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(pool.map(lambda path: TestClient(app).get(path), paths))
    assert [response.status_code for response in responses] == [200, 200, 200]
    assert responses[0].json()['user_id'] == 'S01'

def test_device_lease_and_images_survive_new_worker(monkeypatch):
    monkeypatch.setenv('MOA_INTERNAL_SECRET','test-only-internal-secret')
    header={'X-MOA-Internal-Secret':'test-only-internal-secret'}
    device='a'*32; owner='b'*32
    path=f'/api/v4/internal/card-state/{device}'
    assert TestClient(app).post(path,headers=header,json={'action':'acquire','owner':owner}).status_code==200
    assert TestClient(app).post(path,headers=header,json={'action':'acquire','owner':'c'*32}).status_code==409
    assert TestClient(app).post(path,headers=header,json={'action':'save','owner':owner,'state':{'jobs':{},'subscriptions':{}}}).status_code==200
    assert TestClient(app).post(path,headers=header,json={'action':'release','owner':owner}).status_code==200
    assert TestClient(app).get(path,headers=header).json()['state']['jobs']=={}
    assert TestClient(app).get(path).status_code==401

def test_vercel_never_silently_uses_ephemeral_sqlite(monkeypatch):
    monkeypatch.setenv('VERCEL','1')
    result=TestClient(app).post('/api/v4/learning/storage-check')
    assert result.status_code==503
    assert result.json()['detail']['code']=='durable_storage_unavailable'

def test_learning_api_surface_is_exposed_in_swagger():
    paths=TestClient(app).get('/openapi.json').json()['paths']
    for path in ('/api/v4/learning/cardnews','/api/v4/learning/subscription','/api/v4/learning/daily/preview',
                 '/api/v4/learning/deliveries/run','/api/v4/learning/card-explanation','/api/v4/learning/storage-check'):
        assert path in paths


def test_private_blob_reads_bypass_cdn(monkeypatch):
    import httpx
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_teststore_dummy")
    def read(url, **kwargs):
        assert kwargs["params"] == {"cache": "0"}
        assert kwargs["headers"]["Accept-Encoding"] == "identity"
        return httpx.Response(200, json={"count": 2}, headers={"etag": "version-2"}, request=httpx.Request("GET", url))
    monkeypatch.setattr(state_store.httpx, "get", read)
    assert state_store.read("tests/cache.json") == ({"count": 2}, "version-2")


def test_preview_swagger_forwards_input_without_subscription(monkeypatch):
    from app.api import learning_delivery
    calls=[]
    monkeypatch.setattr(learning_delivery,"_render_api",lambda path,device,method,body: calls.append((path,device,method,body)) or {"job":{"status":"generated"}})
    result=TestClient(app).post('/api/v4/learning/daily/preview',headers={'X-Device-Id':'91f5aa04-928d-4a04-a0ae-123456789abc'},json={'learningUserId':'S08','level':2,'watchlist':['005930']})
    assert result.status_code==200
    assert calls[0][0]=='/api/daily/preview'
    assert calls[0][3]['learningUserId']=='S08'
    assert calls[0][3]['level']==2
    assert 'enabled' not in calls[0][3]
