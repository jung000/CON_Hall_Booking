import os
import json
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# Load Firebase credentials from env variable
cred_data = None
if "FIREBASE_CREDENTIALS" in os.environ:
    cred_data = json.loads(os.environ["FIREBASE_CREDENTIALS"])
elif "FIREBASE_CREDENTIALS_B64" in os.environ:
    import base64
    cred_data = json.loads(base64.b64decode(os.environ["FIREBASE_CREDENTIALS_B64"]))

if not cred_data:
    raise RuntimeError("Missing FIREBASE_CREDENTIALS or FIREBASE_CREDENTIALS_B64 environment variable")

cred = credentials.Certificate(cred_data)
firebase_admin.initialize_app(cred)
db = firestore.client()

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "con123")

def check_room_availability(room_ids, start_date, end_date, start_time, end_time):
    """Check if rooms are available for the given time period"""
    for room_id in room_ids:
        # Check for approved bookings that overlap with the requested time
        overlapping_bookings = db.collection('bookings') \
            .where('rooms', 'array_contains', room_id) \
            .where('status', '==', 'approved') \
            .where('startDate', '<=', end_date) \
            .where('endDate', '>=', start_date) \
            .stream()
        
        for booking in overlapping_bookings:
            booking_data = booking.to_dict()
            # Check if time slots overlap
            if (start_time < booking_data['endTime'] and 
                end_time > booking_data['startTime']):
                return False, f"Room {room_id} is already booked for this time slot"
    return True, ""

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/admin/login', methods=['POST'])
def admin_login():
    data = request.json
    if not data:
        return jsonify({"success": False, "error": "Missing JSON body"}), 400
    username = data.get("username")
    password = data.get("password")
    if username == ADMIN_USER and password == ADMIN_PASS:
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Invalid login credentials"}), 401

@app.route('/rooms', methods=['GET'])
def get_rooms():
    rooms_ref = db.collection('rooms').stream()
    rooms = []
    
    # Get filter parameters if provided
    start_date = request.args.get("startDate")
    end_date = request.args.get("endDate")
    start_time = request.args.get("startTime")
    end_time = request.args.get("endTime")
    
    for r in rooms_ref:
        doc = r.to_dict()
        doc['id'] = r.id
        doc.setdefault('available', True)
        
        # Check availability if date/time filters are provided
        if start_date and end_date and start_time and end_time:
            available, _ = check_room_availability(
                [r.id], start_date, end_date, start_time, end_time
            )
            doc['available'] = available
        
        rooms.append(doc)
    return jsonify(rooms)

@app.route('/rooms/add', methods=['POST'])
def add_room():
    data = request.json
    if not data or not data.get("name"):
        return jsonify({"success": False, "error": "Room name is required"}), 400
    doc_ref = db.collection('rooms').document()
    doc_ref.set({"name": data["name"], "available": True})
    socketio.emit('update_events')
    return jsonify({"success": True, "room_id": doc_ref.id, "message": "Room added successfully"})

@app.route('/book', methods=['POST'])
def book_room():
    data = request.json
    if not data:
        return jsonify({"success": False, "error": "Missing booking data"}), 400
    
    # Check room availability
    available, message = check_room_availability(
        data.get("rooms", []),
        data.get("startDate"),
        data.get("endDate"),
        data.get("startTime"),
        data.get("endTime")
    )
    
    if not available:
        return jsonify({"success": False, "error": message}), 400
    
    doc_ref = db.collection('bookings').document()
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

@app.route('/pending', methods=['GET'])
def get_pending():
    bookings = db.collection('bookings').where('status', '==', 'pending').stream()
    result = []
    for b in bookings:
        doc = b.to_dict()
        doc['id'] = b.id
        result.append(doc)
    return jsonify(result)

@app.route('/approved', methods=['GET'])
def get_approved():
    query = db.collection('bookings').where('status', '==', 'approved')
    date = request.args.get("date")
    room = request.args.get("room")
    if date:
        query = query.where('startDate', '<=', date).where('endDate', '>=', date)
    if room:
        query = query.where('rooms', 'array_contains', room)
    bookings = query.stream()
    result = []
    for b in bookings:
        doc = b.to_dict()
        doc['id'] = b.id
        result.append(doc)
    return jsonify(result)

@app.route('/approve', methods=['POST'])
def approve_booking():
    data = request.json
    if not data or not data.get("id"):
        return jsonify({"success": False, "error": "Missing booking ID"}), 400
    db.collection('bookings').document(data['id']).update({"status": "approved"})
    socketio.emit('update_events')
    return jsonify({"success": True})

@app.route('/reject', methods=['POST'])
def reject_booking():
    data = request.json
    if not data or not data.get("id"):
        return jsonify({"success": False, "error": "Missing booking ID"}), 400
    db.collection('bookings').document(data['id']).update({"status": "rejected"})
    socketio.emit('update_events')
    return jsonify({"success": True})

@app.route('/delete', methods=['POST'])
def delete_booking():
    data = request.json
    if not data or not data.get("id"):
        return jsonify({"success": False, "error": "Missing booking ID"}), 400
    db.collection('bookings').document(data['id']).delete()
    socketio.emit('update_events')
    return jsonify({"success": True})

@app.route('/stats', methods=['GET'])
def get_stats():
    pending = len(list(db.collection('bookings').where('status', '==', 'pending').stream()))
    approved = len(list(db.collection('bookings').where('status', '==', 'approved').stream()))
    total_rooms = len(list(db.collection('rooms').stream()))
    return jsonify({"pending": pending, "approved": approved, "total_rooms": total_rooms})

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)