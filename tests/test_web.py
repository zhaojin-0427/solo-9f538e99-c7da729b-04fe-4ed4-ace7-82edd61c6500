import io

import pytest

from app import create_app, db

DEFINITION = """\
冷却泵已安装
备用泵外观检查 | 1=是
备用泵压力检查 | 1=是 & 2=是
安全阀检查
"""


@pytest.fixture()
def app(tmp_path):
    application = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "test.db"),
            "SECRET_KEY": "test",
        }
    )
    yield application


@pytest.fixture()
def client(app):
    return app.test_client()


def make_version(client, name="表版A", definition=DEFINITION):
    response = client.post(
        "/versions",
        data={"name": name, "source": "一号机", "definition": definition},
    )
    assert response.status_code == 302, response.data
    return int(response.headers["Location"].rstrip("/").split("/")[-1])


def csv_bytes(rows):
    header = "记录号,冷却泵已安装,备用泵外观检查,备用泵压力检查,安全阀检查\n"
    body = "".join(
        f"{record_no},{a},{b},{c},{d}\n" for record_no, a, b, c, d in rows
    )
    return (header + body).encode("utf-8")


def upload(client, version_id, data):
    return client.post(
        f"/versions/{version_id}/import",
        data={"csv": (io.BytesIO(data), "records.csv")},
        content_type="multipart/form-data",
    )


def test_index_and_create_version(client):
    assert client.get("/").status_code == 200
    version_id = make_version(client)
    page = client.get(f"/versions/{version_id}").get_data(as_text=True)
    assert "冷却泵已安装" in page
    assert "始终显示" in page


def test_invalid_definition_not_saved(client):
    response = client.post(
        "/versions",
        data={"name": "坏表版", "definition": "甲 | 1=是"},
    )
    assert response.status_code == 400
    assert "只能引用前面" in response.get_data(as_text=True)
    assert client.get("/").get_data(as_text=True).count("坏表版") == 0


def test_import_and_counts(client):
    version_id = make_version(client)
    response = upload(
        client,
        version_id,
        csv_bytes(
            [
                ("R1", "是", "是", "是", "是"),
                ("R2", "是", "是", "", ""),
                ("R3", "否", "是", "是", ""),
                ("R4", "", "", "", ""),
            ]
        ),
    )
    assert response.status_code == 302
    with client.application.app_context():
        counts = db.get_counts(version_id, 4)
    # 冷却泵已安装: always shown -> original answers only.
    assert counts[0] == {"answered": 3, "missing": 1, "exempt": 0, "pending": 0}
    # 备用泵外观检查: R1/R2 answered, R3 exempt (parent 否), R4 pending
    assert counts[1] == {"answered": 2, "missing": 0, "exempt": 1, "pending": 1}
    # 备用泵压力检查: R1 answered; R2 missing; R3 exempt (parent 1 is 否);
    # R4 pending because 冷却泵留空 decides whether it is shown.
    assert counts[2] == {"answered": 1, "missing": 1, "exempt": 1, "pending": 1}
    # 安全阀: always shown -> answered 1, missing 3
    assert counts[3] == {"answered": 1, "missing": 3, "exempt": 0, "pending": 0}


def test_second_import_rejected(client):
    version_id = make_version(client)
    assert upload(client, version_id, csv_bytes([("R1", "是", "是", "是", "是")])).status_code == 302
    response = upload(client, version_id, csv_bytes([("R2", "是", "是", "是", "是")]))
    assert response.status_code == 400
    with client.application.app_context():
        assert db.get_counts(version_id, 4)[0]["answered"] == 1


def test_csv_validation_rejects_whole_file(client):
    version_id = make_version(client)
    bad = (
        "记录号,冷却泵已安装,备用泵外观检查,备用泵压力检查,安全阀检查\n"
        "R1,是,是,是,是\n"
        "R1,是,是,是,是\n"
        "R2,是,也许,是,是\n"
    ).encode("utf-8")
    response = upload(client, version_id, bad)
    assert response.status_code == 400
    page = response.get_data(as_text=True)
    assert "记录号重复" in page
    assert "只能填是、否或留空" in page
    with client.application.app_context():
        assert db.get_counts(version_id, 4)[0]["answered"] == 0


