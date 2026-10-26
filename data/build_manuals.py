#!/usr/bin/env python3
"""Build synthetic machine manuals (one PDF per machine type) for search_manuals.

Usage:
    python data/build_manuals.py --out data/manuals

Alarm thresholds and sensor lists are imported from generate_db.PROFILES, so the
manuals always agree with the database. Each manual contains concrete, checkable
facts (limits, intervals, part numbers) that the eval set can ask about.
"""
import argparse
import html
from pathlib import Path

import pymupdf
from generate_db import PROFILES

MEDIABOX = pymupdf.paper_rect("a4")
WHERE = MEDIABOX + (50, 60, -50, -60)

# Troubleshooting per (machine_type, sensor_type): (probable causes, corrective actions)
TROUBLESHOOTING = {
    "CNC Mill": {
        "temperature": (
            ["Insufficient oil-air lubrication of the spindle bearings",
             "Spindle chiller fault or low coolant level",
             "Excessive bearing preload after a tool crash",
             "Prolonged operation above 10,000 rpm"],
            ["Check the oil-air lubrication unit: supply pressure must be 4-6 bar.",
             "Verify the spindle chiller set point is 20 +/- 2 degC and the coolant level is above MIN.",
             "Run the spindle warm-up program (50% speed, 10 min) before production.",
             "At HIGH_HIGH: stop the spindle, let it cool for 30 min and inspect the bearings "
             "before restart. Replace with bearing set SP-CNC-BRG-7014 if damaged."]),
        "vibration": (
            ["Tool or tool-holder imbalance",
             "Worn spindle bearings",
             "Low drawbar clamping force",
             "Chatter from aggressive cutting parameters"],
            ["Balance tools to grade G2.5 at 12,000 rpm.",
             "Measure drawbar clamping force: minimum 18 kN; replace disc springs (SP-CNC-DSP-018) if lower.",
             "Reduce depth of cut by 30% and re-check.",
             "Above 7.1 mm/s (ISO 10816-3 zone D) stop the machine and replace bearing set SP-CNC-BRG-7014."]),
        "rpm": (
            ["Program S-word above the rated spindle speed",
             "Spindle encoder cable damaged",
             "Drive parameter set incorrectly after service"],
            ["Check the NC program speed command; rated maximum spindle speed is 12,000 rpm.",
             "Inspect the encoder cable and connector X12 on the spindle drive.",
             "Restore drive parameters from the last backup (parameter group P1000)."]),
        "current": (
            ["Worn or dull cutting tools",
             "Feed rate too high for the material",
             "Insufficient axis way lubrication"],
            ["Check tool wear; replace inserts when flank wear exceeds 0.3 mm.",
             "Reduce feed override by 20% and observe the drive current.",
             "Check the way-lube tank level and the lubrication interval (every 30 min of axis travel)."]),
    },
    "Compressor": {
        "temperature": (
            ["Clogged oil cooler or aftercooler fins",
             "Low compressor oil level",
             "Ambient temperature above 40 degC or poor room ventilation",
             "Thermostatic bypass valve stuck open"],
            ["Clean the cooler fins with compressed air from the inside out.",
             "Check the oil level in the sight glass; top up with RotoFluid-46 only.",
             "Ensure room ventilation keeps ambient temperature below 40 degC.",
             "At HIGH_HIGH the controller trips the compressor automatically. Replace the "
             "thermostatic valve kit SP-CMP-TV-46 before restart if the fault repeats."]),
        "pressure": (
            ["Unload pressure set point configured too high",
             "Minimum-pressure valve stuck",
             "Downstream consumers or isolation valves closed"],
            ["Verify the unload pressure set point is 8.0 bar (load at 7.0 bar).",
             "Inspect the minimum-pressure valve; service kit SP-CMP-MPV-02.",
             "Never bypass the safety valve; it opens at 10 bar and must be tested every 12 months."]),
        "vibration": (
            ["Motor-to-air-end misalignment",
             "Worn coupling element",
             "Motor bearing damage"],
            ["Perform laser shaft alignment (max offset 0.05 mm).",
             "Replace the flexible coupling element SP-CMP-CPL-55.",
             "Re-grease motor bearings with 20 g of Polyrex EM every 4,000 operating hours."]),
        "current": (
            ["Supply voltage below tolerance",
             "Operating pressure above set point",
             "Air-end wear increasing torque demand"],
            ["Measure the supply voltage: 400 V +/- 10% on all three phases.",
             "Check the pressure set point (see pressure troubleshooting).",
             "If current stays high at normal pressure, schedule an air-end inspection."]),
    },
    "Pump": {
        "pressure": (
            ["Discharge valve partially closed",
             "Clogged downstream filter",
             "Blocked or scaled discharge pipe"],
            ["Check all discharge valve positions against the P&ID.",
             "Measure differential pressure across the downstream filter; replace the element "
             "SP-PMP-FLT-80 if it exceeds 0.8 bar.",
             "Never operate the pump against a closed valve for more than 30 seconds."]),
        "flow": (
            ["Bypass valve open",
             "Leak or burst pipe downstream",
             "Pump operating at run-out (far right of the curve)"],
            ["Verify the bypass valve is closed during normal operation.",
             "Walk down the discharge line for leaks.",
             "Throttle the discharge valve back to the best-efficiency point (320 l/min); run-out "
             "operation causes cavitation and motor overload."]),
        "vibration": (
            ["Cavitation due to low suction pressure (insufficient NPSH)",
             "Pump/motor misalignment",
             "Worn pump bearings or damaged impeller"],
            ["Check suction pressure: it must stay above 0.5 bar. A gravel-like noise indicates cavitation.",
             "Perform laser alignment (max offset 0.05 mm).",
             "Replace the bearing kit SP-PMP-BRG-62 and inspect the impeller for erosion."]),
        "temperature": (
            ["Motor overload",
             "Blocked motor cooling fan or fan cover",
             "Phase imbalance above 2%"],
            ["Compare motor current to the nameplate rating.",
             "Clean the fan cover; the motor needs 10 cm of free space for airflow.",
             "Measure phase voltages; imbalance above 2% must be corrected by the electrical team."]),
    },
    "Conveyor": {
        "current": (
            ["Material jam or overloaded belt",
             "Belt mistracking rubbing on the frame",
             "Seized carrying or return roller"],
            ["Stop the conveyor and lock out before clearing any jam.",
             "Check belt tracking and tension (deflection 15-20 mm per metre of span).",
             "Locate seized rollers with a thermal camera and replace (SP-CNV-ROL-89)."]),
        "temperature": (
            ["Low gearbox oil level",
             "Wrong oil grade",
             "Continuous overload"],
            ["Check gearbox oil level weekly.",
             "Use only ISO VG 220 synthetic gear oil; change the oil every 2,500 operating hours.",
             "At HIGH_HIGH stop the conveyor and check for overload before restart."]),
        "vibration": (
            ["Damaged belt splice",
             "Worn drive-end bearing",
             "Loose drive mounting bolts"],
            ["Inspect the belt splice for lifting or cracks.",
             "Replace the drive-end bearing unit SP-CNV-BRG-208.",
             "Re-torque drive mounting bolts to 85 Nm."]),
    },
    "Hydraulic Press": {
        "pressure": (
            ["Pressure relief valve set incorrectly",
             "Blocked return line or return filter",
             "Proportional valve fault"],
            ["Check the main relief valve setting: 260 bar. Only authorised staff may adjust it.",
             "Inspect the return filter indicator; replace element SP-HYD-RF-10 when red.",
             "Run the proportional valve self-test from the HMI service menu."]),
        "temperature": (
            ["Oil cooler fouled or cooling water flow too low",
             "Internal leakage in cylinders or valves",
             "Low oil level in the tank"],
            ["Normal oil temperature is 40-55 degC; check cooling water flow (min 30 l/min).",
             "Check for internal leakage by monitoring pressure decay with the ram held at top position.",
             "Above 75 degC the oil degrades: after any HIGH_HIGH event take an oil sample and "
             "replace the oil (HLP 46, 400 l) if the analysis fails."]),
        "flow": (
            ["Pump displacement controller fault",
             "Leak in the pump outlet line",
             "Accumulator pre-charge lost"],
            ["Check the pump displacement controller signal (4-20 mA).",
             "Inspect hoses and fittings on the pump outlet; replace hoses every 6 years.",
             "Check accumulator nitrogen pre-charge: 90 bar."]),
        "vibration": (
            ["Worn ram guides or gibs",
             "Die misalignment",
             "Loose tooling clamps"],
            ["Measure guide clearance; adjust gibs if clearance exceeds 0.1 mm.",
             "Re-align the die using the alignment pins.",
             "Check hydraulic tool clamps for full clamping pressure."]),
    },
    "Robot Arm": {
        "temperature": (
            ["Duty cycle too high for the programmed motion",
             "Payload above the rated value or wrong payload data",
             "Joint brake not fully releasing"],
            ["Check the payload data in the controller matches the real tool and part.",
             "Reduce the speed override to 70% and add dwell time in the program.",
             "Test the joint 2 brake release; replace the brake (SP-ROB-BRK-J2) if it drags."]),
        "current": (
            ["Collision or contact with fixtures",
             "Gearbox wear on joint 1",
             "Damaged motor power cable"],
            ["Check the collision log in the controller.",
             "Check joint 1 gearbox backlash: max 0.1 deg. Change gearbox oil every 20,000 hours.",
             "Inspect the dress pack and motor cables for wear."]),
        "vibration": (
            ["Loose base anchor bolts",
             "Gearbox backlash",
             "Resonance from high acceleration settings"],
            ["Re-torque base anchor bolts to 300 Nm.",
             "Measure backlash on all axes (see current troubleshooting).",
             "Reduce acceleration in the motion profile by 20%."]),
    },
}

