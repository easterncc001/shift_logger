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
from sqlalchemy import func
import json

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your_secret_key')

# Fix for Heroku/Render PostgreSQL URL
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Test PostgreSQL connection and fallback to SQLite if it fails
def get_database_url():
    if not DATABASE_URL:
        print("No DATABASE_URL found, using SQLite")
        return 'sqlite:///shifts.db'
    
    # Test if PostgreSQL is accessible
    try:
        import psycopg2
        from urllib.parse import urlparse
        parsed = urlparse(DATABASE_URL)
        test_conn = psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port,
            database=parsed.path[1:],
            user=parsed.username,
            password=parsed.password,
            connect_timeout=5
        )
        test_conn.close()
        print(f"PostgreSQL connection successful to {parsed.hostname}")
        return DATABASE_URL
    except Exception as e:
        print(f"PostgreSQL connection failed: {e}")
        print("Falling back to SQLite database")
        return 'sqlite:///shifts.db'

# Set the database URL
database_url = get_database_url()
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Only use PostgreSQL-specific options if we're actually using PostgreSQL
if 'postgresql' in database_url:
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
        'connect_args': {
            'connect_timeout': 10,
            'application_name': 'shift_logger'
        }
    }
else:
    # SQLite-specific options
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True
    }

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', "EasternCC001")

db = SQLAlchemy(app)

# Initialize database tables with error handling
def init_database():
    try:
        with app.app_context():
            db.create_all()
            print(f"Database initialized successfully using: {app.config['SQLALCHEMY_DATABASE_URI']}")
    except Exception as e:
        print(f"Database initialization error: {e}")
        # If PostgreSQL fails, try to switch to SQLite
        if 'postgresql' in app.config['SQLALCHEMY_DATABASE_URI']:
            print("Attempting to switch to SQLite...")
            try:
                app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///shifts.db'
                app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_pre_ping': True}
                # Recreate the engine
                db.engine.dispose()
                db.get_engine().dispose()
                with app.app_context():
                    db.create_all()
                print("Successfully switched to SQLite database")
            except Exception as sqlite_error:
                print(f"Failed to switch to SQLite: {sqlite_error}")
        # Continue running even if database init fails

def cleanup_db_session():
    """Clean up database session to prevent binding errors"""
    try:
        db.session.remove()
        db.session.close()
    except Exception as e:
        print(f"Error cleaning up session: {e}")

def ensure_tables_exist():
    """Ensure database tables exist, create them if they don't"""
    try:
        with app.app_context():
            # Check if tables exist by trying to query them
            db.session.execute(text("SELECT 1 FROM shift LIMIT 1"))
            db.session.commit()
            print("Database tables already exist")
        return True
    except Exception as e:
        print(f"Tables don't exist or error: {e}")
        try:
            with app.app_context():
                db.create_all()
                print("Database tables created successfully")
            return True
        except Exception as create_error:
            print(f"Failed to create tables: {create_error}")
            return False

init_database()

