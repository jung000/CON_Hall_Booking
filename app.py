from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
from datetime import datetime, date, time
import os
from sqlalchemy import and_, or_

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

def check_room_availability(room_id, start_date, end_date, start_time, end_time, exclude_booking_id=None):
    """Check if a room is available for the given time period"""
    # Convert string dates to date objects
    start_d = datetime.strptime(start_date, "%Y-%m-%d").date()
    end_d = datetime.strptime(end_date, "%Y-%m-%d").date()
    
    # Convert string times to time objects
    start_t = datetime.strptime(start_time, "%H:%M").time()
    end_t = datetime.strptime(end_time, "%H:%M").time()
    
    # Query for overlapping bookings
    overlapping_query = Booking.query.join(booking_rooms).join(Room).filter(
        Room.id == room_id,
        Booking.status == "approved",
        or_(
            # Case 1: New booking starts during existing booking
            and_(
                Booking.startDate <= start_date,
                Booking.endDate >= start_date,
                Booking.startTime <= start_time,
                Booking.endTime > start_time
            ),
            # Case 2: New booking ends during existing booking
            and_(
                Booking.startDate <= end_date,
                Booking.endDate >= end_date,
                Booking.startTime < end_time,
                Booking.endTime >= end_time
            ),
            # Case 3: New booking completely contains existing booking
            and_(
                Booking.startDate >= start_date,
                Booking.endDate <= end_date
            ),
            # Case 4: Existing booking completely contains new booking
            and_(
                Booking.startDate <= start_date,
                Booking.endDate >= end_date
            )
        )
    )
    
    if exclude_booking_id:
        overlapping_query = overlapping_query.filter(Booking.id != exclude_booking_id)
    
    return overlapping_query.count() == 0

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/rooms")
def get_rooms():
    """Get all rooms with availability information if date/time filters provided"""
    start_date = request.args.get("startDate")
    end_date = request.args.get("endDate")
    start_time = request.args.get("startTime")
    end_time = request.args.get("endTime")
    
    rooms = Room.query.all()
    result = []
    
    for room in rooms:
        room_data = {"id": room.id, "name": room.name}
        
        # Check availability if date/time parameters are provided
        if start_date and end_date and start_time and end_time:
            room_data["available"] = check_room_availability(
                room.id, start_date, end_date, start_time, end_time
            )
        
        result.append(room_data)
    
    return jsonify(result)

@app.route("/book", methods=["POST"])
def book():
    data = request.get_json()
    
    # Validate required fields
    required_fields = ["eventName", "startDate", "endDate", "startTime", "endTime", "participants", "department", "rooms"]
    for field in required_fields:
        if not data.get(field):
            return jsonify({"success": False, "error": f"Missing required field: {field}"})
    
    # Validate dates
    try:
        start_date = datetime.strptime(data["startDate"], "%Y-%m-%d").date()
        end_date = datetime.strptime(data["endDate"], "%Y-%m-%d").date()
        today = date.today()
        
        if start_date < today:
            return jsonify({"success": False, "error": "Start date cannot be in the past"})
        if end_date < start_date:
            return jsonify({"success": False, "error": "End date cannot be before start date"})
    except ValueError:
        return jsonify({"success": False, "error": "Invalid date format"})
    
    # Validate time
    if data["startTime"] >= data["endTime"] and data["startDate"] == data["endDate"]:
        return jsonify({"success": False, "error": "End time must be after start time"})
    
    # Check for room availability
    room_objects = []
    for room_id in data["rooms"]:
        room = Room.query.get(room_id)
        if not room:
            return jsonify({"success": False, "error": f"Room with ID {room_id} not found"})
        
        # Check if room is available
        if not check_room_availability(room_id, data["startDate"], data["endDate"], data["startTime"], data["endTime"]):
            return jsonify({"success": False, "error": f"Room '{room.name}' is not available for the selected time period"})
        
        room_objects.append(room)
    
    # Create booking
    booking = Booking(
        eventName=data["eventName"],
        startDate=data["startDate"],
        endDate=data["endDate"],
        startTime=data["startTime"],
        endTime=data["endTime"],
        participants=data["participants"],
        department=data["department"],
        notes=data.get("notes", ""),
        status="pending"
    )
    
    # Add rooms to booking
    for room in room_objects:
        booking.rooms.append(room)
    
    try:
        db.session.add(booking)
        db.session.commit()
        socketio.emit('update_events')
        return jsonify({"success": True, "booking": serialize(booking)})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)})

