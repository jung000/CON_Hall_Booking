from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
from datetime import datetime, date, time
import os
from sqlalchemy import and_, or_
import logging
import json

app = Flask(__name__)
# Use PostgreSQL if DATABASE_URL is provided, else fallback to SQLite
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///hall_booking.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({"success": False, "error": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return jsonify({"success": False, "error": "Internal server error"}), 500

@app.errorhandler(400)
def bad_request(error):
    return jsonify({"success": False, "error": "Bad request"}), 400

# Ensure all API responses are JSON
@app.after_request
def after_request(response):
    if request.path.startswith('/') and not request.path.startswith('/static'):
        if response.content_type != 'application/json':
            # Try to convert to JSON if it's an error response
            if 400 <= response.status_code < 600:
                try:
                    data = json.loads(response.get_data())
                    response.data = json.dumps({"success": False, "error": data.get("description", "Unknown error")})
                    response.content_type = 'application/json'
                except:
                    response.data = json.dumps({"success": False, "error": "Unknown error"})
                    response.content_type = 'application/json'
    return response

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

def serialize_booking(b):
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

def serialize_room(r):
    return {
        "id": r.id,
        "name": r.name
    }

def check_room_availability(room_id, start_date, end_date, start_time, end_time, exclude_booking_id=None):
    """Check if a room is available for the given time period"""
    try:
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
    except Exception as e:
        app.logger.error(f"Error checking room availability: {str(e)}")
        return False

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/rooms")
def get_rooms():
    """Get all rooms with availability information if date/time filters provided"""
    try:
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
    except Exception as e:
        app.logger.error(f"Error getting rooms: {str(e)}")
        return jsonify({"success": False, "error": "Failed to get rooms"}), 500

@app.route("/book", methods=["POST"])
def book():
    """Create a new booking"""
    try:
        # Validate JSON content
        if not request.is_json:
            return jsonify({"success": False, "error": "Request must be JSON"}), 400
            
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400
        
        # Validate required fields
        required_fields = ["eventName", "startDate", "endDate", "startTime", "endTime", "participants", "department", "rooms"]
        missing_fields = [field for field in required_fields if not data.get(field)]
        if missing_fields:
            return jsonify({"success": False, "error": f"Missing required fields: {', '.join(missing_fields)}"}), 400
        
        # Validate dates
        try:
            start_date = datetime.strptime(data["startDate"], "%Y-%m-%d").date()
            end_date = datetime.strptime(data["endDate"], "%Y-%m-%d").date()
            today = date.today()
            
            if start_date < today:
                return jsonify({"success": False, "error": "Start date cannot be in the past"}), 400
            if end_date < start_date:
                return jsonify({"success": False, "error": "End date cannot be before start date"}), 400
        except ValueError:
            return jsonify({"success": False, "error": "Invalid date format. Use YYYY-MM-DD"}), 400
        
        # Validate time
        if data["startTime"] >= data["endTime"] and data["startDate"] == data["endDate"]:
            return jsonify({"success": False, "error": "End time must be after start time"}), 400
        
        # Validate participants
        try:
            participants = int(data["participants"])
            if participants < 1:
                return jsonify({"success": False, "error": "Must have at least 1 participant"}), 400
        except (ValueError, TypeError):
            return jsonify({"success": False, "error": "Invalid participants number"}), 400
        
        # Check for room availability
        room_objects = []
        for room_id in data["rooms"]:
            room = Room.query.get(room_id)
            if not room:
                return jsonify({"success": False, "error": f"Room with ID {room_id} not found"}), 404
            
            # Check if room is available
            if not check_room_availability(room_id, data["startDate"], data["endDate"], data["startTime"], data["endTime"]):
                return jsonify({"success": False, "error": f"Room '{room.name}' is not available for the selected time period"}), 400
            
            room_objects.append(room)
        
        # Create booking
        booking = Booking(
            eventName=data["eventName"].strip(),
            startDate=data["startDate"],
            endDate=data["endDate"],
            startTime=data["startTime"],
            endTime=data["endTime"],
            participants=participants,
            department=data["department"].strip(),
            notes=data.get("notes", "").strip(),
            status="pending"
        )
        
        # Add rooms to booking
        for room in room_objects:
            booking.rooms.append(room)
        
        db.session.add(booking)
        db.session.commit()
        socketio.emit('update_events')
        return jsonify({"success": True, "booking": serialize_booking(booking)})
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Booking error: {str(e)}")
        return jsonify({"success": False, "error": "Failed to create booking"}), 500

@app.route("/pending")
def pending():
    """Get all pending booking requests"""
    try:
        bookings = Booking.query.filter_by(status="pending").all()
        return jsonify([serialize_booking(b) for b in bookings])
    except Exception as e:
        app.logger.error(f"Error getting pending bookings: {str(e)}")
        return jsonify({"success": False, "error": "Failed to get pending bookings"}), 500