# Job site timezone mapping
JOB_SITE_TIMEZONES = {
    "2025 DC water": "America/New_York",
    "2503 - SAC Project": "America/New_York",
    "BRWRF Phase 3 Package 1": "America/New_York",
    "DC Water Projects": "America/New_York",
    "Envision EAUS Battery Plant": "America/Chicago",
    "HMG-Metaplant America": "America/New_York",
    "[HMMA] Stamp Shop Roof Improvement": "America/Chicago",
    "HYUNDAI PRODUCT CENTER OFFICE": "America/New_York",
    "JWA22": "America/Indiana/Indianapolis",
    "[KARR] Korean Ambassador's Residence Renovation": "America/New_York",
    "LGES - ALPHA Project - 1F0": "America/Phoenix",
    "LGESMI2 EV BATTERY PLANT MAIN PROD BLDG": "America/Detroit",
    "Library for ECC": "America/New_York",
    "Lotte Hotel Westfield, IN": "America/Indiana/Indianapolis",
    "LS Cable & System Manufacturing Facility for Submarine Cable in Virgina": "America/New_York",
    "PG World Market": "America/New_York",
    "Tift County 2025 Industrial Building": "America/New_York",
    "Tous Les Jours (Reston)": "America/New_York",
    "TPO Roof Project": "America/New_York"
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
    code = db.Column(db.String(16), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    qr_batch_id = db.Column(db.String(64))  # New column for QR batch ID
    flagged = db.Column(db.Boolean, default=False)  # Auto-closed or problematic shift

class Break(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shift_code = db.Column(db.String(16), nullable=False)  # No longer a ForeignKey
    start = db.Column(db.DateTime, nullable=False)
    end = db.Column(db.DateTime)
    # No foreign key constraint on shift_code

class SubcontractorProjectHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    subcontractor = db.Column(db.String(120), nullable=False)
    job_site = db.Column(db.String(255), nullable=False)
    first_day = db.Column(db.DateTime, nullable=False)
    last_day = db.Column(db.DateTime, nullable=False)
    manpower = db.Column(db.Integer, default=0)
    __table_args__ = (db.UniqueConstraint('subcontractor', 'job_site', name='uix_subcontractor_jobsite'),)

class WorkerCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    subcontractor = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(16), unique=True, nullable=False)
    __table_args__ = (db.UniqueConstraint('name', 'subcontractor', name='uix_worker_name_sub'),)

JOB_SITES = list(JOB_SITE_TIMEZONES.keys())

def generate_code():
    import random
    # Ensure code is unique
    while True:
        code = str(random.randint(100000, 999999))
        if not Shift.query.filter_by(code=code).first():
            return code

def get_or_create_code(name: str, subcontractor: str) -> str:
    """Return existing persistent code for worker or create a new one."""
    worker = WorkerCode.query.filter_by(name=name, subcontractor=subcontractor).first()
    if worker:
        return worker.code
    # Generate a new unique code
    code = generate_code()
    new_worker = WorkerCode(name=name, subcontractor=subcontractor, code=code)
    db.session.add(new_worker)
    db.session.commit()
    return code

def get_worker_by_code(code: str):
    return WorkerCode.query.filter_by(code=code).first()

def format_seconds(secs):
    hours = int(secs // 3600)
    minutes = int((secs % 3600) // 60)
    return f"{hours}h {minutes}m"

@app.route("/reset-session")
def reset_session():
    """Reset database session to fix binding issues"""
    try:
        cleanup_db_session()
        # Ensure tables exist after session reset
        ensure_tables_exist()
        return {
            "status": "success",
            "message": "Database session reset successfully",
            "database_url": app.config['SQLALCHEMY_DATABASE_URI']
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to reset session: {str(e)}"
        }

@app.route("/create-tables")
def create_tables():
    """Manually create database tables"""
    try:
        with app.app_context():
            db.create_all()
        return {
            "status": "success",
            "message": "Database tables created successfully",
            "database_url": app.config['SQLALCHEMY_DATABASE_URI']
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to create tables: {str(e)}"
        }

@app.route("/force-sqlite")
def force_sqlite():
    """Force the app to use SQLite database immediately"""
    try:
        # Update the database URI to SQLite
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///shifts.db'
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_pre_ping': True}
        
        # Dispose of existing connections
        db.engine.dispose()
        db.get_engine().dispose()
        
        # Recreate the engine and initialize tables
        with app.app_context():
            db.create_all()
        
        return {
            "status": "success",
            "message": "Forced switch to SQLite database completed",
            "database_url": app.config['SQLALCHEMY_DATABASE_URI'],
            "note": "You may need to refresh the page for changes to take effect"
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to force SQLite switch: {str(e)}"
        }

@app.route("/switch-to-sqlite")
def switch_to_sqlite():
    """Manually switch to SQLite database"""
    try:
        # Update the database URI to SQLite
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///shifts.db'
        
        # Recreate the database engine
        db.engine.dispose()
        db.get_engine().dispose()
        
        # Initialize tables
        with app.app_context():
            db.create_all()
        
        return {
            "status": "success",
            "message": "Switched to SQLite database",
            "database_url": app.config['SQLALCHEMY_DATABASE_URI']
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to switch to SQLite: {str(e)}"
        }

@app.route("/db-status")
def db_status():
    """Check database connection status"""
    try:
        # Test database connection
        db.session.execute(text("SELECT 1"))
        db.session.commit()
        connection_status = "Connected"
    except Exception as e:
        connection_status = f"Error: {str(e)}"
    
    return {
        "database_url": app.config['SQLALCHEMY_DATABASE_URI'].replace(
            app.config['SQLALCHEMY_DATABASE_URI'].split('@')[0].split('://')[1] if '@' in app.config['SQLALCHEMY_DATABASE_URI'] else '',
            '***'
        ) if 'postgresql' in app.config['SQLALCHEMY_DATABASE_URI'] else app.config['SQLALCHEMY_DATABASE_URI'],
        "connection_status": connection_status,
        "database_type": "PostgreSQL" if "postgresql" in app.config['SQLALCHEMY_DATABASE_URI'] else "SQLite"
    }

@app.route("/health")
def health_check():
    """Simple health check endpoint that doesn't require database access"""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}

@app.route("/", methods=["GET", "POST"])
def index():
    try:
        close_overdue_shifts()
    except Exception as e:
        print(f"Error in close_overdue_shifts: {e}")
        # Continue running even if database operations fail
    
    if request.method == "POST":
        action = request.form.get("action")
        now = datetime.now()

        if action == "clockin":
            try:
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
                
                # Check if already clocked in at any job site
                existing_shift = Shift.query.filter_by(name=name, subcontractor=subcontractor, clock_out=None).first()
                if existing_shift:
                    flash(f"You are already clocked in at job site: {existing_shift.job_site}.", "error")
                    return redirect(url_for("index"))
                
                code = get_or_create_code(name, subcontractor)
                shift = Shift(name=name, subcontractor=subcontractor, job_site=job_site, clock_in=now, code=code)
                db.session.add(shift)
                db.session.commit()
                flash(
                    f"Your code is: <b>{code}</b><br>"
                    f"<span style='color:red;'>This code is required to clock out. Please write it down or remember it. It will not be shown again!</span>",
                    "success"
                )
                return redirect(url_for("index"))
            except Exception as e:
                print(f"Error in clockin: {e}")
                flash("Database error. Please try again later.", "error")
                return redirect(url_for("index"))

        elif action == "break":
            try:
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
            except Exception as e:
                print(f"Error in break: {e}")
                flash("Database error. Please try again later.", "error")
                return redirect(url_for("index"))

        elif action == "resume":
            try:
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
            except Exception as e:
                print(f"Error in resume: {e}")
                flash("Database error. Please try again later.", "error")
                return redirect(url_for("index"))

        elif action == "clockout":
            try:
                input_code = request.form.get("input_code")
                if not input_code:
                    flash("Please enter your code to clock out.", "error")
                    return redirect(url_for("index"))
                shift = Shift.query.filter_by(code=input_code, clock_out=None).order_by(Shift.clock_in.desc()).first()
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
            except Exception as e:
                print(f"Error in clockout: {e}")
                flash("Database error. Please try again later.", "error")
                return redirect(url_for("index"))

        elif action == "quickclockin":
            try:
                input_code = request.form.get("code", "").strip()
                job_site = request.form.get("job_site", "")
                if not input_code or not job_site:
                    flash("Please enter your code and select a job site.", "error")
                    return redirect(url_for("index"))

                # find worker info
                worker = get_worker_by_code(input_code)
                if not worker:
                    flash("Code not found. If you're a new worker please use the New Worker form.", "error")
                    return redirect(url_for("index"))

                # Check already clocked in at any job site
                active = Shift.query.filter_by(name=worker.name, subcontractor=worker.subcontractor, clock_out=None).first()
                if active:
                    flash(f"You are already clocked in at job site: {active.job_site}.", "error")
                    return redirect(url_for("index"))

                shift = Shift(
                    name=worker.name,
                    subcontractor=worker.subcontractor,
                    job_site=job_site,
                    clock_in=datetime.now(),
                    code=input_code,
                )
                db.session.add(shift)
                db.session.commit()
                flash("Clock-in successful!", "success")
                return redirect(url_for("index"))
            except Exception as e:
                print(f"Error in quickclockin: {e}")
                flash("Database error. Please try again later.", "error")
                return redirect(url_for("index"))

        else:
            flash("Invalid action or state.", "error")
            return redirect(url_for("index"))

    # Get subcontractor suggestions with error handling
    try:
        subcontractors = get_subcontractor_suggestions()
    except Exception as e:
        print(f"Error getting subcontractor suggestions in index: {e}")
        subcontractors = []

    return render_template("index.html", job_sites=JOB_SITES, subcontractors=subcontractors)

def get_subcontractor_suggestions():
    """Get list of existing subcontractors for auto-suggestions"""
    try:
        # Ensure tables exist before querying
        if not ensure_tables_exist():
            return []
            
        subcontractors = [row[0] for row in db.session.query(Shift.subcontractor).distinct().all()]
        return [s for s in subcontractors if s]  # Filter out None/empty values
    except Exception as e:
        print(f"Error getting subcontractor suggestions: {e}")
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
        
        # Ensure tables exist before querying
        if not ensure_tables_exist():
            flash("Database tables are not available. Please try again later.", "error")
            return render_template("admin_login.html")
        
        subcontractor_filter = request.args.get('subcontractor', '')
        job_site_filter = request.args.get('job_site', '')
        
        # Get shifts with error handling and convert to dicts
        try:
            query = Shift.query
            if subcontractor_filter:
                query = query.filter_by(subcontractor=subcontractor_filter)
            if job_site_filter:
                query = query.filter_by(job_site=job_site_filter)
            shift_objects = query.order_by(Shift.created_at.desc()).all()
            
            # Convert to dictionaries to avoid session binding issues
            shifts = []
            for s in shift_objects:
                shifts.append({
                    "id": s.id,
                    "name": s.name,
                    "subcontractor": s.subcontractor,
                    "job_site": s.job_site,
                    "clock_in": s.clock_in,
                    "clock_out": s.clock_out,
                    "total_time": s.total_time,
                    "working_time": s.working_time,
                    "breaks": s.breaks,
                    "code": s.code,
                    "created_at": s.created_at,
                    "qr_batch_id": s.qr_batch_id,
                    "flagged": s.flagged,
                })
        except Exception as e:
            print(f"Error querying shifts: {e}")
            shifts = []
            flash("Error loading shift data. Please try again.", "error")
        
        # Get project history and summary with filters
        try:
            history_objects = build_project_history(subcontractor=subcontractor_filter or None, job_site=job_site_filter or None)
            subcontractor_stats = calculate_subcontractor_days(subcontractor=subcontractor_filter or None, job_site=job_site_filter or None)
            
            # Convert history objects to dictionaries (these are SQLAlchemy result objects)
            histories = []
            for h in history_objects:
                histories.append({
                    "subcontractor": h.subcontractor,
                    "job_site": h.job_site,
                    "first_day": h.first_day,
                    "last_day": h.last_day,
                    "manpower": h.manpower,
                })
        except Exception as e:
            print(f"Error loading project history: {e}")
            histories = []
            subcontractor_stats = {}
            flash("Error loading project history. Please try again.", "error")
        
        # Get unique subcontractors and job sites for filters
        try:
            subcontractors = [row[0] for row in db.session.query(Shift.subcontractor).distinct().all()]
            job_sites = [row[0] for row in db.session.query(Shift.job_site).distinct().all()]
        except Exception as e:
            print(f"Error loading filter options: {e}")
            subcontractors = []
            job_sites = []
        
        try:
            close_overdue_shifts()
        except Exception as e:
            print(f"Error closing overdue shifts in admin: {e}")
        
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
        print(f"Admin view error: {e}")
        flash(f"Admin view error: {str(e)}", "error")
        return render_template("admin_login.html")

def calculate_subcontractor_days(subcontractor=None, job_site=None):
    """Calculate days worked and total hours for each subcontractor, with optional filters"""
    subcontractor_stats = {}
    query = Shift.query.filter(Shift.clock_out.isnot(None))
    if subcontractor:
        query = query.filter_by(subcontractor=subcontractor)
    if job_site:
        query = query.filter_by(job_site=job_site)
    completed_shifts = query.all()
    for shift in completed_shifts:
        if shift.subcontractor not in subcontractor_stats:
            subcontractor_stats[shift.subcontractor] = {
                'days': 0,
                'hours': 0.0
            }
        subcontractor_stats[shift.subcontractor]['days'] += 1
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
    # Get the filtered project history (use current filters if present)
    subcontractor_filter = request.args.get('subcontractor', '')
    job_site_filter = request.args.get('job_site', '')
    histories = build_project_history(subcontractor=subcontractor_filter or None, job_site=job_site_filter or None)
    def generate():
        data = [
            ["Subcontractor", "Job Site", "First Day", "Last Day", "Manpower"]
        ]
        for h in histories:
            data.append([
                h.subcontractor,
                h.job_site,
                h.first_day.strftime('%Y-%m-%d'),
                h.last_day.strftime('%Y-%m-%d'),
                h.manpower
            ])
        output = ''
        for row in data:
            output += ','.join(f'"{str(cell)}"' for cell in row) + '\n'
        return output
    return Response(generate(), mimetype='text/csv', headers={"Content-Disposition": "attachment;filename=subcontractor_project_history.csv"})

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

QR_BATCH_FILE = "qr_batches.json"

def load_qr_batches():
    if os.path.exists(QR_BATCH_FILE):
        with open(QR_BATCH_FILE, "r") as f:
            return json.load(f)
    return {}

def save_qr_batches(batches):
    with open(QR_BATCH_FILE, "w") as f:
        json.dump(batches, f)

@app.route("/admin/qr_codes")
def admin_qr_codes():
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_view"))
    qr_codes = {}
    errors = []
    batches = load_qr_batches()
    for job_site in JOB_SITES:
        qr_codes[job_site] = {}
        for action in ["clockin", "clockout"]:
            key = f"{job_site}::{action}"
            batch_id = batches.get(key)
            if not batch_id:
                batch_id = str(uuid.uuid4())
                batches[key] = batch_id
            try:
                qr_image, timestamp, qr_url = generate_qr_code(job_site, batch_id, action=action)
                qr_codes[job_site][action] = {
                    'image': qr_image,
                    'timestamp': timestamp,
                    'url': qr_url,
                    'batch_id': batch_id
                }
            except Exception as e:
                errors.append(f"Error generating QR for {job_site} ({action}): {e}")
    save_qr_batches(batches)
    if errors:
        flash("<br>".join(errors), "error")
    return render_template("qr_codes.html", qr_codes=qr_codes)

@app.route("/admin/qr_codes/refresh/<job_site>/<action>")
def refresh_qr_code(job_site, action):
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_view"))
    batches = load_qr_batches()
    key = f"{job_site}::{action}"
    batch_id = str(uuid.uuid4())
    batches[key] = batch_id
    save_qr_batches(batches)
    qr_image, timestamp, qr_url = generate_qr_code(job_site, batch_id, action=action)
    return {
        'image': qr_image,
        'timestamp': timestamp,
        'url': qr_url,
        'batch_id': batch_id,
        'action': action
    }

@app.route("/admin/qr_codes/refresh_all")
def refresh_all_qr_codes():
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_view"))
    batches = load_qr_batches()
    for job_site in JOB_SITES:
        for action in ["clockin", "clockout"]:
            key = f"{job_site}::{action}"
            batches[key] = str(uuid.uuid4())
    save_qr_batches(batches)
    return {"status": "ok"}

@app.route("/initdb")
def init_db():
    try:
        with app.app_context():
            db.create_all()
        return "Database tables created successfully!"
    except Exception as e:
        return f"Error creating tables: {str(e)}"

def generate_qr_code(job_site, batch_id, timestamp=None, action="clockin"):
    if timestamp is None:
        timestamp = int(time.time())
    site_id = hashlib.md5(job_site.encode()).hexdigest()[:8]
    from flask import request
    host_url = request.host_url.rstrip('/')
    if action == "clockin":
        qr_data = f"{host_url}/scan?site={site_id}&batch={batch_id}&t={timestamp}&action=clockin"
    else:
        qr_data = f"{host_url}/scan?site={site_id}&batch={batch_id}&t={timestamp}&action=clockout"
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
    
    # Validate batch_id
    if not batch_id:
        return "Invalid QR code (missing batch ID).", 400
    
    job_site = get_job_site_from_id(site_id)
    if not job_site:
        return "Invalid job site.", 400
    
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
    
    # Check if already clocked in at any job site
    existing_shift = Shift.query.filter_by(name=name, subcontractor=subcontractor, clock_out=None).first()
    if existing_shift:
        flash(f"You are already clocked in at job site: {existing_shift.job_site}.", "error")
        return redirect(url_for("qr_scan", site=hashlib.md5(job_site.encode()).hexdigest()[:8], batch=batch_id, t=int(time.time())))
    
    # Clock in
    now = datetime.now()
    code = get_or_create_code(name, subcontractor)
    shift = Shift(name=name, subcontractor=subcontractor, job_site=job_site, clock_in=now, code=code, qr_batch_id=batch_id)
    db.session.add(shift)
    db.session.commit()
    flash(f"Successfully clocked in! Your code is: <b>{code}</b>", "success")
    return redirect(url_for("qr_scan", site=hashlib.md5(job_site.encode()).hexdigest()[:8], batch=batch_id, t=int(time.time())))

def sync_to_procore(shift):
    # Procore integration disabled; nothing to do
    return

@app.route("/qr_clock_out", methods=["POST"])
def qr_clock_out():
    """Handle clock out via QR code"""
    code = request.form.get("code", "").strip()
    job_site = request.form.get("job_site", "")
    batch_id = request.args.get('batch') or request.form.get('batch_id')
    
    if not code or not job_site or not batch_id:
        flash("Please enter your code.", "error")
        return redirect(url_for("qr_scan", site=hashlib.md5(job_site.encode()).hexdigest()[:8], batch=batch_id, t=int(time.time())))
    
    # Find shift by code and job site
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

    # Update project history tracking
    update_subcontractor_history(shift)

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

def build_project_history(subcontractor=None, job_site=None):
    query = db.session.query(
        Shift.subcontractor,
        Shift.job_site,
        func.min(Shift.clock_in).label('first_day'),
        func.max(Shift.clock_out).label('last_day'),
        func.count(Shift.id).label('manpower')
    ).filter(Shift.clock_out.isnot(None))
    if subcontractor:
        query = query.filter(Shift.subcontractor == subcontractor)
    if job_site:
        query = query.filter(Shift.job_site == job_site)
    records = query.group_by(Shift.subcontractor, Shift.job_site).all()
    return records

def close_overdue_shifts(max_hours: int = 24):
    """Auto-close any open shift older than max_hours and flag it."""
    try:
        # Ensure tables exist before querying
        if not ensure_tables_exist():
            print("Cannot close overdue shifts - tables not available")
            return
            
        cutoff = datetime.utcnow() - timedelta(hours=max_hours)
        overdue_shifts = Shift.query.filter(Shift.clock_out.is_(None), Shift.clock_in < cutoff).all()
        for s in overdue_shifts:
            s.clock_out = s.clock_in + timedelta(hours=max_hours)
            s.total_time = format_seconds(max_hours * 3600)
            s.working_time = s.total_time
            s.breaks = "AUTO-CLOSED"
            s.flagged = True
        if overdue_shifts:
            db.session.commit()
    except Exception as e:
        print(f"Error closing overdue shifts: {e}")
        # Don't crash the app if database is unavailable
        pass

@app.route('/add_flagged_column')
def add_flagged_column():
    try:
        db.session.execute(text("ALTER TABLE shift ADD COLUMN flagged BOOLEAN DEFAULT FALSE;"))
        db.session.commit()
        return "flagged column added"
    except Exception as e:
        return str(e)

@app.route('/admin/edit/<int:shift_id>', methods=['GET', 'POST'])
def admin_edit_shift(shift_id):
    if not session.get("admin_authenticated"):
        return redirect(url_for('admin_view'))

    shift = Shift.query.get_or_404(shift_id)

    if request.method == 'POST':
        try:
            clock_in_str = request.form.get('clock_in')
            clock_out_str = request.form.get('clock_out')
            shift.clock_in = datetime.strptime(clock_in_str, '%Y-%m-%dT%H:%M')
            if clock_out_str:
                shift.clock_out = datetime.strptime(clock_out_str, '%Y-%m-%dT%H:%M')
                duration = (shift.clock_out - shift.clock_in).total_seconds()
                shift.total_time = format_seconds(duration)
                shift.working_time = shift.total_time
            db.session.commit()
            flash('Shift updated.', 'success')
            return redirect(url_for('admin_view'))
        except ValueError:
            flash('Invalid date/time format.', 'error')

    return render_template('edit_shift.html', shift=shift)

@app.route("/admin/qr_codes/print/<job_site>/<action>")
def print_qr_code(job_site, action):
    batches = load_qr_batches()
    key = f"{job_site}::{action}"
    batch_id = batches.get(key)
    if not batch_id:
        # Generate a new batch ID if missing
        import uuid
        batch_id = str(uuid.uuid4())
        batches[key] = batch_id
        save_qr_batches(batches)
    qr_image, timestamp, qr_url = generate_qr_code(job_site, batch_id, action=action)
    return render_template("print_qr.html", job_site=job_site, action=action, qr_image=qr_image, batch_id=batch_id)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
