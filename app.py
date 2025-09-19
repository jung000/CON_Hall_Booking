from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
from datetime import datetime, date
import os

app = Flask(__name__)
# Use PostgreSQL if DATABASE_URL is provided, else fallback to SQLite
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///hall_booking.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Association table
booking_rooms = db.Table(
    "booking_rooms",
    db.Column("booking_id", db.Integer, db.ForeignKey("booking.id")),
    db.Column("room_id", db.Integer, db.ForeignKey("room.id"))
)

class Room(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)

class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    eventName = db.Column(db.String(200), nullable=False)
    startDate = db.Column(db.String(20), nullable=False)
    endDate = db.Column(db.String(20), nullable=False)
    startTime = db.Column(db.String(10), nullable=False)
    endTime = db.Column(db.String(10), nullable=False)
    participants = db.Column(db.Integer, nullable=False)
    department = db.Column(db.String(100), nullable=False)
    notes = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default="pending")
    rooms = db.relationship("Room", secondary=booking_rooms, backref="bookings")

def serialize(b):
    return {
        "id": b.id,
        "eventName": b.eventName,
        "startDate": b.startDate,
        "endDate": b.endDate,
        "startTime": b.startTime,
        "endTime": b.endTime,
        "participants": b.participants,
        "department": b.department,
        "notes": b.notes,
        "status": b.status,
        "rooms": [r.name for r in b.rooms]
    }

@app.route("/")
def home():
    return render_template("index.html")

# --- Your booking, approve, reject, delete routes remain the same ---
# (Copy from your last working version with date validation + multiple rooms)

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        if Room.query.count() == 0:
            db.session.add_all([
                Room(name="Board Room"),
                Room(name="CSSC Conference Hall 1"),
                Room(name="CSSC Conference Hall 2")
            ])
            db.session.commit()
    socketio.run(app, debug=True, allow_unsafe_werkzeug=True)
