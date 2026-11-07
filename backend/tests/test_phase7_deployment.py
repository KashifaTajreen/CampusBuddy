import pytest
from backend.app.config import Settings
from backend.scripts.start_service import validate_assets
from backend.tests.test_auth_api import client as client, key as key


def test_deployment_requires_explicit_model_assets(tmp_path):
    with pytest.raises(ValueError,match="E5"):
        validate_assets(Settings(_env_file=None,embedding_provider="onnx",embedding_model_path=str(tmp_path)))
    with pytest.raises(ValueError,match="Whisper"):
        validate_assets(Settings(_env_file=None,embedding_provider="lexical",speech_model_path=str(tmp_path)))
    with pytest.raises(ValueError,match="OCR"):
        validate_assets(Settings(_env_file=None,embedding_provider="lexical",speech_enabled=False,ocr_tessdata_path=str(tmp_path)))
    validate_assets(Settings(_env_file=None,embedding_provider="lexical",speech_enabled=False,ocr_enabled=False))


def test_readiness_returns_no_data_and_detects_unmigrated_database(client,env,monkeypatch):
    from backend.app import db
    from backend.app.db import make_engine
    assert client.get("/ready").json()=={"status":"ready"}
    empty=make_engine("sqlite:///:memory:")
    monkeypatch.setattr(db,"engine",empty)
    response=client.get("/ready")
    assert response.status_code==503 and response.json()=={"detail":"Database readiness check failed."}
    empty.dispose()
