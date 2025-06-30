from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import os

app = Flask(__name__)
app.secret_key = 'your_secret_key'

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

@app.route("/")
def index():
    return "Hello, world! Your Flask app is running."

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
