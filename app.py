from flask import Flask, render_template, request, redirect, url_for, flash, session, Response
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import os
import pytz
import qrcode
import io
import base64
import hashlib
import time
from sqlalchemy import text

app = Flask(__name__)
app.secret_key = 'your_secret_key'

DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///shifts.db')
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

ADMIN_PASSWORD = "EasternCC001"

db = SQLAlchemy(app)

# Initialize database tables with error handling
try:
    with app.app_context():
        db.create_all()
except Exception as e:
    print(f"Database initialization error: {e}")

# Job site timezone mapping
JOB_SITE_TIMEZONES = {
    "2025 DC water - Washington DC 20032": "America/New_York",
    "BRWRF Phase 3 Package 1 - Ashburn 20147": "America/New_York", 
    "DC Water Projects - Washington DC 20032": "America/New_York",
    "[HMMA] Stamp Shop Roof Improvement - Montgomery, Alabama 36105": "America/Chicago",
    "Hyundai Product Center Office - Carnesville, Georgia": "America/New_York",
    "JWA22 - Kokomo, Indiana 46901": "America/Indiana/Indianapolis",
    "[KARR] Korean Ambassador's Residence Renovation, NW Washington 20016": "America/New_York",
    "LGES-ALPHA Project-1F0 - Queen Creek 85140": "America/Phoenix",
    "LGESMI2 EV Battery Plant Main BLDG - Holland Michigan 49423": "America/Detroit",
    "TPO Roof Project - Fairfax 22030": "America/New_York"
}

def get_local_time(utc_time, job_site):
    """Convert UTC time to local time based on job site"""
    if not utc_time:
        return None
    
    timezone_name = JOB_SITE_TIMEZONES.get(job_site, "UTC")
    try:
        tz = pytz.timezone(timezone_name)
        # If utc_time is naive (no timezone), assume it's UTC
        if utc_time.tzinfo is None:
            utc_time = pytz.utc.localize(utc_time)
        return utc_time.astimezone(tz)
    except Exception:
        return utc_time

def format_time_for_display(dt, job_site):
    """Format datetime for display in the appropriate timezone"""
    if not dt:
        return ""
    local_time = get_local_time(dt, job_site)
    if local_time:
        return local_time.strftime('%Y-%m-%d %I:%M %p')
    return dt.strftime('%Y-%m-%d %I:%M %p')

