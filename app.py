from flask import Flask, render_template, request, redirect, url_for, session, flash
from datetime import datetime
import random
import json
import os
import sqlite3
from contextlib import contextmanager

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Change this to a random string

SESSION_FILE = "web_session.json"
LOG_FILE = "shift_log.txt"
DATABASE_FILE = "shifts.db"

JOB_SITES = [
    "2025 DC water - Washington DC 20032",
    "BRWRF Phase 3 Package 1 - Ashburn 20147",
    "DC Water Projects - Washington DC 20032",
    "[HMMA] Stamp Shop Roof Improvement - Montgomery, Alabama 36105",
    "Hyundai Product Center Office - Carnesville, Georgia",
    "JWA22 - Kokomo, Indiana 46901",
    "[KARR] Korean Ambassador's Residence Renovation, NW Washington 20016",
    "LGES-ALPHA Project-1F0 - Queen Creek 85140",
    "LGESMI2 EV Battery Plant Main BLDG - Holland Michigan 49423",
    "TPO Roof Project - Fairfax 22030"
]

@contextmanager
def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_database():
    with get_db_connection() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS shifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                job_site TEXT NOT NULL,
                clock_in TEXT NOT NULL,
                clock_out TEXT,
                total_time TEXT,
                working_time TEXT,
                breaks TEXT,
                code TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()

def save_session_data(data):
    with open(SESSION_FILE, "w") as f:
        json.dump(data, f)

def load_session_data():
    if os.path.exists(SESSION_FILE):
        with open(SESSION_FILE, "r") as f:
            return json.load(f)
    return {}

def log_event(event, dt, code=None):
    with open(LOG_FILE, "a") as f:
        code_str = f" [Code: {code}]" if code else ""
        f.write(f"{dt.strftime('%Y-%m-%d %I:%M %p')} - {event}{code_str}\n")

def generate_code():
    return str(random.randint(100000, 999999))

