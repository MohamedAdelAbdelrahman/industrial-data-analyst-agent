"""Smoke tests: the generated DB is internally consistent (alarms <-> readings <-> maintenance)."""
import sqlite3

import pytest


@pytest.fixture(scope="module")
def db(small_db):
    con = sqlite3.connect(small_db)
    yield con
    con.close()


def scalar(con, sql):
    return con.execute(sql).fetchone()[0]


def test_tables_populated(db):
    for t in ["machines", "sensors", "readings", "alarms", "maintenance_events"]:
        assert scalar(db, f"SELECT COUNT(*) FROM {t}") > 0, t


def test_bad_quality_readings_are_null(db):
    assert scalar(db, "SELECT COUNT(*) FROM readings WHERE quality = 'bad' AND value IS NOT NULL") == 0


def test_decommissioned_machine_has_no_readings(db):
    assert scalar(db, """
        SELECT COUNT(*) FROM readings r JOIN sensors s USING (sensor_id)
        JOIN machines m USING (machine_id) WHERE m.status = 'decommissioned'""") == 0


def test_threshold_alarm_peaks_exceed_warn(db):
    assert scalar(db, """
        SELECT COUNT(*) FROM alarms a JOIN sensors s USING (sensor_id)
        WHERE a.alarm_type IN ('HIGH','HIGH_HIGH') AND a.peak_value <= s.warn_threshold""") == 0


def test_critical_alarms_exceed_crit(db):
    assert scalar(db, """
        SELECT COUNT(*) FROM alarms a JOIN sensors s USING (sensor_id)
        WHERE a.severity = 'critical' AND a.peak_value <= s.crit_threshold""") == 0


def test_corrective_events_link_to_critical_alarms(db):
    assert scalar(db, """
        SELECT COUNT(*) FROM maintenance_events e JOIN alarms a ON a.alarm_id = e.related_alarm_id
        WHERE a.severity <> 'critical' OR a.machine_id <> e.machine_id""") == 0
