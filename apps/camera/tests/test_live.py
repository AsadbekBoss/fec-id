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


class FakeSink:
    def __init__(self, resp: dict[str, Any]) -> None:
        self.resp, self.payloads = resp, []  # type: ignore[var-annotated]

    def report(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        return self.resp


def test_confident_matches_are_reported_with_cooldown(settings: Settings) -> None:
    sink = FakeSink({"recorded": True, "counted": True, "direction": "IN"})
    sup = Supervisor(settings, recognizer=FakeRecognizer(), sink=sink)
    try:
        sup.apply([test_spec("door").model_copy(update={"recognition_enabled": True})])
        assert wait_until(
            lambda: len(sup.observations.since(0)[0]) > 0 and sup.live_statuses()[0]["frames_sent"] >= 3, timeout=8
        )
        # many sightings, ONE report (cooldown: 5 min by default)
        assert len(sink.payloads) == 1
        p = sink.payloads[0]
        assert p["decision"] == "MATCHED" and p["employee_id"] == "emp-7" and p["camera_id"] == "door"
        assert p["similarity"] == 0.8 and "event_uid" in p and p["event_time"].endswith("+00:00")
        assert sup.observations.since(0)[0][0]["attendance"] == "IN"
    finally:
        sup.stop_all()


def test_attendance_summary() -> None:
    from camera.live import attendance_summary

    assert attendance_summary({"recorded": False, "reason": "attendance_disabled"}) == "attendance_disabled"
    assert attendance_summary({"recorded": True, "counted": True, "direction": "OUT"}) == "OUT"
    assert attendance_summary({"recorded": True, "counted": False, "ignore_reason": "DEBOUNCED"}) == "DEBOUNCED"


def test_crop_face_is_a_small_crop_not_the_frame() -> None:
    import cv2
    import numpy as np

    from camera.live import crop_face

    frame = np.zeros((720, 1280, 3), np.uint8)
    jpeg = crop_face(frame, [600, 300, 100, 120])
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert img is not None and img.shape[1] < 400 and img.shape[0] < 400  # face + margin only
    tiny = cv2.imdecode(np.frombuffer(crop_face(frame, [10, 10, 30, 30]), np.uint8), cv2.IMREAD_COLOR)
    assert min(tiny.shape[:2]) >= 128  # upscaled for the reviewer
    assert crop_face(frame, [0, 0, 0, 0]) == b""


class ReviewRecognizer:
    def recognize(self, jpeg: bytes) -> dict[str, Any]:
        return {
            "model_id": "m@1",
            "faces": [
                {
                    "decision": "AMBIGUOUS",
                    "employee_id": None,
                    "top1_score": 0.38,
                    "box": [100, 100, 120, 140],
                    "detection_score": 0.9,
                    "quality": {"face_width_px": 120, "reasons": []},
                }
            ],
        }


class ReviewSink(FakeSink):
    def __init__(self) -> None:
        super().__init__({"recorded": False})
        self.reviews: list[tuple[dict[str, Any], bytes]] = []

    def report_review(self, meta: dict[str, Any], face_jpeg: bytes) -> dict[str, Any]:
        self.reviews.append((meta, face_jpeg))
        return {"stored": True, "review_id": 1}


def test_uncertain_faces_go_to_review_once_per_minute(settings: Settings) -> None:
    sink = ReviewSink()
    sup = Supervisor(settings, recognizer=ReviewRecognizer(), sink=sink)
    try:
        sup.apply([test_spec("door").model_copy(update={"recognition_enabled": True})])
        assert wait_until(lambda: sup.live_statuses() and sup.live_statuses()[0]["frames_sent"] >= 3, timeout=8)
        assert len(sink.reviews) == 1 and sink.payloads == []  # never reported as attendance
        meta, jpeg = sink.reviews[0]
        assert meta["decision"] == "AMBIGUOUS" and "employee_id" not in meta and jpeg[:2] == b"\xff\xd8"
        item = sup.observations.since(0)[0][0]
        assert item["attendance"] == "review" and item["employee_id"] is None
    finally:
        sup.stop_all()


class VisitorRecognizer:
    """Two strangers take turns in front of the camera: v1, v2, v1, v2, ..."""

    def __init__(self) -> None:
        self.calls = 0

    def recognize(self, jpeg: bytes) -> dict[str, Any]:
        self.calls += 1
        face = {
            "decision": "UNKNOWN",
            "employee_id": None,
            "top1_score": 0.1,
            "box": [100, 100, 120, 140],
            "detection_score": 0.9,
            "quality": {"face_width_px": 120, "reasons": []},
            "visitor_id": f"v{1 + self.calls % 2}",
        }
        return {"model_id": "m@1", "faces": [face]}


def test_one_review_card_per_visitor(settings: Settings) -> None:
    sink = ReviewSink()
    rec = VisitorRecognizer()
    sup = Supervisor(settings, recognizer=rec, sink=sink)
    try:
        sup.apply([test_spec("door").model_copy(update={"recognition_enabled": True})])
        assert wait_until(lambda: rec.calls >= 6, timeout=10)
        # both people got a card at once (no per-minute wait), each exactly one
        assert sorted(m["decision"] for m, _ in sink.reviews) == ["UNKNOWN", "UNKNOWN"]
        assert len(sink.reviews) == 2
    finally:
        sup.stop_all()
