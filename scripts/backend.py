import json
from datetime import datetime, timedelta

import pytz
import redis
from database import (create_grow, end_grow, get_active_grow,
                      get_aggregated_data, get_all_grows, get_database_stats,
                      get_latest_sensor_data, get_sensor_data_range,
                      set_active_grow, update_grow_stage)
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
redis_client = redis.Redis(host='localhost', port=6379, db=0)
ARGENTINA_TZ = pytz.timezone('America/Argentina/Cordoba')

try:
    # Optional: keep backend import-safe even if file is missing in older deployments.
    from gpio_outputs import OUTPUTS  # type: ignore
except Exception:
    OUTPUTS = []

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


@app.route('/api/output-status', methods=['GET'])
def get_output_status():
    """
    Endpoint to get current output/relay status from Redis plus BCM pin mapping.
    """
    outputs = []
    for o in OUTPUTS:
        redis_key = o.get("redis_key")
        raw = redis_client.get(redis_key) if redis_key else None
        if raw is None:
            state = None
        else:
            try:
                val = raw.decode("utf-8").strip().lower()
            except Exception:
                val = str(raw).strip().lower()
            if val in ("true", "1", "on", "yes"):
                state = True
            elif val in ("false", "0", "off", "no"):
                state = False
            else:
                state = None

        outputs.append(
            {
                "name": o.get("name"),
                "label": o.get("label"),
                "pin_bcm": o.get("pin_bcm"),
                "redis_key": redis_key,
                "state": state,
            }
        )

    return jsonify({"outputs": outputs})


@app.route('/api/sensor-history', methods=['GET'])
def get_sensor_history():
    """
    Endpoint to get sensor history from SQLite database.
    
    Query parameters:
    - period: Predefined time period (1h, 6h, 12h, 24h, 7d, 30d, 90d)
    - start: Start timestamp (unix)
    - end: End timestamp (unix)
    - limit: Maximum number of records
    - aggregate: Aggregation interval in seconds (optional)
    
    Examples:
    - /api/sensor-history?period=24h
    - /api/sensor-history?period=7d&aggregate=3600
    - /api/sensor-history?start=1672531200&end=1672617600
    """
    try:
        # Get query parameters
        period = request.args.get('period')
        start = request.args.get('start', type=int)
        end = request.args.get('end', type=int)
        limit = request.args.get('limit', type=int)
        aggregate = request.args.get('aggregate', type=int)
        
        # Calculate time range based on period
        if period:
            current_time = datetime.now(ARGENTINA_TZ)
            current_timestamp = int(current_time.timestamp())
            
            periods = {
                '1h': 3600,
                '6h': 6 * 3600,
                '12h': 12 * 3600,
                '24h': 24 * 3600,
                '7d': 7 * 24 * 3600,
                '30d': 30 * 24 * 3600,
                '90d': 90 * 24 * 3600
            }
            
            if period not in periods:
                return jsonify({'error': f'Invalid period. Use one of: {", ".join(periods.keys())}'}), 400
            
            start = current_timestamp - periods[period]
            end = current_timestamp
        
        # If no time range specified, return recent data
        if start is None and end is None:
            if limit is None:
                limit = 100
            data = get_latest_sensor_data(limit=limit)
            return jsonify({
                'data': data,
                'count': len(data),
                'aggregated': False
            })
        
        # Get aggregated or raw data
        if aggregate:
            data = get_aggregated_data(start, end, aggregate)
            return jsonify({
                'data': data,
                'count': len(data),
                'start': start,
                'end': end,
                'aggregated': True,
                'interval_seconds': aggregate
            })
        else:
            data = get_sensor_data_range(start, end, limit)
            return jsonify({
                'data': data,
                'count': len(data),
                'start': start,
                'end': end,
                'aggregated': False
            })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/database-stats', methods=['GET'])
