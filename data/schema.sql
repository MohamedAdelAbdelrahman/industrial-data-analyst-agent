-- Industrial sensor database (SQLite)
-- All timestamps are ISO-8601 text in UTC: 'YYYY-MM-DD HH:MM:SS'
-- This file is also used verbatim as the schema block in the Text-to-SQL prompts.

PRAGMA foreign_keys = ON;

CREATE TABLE machines (
    machine_id    INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,      -- e.g. 'CNC-Mill-01'
    machine_type  TEXT NOT NULL,             -- 'CNC Mill','Compressor','Pump','Conveyor','Hydraulic Press','Robot Arm'
    manufacturer  TEXT NOT NULL,
    model         TEXT NOT NULL,
    plant         TEXT NOT NULL,             -- 'Plant A','Plant B'
    line          TEXT NOT NULL,             -- 'Line 1'..'Line 4'
    install_date  TEXT NOT NULL,             -- 'YYYY-MM-DD'
    status        TEXT NOT NULL              -- 'running','idle','maintenance','decommissioned'
);

CREATE TABLE sensors (
    sensor_id       INTEGER PRIMARY KEY,
    machine_id      INTEGER NOT NULL REFERENCES machines(machine_id),
    sensor_type     TEXT NOT NULL,           -- 'temperature','vibration','pressure','current','rpm','flow'
    unit            TEXT NOT NULL,           -- 'degC','mm/s','bar','A','rpm','l/min'
    mount_location  TEXT NOT NULL,           -- e.g. 'spindle bearing', 'outlet'
    warn_threshold  REAL NOT NULL,           -- HIGH alarm above this value
    crit_threshold  REAL NOT NULL,           -- HIGH_HIGH alarm above this value
    install_date    TEXT NOT NULL
);

CREATE TABLE readings (
    reading_id  INTEGER PRIMARY KEY,
    sensor_id   INTEGER NOT NULL REFERENCES sensors(sensor_id),
    ts          TEXT NOT NULL,               -- measurement time
    value       REAL,                        -- NULL when quality = 'bad'
    quality     TEXT NOT NULL                -- 'good','uncertain','bad'
);

CREATE TABLE alarms (
    alarm_id          INTEGER PRIMARY KEY,
    sensor_id         INTEGER NOT NULL REFERENCES sensors(sensor_id),
    machine_id        INTEGER NOT NULL REFERENCES machines(machine_id),
    alarm_type        TEXT NOT NULL,         -- 'HIGH','HIGH_HIGH','COMM_FAULT'
    severity          TEXT NOT NULL,         -- 'warning','critical'
    triggered_at      TEXT NOT NULL,
    cleared_at        TEXT,                  -- NULL = still active
    peak_value        REAL,                  -- max value during alarm (NULL for COMM_FAULT)
    acknowledged_by   TEXT,                  -- NULL = not acknowledged
    acknowledged_at   TEXT
);

CREATE TABLE maintenance_events (
    event_id          INTEGER PRIMARY KEY,
    machine_id        INTEGER NOT NULL REFERENCES machines(machine_id),
    event_type        TEXT NOT NULL,         -- 'preventive','corrective','inspection'
    start_time        TEXT NOT NULL,
    end_time          TEXT NOT NULL,
    technician        TEXT NOT NULL,
    cost_eur          REAL NOT NULL,
    description       TEXT NOT NULL,
    related_alarm_id  INTEGER REFERENCES alarms(alarm_id)  -- set for corrective events triggered by an alarm
);

CREATE INDEX idx_sensors_machine  ON sensors(machine_id);
CREATE INDEX idx_readings_sensor_ts ON readings(sensor_id, ts);
CREATE INDEX idx_alarms_machine_ts ON alarms(machine_id, triggered_at);
CREATE INDEX idx_alarms_sensor    ON alarms(sensor_id);
CREATE INDEX idx_maint_machine    ON maintenance_events(machine_id, start_time);