def format_seconds(secs):
    hours = int(secs // 3600)
    minutes = int((secs % 3600) // 60)
    return f"{hours}h {minutes}m"

def write_clockin_to_db(name, job_site, clock_in_dt, code):
    with get_db_connection() as conn:
        conn.execute('''
            INSERT INTO shifts (name, job_site, clock_in, code)
            VALUES (?, ?, ?, ?)
        ''', (name, job_site, clock_in_dt.strftime('%Y-%m-%d %I:%M %p'), code))
        conn.commit()

def update_clockout_in_db(code, clock_out_dt, total_time, working_time, breaks_str):
    with get_db_connection() as conn:
        conn.execute('''
            UPDATE shifts 
            SET clock_out = ?, total_time = ?, working_time = ?, breaks = ?
            WHERE code = ? AND clock_out IS NULL
        ''', (clock_out_dt.strftime('%Y-%m-%d %I:%M %p'), total_time, working_time, breaks_str, code))
        conn.commit()

@app.route("/", methods=["GET", "POST"])
def index():
    data = load_session_data()
    clocked_in = data.get("clocked_in", False)
    on_break = data.get("on_break", False)
    code = data.get("code", None)
    clock_in_time = data.get("clock_in_time")
    breaks = data.get("breaks", [])
    name = data.get("name", "")
    job_site = data.get("job_site", "")
    status = ""
    show_code = False

    if request.method == "POST":
        action = request.form.get("action")
        now = datetime.now()

        if action == "clockin" and not clocked_in:
            name = request.form.get("name", "").strip()
            job_site = request.form.get("job_site", "")
            if not name:
                flash("Please enter your name.", "error")
                return redirect(url_for("index"))
            if not job_site:
                flash("Please select a job site.", "error")
                return redirect(url_for("index"))
            code = generate_code()
            log_event("Clocked In", now, code)
            clocked_in = True
            on_break = False
            clock_in_time = now.isoformat()
            breaks = []
            write_clockin_to_db(name, job_site, now, code)
            flash(
                f"Your code is: <b>{code}</b><br>"
                f"<span style='color:red;'>This code is required to clock out. Please write it down or remember it. It will not be shown again!</span>",
                "success"
            )
            status = "Clocked in."
            show_code = False  # Do not show code in template
        elif action == "break" and clocked_in and not on_break:
            log_event("Break Start", now, code)
            on_break = True
            breaks.append({"start": now.isoformat(), "end": None})
            status = "Break started."
            show_code = True
        elif action == "resume" and clocked_in and on_break:
            log_event("Break End", now, code)
            on_break = False
            if breaks and breaks[-1]["end"] is None:
                breaks[-1]["end"] = now.isoformat()
            status = "Break ended."
            show_code = True
        elif action == "clockout" and clocked_in:
            input_code = request.form.get("input_code")
            if input_code != code:
                flash("Incorrect code.", "error")
                return redirect(url_for("index"))
            if on_break:
                log_event("Break End (auto on clock out)", now, code)
                on_break = False
                if breaks and breaks[-1]["end"] is None:
                    breaks[-1]["end"] = now.isoformat()
            log_event("Clocked Out", now, code)
            clocked_in = False

            # Calculate shift times
            if clock_in_time:
                clock_in_dt = datetime.fromisoformat(clock_in_time)
                clock_out_dt = now
                total_time = clock_out_dt - clock_in_dt
                total_break = sum(
                    (datetime.fromisoformat(b["end"]) - datetime.fromisoformat(b["start"])).total_seconds()
                    for b in breaks if b["end"] is not None
                )
                working_time = total_time.total_seconds() - total_break
                breaks_str = "; ".join(
                    f"{datetime.fromisoformat(b['start']).strftime('%I:%M %p')} - {datetime.fromisoformat(b['end']).strftime('%I:%M %p')}"
                    for b in breaks if b["end"] is not None
                )
                update_clockout_in_db(
                    code,
                    clock_out_dt,
                    format_seconds(total_time.total_seconds()),
                    format_seconds(working_time),
                    breaks_str
                )
                flash(
                    f"Shift complete!<br>"
                    f"Total time: <b>{format_seconds(total_time.total_seconds())}</b><br>"
                    f"Actual working time: <b>{format_seconds(working_time)}</b>",
                    "success"
                )
            else:
                flash("Shift complete!", "success")

            code = None
            clock_in_time = None
            breaks = []
            name = ""
            job_site = ""
            show_code = False
        else:
            flash("Invalid action or state.", "error")
            return redirect(url_for("index"))

        save_session_data({
            "clocked_in": clocked_in,
            "on_break": on_break,
            "code": code,
            "clock_in_time": clock_in_time,
            "breaks": breaks,
            "name": name,
            "job_site": job_site
        })
        if action != "clockout":
            flash(status, "success")
        return redirect(url_for("index"))

    return render_template("index.html", 
                         clocked_in=clocked_in, 
                         on_break=on_break, 
                         code=code, 
                         job_sites=JOB_SITES,
                         name=name,
                         job_site=job_site)

@app.route("/export")
def export_data():
    """Export shift data as CSV for download"""
    with get_db_connection() as conn:
        cursor = conn.execute('''
            SELECT name, job_site, clock_in, clock_out, total_time, working_time, breaks, code
            FROM shifts 
            ORDER BY created_at DESC
        ''')
        rows = cursor.fetchall()
    
    csv_data = "Name,Job Site,Clock In,Clock Out,Total Time,Working Time,Breaks,Code\n"
    for row in rows:
        csv_data += f'"{row["name"]}","{row["job_site"]}","{row["clock_in"] or ""}","{row["clock_out"] or ""}","{row["total_time"] or ""}","{row["working_time"] or ""}","{row["breaks"] or ""}","{row["code"]}"\n'
    
    from flask import Response
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=shifts_export.csv"}
    )

@app.route("/export-by-site")
def export_by_site():
    """Export all job sites as separate CSV files in a zip"""
    import zipfile
    import io
    from datetime import datetime
    
    # Get all unique job sites
    with get_db_connection() as conn:
        cursor = conn.execute('SELECT DISTINCT job_site FROM shifts ORDER BY job_site')
        job_sites = [row['job_site'] for row in cursor.fetchall()]
    
    # Create zip file in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for job_site in job_sites:
            # Get data for this job site
            with get_db_connection() as conn:
                cursor = conn.execute('''
                    SELECT name, job_site, clock_in, clock_out, total_time, working_time, breaks, code
                    FROM shifts 
                    WHERE job_site = ?
                    ORDER BY created_at DESC
                ''', (job_site,))
                rows = cursor.fetchall()
            
            # Create CSV for this job site
            csv_data = "Name,Job Site,Clock In,Clock Out,Total Time,Working Time,Breaks,Code\n"
            for row in rows:
                csv_data += f'"{row["name"]}","{row["job_site"]}","{row["clock_in"] or ""}","{row["clock_out"] or ""}","{row["total_time"] or ""}","{row["working_time"] or ""}","{row["breaks"] or ""}","{row["code"]}"\n'
            
            # Clean filename
            safe_filename = "".join(c for c in job_site if c.isalnum() or c in (' ', '-', '_')).rstrip()
            safe_filename = safe_filename.replace(' ', '_')
            zip_file.writestr(f"{safe_filename}.csv", csv_data)
    
    zip_buffer.seek(0)
    
    from flask import Response
    return Response(
        zip_buffer.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": f"attachment;filename=shifts_by_site_{datetime.now().strftime('%Y%m%d')}.zip"}
    )

@app.route("/export-site/<job_site>")
def export_single_site(job_site):
    """Export data for a specific job site"""
    with get_db_connection() as conn:
        cursor = conn.execute('''
            SELECT name, job_site, clock_in, clock_out, total_time, working_time, breaks, code
            FROM shifts 
            WHERE job_site = ?
            ORDER BY created_at DESC
        ''', (job_site,))
        rows = cursor.fetchall()
    
    csv_data = "Name,Job Site,Clock In,Clock Out,Total Time,Working Time,Breaks,Code\n"
    for row in rows:
        csv_data += f'"{row["name"]}","{row["job_site"]}","{row["clock_in"] or ""}","{row["clock_out"] or ""}","{row["total_time"] or ""}","{row["working_time"] or ""}","{row["breaks"] or ""}","{row["code"]}"\n'
    
    # Clean filename
    safe_filename = "".join(c for c in job_site if c.isalnum() or c in (' ', '-', '_')).rstrip()
    safe_filename = safe_filename.replace(' ', '_')
    
    from flask import Response
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename={safe_filename}_shifts.csv"}
    )

@app.route("/view-data")
def view_data():
    """View all shift data in a web table"""
    job_site_filter = request.args.get('job_site', '')
    
    with get_db_connection() as conn:
        if job_site_filter:
            cursor = conn.execute('''
                SELECT name, job_site, clock_in, clock_out, total_time, working_time, breaks, code, created_at
                FROM shifts 
                WHERE job_site = ?
                ORDER BY created_at DESC
            ''', (job_site_filter,))
        else:
            cursor = conn.execute('''
                SELECT name, job_site, clock_in, clock_out, total_time, working_time, breaks, code, created_at
                FROM shifts 
                ORDER BY created_at DESC
            ''')
        rows = cursor.fetchall()
        
        # Get all job sites for filter dropdown
        cursor = conn.execute('SELECT DISTINCT job_site FROM shifts ORDER BY job_site')
        job_sites = [row['job_site'] for row in cursor.fetchall()]
    
    return render_template("view_data.html", shifts=rows, job_sites=job_sites, selected_site=job_site_filter)

if __name__ == "__main__":
    init_database()
    app.run(debug=True)