def test_missing_column_rejected(client):
    version_id = make_version(client)
    bad = "记录号,冷却泵已安装,安全阀检查\nR1,是,是\n".encode("utf-8")
    response = upload(client, version_id, bad)
    assert response.status_code == 400
    assert "缺少检查项列" in response.get_data(as_text=True)


def test_record_lookup_and_correction(client):
    version_id = make_version(client)
    upload(client, version_id, csv_bytes([("R4", "", "", "", "")]))

    page = client.get(f"/versions/{version_id}/records/R4").get_data(as_text=True)
    assert "待核" in page
    assert "漏填" in page

    response = client.post(
        f"/versions/{version_id}/records/R4/correct",
        data={"item_index": "0", "value": "否"},
        follow_redirects=True,
    )
    page = response.get_data(as_text=True)
    assert "更正已保存" in page
    # The response itself shows the new judgment and refreshed counts.
    assert 'badge exempt' in page
    assert '本表版当前计数' in page
    # 冷却泵 now 否: 备用泵两项 exempt; 安全阀 was blank and always shown.
    with client.application.app_context():
        counts = db.get_counts(version_id, 4)
        record = db.get_record(version_id, "R4")
    assert counts[0] == {"answered": 1, "missing": 0, "exempt": 0, "pending": 0}
    assert counts[1]["exempt"] == 1
    assert record["categories"] == ["answered", "exempt", "exempt", "missing"]


def test_correction_only_rejudges_one_record(client):
    version_id = make_version(client)
    upload(
        client,
        version_id,
        csv_bytes(
            [
                ("R1", "是", "是", "是", "是"),
                ("R2", "", "", "", ""),
            ]
        ),
    )
    client.post(
        f"/versions/{version_id}/records/R2/correct",
        data={"item_index": "0", "value": "是"},
    )
    with client.application.app_context():
        first = db.get_record(version_id, "R1")
        second = db.get_record(version_id, "R2")
        counts = db.get_counts(version_id, 4)
    assert first["categories"] == ["answered"] * 4
    # 备用泵压力检查 depends on 备用泵外观检查 which is blank -> pending.
    assert second["categories"] == ["answered", "missing", "pending", "missing"]
    assert counts[0] == {"answered": 2, "missing": 0, "exempt": 0, "pending": 0}


def test_failed_correction_leaves_everything_unchanged(client, monkeypatch):
    version_id = make_version(client)
    upload(client, version_id, csv_bytes([("R1", "是", "是", "是", "是")]))
    with client.application.app_context():
        before_record = db.get_record(version_id, "R1")
        before_counts = db.get_counts(version_id, 4)

    # Simulate a failure after the writes but before COMMIT.
    def boom(_connection):
        raise RuntimeError("simulated disk failure")

    monkeypatch.setattr(db, "_commit", boom)
    response = client.post(
        f"/versions/{version_id}/records/R1/correct",
        data={"item_index": "0", "value": "否"},
        follow_redirects=True,
    )
    assert "更正失败" in response.get_data(as_text=True)

    with client.application.app_context():
        after_record = db.get_record(version_id, "R1")
        after_counts = db.get_counts(version_id, 4)
    assert after_record["answers"] == before_record["answers"]
    assert after_record["categories"] == before_record["categories"]
    assert after_counts == before_counts


def test_failed_correction_survives_restart(tmp_path):
    database = str(tmp_path / "restart.db")

    first_app = create_app({"TESTING": True, "DATABASE": database, "SECRET_KEY": "x"})
    with first_app.test_client() as client:
        version_id = make_version(client)
        upload(client, version_id, csv_bytes([("R1", "是", "是", "是", "是")]))

    # Force a failure by patching utcnow during the correction.
    failing_app = create_app(
        {"TESTING": True, "DATABASE": database, "SECRET_KEY": "x"}
    )
    original = db._commit
    try:
        db._commit = lambda _connection: (_ for _ in ()).throw(
            RuntimeError("disk full")
        )
        with failing_app.test_client() as client:
            response = client.post(
                f"/versions/{version_id}/records/R1/correct",
                data={"item_index": "0", "value": "否"},
            )
            assert response.status_code == 302
    finally:
        db._commit = original

    restarted = create_app({"TESTING": True, "DATABASE": database, "SECRET_KEY": "x"})
    with restarted.test_client() as client:
        page = client.get(f"/versions/{version_id}/records/R1").get_data(as_text=True)
        assert "是" in page
        with restarted.app_context():
            record = db.get_record(version_id, "R1")
            counts = db.get_counts(version_id, 4)
    assert record["answers"][0] == "是"
    assert record["categories"] == ["answered"] * 4
    assert counts[0]["answered"] == 1


