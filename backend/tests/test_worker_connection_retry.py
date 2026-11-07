import sys

import pytest
from sqlalchemy.exc import OperationalError

from backend.app import worker


def test_worker_recovers_from_database_connection_drop(monkeypatch):
    monkeypatch.setattr(sys,"argv",["worker"])
    monkeypatch.setattr(worker,"initialize_local",lambda:None)
    attempts=[]
    waits=[]

    def work(_factory):
        attempts.append(1)
        if len(attempts)==1:
            raise OperationalError("connection",None,Exception("test connection drop"))
        raise KeyboardInterrupt

    monkeypatch.setattr(worker,"work_once",work)
    monkeypatch.setattr(worker.time,"sleep",waits.append)
    with pytest.raises(KeyboardInterrupt):
        worker.main()
    assert len(attempts)==2 and waits==[2]