@app.route("/pending")
def pending():
    """Get all pending booking requests"""
    bookings = Booking.query.filter_by(status="pending").all()
    return jsonify([serialize(b) for b in bookings])

@app.route("/approved")
def approved():
    """Get approved bookings with optional filtering"""
    room_filter = request.args.get("room")
    date_filter = request.args.get("date")
    
    query = Booking.query.filter_by(status="approved")
    
    # Apply room filter if provided
    if room_filter:
        query = query.join(booking_rooms).join(Room).filter(Room.id == room_filter)
    
    # Apply date filter if provided
    if date_filter:
        query = query.filter(
            and_(
                Booking.startDate <= date_filter,
                Booking.endDate >= date_filter
            )
        )
    
    bookings = query.all()
    return jsonify([serialize(b) for b in bookings])

@app.route("/approve", methods=["POST"])
def approve():
    """Approve a booking request"""
    data = request.get_json()
    booking = Booking.query.get(data.get("id"))
    
    if not booking:
        return jsonify({"success": False, "error": "Booking not found"})
    
    # Check if rooms are still available
    for room in booking.rooms:
        if not check_room_availability(
            room.id, booking.startDate, booking.endDate, 
            booking.startTime, booking.endTime, booking.id
        ):
            return jsonify({
                "success": False, 
                "error": f"Room '{room.name}' is no longer available for the selected time period"
            })
    
    booking.status = "approved"
    
    try:
        db.session.commit()
        socketio.emit('update_events')
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)})

@app.route("/reject", methods=["POST"])
def reject():
    """Reject a booking request"""
    data = request.get_json()
    booking = Booking.query.get(data.get("id"))
    
    if not booking:
        return jsonify({"success": False, "error": "Booking not found"})
    
    booking.status = "rejected"
    
    try:
        db.session.commit()
        socketio.emit('update_events')
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)})

@app.route("/delete", methods=["POST"])
def delete():
    """Delete a booking"""
    data = request.get_json()
    booking = Booking.query.get(data.get("id"))
    
    if not booking:
        return jsonify({"success": False, "error": "Booking not found"})
    
    try:
        db.session.delete(booking)
        db.session.commit()
        socketio.emit('update_events')
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)})

@app.route("/rooms/add", methods=["POST"])
def add_room():
    """Add a new room"""
    data = request.get_json()
    name = data.get("name", "").strip()
    
    if not name:
        return jsonify({"success": False, "error": "Room name is required"})
    
    # Check if room already exists
    existing_room = Room.query.filter_by(name=name).first()
    if existing_room:
        return jsonify({"success": False, "error": "Room with this name already exists"})
    
    try:
        room = Room(name=name)
        db.session.add(room)
        db.session.commit()
        socketio.emit('update_events')
        return jsonify({"success": True, "message": f"Room '{name}' added successfully"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)})

@app.route("/admin/login", methods=["POST"])
def admin_login():
    """Admin login endpoint (in production, use proper authentication)"""
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")
    
    # Simple hardcoded admin credentials (replace with proper auth in production)
    if username == "Admin" and password == "Admin@cihsr2411":
        return jsonify({"success": True, "message": "Login successful"})
    else:
        return jsonify({"success": False, "error": "Invalid credentials"})

@app.route("/stats")
def get_stats():
    """Get booking statistics"""
    pending_count = Booking.query.filter_by(status="pending").count()
    approved_count = Booking.query.filter_by(status="approved").count()
    total_rooms = Room.query.count()
    
    return jsonify({
        "pending": pending_count,
        "approved": approved_count,
        "total_rooms": total_rooms
    })

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