def database_stats():
    """
    Endpoint to get database statistics
    """
    try:
        stats = get_database_stats()
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/history/aggregated', methods=['GET'])
def get_history_aggregated():
    """
    Endpoint to get aggregated historical data for charts.
    
    Query parameters:
    - days: Number of days to look back (default: 7)
    - interval: Aggregation interval - hourly, 6hourly, daily (default: hourly)
    - grow_id: Optional grow ID to filter by (if not provided, uses active grow)
    
    Examples:
    - /api/history/aggregated?days=1&interval=hourly
    - /api/history/aggregated?days=7&interval=6hourly
    - /api/history/aggregated?days=30&interval=daily
    - /api/history/aggregated?days=7&interval=hourly&grow_id=2
    """
    try:
        days = request.args.get('days', default=7, type=int)
        interval = request.args.get('interval', default='hourly', type=str)
        grow_id = request.args.get('grow_id', type=int)
        
        # Map interval names to seconds
        interval_map = {
            'hourly': 3600,
            '6hourly': 6 * 3600,
            'daily': 24 * 3600
        }
        
        if interval not in interval_map:
            return jsonify({'error': f'Invalid interval. Use one of: {", ".join(interval_map.keys())}'}), 400
        
        interval_seconds = interval_map[interval]
        
        # Calculate time range
        current_time = datetime.now(ARGENTINA_TZ)
        end_timestamp = int(current_time.timestamp())
        start_timestamp = end_timestamp - (days * 24 * 3600)
        
        # Get aggregated data
        data = get_aggregated_data(start_timestamp, end_timestamp, interval_seconds, grow_id)
        
        return jsonify({
            'data': data,
            'count': len(data),
            'days': days,
            'interval': interval,
            'grow_id': grow_id,
            'start_datetime': datetime.fromtimestamp(start_timestamp, ARGENTINA_TZ).strftime('%Y-%m-%d %H:%M:%S'),
            'end_datetime': datetime.fromtimestamp(end_timestamp, ARGENTINA_TZ).strftime('%Y-%m-%d %H:%M:%S')
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ===============================
# Grow Management Endpoints
# ===============================

@app.route('/api/grows', methods=['GET'])
def list_grows():
    """
    Get all grows with their statistics
    """
    try:
        grows = get_all_grows()
        return jsonify({
            'grows': grows,
            'count': len(grows)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/grows/active', methods=['GET'])
def active_grow():
    """
    Get the currently active grow
    """
    try:
        grow = get_active_grow()
        if grow:
            return jsonify(grow)
        return jsonify({'error': 'No active grow found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/grows', methods=['POST'])
def create_new_grow():
    """
    Create a new grow
    
    Body parameters:
    - name: Name of the grow (required)
    - stage: Growth stage (early_veg, late_veg, flowering, dry) (default: early_veg)
    - notes: Optional notes
    
    Example:
    POST /api/grows
    {
        "name": "Cultivo #2 - OG Kush",
        "stage": "early_veg",
        "notes": "Semillas feminizadas"
    }
    """
    try:
        data = request.get_json()
        
        if not data or 'name' not in data:
            return jsonify({'error': 'Name is required'}), 400
        
        name = data['name']
        stage = data.get('stage', 'early_veg')
        notes = data.get('notes', '')
        
        # Validate stage
        valid_stages = ['early_veg', 'late_veg', 'flowering', 'dry']
        if stage not in valid_stages:
            return jsonify({'error': f'Invalid stage. Use one of: {", ".join(valid_stages)}'}), 400
        
        grow_id = create_grow(name, stage, notes)
        
        if grow_id:
            return jsonify({
                'success': True,
                'grow_id': grow_id,
                'message': f'Grow "{name}" created successfully'
            }), 201
        else:
            return jsonify({'error': 'Failed to create grow'}), 500
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/grows/<int:grow_id>/end', methods=['POST'])
def finish_grow(grow_id):
    """
    End a grow by setting its end date and deactivating it
    
    Example:
    POST /api/grows/1/end
    """
    try:
        success = end_grow(grow_id)
        
        if success:
            return jsonify({
                'success': True,
                'message': f'Grow {grow_id} ended successfully'
            })
        else:
            return jsonify({'error': 'Failed to end grow'}), 500
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/grows/<int:grow_id>/activate', methods=['POST'])
def activate_grow(grow_id):
    """
    Set a grow as active
    
    Example:
    POST /api/grows/2/activate
    """
    try:
        success = set_active_grow(grow_id)
        
        if success:
            return jsonify({
                'success': True,
                'message': f'Grow {grow_id} activated successfully'
            })
        else:
            return jsonify({'error': 'Failed to activate grow'}), 500
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/grows/<int:grow_id>/stage', methods=['PUT'])
def update_stage(grow_id):
    """
    Update the stage of a grow
    
    Body parameters:
    - stage: New stage (early_veg, late_veg, flowering, dry)
    
    Example:
    PUT /api/grows/1/stage
    {
        "stage": "flowering"
    }
    """
    try:
        data = request.get_json()
        
        if not data or 'stage' not in data:
            return jsonify({'error': 'Stage is required'}), 400
        
        stage = data['stage']
        
        # Validate stage
        valid_stages = ['early_veg', 'late_veg', 'flowering', 'dry']
        if stage not in valid_stages:
            return jsonify({'error': f'Invalid stage. Use one of: {", ".join(valid_stages)}'}), 400
        
        success = update_grow_stage(grow_id, stage)
        
        if success:
            return jsonify({
                'success': True,
                'message': f'Grow {grow_id} stage updated to {stage}'
            })
        else:
            return jsonify({'error': 'Failed to update stage'}), 500
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000) 