@app.route("/approved")
def approved():
    """Get approved bookings with optional filtering"""
    try:
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
        return jsonify([serialize_booking(b) for b in bookings])
    except Exception as e:
        app.logger.error(f"Error getting approved bookings: {str(e)}")
        return jsonify({"success": False, "error": "Failed to get approved bookings"}), 500

@app.route("/approve", methods=["POST"])
def approve():
    """Approve a booking request"""
    try:
        if not request.is_json:
            return jsonify({"success": False, "error": "Request must be JSON"}), 400
            
        data = request.get_json()
        if not data or "id" not in data:
            return jsonify({"success": False, "error": "Booking ID required"}), 400
        
        booking = Booking.query.get(data["id"])
        if not booking:
            return jsonify({"success": False, "error": "Booking not found"}), 404
        
        # Check if rooms are still available
        for room in booking.rooms:
            if not check_room_availability(
                room.id, booking.startDate, booking.endDate, 
                booking.startTime, booking.endTime, booking.id
            ):
                return jsonify({
                    "success": False, 
                    "error": f"Room '{room.name}' is no longer available for the selected time period"
                }), 400
        
        booking.status = "approved"
        db.session.commit()
        socketio.emit('update_events')
        return jsonify({"success": True})
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Approve error: {str(e)}")
        return jsonify({"success": False, "error": "Failed to approve booking"}), 500

@app.route("/reject", methods=["POST"])
def reject():
    """Reject a booking request"""
    try:
        if not request.is_json:
            return jsonify({"success": False, "error": "Request must be JSON"}), 400
            
        data = request.get_json()
        if not data or "id" not in data:
            return jsonify({"success": False, "error": "Booking ID required"}), 400
        
        booking = Booking.query.get(data["id"])
        if not booking:
            return jsonify({"success": False, "error": "Booking not found"}), 404
        
        booking.status = "rejected"
        db.session.commit()
        socketio.emit('update_events')
        return jsonify({"success": True})
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Reject error: {str(e)}")
        return jsonify({"success": False, "error": "Failed to reject booking"}), 500

@app.route("/delete", methods=["POST"])
def delete():
    """Delete a booking"""
    try:
        if not request.is_json:
            return jsonify({"success": False, "error": "Request must be JSON"}), 400
            
        data = request.get_json()
        if not data or "id" not in data:
            return jsonify({"success": False, "error": "Booking ID required"}), 400
        
        booking = Booking.query.get(data["id"])
        if not booking:
            return jsonify({"success": False, "error": "Booking not found"}), 404
        
        db.session.delete(booking)
        db.session.commit()
        socketio.emit('update_events')
        return jsonify({"success": True})
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Delete error: {str(e)}")
        return jsonify({"success": False, "error": "Failed to delete booking"}), 500

@app.route("/rooms/add", methods=["POST"])
def add_room():
    """Add a new room"""
    try:
        if not request.is_json:
            return jsonify({"success": False, "error": "Request must be JSON"}), 400
            
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400
        
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"success": False, "error": "Room name is required"}), 400
        
        # Check if room already exists
        existing_room = Room.query.filter_by(name=name).first()
        if existing_room:
            return jsonify({"success": False, "error": "Room with this name already exists"}), 400
        
        room = Room(name=name)
        db.session.add(room)
        db.session.commit()
        socketio.emit('update_events')
        return jsonify({
            "success": True, 
            "message": f"Room '{name}' added successfully",
            "room": serialize_room(room)
        })
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error adding room: {str(e)}")
        return jsonify({"success": False, "error": "Failed to add room"}), 500

@app.route("/admin/login", methods=["POST"])
def admin_login():
    """Admin login endpoint"""
    try:
        if not request.is_json:
            return jsonify({"success": False, "error": "Request must be JSON"}), 400
            
        data = request.get_json()
        username = data.get("username")
        password = data.get("password")
        
        # Simple hardcoded admin credentials
        if username == "Admin" and password == "Admin@cihsr2411":
            return jsonify({"success": True, "message": "Login successful"})
        else:
            return jsonify({"success": False, "error": "Invalid credentials"}), 401
            
    except Exception as e:
        app.logger.error(f"Login error: {str(e)}")
        return jsonify({"success": False, "error": "Login failed"}), 500

@app.route("/stats")
def get_stats():
    """Get booking statistics"""
    try:
        pending_count = Booking.query.filter_by(status="pending").count()
        approved_count = Booking.query.filter_by(status="approved").count()
        total_rooms = Room.query.count()
        
        return jsonify({
            "pending": pending_count,
            "approved": approved_count,
            "total_rooms": total_rooms
        })
    except Exception as e:
        app.logger.error(f"Stats error: {str(e)}")
        return jsonify({"success": False, "error": "Failed to get statistics"}), 500

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