# Maintenance schedule per machine type: (interval, task)
SCHEDULE = {
    "CNC Mill": [("Daily", "Check coolant concentration (6-8%) and level"),
                 ("Weekly", "Clean chip conveyor; check way-lube level"),
                 ("Every 30 days", "Spindle lubrication service and filter change"),
                 ("Every 6 months", "Spindle runout check (max 0.003 mm)"),
                 ("Yearly", "Ball-screw backlash compensation and geometry check")],
    "Compressor": [("Daily", "Check outlet pressure and drain condensate"),
                   ("Every 30 days", "Clean cooler fins; check oil level"),
                   ("Every 2,000 h", "Replace air filter SP-CMP-AF-37"),
                   ("Every 4,000 h", "Replace oil, oil filter and separator element"),
                   ("Every 12 months", "Test safety valve (opens at 10 bar)")],
    "Pump": [("Weekly", "Check mechanical seal for leakage (max 10 drops/min)"),
             ("Every 30 days", "Check alignment and bearing temperature"),
             ("Every 3 months", "Replace downstream filter element SP-PMP-FLT-80"),
             ("Every 12 months", "Replace mechanical seal SP-PMP-SEAL-80")],
    "Conveyor": [("Daily", "Visual check of belt tracking"),
                 ("Every 30 days", "Check belt tension and roller condition"),
                 ("Every 2,500 h", "Change gearbox oil (ISO VG 220)"),
                 ("Yearly", "Replace drive-end bearing unit SP-CNV-BRG-208")],
    "Hydraulic Press": [("Daily", "Check oil level and leaks"),
                        ("Every 30 days", "Check accumulator pre-charge (90 bar)"),
                        ("Every 6 months", "Oil analysis (ISO 4406 target 17/15/12)"),
                        ("Every 6 years", "Replace all hydraulic hoses")],
    "Robot Arm": [("Every 30 days", "Check dress pack and cable wear"),
                  ("Every 6 months", "Check base anchor bolt torque (300 Nm)"),
                  ("Every 12 months", "Replace controller backup battery"),
                  ("Every 20,000 h", "Change gearbox oil on all axes")],
}

