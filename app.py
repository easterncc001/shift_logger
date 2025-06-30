from flask import Flask, render_template, request, redirect, url_for, flash, send_file, Response, session
from datetime import datetime
import os
from flask_sqlalchemy import SQLAlchemy
import csv

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Change this to a random string

# Database config
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///shifts.db')
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

with app.app_context():
    db.create_all()

class Shift(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    job_site = db.Column(db.String(255), nullable=False)
    clock_in = db.Column(db.DateTime, nullable=False)
    clock_out = db.Column(db.DateTime)
    total_time = db.Column(db.String(32))
    working_time = db.Column(db.String(32))
    breaks = db.Column(db.String(255))
    code = db.Column(db.String(16), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Break(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shift_code = db.Column(db.String(16), db.ForeignKey('shift.code'), nullable=False)
    start = db.Column(db.DateTime, nullable=False)
    end = db.Column(db.DateTime)

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

ADMIN_PASSWORD = "EasternCC001"  # Change this to your desired admin password

def generate_code():
    import random
    return str(random.randint(100000, 999999))

def format_seconds(secs):
    hours = int(secs // 3600)
    minutes = int((secs % 3600) // 60)
    return f"{hours}h {minutes}m"

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        action = request.form.get("action")
        now = datetime.now()

        if action == "clockin":
            name = request.form.get("name", "").strip()
            job_site = request.form.get("job_site", "")
            if not name:
                flash("Please enter your name.", "error")
                return redirect(url_for("index"))
            if not job_site:
                flash("Please select a job site.", "error")
                return redirect(url_for("index"))
            code = generate_code()
            shift = Shift(name=name, job_site=job_site, clock_in=now, code=code)
            db.session.add(shift)
            db.session.commit()
            flash(
                f"Your code is: <b>{code}</b><br>"
                f"<span style='color:red;'>This code is required to clock out. Please write it down or remember it. It will not be shown again!</span>",
                "success"
            )
            return redirect(url_for("index"))

        elif action == "break":
            input_code = request.form.get("input_code")
            if not input_code:
                flash("Please enter your code to start a break.", "error")
                return redirect(url_for("index"))
            shift = Shift.query.filter_by(code=input_code).first()
            if not shift:
                flash("Code not found.", "error")
                return redirect(url_for("index"))
            # Only start a new break if not already on break
            last_break = Break.query.filter_by(shift_code=input_code).order_by(Break.id.desc()).first()
            if not last_break or last_break.end is not None:
                new_break = Break(shift_code=input_code, start=now)
                db.session.add(new_break)
                db.session.commit()
                flash("Break started.", "success")
            else:
                flash("You are already on a break.", "error")
            return redirect(url_for("index"))

        elif action == "resume":
            input_code = request.form.get("input_code")
            if not input_code:
                flash("Please enter your code to resume.", "error")
                return redirect(url_for("index"))
            last_break = Break.query.filter_by(shift_code=input_code).order_by(Break.id.desc()).first()
            if last_break and last_break.end is None:
                last_break.end = now
                db.session.commit()
                flash("Break ended.", "success")
            else:
                flash("No break to resume.", "error")
            return redirect(url_for("index"))

        elif action == "clockout":
            input_code = request.form.get("input_code")
            if not input_code:
                flash("Please enter your code to clock out.", "error")
                return redirect(url_for("index"))
            shift = Shift.query.filter_by(code=input_code).first()
            if not shift:
                flash("Code not found. Please check your code.", "error")
                return redirect(url_for("index"))
            if shift.clock_out:
                flash("You have already clocked out for this shift.", "error")
                return redirect(url_for("index"))
            clock_in_dt = shift.clock_in
            clock_out_dt = now
            total_time = clock_out_dt - clock_in_dt
            breaks = Break.query.filter_by(shift_code=input_code).all()
            total_break = 0
            breaks_str = ""
            if breaks:
                for b in breaks:
                    if b.start and b.end:
                        total_break += (b.end - b.start).total_seconds()
                breaks_str = "; ".join(
                    f"{b.start.strftime('%I:%M %p')} - {b.end.strftime('%I:%M %p')}" for b in breaks if b.start and b.end
                )
            working_time = total_time.total_seconds() - total_break
            shift.clock_out = clock_out_dt
            shift.total_time = format_seconds(total_time.total_seconds())
            shift.working_time = format_seconds(working_time)
            shift.breaks = breaks_str
            db.session.commit()
            flash(
                f"Shift complete!<br>"
                f"Total time: <b>{format_seconds(total_time.total_seconds())}</b><br>"
                f"Actual working time: <b>{format_seconds(working_time)}</b>",
                "success"
            )
            return redirect(url_for("index"))

        else:
            flash("Invalid action or state.", "error")
            return redirect(url_for("index"))

    return render_template("index.html", job_sites=JOB_SITES)

@app.route("/admin", methods=["GET", "POST"])
def admin_view():
    if not session.get("admin_authenticated"):
        if request.method == "POST":
            password = request.form.get("password", "")
            if password == ADMIN_PASSWORD:
                session["admin_authenticated"] = True
            else:
                flash("Incorrect password.", "error")
                return render_template("admin_login.html")
        else:
            return render_template("admin_login.html")
    job_site_filter = request.args.get('job_site', '')
    query = Shift.query
    if job_site_filter:
        query = query.filter_by(job_site=job_site_filter)
    shifts = query.order_by(Shift.created_at.desc()).all()
    job_sites = [row[0] for row in db.session.query(Shift.job_site).distinct().all()]
    return render_template("admin.html", shifts=shifts, job_sites=job_sites, selected_site=job_site_filter)

@app.route("/admin/export")
def admin_export():
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_view"))
    shifts = Shift.query.order_by(Shift.created_at.desc()).all()
    def generate():
        data = [
            ["Name", "Job Site", "Clock In", "Clock Out", "Total Time", "Working Time", "Breaks", "Code"]
        ]
        for s in shifts:
            data.append([
                s.name, s.job_site,
                s.clock_in.strftime('%Y-%m-%d %I:%M %p') if s.clock_in else '',
                s.clock_out.strftime('%Y-%m-%d %I:%M %p') if s.clock_out else '',
                s.total_time or '',
                s.working_time or '',
                s.breaks or '',
                s.code
            ])
        output = ''
        for row in data:
            output += ','.join(f'"{str(cell)}"' for cell in row) + '\n'
        return output
    return Response(generate(), mimetype='text/csv', headers={"Content-Disposition": "attachment;filename=shifts_export.csv"})

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_authenticated", None)
    flash("Logged out.", "success")
    return redirect(url_for("admin_view"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)