class Shift(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    subcontractor = db.Column(db.String(120), nullable=False)
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
            subcontractor = request.form.get("subcontractor", "").strip()
            job_site = request.form.get("job_site", "")
            
            if not name:
                flash("Please enter your name.", "error")
                return redirect(url_for("index"))
            if not subcontractor:
                flash("Please enter your subcontractor company.", "error")
                return redirect(url_for("index"))
            if not job_site:
                flash("Please select a job site.", "error")
                return redirect(url_for("index"))
            
            # Check if already clocked in
            existing_shift = Shift.query.filter_by(name=name, subcontractor=subcontractor, job_site=job_site, clock_out=None).first()
            if existing_shift:
                flash("You are already clocked in at this job site.", "error")
                return redirect(url_for("index"))
            
            code = generate_code()
            shift = Shift(name=name, subcontractor=subcontractor, job_site=job_site, clock_in=now, code=code)
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

    return render_template("index.html", job_sites=JOB_SITES, subcontractors=get_subcontractor_suggestions())

def get_subcontractor_suggestions():
    """Get list of existing subcontractors for auto-suggestions"""
    try:
        subcontractors = [row[0] for row in db.session.query(Shift.subcontractor).distinct().all()]
        return [s for s in subcontractors if s]  # Filter out None/empty values
    except Exception:
        return []

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
    
    subcontractor_filter = request.args.get('subcontractor', '')
    query = Shift.query
    if subcontractor_filter:
        query = query.filter_by(subcontractor=subcontractor_filter)
    
    shifts = query.order_by(Shift.created_at.desc()).all()
    subcontractors = [row[0] for row in db.session.query(Shift.subcontractor).distinct().all()]
    
    # Calculate days worked for each subcontractor
    subcontractor_days = calculate_subcontractor_days()
    
    return render_template("admin.html", 
                         shifts=shifts, 
                         subcontractors=subcontractors, 
                         selected_subcontractor=subcontractor_filter,
                         subcontractor_days=subcontractor_days,
                         format_time_for_display=format_time_for_display)

def calculate_subcontractor_days():
    """Calculate days worked for each subcontractor (8 hours = 1 day)"""
    subcontractor_days = {}
    
    # Get all completed shifts
    completed_shifts = Shift.query.filter(Shift.clock_out.isnot(None)).all()
    
    for shift in completed_shifts:
        if not shift.working_time:
            continue
            
        # Parse working time (format: "Xh Ym")
        try:
            time_parts = shift.working_time.split()
            hours = int(time_parts[0].replace('h', ''))
            minutes = int(time_parts[1].replace('m', '')) if len(time_parts) > 1 else 0
            total_hours = hours + (minutes / 60)
            
            # Calculate days (8 hours = 1 day)
            days = total_hours / 8
            
            if shift.subcontractor not in subcontractor_days:
                subcontractor_days[shift.subcontractor] = 0
            subcontractor_days[shift.subcontractor] += days
            
        except (ValueError, IndexError):
            continue
    
    return subcontractor_days

@app.route("/admin/export")
def admin_export():
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_view"))
    shifts = Shift.query.order_by(Shift.created_at.desc()).all()
    def generate():
        data = [
            ["Name", "Subcontractor", "Job Site", "Working Time", "Days Worked", "Code"]
        ]
        for s in shifts:
            # Calculate days worked
            days_worked = 0
            if s.working_time:
                try:
                    time_parts = s.working_time.split()
                    hours = int(time_parts[0].replace('h', ''))
                    minutes = int(time_parts[1].replace('m', '')) if len(time_parts) > 1 else 0
                    total_hours = hours + (minutes / 60)
                    days_worked = total_hours / 8
                except (ValueError, IndexError):
                    pass
            
            data.append([
                s.name, 
                s.subcontractor,
                s.job_site,
                s.working_time or '',
                f"{days_worked:.2f}",
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

@app.route("/admin/delete/<int:shift_id>", methods=["POST"])
def admin_delete_shift(shift_id):
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_view"))
    shift = Shift.query.get_or_404(shift_id)
    # Delete associated breaks
    Break.query.filter_by(shift_code=shift.code).delete()
    db.session.delete(shift)
    db.session.commit()
    flash("Shift entry deleted.", "success")
    return redirect(url_for("admin_view"))

@app.route("/admin/qr_codes")
def admin_qr_codes():
    """Generate QR codes for all job sites"""
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_view"))
    
    qr_codes = {}
    for job_site in JOB_SITES:
        qr_image, timestamp = generate_qr_code(job_site)
        qr_codes[job_site] = {
            'image': qr_image,
            'timestamp': timestamp,
            'url': f"https://your-app.onrender.com/scan?site={hashlib.md5(job_site.encode()).hexdigest()[:8]}&t={timestamp}"
        }
    
    return render_template("qr_codes.html", qr_codes=qr_codes)

@app.route("/initdb")
def init_db():
    try:
        with app.app_context():
            db.create_all()
        return "Database tables created successfully!"
    except Exception as e:
        return f"Error creating tables: {str(e)}"

def generate_qr_code(job_site, timestamp=None):
    """Generate QR code for a job site with timestamp validation"""
    if timestamp is None:
        timestamp = int(time.time())
    
    # Create unique identifier for job site
    site_id = hashlib.md5(job_site.encode()).hexdigest()[:8]
    
    # Create QR code data with timestamp
    qr_data = f"https://your-app.onrender.com/scan?site={site_id}&t={timestamp}"
    
    # Generate QR code
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(qr_data)
    qr.make(fit=True)
    
    # Create image
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Convert to base64 for display
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    img_str = base64.b64encode(buffer.getvalue()).decode()
    
    return img_str, timestamp

def validate_qr_timestamp(timestamp, max_age_hours=24):
    """Validate QR code timestamp (prevent old QR codes)"""
    try:
        qr_time = int(timestamp)
        current_time = int(time.time())
        age_hours = (current_time - qr_time) / 3600
        
        return age_hours <= max_age_hours
    except (ValueError, TypeError):
        return False

def get_job_site_from_id(site_id):
    """Get job site name from site ID"""
    for site in JOB_SITES:
        if hashlib.md5(site.encode()).hexdigest()[:8] == site_id:
            return site
    return None

@app.route("/scan")
def qr_scan():
    """Handle QR code scanning for clock in/out"""
    site_id = request.args.get('site')
    timestamp = request.args.get('t')
    
    # Validate timestamp
    if not validate_qr_timestamp(timestamp):
        return "QR code expired. Please get a new QR code.", 400
    
    # Get job site from ID
    job_site = get_job_site_from_id(site_id)
    if not job_site:
        return "Invalid job site.", 400
    
    # Check if user is already clocked in at this site
    # We'll need to identify the user somehow - for now, we'll use a simple approach
    # In a real implementation, you might want user authentication
    
    return render_template("qr_scan.html", job_site=job_site)

@app.route("/qr_clock_in", methods=["POST"])
def qr_clock_in():
    """Handle clock in via QR code"""
    name = request.form.get("name", "").strip()
    subcontractor = request.form.get("subcontractor", "").strip()
    job_site = request.form.get("job_site", "")
    
    if not name or not subcontractor or not job_site:
        flash("Please fill in all fields.", "error")
        return redirect(url_for("qr_scan", site=hashlib.md5(job_site.encode()).hexdigest()[:8], t=int(time.time())))
    
    # Check if already clocked in
    existing_shift = Shift.query.filter_by(name=name, subcontractor=subcontractor, job_site=job_site, clock_out=None).first()
    if existing_shift:
        flash("You are already clocked in at this job site.", "error")
        return redirect(url_for("qr_scan", site=hashlib.md5(job_site.encode()).hexdigest()[:8], t=int(time.time())))
    
    # Clock in
    now = datetime.now()
    code = generate_code()
    shift = Shift(name=name, subcontractor=subcontractor, job_site=job_site, clock_in=now, code=code)
    db.session.add(shift)
    db.session.commit()
    
    flash(f"Successfully clocked in! Your code is: <b>{code}</b>", "success")
    return redirect(url_for("qr_scan", site=hashlib.md5(job_site.encode()).hexdigest()[:8], t=int(time.time())))

@app.route("/qr_clock_out", methods=["POST"])
def qr_clock_out():
    """Handle clock out via QR code"""
    code = request.form.get("code", "").strip()
    job_site = request.form.get("job_site", "")
    
    if not code or not job_site:
        flash("Please enter your code.", "error")
        return redirect(url_for("qr_scan", site=hashlib.md5(job_site.encode()).hexdigest()[:8], t=int(time.time())))
    
    # Find shift
    shift = Shift.query.filter_by(code=code, job_site=job_site, clock_out=None).first()
    if not shift:
        flash("Code not found or already clocked out.", "error")
        return redirect(url_for("qr_scan", site=hashlib.md5(job_site.encode()).hexdigest()[:8], t=int(time.time())))
    
    # Clock out
    now = datetime.now()
    clock_in_dt = shift.clock_in
    clock_out_dt = now
    total_time = clock_out_dt - clock_in_dt
    
    # Calculate breaks
    breaks = Break.query.filter_by(shift_code=code).all()
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
    
    flash(f"Successfully clocked out! Working time: <b>{format_seconds(working_time)}</b>", "success")
    return redirect(url_for("qr_scan", site=hashlib.md5(job_site.encode()).hexdigest()[:8], t=int(time.time())))

@app.route('/add_subcontractor_column')
def add_subcontractor_column():
    try:
        db.session.execute(text("ALTER TABLE shift ADD COLUMN subcontractor VARCHAR(120) NOT NULL DEFAULT '';"))
        db.session.commit()
        return "Column added!"
    except Exception as e:
        return f"Error: {e}"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
