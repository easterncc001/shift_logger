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
import uuid
import requests
from sqlalchemy import func, distinct

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your_secret_key')

# Fix for Heroku/Render PostgreSQL URL
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
elif not DATABASE_URL:
    DATABASE_URL = 'sqlite:///shifts.db'

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', "EasternCC001")

# Procore configuration
PROCORE_CLIENT_ID = os.environ.get('PROCORE_CLIENT_ID')
PROCORE_CLIENT_SECRET = os.environ.get('PROCORE_CLIENT_SECRET')
PROCORE_COMPANY_ID = os.environ.get('PROCORE_COMPANY_ID')

# Map job sites to Procore project IDs
PROCORE_PROJECT_MAP = {
    "2025 DC water - Washington DC 20032": os.environ.get('PROCORE_DC_WATER_PROJECT_ID'),
    "BRWRF Phase 3 Package 1 - Ashburn 20147": os.environ.get('PROCORE_BRWRF_PROJECT_ID'),
    # Add mappings for other job sites
}

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
    qr_batch_id = db.Column(db.String(64))  # New column for QR batch ID

class Break(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shift_code = db.Column(db.String(16), db.ForeignKey('shift.code'), nullable=False)
    start = db.Column(db.DateTime, nullable=False)
    end = db.Column(db.DateTime)

class SubcontractorProjectHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    subcontractor = db.Column(db.String(120), nullable=False)
    job_site = db.Column(db.String(255), nullable=False)
    first_day = db.Column(db.DateTime, nullable=False)
    last_day = db.Column(db.DateTime, nullable=False)
    manpower = db.Column(db.Integer, default=0)
    __table_args__ = (db.UniqueConstraint('subcontractor', 'job_site', name='uix_subcontractor_jobsite'),)

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
    # Ensure code is unique
    while True:
        code = str(random.randint(100000, 999999))
        if not Shift.query.filter_by(code=code).first():
            return code

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
    try:
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
        job_site_filter = request.args.get('job_site', '')
        
        # Get shifts
        query = Shift.query
        if subcontractor_filter:
            query = query.filter_by(subcontractor=subcontractor_filter)
        if job_site_filter:
            query = query.filter_by(job_site=job_site_filter)
        shifts = query.order_by(Shift.created_at.desc()).all()
        
        # Get subcontractor history with proper date formatting
        history_query = SubcontractorProjectHistory.query
        if subcontractor_filter:
            history_query = history_query.filter_by(subcontractor=subcontractor_filter)
        if job_site_filter:
            history_query = history_query.filter_by(job_site=job_site_filter)
        histories = history_query.order_by(
            SubcontractorProjectHistory.subcontractor,
            SubcontractorProjectHistory.job_site
        ).all()
        
        # Get unique subcontractors and job sites for filters
        subcontractors = [row[0] for row in db.session.query(Shift.subcontractor).distinct().all()]
        job_sites = [row[0] for row in db.session.query(Shift.job_site).distinct().all()]
        
        # Calculate total days and hours worked
        subcontractor_stats = calculate_subcontractor_days()
        
        return render_template(
            "admin.html", 
            shifts=shifts,
            histories=histories,
            subcontractors=subcontractors,
            job_sites=job_sites,
            selected_subcontractor=subcontractor_filter,
            selected_job_site=job_site_filter,
            subcontractor_stats=subcontractor_stats,
            format_time_for_display=format_time_for_display
        )
    except Exception as e:
        print(f"Admin view error: {str(e)}")  # This will show in your server logs
        db.session.rollback()  # Roll back any failed transactions
        return f"An error occurred: {str(e)}", 500  # Return error to browser

def calculate_subcontractor_days():
    """Calculate days worked and total hours for each subcontractor"""
    subcontractor_stats = {}
    
    # Get all completed shifts
    completed_shifts = Shift.query.filter(Shift.clock_out.isnot(None)).all()
    
    for shift in completed_shifts:
        if shift.subcontractor not in subcontractor_stats:
            subcontractor_stats[shift.subcontractor] = {
                'days': 0,
                'hours': 0.0
            }
        # Count 1 day for each completed shift
        subcontractor_stats[shift.subcontractor]['days'] += 1
        
        # Calculate hours from working_time
        if shift.working_time:
            time_parts = shift.working_time.split()
            hours = float(time_parts[0].replace('h', ''))
            minutes = float(time_parts[1].replace('m', '')) if len(time_parts) > 1 else 0
            total_hours = hours + (minutes / 60)
            subcontractor_stats[shift.subcontractor]['hours'] += total_hours
            
    return subcontractor_stats

def update_subcontractor_history(shift):
    """Update subcontractor project history when a shift is completed"""
    history = SubcontractorProjectHistory.query.filter_by(
        subcontractor=shift.subcontractor,
        job_site=shift.job_site
    ).first()
    
    shift_date = shift.clock_out.date()
    
    if not history:
        # First time this subcontractor works on this job site
        history = SubcontractorProjectHistory(
            subcontractor=shift.subcontractor,
            job_site=shift.job_site,
            first_day=shift_date,
            last_day=shift_date,
            manpower=1  # Start with 1 for this shift
        )
        db.session.add(history)
    else:
        # Update existing history
        # Update first/last day if needed
        if shift_date < history.first_day.date():
            history.first_day = shift_date
        if shift_date > history.last_day.date():
            history.last_day = shift_date
        # Always increment manpower for each shift
        history.manpower += 1
    
    db.session.commit()

def update_daily_manpower(shift):
    """Update daily manpower count when a shift is completed"""
    shift_date = shift.clock_out.date()
    
    # Get or create daily manpower record
    daily = DailyManpower.query.filter_by(
        date=shift_date,
        job_site=shift.job_site,
        subcontractor=shift.subcontractor
    ).first()
    
    if not daily:
        daily = DailyManpower(
            date=shift_date,
            job_site=shift.job_site,
            subcontractor=shift.subcontractor,
            manpower=1
        )
        db.session.add(daily)
    else:
        daily.manpower += 1
    
    db.session.commit()

def get_daily_manpower_summary(start_date=None, end_date=None, job_site=None, subcontractor=None):
    """Get daily manpower summary with optional filters"""
    query = DailyManpower.query
    
    if start_date:
        query = query.filter(DailyManpower.date >= start_date)
    if end_date:
        query = query.filter(DailyManpower.date <= end_date)
    if job_site:
        query = query.filter_by(job_site=job_site)
    if subcontractor:
        query = query.filter_by(subcontractor=subcontractor)
    
    return query.order_by(DailyManpower.date.desc()).all()

def get_cumulative_manpower_totals(start_date=None, end_date=None, job_site=None, subcontractor=None):
    """Calculate cumulative days worked per subcontractor (1 day per date regardless of number of workers)"""
    query = DailyManpower.query
    
    if start_date:
        query = query.filter(DailyManpower.date >= start_date)
    if end_date:
        query = query.filter(DailyManpower.date <= end_date)
    if job_site:
        query = query.filter_by(job_site=job_site)
    if subcontractor:
        query = query.filter_by(subcontractor=subcontractor)
    
    # Group by subcontractor and date, then count distinct dates
    totals = db.session.query(
        DailyManpower.subcontractor,
        func.count(distinct(DailyManpower.date)).label('total_days')
    ).group_by(DailyManpower.subcontractor).all()
    
    return {record.subcontractor: record.total_days for record in totals}

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
    """Generate QR codes for all job sites with unique batch IDs"""
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_view"))
    
    qr_codes = {}
    errors = []
    batch_id = str(uuid.uuid4())  # Unique batch ID for this generation event
    for job_site in JOB_SITES:
        try:
            qr_image, timestamp, qr_url = generate_qr_code(job_site, batch_id)
            qr_codes[job_site] = {
                'image': qr_image,
                'timestamp': timestamp,
                'url': qr_url,
                'batch_id': batch_id
            }
        except Exception as e:
            errors.append(f"Error generating QR for {job_site}: {e}")
    if errors:
        flash("<br>".join(errors), "error")
    return render_template("qr_codes.html", qr_codes=qr_codes)

@app.route("/initdb")
def init_db():
    try:
        with app.app_context():
            db.create_all()
        return "Database tables created successfully!"
    except Exception as e:
        return f"Error creating tables: {str(e)}"

def generate_qr_code(job_site, batch_id, timestamp=None):
    """Generate QR code for a job site with batch ID and timestamp validation"""
    if timestamp is None:
        timestamp = int(time.time())
    site_id = hashlib.md5(job_site.encode()).hexdigest()[:8]
    from flask import request
    host_url = request.host_url.rstrip('/')
    qr_data = f"{host_url}/scan?site={site_id}&batch={batch_id}&t={timestamp}"
    qr_url = qr_data
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(qr_data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    img_str = base64.b64encode(buffer.getvalue()).decode()
    return img_str, timestamp, qr_url

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
    batch_id = request.args.get('batch')
    timestamp = request.args.get('t')
    
    # Validate batch_id and timestamp
    if not batch_id:
        return "Invalid QR code (missing batch ID).", 400
    # Check if any active shifts exist for this job site and batch_id
    job_site = get_job_site_from_id(site_id)
    if not job_site:
        return "Invalid job site.", 400
    active_shifts = Shift.query.filter_by(job_site=job_site, qr_batch_id=batch_id, clock_out=None).all()
    if not active_shifts and not validate_qr_timestamp(timestamp):
        return "QR code expired. Please get a new QR code.", 400
    return render_template("qr_scan.html", job_site=job_site, batch_id=batch_id)

@app.route("/qr_clock_in", methods=["POST"])
def qr_clock_in():
    """Handle clock in via QR code"""
    name = request.form.get("name", "").strip()
    subcontractor = request.form.get("subcontractor", "").strip()
    job_site = request.form.get("job_site", "")
    batch_id = request.args.get('batch') or request.form.get('batch_id')
    if not name or not subcontractor or not job_site or not batch_id:
        flash("Please fill in all fields.", "error")
        return redirect(url_for("qr_scan", site=hashlib.md5(job_site.encode()).hexdigest()[:8], batch=batch_id, t=int(time.time())))
    # Check if already clocked in
    existing_shift = Shift.query.filter_by(name=name, subcontractor=subcontractor, job_site=job_site, clock_out=None, qr_batch_id=batch_id).first()
    if existing_shift:
        flash("You are already clocked in at this job site.", "error")
        return redirect(url_for("qr_scan", site=hashlib.md5(job_site.encode()).hexdigest()[:8], batch=batch_id, t=int(time.time())))
    # Clock in
    now = datetime.now()
    code = generate_code()
    shift = Shift(name=name, subcontractor=subcontractor, job_site=job_site, clock_in=now, code=code, qr_batch_id=batch_id)
    db.session.add(shift)
    db.session.commit()
    flash(f"Successfully clocked in! Your code is: <b>{code}</b>", "success")
    return redirect(url_for("qr_scan", site=hashlib.md5(job_site.encode()).hexdigest()[:8], batch=batch_id, t=int(time.time())))

def sync_to_procore(shift):
    """Sync completed shift data to Procore's Manpower interface"""
    if not all([PROCORE_CLIENT_ID, PROCORE_CLIENT_SECRET, PROCORE_COMPANY_ID]):
        print("Procore credentials not configured")
        return
    
    # Get project ID for the job site
    project_id = PROCORE_PROJECT_MAP.get(shift.job_site)
    if not project_id:
        print(f"No Procore project ID mapping found for job site: {shift.job_site}")
        return

    try:
        # Get access token
        token_url = "https://api.procore.com/oauth/token"
        token_data = {
            "grant_type": "client_credentials",
            "client_id": PROCORE_CLIENT_ID,
            "client_secret": PROCORE_CLIENT_SECRET
        }
        token_response = requests.post(token_url, data=token_data)
        token_response.raise_for_status()
        access_token = token_response.json()["access_token"]

        # Combine queries to reduce database calls
        today = shift.clock_out.date()
        
        # Single query to get both daily count and cumulative days
        result = db.session.query(
            DailyManpower.manpower,
            func.count(distinct(DailyManpower.date)).label('total_days')
        ).filter(
            DailyManpower.subcontractor == shift.subcontractor,
            DailyManpower.job_site == shift.job_site
        ).group_by(
            DailyManpower.manpower
        ).filter(
            DailyManpower.date == today
        ).first()

        # Extract values from result
        workers_count = result.manpower if result else 1
        cumulative_total = result.total_days if result else 1

        # Calculate hours (moved outside try block since it's simpler)
        time_parts = shift.working_time.split()
        hours = float(time_parts[0].replace('h', ''))
        minutes = float(time_parts[1].replace('m', '')) if len(time_parts) > 1 else 0
        total_hours = hours + (minutes / 60)

        # Prepare manpower data matching Procore's interface
        manpower_data = {
            "manpower_log": {
                "company": shift.subcontractor,
                "workers": workers_count,
                "hours": 10,
                "total_hours": total_hours,
                "location": shift.job_site,
                "manpower": cumulative_total,
                "date": shift.clock_out.strftime("%Y-%m-%d"),
                "notes": f"Daily workers: {workers_count}\nCumulative days: {cumulative_total}"
            }
        }

        # Send data to Procore
        api_url = f"https://api.procore.com/rest/v1.0/projects/{project_id}/manpower_logs"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        response = requests.post(api_url, json=manpower_data, headers=headers)
        response.raise_for_status()
        print(f"Successfully synced manpower data to Procore for {shift.subcontractor}")

    except requests.exceptions.RequestException as e:
        print(f"Error syncing to Procore: {e}")
    except (ValueError, IndexError, AttributeError) as e:
        print(f"Error processing shift data: {e}")

@app.route("/qr_clock_out", methods=["POST"])
def qr_clock_out():
    """Handle clock out via QR code"""
    code = request.form.get("code", "").strip()
    job_site = request.form.get("job_site", "")
    batch_id = request.args.get('batch') or request.form.get('batch_id')
    if not code or not job_site or not batch_id:
        flash("Please enter your code.", "error")
        return redirect(url_for("qr_scan", site=hashlib.md5(job_site.encode()).hexdigest()[:8], batch=batch_id, t=int(time.time())))
    
    # Find shift (prefer batch_id, fallback to old logic for backward compatibility)
    shift = Shift.query.filter_by(code=code, job_site=job_site, clock_out=None, qr_batch_id=batch_id).first()
    if not shift:
        # Fallback: try without batch_id for old shifts
        shift = Shift.query.filter_by(code=code, job_site=job_site, clock_out=None).first()
    if not shift:
        flash("Code not found or already clocked out.", "error")
        return redirect(url_for("qr_scan", site=hashlib.md5(job_site.encode()).hexdigest()[:8], batch=batch_id, t=int(time.time())))
    
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

    # Update tracking
    update_subcontractor_history(shift)
    update_daily_manpower(shift)

    # Sync to Procore if configured
    sync_to_procore(shift)

    flash(
        f"Shift complete!<br>"
        f"Total time: <b>{format_seconds(total_time.total_seconds())}</b><br>"
        f"Actual working time: <b>{format_seconds(working_time)}</b>",
        "success"
    )
    return redirect(url_for("qr_scan", site=hashlib.md5(job_site.encode()).hexdigest()[:8], batch=batch_id, t=int(time.time())))

@app.route('/add_subcontractor_column')
def add_subcontractor_column():
    try:
        db.session.execute(text("ALTER TABLE shift ADD COLUMN subcontractor VARCHAR(120) NOT NULL DEFAULT '';"))
        db.session.commit()
        return "Column added!"
    except Exception as e:
        return f"Error: {e}"

@app.route('/add_qr_batch_id_column')
def add_qr_batch_id_column():
    try:
        db.session.execute(text("ALTER TABLE shift ADD COLUMN qr_batch_id VARCHAR(64);"))
        db.session.commit()
        return "qr_batch_id column added!"
    except Exception as e:
        return f"Error: {e}"

@app.route('/add_daily_manpower_table')
def add_daily_manpower_table():
    """Add the daily manpower tracking table"""
    try:
        with app.app_context():
            db.create_all()
        return "Daily manpower table added successfully!"
    except Exception as e:
        return f"Error: {e}"

@app.route('/rename_total_days_to_manpower')
def rename_total_days_to_manpower():
    try:
        db.session.execute(text("ALTER TABLE subcontractor_project_history RENAME COLUMN total_days TO manpower;"))
        db.session.commit()
        return "Column renamed successfully!"
    except Exception as e:
        return f"Error: {e}"

@app.route('/check_tables')
def check_tables():
    try:
        # Check Shift table
        shifts = db.session.query(Shift).first()
        shift_columns = Shift.__table__.columns.keys()
        
        # Check SubcontractorProjectHistory table
        histories = db.session.query(SubcontractorProjectHistory).first()
        history_columns = SubcontractorProjectHistory.__table__.columns.keys()
        
        # Check Break table
        breaks = db.session.query(Break).first()
        break_columns = Break.__table__.columns.keys()
        
        return {
            'shift_columns': shift_columns,
            'history_columns': history_columns,
            'break_columns': break_columns,
            'tables_exist': True
        }
    except Exception as e:
        return f"Database error: {str(e)}"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
