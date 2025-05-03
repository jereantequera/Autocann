import json
import redis
from flask import Flask, jsonify, render_template

app = Flask(__name__)
redis_client = redis.Redis(host='localhost', port=6379, db=0)

@app.route('/')
def index():
    """
    Serve the main dashboard page
    """
    return render_template('index.html')

@app.route('/api/historical-data', methods=['GET'])
def get_historical_data():
    """
    Endpoint to get all historical data from Redis
    Returns data for all time windows (6h, 12h, 24h, 1w)
    """
    time_windows = ['6h', '12h', '24h', '1w']
    response_data = {}
    
    for window in time_windows:
        key = f'historical_data_{window}'
        data = redis_client.get(key)
        if data:
            response_data[window] = json.loads(data)
        else:
            response_data[window] = []
    
    return jsonify(response_data)

@app.route('/api/current-data', methods=['GET'])
def get_current_data():
    """
    Endpoint to get current sensor data from Redis
    """
    data = redis_client.get('sensors')
    if data:
        return jsonify(json.loads(data))
    return jsonify({'error': 'No current data available'}), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000) 