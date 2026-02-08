from flask import Flask, request, jsonify
import threading
import logging
import os

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.route('/')
def home():
    """Home endpoint for uptime monitoring"""
    return jsonify({
        "status": "online",
        "service": "APEX Cricket Bot",
        "version": "2.0"
    })

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat()
    })

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    """Telegram webhook endpoint"""
    if request.method == 'POST':
        update = request.get_json()
        # Process Telegram update here
        logger.info(f"Received update: {update}")
        return 'OK'
    
    return 'Method not allowed', 405

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
