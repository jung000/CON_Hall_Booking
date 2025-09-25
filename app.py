import os
import json
import base64
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
import firebase_admin
from firebase_admin import credentials, firestore
from google.api_core.exceptions import FailedPrecondition

# ----------------- Flask & SocketIO -----------------
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# ----------------- Admin credentials -----------------
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "con123")

# ----------------- Firebase Init -----------------
cred_data = None
if os.environ.get("FIREBASE_CREDENTIALS"):
    cred_data = json.loads(os.environ["FIREBASE_CREDENTIALS"])
elif os.environ.get("FIREBASE_CREDENTIALS_B64"):
    cred_data = json.loads(base64.b64decode(os.environ["FIREBASE_CREDENTIALS_B64"]))

if not cred_data:
    raise RuntimeError("Missing FIREBASE_CREDENTIALS or FIREBASE_CREDENTIALS_B64 environment variable")

cred = credentials.Certificate(cred_data)

if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)

db = firestore.client()

# ----------------- Default Rooms -----------------
DEFAULT_ROOMS = [
    "CSSE Conference Hall 1",
    "CSSE Conference Hall 2",
    "ARES",
    "OSCE",
    "Board Room",
]

# Ensure default rooms exist
rooms_ref = db.collection("rooms")
existing = [r.to_dict().get("name") for r in rooms_ref.stream()]
for room in DEFAULT_ROOMS:
    if room not in existing:
        rooms_ref.document().set({"name": room, "available": True})

# ----------------- Helpers -----------------
def check_room_availability(rooms, start_date, end_date, start_time, end_time):
    """Check if rooms are available within a date/time range."""
    try:
        query = (
            db.collection("bookings")
            .where("rooms", "array_contains_any", rooms)
            .where("status", "==", "approved")
            .where("startDate", "<=", end_date)
            .where("endDate", ">=", start_date)
        )

        overlapping_bookings = query.stream()
        for booking in overlapping_bookings:
            b = booking.to_dict()
            if not (end_time <= b["startTime"] or start_time >= b["endTime"]):
                return False, f"Room(s) {rooms} already booked for overlapping time"
        return True, "Available"

    except FailedPrecondition as e:
        return False, f"Database index missing: {str(e)}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"

# ----------------- Routes -----------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/rooms", methods=["GET"])
def get_rooms():
    start_date = request.args.get("startDate")
    end_date = request.args.get("endDate")
    start_time = request.args.get("startTime")
    end_time = request.args.get("endTime")

    rooms_ref = db.collection("rooms").stream()
    rooms = []
    for r in rooms_ref:
        doc = r.to_dict()
        doc['id'] = r.id
        doc.setdefault('available', True)

        if start_date and end_date and start_time and end_time:
            available, _ = check_room_availability([doc['name']], start_date, end_date, start_time, end_time)
            doc['available'] = available
        rooms.append(doc)
    return jsonify(rooms)


@app.route("/book", methods=["POST"])
def book_room():
    data = request.json
    if not data:
        return jsonify({"success": False, "error": "Missing booking data"}), 400

    available, message = check_room_availability(
        data.get("rooms", []),
        data.get("startDate"),
        data.get("endDate"),
        data.get("startTime"),
        data.get("endTime")
    )

    if not available:
        return jsonify({"success": False, "error": message}), 400

    doc_ref = db.collection("bookings").document()
    booking = {
        "eventName": data.get("eventName"),
        "rooms": data.get("rooms", []),
        "startDate": data.get("startDate"),
        "endDate": data.get("endDate"),
        "startTime": data.get("startTime"),
        "endTime": data.get("endTime"),
        "participants": data.get("participants", 1),
        "department": data.get("department"),
        "notes": data.get("notes"),
        "status": "pending",
        "createdAt": datetime.now().isoformat()
    }
    doc_ref.set(booking)
    socketio.emit('update_events')
    return jsonify({"success": True, "id": doc_ref.id, "booking": booking})


@app.route("/admin/login", methods=["POST"])
def admin_login():
    data = request.json
    if not data:
        return jsonify({"success": False, "error": "Missing credentials"}), 400
    if data.get("username") == ADMIN_USER and data.get("password") == ADMIN_PASS:
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Invalid credentials"}), 401


@app.route("/admin/approve/<booking_id>", methods=["POST"])
def approve_booking(booking_id):
    try:
        db.collection("bookings").document(booking_id).update({"status": "approved"})
        socketio.emit("update_events")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/admin/reject/<booking_id>", methods=["POST"])
def reject_booking(booking_id):
    try:
        db.collection("bookings").document(booking_id).update({"status": "rejected"})
        socketio.emit("update_events")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/admin/delete/<booking_id>", methods=["POST"])
def delete_booking(booking_id):
    try:
        db.collection("bookings").document(booking_id).delete()
        socketio.emit("update_events")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/stats", methods=["GET"])
def get_stats():
    pending = len(list(db.collection('bookings').where('status', '==', 'pending').stream()))
    approved = len(list(db.collection('bookings').where('status', '==', 'approved').stream()))
    total_rooms = len(list(db.collection('rooms').stream()))
    return jsonify({"pending": pending, "approved": approved, "total_rooms": total_rooms})


# ----------------- Run App -----------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port)
