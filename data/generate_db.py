#!/usr/bin/env python3
"""Generate a synthetic industrial sensor SQLite database.

Usage:
    python data/generate_db.py --out data/sensors.db --seed 42 --days 60 --interval-min 15

Design notes
  * Readings follow a daily load cycle + noise; a few anomaly episodes per sensor
    ramp a value above its warn/crit thresholds.
  * Alarms are DERIVED from the readings (runs above threshold), so alarms,
    readings and thresholds are mutually consistent -> questions that join them
    have verifiable, non-trivial answers.
  * Corrective maintenance is linked to critical alarms (related_alarm_id).
  * Deliberate "SQL traps": NULL values (bad quality), active alarms
    (cleared_at IS NULL), unacknowledged alarms, machines without alarms,
    sensors of different units, a decommissioned machine.
"""
import argparse
import os
import random
import sqlite3
from datetime import datetime, timedelta

import numpy as np

FMT = "%Y-%m-%d %H:%M:%S"

# machine_type -> (manufacturers/models, sensor profile)
# sensor profile: (sensor_type, unit, mount_location, base, noise, warn, crit)
PROFILES = {
    "CNC Mill": {
        "makers": [("DMG Mori", "DMU 50"), ("Haas", "VF-2"), ("Mazak", "VCN-530C")],
        "sensors": [
            ("temperature", "degC", "spindle bearing", 55, 1.5, 75, 90),
            ("vibration", "mm/s", "spindle housing", 2.0, 0.3, 4.5, 7.1),
            ("rpm", "rpm", "spindle", 8000, 150, 11000, 12000),
            ("current", "A", "main drive", 18, 1.2, 28, 34),
        ],
    },
    "Compressor": {
        "makers": [("Atlas Copco", "GA 37"), ("Kaeser", "CSD 85"), ("Ingersoll Rand", "R55")],
        "sensors": [
            ("temperature", "degC", "discharge", 85, 2.0, 105, 115),
            ("pressure", "bar", "outlet", 7.5, 0.15, 8.5, 9.5),
            ("vibration", "mm/s", "motor", 2.5, 0.4, 5.0, 8.0),
            ("current", "A", "motor", 55, 3.0, 75, 88),
        ],
    },
    "Pump": {
        "makers": [("Grundfos", "NK 80"), ("KSB", "Etanorm 50"), ("Wilo", "IL 100")],
        "sensors": [
            ("pressure", "bar", "outlet", 4.0, 0.1, 5.2, 6.0),
            ("flow", "l/min", "outlet", 320, 12, 420, 470),
            ("vibration", "mm/s", "bearing", 1.8, 0.25, 4.0, 6.5),
            ("temperature", "degC", "motor winding", 60, 1.8, 80, 95),
        ],
    },
    "Conveyor": {
        "makers": [("Siemens", "SIMOTICS-C"), ("Interroll", "DC-48"), ("Dematic", "BeltMax")],
        "sensors": [
            ("current", "A", "drive motor", 9, 0.8, 14, 18),
            ("temperature", "degC", "gearbox", 48, 1.2, 70, 85),
            ("vibration", "mm/s", "drive end", 1.5, 0.2, 3.5, 5.5),
        ],
    },
    "Hydraulic Press": {
        "makers": [("Schuler", "HP-400"), ("Lasco", "SPS 250"), ("Dieffenbacher", "DCP-630")],
        "sensors": [
            ("pressure", "bar", "hydraulic circuit", 180, 4, 220, 250),
            ("temperature", "degC", "oil tank", 45, 1.5, 65, 75),
            ("flow", "l/min", "pump outlet", 90, 4, 120, 140),
            ("vibration", "mm/s", "ram", 2.2, 0.3, 4.8, 7.5),
        ],
    },
    "Robot Arm": {
        "makers": [("KUKA", "KR 210"), ("ABB", "IRB 6700"), ("Fanuc", "M-20iD")],
        "sensors": [
            ("temperature", "degC", "joint 2 motor", 50, 1.5, 72, 85),
            ("current", "A", "joint 1 drive", 12, 1.0, 20, 26),
            ("vibration", "mm/s", "base", 1.2, 0.2, 3.0, 4.8),
        ],
    },
}

# (machine_type, count)
FLEET = [("CNC Mill", 6), ("Compressor", 4), ("Pump", 6), ("Conveyor", 4),
         ("Hydraulic Press", 3), ("Robot Arm", 4)]

PLANTS = ["Plant A", "Plant B"]
LINES = ["Line 1", "Line 2", "Line 3", "Line 4"]
TECHNICIANS = ["M. Becker", "S. Hoffmann", "L. Novak", "T. Schmidt", "A. Rossi", "K. Weber"]
ACK_USERS = ["operator_jmeier", "operator_kfischer", "operator_tbauer", "shift_lead_r"]

