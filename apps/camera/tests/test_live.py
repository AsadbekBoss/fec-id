from typing import Any

from fastapi.testclient import TestClient

from camera.api import create_app
from camera.config import Settings
from camera.live import ObservationLog
from camera.supervisor import Supervisor

from .conftest import TOKEN, test_spec, wait_until

AUTH = {"Authorization": f"Bearer {TOKEN}"}


def face(decision: str, emp: str | None = None, score: float = 0.5) -> dict[str, Any]:
    return {
        "decision": decision,
        "employee_id": emp,
        "top1_score": score,
        "quality": {"face_width_px": 140, "reasons": []},
    }


def test_identity_only_for_matched_and_coalescing() -> None:
    log = ObservationLog(coalesce_seconds=3)
    log.add("c1", face("MATCHED", "emp-1", 0.7), 0.0)
    log.add("c1", face("MATCHED", "emp-1", 0.72), 1.0)  # same person again: coalesced
    log.add("c1", face("AMBIGUOUS", "emp-1", 0.38), 1.5)  # uncertain: identity dropped
    log.add("c1", face("MATCHED", "emp-1", 0.7), 10.0)  # later: new entry
    items, last = log.since(0)
    assert [(i["decision"], i["employee_id"], i["count"]) for i in items] == [
        ("MATCHED", "emp-1", 2),
        ("AMBIGUOUS", None, 1),
        ("MATCHED", "emp-1", 1),
    ]
    assert last == 4 and log.since(last)[0] == []
    assert all("_t" not in i for i in items)


class FakeRecognizer:
    def __init__(self) -> None:
        self.calls = 0

    def recognize(self, jpeg: bytes) -> dict[str, Any]:
        assert jpeg[:2] == b"\xff\xd8"
        self.calls += 1
        return {"faces": [face("MATCHED", "emp-7", 0.8)]}


def test_live_recognition_only_when_enabled(settings: Settings) -> None:
    rec = FakeRecognizer()
    sup = Supervisor(settings, recognizer=rec)
    try:
        sup.apply([test_spec("off"), test_spec("on").model_copy(update={"recognition_enabled": True})])
        assert wait_until(lambda: rec.calls >= 2, timeout=8)
        statuses = {s["camera_id"]: s for s in sup.live_statuses()}
        assert set(statuses) == {"on"} and statuses["on"]["recognition"] == "active"
        items, _ = sup.observations.since(0)
        assert items and {i["camera_id"] for i in items} == {"on"} and items[0]["employee_id"] == "emp-7"
        # switching recognition off (new config version) stops the live loop
        sup.apply([test_spec("on", version=2)])
        assert sup.live_statuses() == []
    finally:
        sup.stop_all()


def test_observations_endpoint(settings: Settings) -> None:
    sup = Supervisor(settings, recognizer=FakeRecognizer())
    sup.observations.add("c1", face("UNKNOWN", None, 0.1), 0.0)
    with TestClient(create_app(settings, supervisor=sup)) as c:
        assert c.get("/internal/v1/observations").status_code == 401
        r = c.get("/internal/v1/observations", params={"since": 0}, headers=AUTH).json()
    assert r["configured"] is True and r["next"] == 1
    assert r["observations"][0]["decision"] == "UNKNOWN" and r["observations"][0]["employee_id"] is None
