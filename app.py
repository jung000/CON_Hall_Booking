from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
from datetime import datetime
import os
import logging

app = Flask(__name__)

# Use Render Postgres if DATABASE_URL is provided, else fallback to SQLite
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///hall_booking.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# ------------------ MODELS ------------------
class Room(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)

class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    eventName = db.Column(db.String(200), nullable=False)
    startDate = db.Column(db.String(50), nullable=False)
    endDate = db.Column(db.String(50), nullable=False)
    startTime = db.Column(db.String(50), nullable=False)
    endTime = db.Column(db.String(50), nullable=False)
    participants = db.Column(db.Integer, nullable=False)
    department = db.Column(db.String(100), nullable=True)
    notes = db.Column(db.String(300), nullable=True)
    status = db.Column(db.String(50), default="pending")

# ------------------ DB INIT ------------------
with app.app_context():
    db.create_all()
    # Insert default rooms if empty
    if Room.query.count() == 0:
        db.session.add_all([
            Room(name="Board Room"),
            Room(name="CSSC Conference Hall 1"),
            Room(name="CSSC Conference Hall 2")
        ])
        db.session.commit()
        app.logger.info("✅ Default rooms created")

# ------------------ ROUTES ------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/rooms", methods=["GET"])
def get_rooms():
    try:
        rooms = Room.query.all()
        return jsonify([{"id": r.id, "name": r.name} for r in rooms])
    except Exception as e:
        app.logger.error(f"Error getting rooms: {e}")
        return jsonify({"error": "Database error"}), 500

@app.route("/rooms/add", methods=["POST"])
def add_room():
    try:
        data = request.json
        new_room = Room(name=data["name"])
        db.session.add(new_room)
        db.session.commit()
        return jsonify({"message": "Room added"})
    except Exception as e:
        app.logger.error(f"Error adding room: {e}")
        return jsonify({"error": "Failed to add room"}), 500

@app.route("/bookings", methods=["POST"])
def create_booking():
    try:
        data = request.json
        booking = Booking(
            eventName=data["eventName"],
            startDate=data["startDate"],
            endDate=data["endDate"],
            startTime=data["startTime"],
            endTime=data["endTime"],
            participants=data["participants"],
            department=data.get("department"),
            notes=data.get("notes"),
            status="pending"
        )
        db.session.add(booking)
        db.session.commit()
        socketio.emit("new_booking", {"eventName": booking.eventName})
        return jsonify({"message": "Booking created"})
    except Exception as e:
        app.logger.error(f"Error creating booking: {e}")
        return jsonify({"error": "Failed to create booking"}), 500

@app.route("/stats", methods=["GET"])
def stats():
    try:
        pending = Booking.query.filter_by(status="pending").count()
        approved = Booking.query.filter_by(status="approved").count()
        rejected = Booking.query.filter_by(status="rejected").count()
        return jsonify({
            "pending": pending,
            "approved": approved,
            "rejected": rejected
        })
    except Exception as e:
        app.logger.error(f"Stats error: {e}")
        return jsonify({"error": "Failed to get stats"}), 500

# ------------------ MAIN ------------------
if __name__ == "__main__":
    socketio.run(app, debug=True, allow_unsafe_werkzeug=True)