PREVENTIVE_DESC = ["Scheduled lubrication and filter change", "Quarterly preventive service",
                   "Bearing check and recalibration", "Routine preventive maintenance"]
CORRECTIVE_DESC = ["Replaced worn bearing", "Replaced faulty sensor and recalibrated",
                   "Cleaned cooling circuit after overheating", "Repaired drive belt",
                   "Replaced seal and refilled fluid", "Tightened loose mounting and rebalanced"]
INSPECTION_DESC = ["Visual inspection, no findings", "Thermal imaging inspection",
                   "Safety inspection per DIN EN ISO 13849", "Vibration analysis walk-down"]


def fmt(dt):
    return dt.strftime(FMT)


def build_fleet(rng, end):
    machines, sensors = [], []
    mid = sid = 0
    counters = {}
    for mtype, n in FLEET:
        for _ in range(n):
            mid += 1
            counters[mtype] = counters.get(mtype, 0) + 1
            maker, model = rng.choice(PROFILES[mtype]["makers"])
            install = (end - timedelta(days=rng.randint(400, 3000))).date()
            status = rng.choices(["running", "idle", "maintenance"], [0.8, 0.15, 0.05])[0]
            machines.append(dict(
                machine_id=mid,
                name=f"{mtype.replace(' ', '-')}-{counters[mtype]:02d}",
                machine_type=mtype, manufacturer=maker, model=model,
                plant=rng.choice(PLANTS), line=rng.choice(LINES),
                install_date=install.isoformat(), status=status))
            for (stype, unit, loc, base, noise, warn, crit) in PROFILES[mtype]["sensors"]:
                sid += 1
                # per-sensor jitter so machines of the same type differ
                j = rng.uniform(0.95, 1.05)
                sensors.append(dict(
                    sensor_id=sid, machine_id=mid, sensor_type=stype, unit=unit,
                    mount_location=loc, base=base * j, noise=noise,
                    warn=round(warn, 2), crit=round(crit, 2),
                    install_date=install.isoformat()))
    # guarantee status variety for question coverage
    for i in (4, 13, 20):
        machines[i]["status"] = "maintenance"
    # one decommissioned machine (no readings in the window -> good edge case)
    machines[-1]["status"] = "decommissioned"
    return machines, sensors


