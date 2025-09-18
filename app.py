from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
from datetime import datetime, date

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///hall_booking.db"
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
    startDate = db.Column(db.String(20), nullable=False)  # YYYY-MM-DD
    endDate = db.Column(db.String(20), nullable=False)    # YYYY-MM-DD
    startTime = db.Column(db.String(10), nullable=False)  # HH:MM
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

def dates_overlap(a_start, a_end, b_start, b_end):
    return not (a_end < b_start or a_start > b_end)

def times_overlap(a_start_t, a_end_t, b_start_t, b_end_t):
    return not (a_end_t <= b_start_t or a_start_t >= b_end_t)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/rooms")
def get_rooms():
    rooms = Room.query.all()
    return jsonify([{"id": r.id, "name": r.name} for r in rooms])

@app.route("/rooms/add", methods=["POST"])
def add_room():
    data = request.get_json()
    name = data.get("name","").strip()
    if not name:
        return jsonify({"success": False, "error": "Room name required"}), 400
    # prevent duplicate
    existing = Room.query.filter_by(name=name).first()
    if existing:
        return jsonify({"success": False, "error": "Room already exists"}), 400
    room = Room(name=name)
    db.session.add(room)
    db.session.commit()
    socketio.emit("update_events")
    return jsonify({"success": True, "message": "Room added!"})

@app.route("/book", methods=["POST"])
def book():
    data = request.get_json()
    try:
        room_ids = [int(x) for x in data.get("rooms",[])]
    except Exception:
        return jsonify({"success": False, "error": "Invalid room selection"}), 400
    if not room_ids:
        return jsonify({"success": False, "error": "Select at least one room"}), 400

    # validate dates/times
    try:
        startDate = datetime.strptime(data.get("startDate"), "%Y-%m-%d").date()
        endDate = datetime.strptime(data.get("endDate"), "%Y-%m-%d").date()
    except Exception:
        return jsonify({"success": False, "error": "Invalid start/end date format"}), 400

    if startDate < date.today():
        return jsonify({"success": False, "error": "Start date cannot be in the past"}), 400
    if endDate < startDate:
        return jsonify({"success": False, "error": "End date cannot be before start date"}), 400

    try:
        startTime = datetime.strptime(data.get("startTime"), "%H:%M").time()
        endTime = datetime.strptime(data.get("endTime"), "%H:%M").time()
    except Exception:
        return jsonify({"success": False, "error": "Invalid start/end time format"}), 400

    if endTime <= startTime and (startDate == endDate):
        return jsonify({"success": False, "error": "End time must be after start time for same-day bookings"}), 400

    rooms = Room.query.filter(Room.id.in_(room_ids)).all()
    if len(rooms) != len(room_ids):
        return jsonify({"success": False, "error": "One or more selected rooms not found"}), 400

    # Check conflicts per room: if any approved booking overlaps in date range AND time overlap -> conflict
    for r in rooms:
        approved = Booking.query.join(booking_rooms).filter(
            booking_rooms.c.room_id == r.id,
            Booking.status == "approved"
        ).all()
        for b in approved:
            b_start = datetime.strptime(b.startDate, "%Y-%m-%d").date()
            b_end = datetime.strptime(b.endDate, "%Y-%m-%d").date()
            if dates_overlap(startDate, endDate, b_start, b_end):
                # if date ranges overlap, check times overlap (assume recurring daily time)
                b_s_time = datetime.strptime(b.startTime, "%H:%M").time()
                b_e_time = datetime.strptime(b.endTime, "%H:%M").time()
                if times_overlap(startTime, endTime, b_s_time, b_e_time):
                    return jsonify({"success": False, "error": f"Time conflict in {r.name} with existing approved booking"}), 400

    booking = Booking(
        eventName=data.get("eventName","").strip(),
        startDate=startDate.strftime("%Y-%m-%d"),
        endDate=endDate.strftime("%Y-%m-%d"),
        startTime=startTime.strftime("%H:%M"),
        endTime=endTime.strftime("%H:%M"),
        participants=int(data.get("participants",1)),
        department=data.get("department","").strip(),
        notes=data.get("notes",""),
        status="pending"
    )
    booking.rooms = rooms
    db.session.add(booking)
    db.session.commit()
    socketio.emit("update_events")
    return jsonify({"success": True, "message": "Booking submitted!", "id": booking.id, "booking": serialize(booking)})

@app.route("/pending")
def pending():
    pending = Booking.query.filter_by(status="pending").all()
    return jsonify([serialize(b) for b in pending])

@app.route("/approved")
def approved():
    approved = Booking.query.filter_by(status="approved").all()
    return jsonify([serialize(b) for b in approved])

@app.route("/rejected")
def rejected():
    rejected = Booking.query.filter_by(status="rejected").all()
    return jsonify([serialize(b) for b in rejected])

@app.route("/approve", methods=["POST"])
def approve():
    data = request.get_json()
    booking = Booking.query.get(data["id"])
    if booking:
        # Before approving, check conflicts again against other approved
        rooms = booking.rooms
        s_date = datetime.strptime(booking.startDate, "%Y-%m-%d").date()
        e_date = datetime.strptime(booking.endDate, "%Y-%m-%d").date()
        s_time = datetime.strptime(booking.startTime, "%H:%M").time()
        e_time = datetime.strptime(booking.endTime, "%H:%M").time()
        for r in rooms:
            approved = Booking.query.join(booking_rooms).filter(
                booking_rooms.c.room_id == r.id,
                Booking.status == "approved",
                Booking.id != booking.id
            ).all()
            for b in approved:
                b_start = datetime.strptime(b.startDate, "%Y-%m-%d").date()
                b_end = datetime.strptime(b.endDate, "%Y-%m-%d").date()
                if dates_overlap(s_date, e_date, b_start, b_end):
                    b_s_time = datetime.strptime(b.startTime, "%H:%M").time()
                    b_e_time = datetime.strptime(b.endTime, "%H:%M").time()
                    if times_overlap(s_time, e_time, b_s_time, b_e_time):
                        return jsonify({"success": False, "error": f"Conflict when approving: room {r.name} is already booked"}), 400
        booking.status = "approved"
        db.session.commit()
        socketio.emit("update_events")
        return jsonify({"success": True, "message": "Booking approved!", "booking": serialize(booking)})
    return jsonify({"success": False, "error": "Booking not found"}), 404

@app.route("/reject", methods=["POST"])
def reject():
    data = request.get_json()
    booking = Booking.query.get(data["id"])
    if booking:
        booking.status = "rejected"
        db.session.commit()
        socketio.emit("update_events")
        return jsonify({"success": True, "message": "Booking rejected!"})
    return jsonify({"success": False, "error": "Booking not found"}), 404

@app.route("/delete", methods=["POST"])
def delete():
    data = request.get_json()
    booking = Booking.query.get(data["id"])
    if booking:
        db.session.delete(booking)
        db.session.commit()
        socketio.emit("update_events")
        return jsonify({"success": True, "message": "Booking deleted!"})
    return jsonify({"success": False, "error": "Booking not found"}), 404

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        if Room.query.count() == 0:
            db.session.add_all([Room(name="Board Room"), Room(name="CSSC Conference Hall 1"), Room(name="CSSC Conference Hall 2")])
            db.session.commit()
    socketio.run(app, debug=True)