def test_unknown_record_404(client):
    version_id = make_version(client)
    assert client.get(f"/versions/{version_id}/records/NOPE").status_code == 404


def test_invalid_correction_value_rejected(client):
    version_id = make_version(client)
    upload(client, version_id, csv_bytes([("R1", "是", "是", "是", "是")]))
    response = client.post(
        f"/versions/{version_id}/records/R1/correct",
        data={"item_index": "0", "value": "也许"},
        follow_redirects=True,
    )
    assert "更正失败" in response.get_data(as_text=True)
    with client.application.app_context():
        assert db.get_record(version_id, "R1")["answers"][0] == "是"


def test_fifty_thousand_records_import(client):
    version_id = make_version(client)
    rows = [
        (f"R{i}", "是", "是", "是" if i % 2 else "", "否")
        for i in range(50_000)
    ]
    response = upload(client, version_id, csv_bytes(rows))
    assert response.status_code == 302
    with client.application.app_context():
        assert db.get_version(version_id)["record_count"] == 50_000


def test_over_limit_rejected(client):
    version_id = make_version(client)
    rows = [(f"R{i}", "是", "是", "是", "否") for i in range(50_001)]
    response = upload(client, version_id, csv_bytes(rows))
    assert response.status_code == 400
    assert "最多导入 50000" in response.get_data(as_text=True).replace(",", "")


def test_versions_are_immutable_at_database_level(app):
    import sqlite3

    with app.app_context():
        version_id = db.create_version("v", "", {"items": []})
        connection = db.get_db()
        for statement in (
            "UPDATE versions SET name = 'x' WHERE id = ?",
            "DELETE FROM versions WHERE id = ?",
        ):
            try:
                connection.execute(statement, (version_id,))
            except sqlite3.DatabaseError as exc:
                assert "immutable" in str(exc)
            else:  # pragma: no cover
                raise AssertionError("version mutation should be rejected")


def test_utf8_bom_and_gb18030_csv(client):
    version_id = make_version(client)
    header = "记录号,冷却泵已安装,备用泵外观检查,备用泵压力检查,安全阀检查\n"
    body = "R1,是,是,是,是\n"
    data = ("﻿" + header + body).encode("utf-8")
    assert upload(client, version_id, data).status_code == 302

    version_id2 = make_version(client, name="表版B")
    data2 = (header + "G1,否,,,\n").encode("gb18030")
    assert upload(client, version_id2, data2).status_code == 302
    with client.application.app_context():
        assert db.get_record(version_id2, "G1")["answers"] == ["否", "", "", ""]


def test_record_lookup_redirects_and_reports_missing(client):
    version_id = make_version(client)
    upload(client, version_id, csv_bytes([("R1", "是", "是", "是", "是")]))
    response = client.get(f"/versions/{version_id}/records?record_no=R1")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/versions/%d/records/R1" % version_id)

    response = client.get(
        f"/versions/{version_id}/records?record_no=NOPE", follow_redirects=True
    )
    assert "没有找到记录号" in response.get_data(as_text=True)


def test_missing_record_column_rejected(client):
    version_id = make_version(client)
    bad = (
        "编号错误,冷却泵已安装,备用泵外观检查,备用泵压力检查,安全阀检查\n"
        "R1,是,是,是,是\n"
    ).encode("utf-8")
    response = upload(client, version_id, bad)
    assert response.status_code == 400
    assert "缺少“记录号”列" in response.get_data(as_text=True)