def gen_readings(np_rng, s, times):
    """Return (values array, quality list) for one sensor."""
    n = len(times)
    hours = np.array([t.hour + t.minute / 60 for t in times])
    load = 0.5 + 0.5 * np.sin((hours - 6) / 24 * 2 * np.pi)           # daily load cycle
    weekday = np.array([t.weekday() for t in times])
    load = np.where(weekday >= 5, load * 0.6, load)                    # weekends quieter
    span = (s["warn"] - s["base"])
    values = s["base"] + 0.08 * span * load + np_rng.normal(0, s["noise"], n)

    # anomaly episodes: triangular ramps that may cross warn and sometimes crit
    for _ in range(np_rng.poisson(1.2)):
        length = int(np_rng.integers(8, 60))
        if n <= length + 2:
            continue
        start = int(np_rng.integers(0, n - length))
        peak_target = s["warn"] + np_rng.uniform(-0.1, 1.4) * (s["crit"] - s["warn"])
        amp = peak_target - s["base"]
        ramp = np.concatenate([np.linspace(0, 1, length // 2), np.linspace(1, 0, length - length // 2)])
        values[start:start + length] += amp * ramp

    # a few sensors are still in alarm at the end of the window -> active alarms
    tail_alarm = np_rng.random() < 0.07
    if tail_alarm:
        k = int(np_rng.integers(4, 12))
        floor = s["warn"] + np_rng.uniform(0.05, 0.9) * (s["crit"] - s["warn"])
        values[-k:] = np.maximum(values[-k:], floor)

    values = np.round(values, 2)
    r = np_rng.random(n)
    quality = np.where(r < 0.004, "bad", np.where(r < 0.02, "uncertain", "good"))
    if tail_alarm:
        quality[-k:] = "good"
    return values, quality


def runs(mask):
    """Yield (start, end_inclusive) index pairs for consecutive True runs."""
    idx = np.flatnonzero(np.diff(np.concatenate([[0], mask.astype(int), [0]])))
    return list(zip(idx[::2], idx[1::2] - 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="sensors.db")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--interval-min", type=int, default=15)
    ap.add_argument("--end", default="2026-10-01 00:00:00")
    ap.add_argument("--schema", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql"))
    args = ap.parse_args()

    rng = random.Random(args.seed)
    np_rng = np.random.default_rng(args.seed)
    end = datetime.strptime(args.end, FMT)
    start = end - timedelta(days=args.days)
    step = timedelta(minutes=args.interval_min)
    times = [start + i * step for i in range(int((end - start) / step))]

    machines, sensors = build_fleet(rng, end)
    decommissioned = {m["machine_id"] for m in machines if m["status"] == "decommissioned"}

    if os.path.exists(args.out):
        os.remove(args.out)
    con = sqlite3.connect(args.out)
    with open(args.schema) as f:
        con.executescript(f.read())

    con.executemany("INSERT INTO machines VALUES (:machine_id,:name,:machine_type,:manufacturer,:model,:plant,:line,:install_date,:status)", machines)
    con.executemany("INSERT INTO sensors VALUES (:sensor_id,:machine_id,:sensor_type,:unit,:mount_location,:warn,:crit,:install_date)",
                    [dict(s, ) for s in sensors])

    ts_str = [fmt(t) for t in times]
    reading_id = 0
    alarm_id = 0
    alarms = []
    for s in sensors:
        if s["machine_id"] in decommissioned:
            continue
        values, quality = gen_readings(np_rng, s, times)
        valid = quality != "bad"

        rows = []
        for i in range(len(times)):
            reading_id += 1
            rows.append((reading_id, s["sensor_id"], ts_str[i],
                         None if not valid[i] else float(values[i]), str(quality[i])))
        con.executemany("INSERT INTO readings VALUES (?,?,?,?,?)", rows)

        # alarms derived from readings
        above = np.where(valid, values > s["warn"], False)
        for a, b in runs(above):
            if b - a + 1 < 2:          # ignore single-sample blips
                continue
            peak = float(values[a:b + 1].max())
            crit = peak > s["crit"]
            alarm_id += 1
            trig = times[a]
            cleared = times[b] + step
            active = (b == len(times) - 1)
            ack = rng.random() < 0.85
            ack_at = trig + timedelta(minutes=rng.randint(2, 90)) if ack else None
            alarms.append((alarm_id, s["sensor_id"], s["machine_id"],
                           "HIGH_HIGH" if crit else "HIGH",
                           "critical" if crit else "warning",
                           fmt(trig), None if active else fmt(cleared), round(peak, 2),
                           rng.choice(ACK_USERS) if ack else None,
                           fmt(ack_at) if ack_at else None))
        # occasional comm faults tied to long bad-quality gaps
        if rng.random() < 0.25:
            t0 = times[rng.randint(0, len(times) - 50)]
            alarm_id += 1
            alarms.append((alarm_id, s["sensor_id"], s["machine_id"], "COMM_FAULT", "warning",
                           fmt(t0), fmt(t0 + timedelta(minutes=rng.randint(20, 240))), None,
                           None, None))

    con.executemany("INSERT INTO alarms VALUES (?,?,?,?,?,?,?,?,?,?)", alarms)

    # ---- maintenance ----
    events = []
    eid = 0

    def add_event(mid, etype, st, hours, desc, alarm=None):
        nonlocal eid
        eid += 1
        cost = {"preventive": rng.uniform(200, 1500), "corrective": rng.uniform(500, 8000),
                "inspection": rng.uniform(80, 400)}[etype]
        events.append((eid, mid, etype, fmt(st), fmt(st + timedelta(hours=hours)),
                       rng.choice(TECHNICIANS), round(cost, 2), desc, alarm))

    for m in machines:
        if m["machine_id"] in decommissioned:
            continue
        t = start + timedelta(days=rng.randint(0, 25))
        while t < end - timedelta(days=1):
            add_event(m["machine_id"], "preventive", t, rng.randint(2, 8), rng.choice(PREVENTIVE_DESC))
            t += timedelta(days=rng.randint(27, 33))
        for _ in range(rng.randint(0, 2)):
            t = start + timedelta(days=rng.randint(0, args.days - 2), hours=rng.randint(6, 16))
            add_event(m["machine_id"], "inspection", t, rng.randint(1, 3), rng.choice(INSPECTION_DESC))

    for a in alarms:
        if a[4] == "critical" and rng.random() < 0.45:
            trig = datetime.strptime(a[5], FMT)
            st = trig + timedelta(hours=rng.randint(1, 12))
            if st < end:
                add_event(a[2], "corrective", st, rng.randint(2, 14), rng.choice(CORRECTIVE_DESC), a[0])

    events.sort(key=lambda e: e[3])
    events = [(i + 1,) + e[1:] for i, e in enumerate(events)]   # renumber chronologically
    # related_alarm_id preserved since alarm ids are untouched
    con.executemany("INSERT INTO maintenance_events VALUES (?,?,?,?,?,?,?,?,?)", events)

    con.commit()
    for t in ["machines", "sensors", "readings", "alarms", "maintenance_events"]:
        print(f"{t:20s} {con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:>9,d}")
    con.close()


if __name__ == "__main__":
    main()
