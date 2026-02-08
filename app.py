from flask import Flask, jsonify
from datetime import datetime
import os

app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({
        "status": "online",
        "service": "APEX Cricket Bot",
        "version": "3.0",
        "timestamp": datetime.utcnow().isoformat()
    })

@app.route('/health')
def health():
    return jsonify({"status": "healthy"})

@app.route('/ping')
def ping():
    return "pong"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