ALARM_RESPONSE = [
    ("HIGH (warning)", "Acknowledge within 15 minutes. Review the trend of the last 2 hours and "
     "schedule an inspection within 24 hours. Follow the troubleshooting table for the sensor."),
    ("HIGH_HIGH (critical)", "Reduce load or stop the machine within 5 minutes and notify the shift "
     "lead. Open a corrective maintenance order. Do not restart until the root cause is found."),
    ("COMM_FAULT", "Sensor readings are invalid (quality 'bad'). Check the sensor cable, connector "
     "and I/O module. If the fault lasts longer than 4 hours, take manual readings every hour."),
]

CSS = """
* { font-family: sans-serif; font-size: 10pt; }
h1 { font-size: 18pt; }
h2 { font-size: 13pt; margin-top: 14pt; }
h3 { font-size: 11pt; }
table { border-collapse: collapse; }
th, td { border: 1px solid #888; padding: 3px; }
th { font-weight: bold; }
"""


def esc(s):
    return html.escape(str(s))


def manual_html(mtype):
    prof = PROFILES[mtype]
    models = ", ".join(f"{mk} {md}" for mk, md in prof["makers"])
    parts = [f"<h1>{esc(mtype)}: Operation and Maintenance Manual</h1>",
             f"<p>Applies to: {esc(models)}. Document no. OM-{mtype.upper().replace(' ', '-')}-2026.</p>",
             "<h2>1. Safety</h2>",
             "<p>Lock out and tag out all energy sources before any maintenance. Only trained "
             "personnel may work on this machine. Wear the PPE required by the plant safety rules.</p>",
             "<h2>2. Sensors and alarm thresholds</h2>",
             "<p>An alarm is raised when a value stays above the threshold for at least two "
             "consecutive samples. HIGH is a warning; HIGH_HIGH is critical.</p>",
             "<table><tr><th>Sensor</th><th>Location</th><th>Unit</th><th>Normal</th>"
             "<th>HIGH (warning)</th><th>HIGH_HIGH (critical)</th></tr>"]
    for stype, unit, loc, base, _noise, warn, crit in prof["sensors"]:
        parts.append(f"<tr><td>{esc(stype)}</td><td>{esc(loc)}</td><td>{esc(unit)}</td>"
                     f"<td>approx. {base:g}</td><td>&gt; {warn:g}</td><td>&gt; {crit:g}</td></tr>")
    parts.append("</table>")

    parts.append("<h2>3. Alarm response</h2>")
    for name, text in ALARM_RESPONSE:
        parts.append(f"<p><b>{esc(name)}:</b> {esc(text)}</p>")

    parts.append("<h2>4. Troubleshooting</h2>")
    for i, (stype, unit, loc, *_rest) in enumerate(prof["sensors"], 1):
        causes, actions = TROUBLESHOOTING[mtype][stype]
        parts.append(f"<h3>4.{i} High {esc(stype)} ({esc(loc)}, {esc(unit)})</h3>")
        parts.append("<p><b>Probable causes:</b></p><ul>"
                     + "".join(f"<li>{esc(c)}</li>" for c in causes) + "</ul>")
        parts.append("<p><b>Corrective actions:</b></p><ol>"
                     + "".join(f"<li>{esc(a)}</li>" for a in actions) + "</ol>")

    parts.append("<h2>5. Maintenance schedule</h2>"
                 "<table><tr><th>Interval</th><th>Task</th></tr>")
    for interval, task in SCHEDULE[mtype]:
        parts.append(f"<tr><td>{esc(interval)}</td><td>{esc(task)}</td></tr>")
    parts.append("</table>")
    return "".join(parts)


def write_pdf(body, path, title):
    story = pymupdf.Story(html=body, user_css=CSS)
    writer = pymupdf.DocumentWriter(str(path))
    more = True
    while more:
        dev = writer.begin_page(MEDIABOX)
        more, _ = story.place(WHERE)
        story.draw(dev)
        writer.end_page()
    writer.close()

    # page header/footer so search hits can cite "doc, page"
    doc = pymupdf.open(path)
    for page in doc:
        page.insert_text((50, 40), title, fontsize=8, color=(0.4, 0.4, 0.4))
        page.insert_text((50, MEDIABOX.height - 30), f"Page {page.number + 1} of {doc.page_count}",
                         fontsize=8, color=(0.4, 0.4, 0.4))
    doc.set_metadata({"title": title})
    doc.saveIncr()
    doc.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).parent / "manuals"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for mtype in PROFILES:
        path = out / f"{mtype.lower().replace(' ', '_')}_manual.pdf"
        write_pdf(manual_html(mtype), path, f"{mtype}: Operation and Maintenance Manual")
        print(f"{path.name:32s} {pymupdf.open(path).page_count} pages")


if __name__ == "__main__":
    main()
