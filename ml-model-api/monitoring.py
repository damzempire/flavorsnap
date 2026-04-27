import time
import psutil
import torch
import numpy as np
import pandas as pd
from functools import wraps
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from collections import defaultdict, deque
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from flask import Flask, Response, request, make_response
try:
    from persistence import log_prediction_history
except Exception:
    log_prediction_history = None
try:
    from anomaly_detection import anomaly_system, AnomalyType
except Exception:
    anomaly_system = None
    AnomalyType = None

# Prometheus Metrics
REQUEST_COUNT = Counter(
    'flask_http_request_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status']
)

REQUEST_DURATION = Histogram(
    'flask_http_request_duration_seconds',
    'HTTP request duration in seconds',
    ['method', 'endpoint'],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

REQUEST_EXCEPTIONS = Counter(
    'flask_http_request_exceptions_total',
    'Total HTTP request exceptions',
    ['method', 'endpoint']
)

MODEL_INFERENCE_COUNT = Counter(
    'model_inference_total',
    'Total model inferences',
    ['label', 'status']
)

MODEL_INFERENCE_DURATION = Histogram(
    'model_inference_duration_seconds',
    'Model inference duration in seconds',
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5]
)

MODEL_INFERENCE_FAILURES = Counter(
    'model_inference_failures_total',
    'Total model inference failures'
)

MODEL_ACCURACY = Gauge(
    'model_accuracy',
    'Current model accuracy'
)

MEMORY_USAGE = Gauge(
    'memory_usage_bytes',
    'Memory usage in bytes'
)

CPU_USAGE = Gauge(
    'cpu_usage_percent',
    'CPU usage percentage'
)

GPU_MEMORY_USAGE = Gauge(
    'gpu_memory_usage_bytes',
    'GPU memory usage in bytes'
)

ACTIVE_CONNECTIONS = Gauge(
    'active_connections',
    'Number of active connections'
)

class MonitoringMiddleware:
    def __init__(self, app: Flask = None):
        self.app = app
        if app:
            self.init_app(app)
    
    def init_app(self, app: Flask):
        app.before_request(self._before_request)
        app.after_request(self._after_request)
        app.teardown_request(self._teardown_request)
        
        # Add metrics endpoint
        @app.route('/metrics')
        def metrics():
            # Update system metrics
            self._update_system_metrics()
            return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)
        
        # Add health check with detailed metrics
        @app.route('/health/detailed')
        def detailed_health():
            return self._get_detailed_health()
    
    def _before_request(self):
        request.start_time = time.time()
    
    def _after_request(self, response):
        if hasattr(request, 'start_time'):
            duration = time.time() - request.start_time
            
            # Record request metrics
            REQUEST_COUNT.labels(
                method=request.method,
                endpoint=request.endpoint or 'unknown',
                status=response.status_code
            ).inc()
            
            REQUEST_DURATION.labels(
                method=request.method,
                endpoint=request.endpoint or 'unknown'
            ).observe(duration)
        
        return response
    
    def _teardown_request(self, exception):
        if exception:
            REQUEST_EXCEPTIONS.labels(
                method=request.method,
                endpoint=request.endpoint or 'unknown'
            ).inc()
    
    def _update_system_metrics(self):
        # Update memory usage
        memory = psutil.virtual_memory()
        MEMORY_USAGE.set(memory.used)
        
        # Update CPU usage
        CPU_USAGE.set(psutil.cpu_percent())
        
        # Update GPU memory if available
        if torch.cuda.is_available():
            GPU_MEMORY_USAGE.set(torch.cuda.memory_allocated())
        
        # Update active connections (placeholder)
        ACTIVE_CONNECTIONS.set(1)  # This would need actual connection tracking
    
    def _get_detailed_health(self) -> Dict[str, Any]:
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        health_data = {
            'status': 'healthy',
            'timestamp': time.time(),
            'system': {
                'cpu_percent': psutil.cpu_percent(),
                'memory': {
                    'total': memory.total,
                    'available': memory.available,
                    'percent': memory.percent,
                    'used': memory.used
                },
                'disk': {
                    'total': disk.total,
                    'used': disk.used,
                    'free': disk.free,
                    'percent': (disk.used / disk.total) * 100
                }
            },
            'gpu': {
                'available': torch.cuda.is_available(),
                'device_count': torch.cuda.device_count() if torch.cuda.is_available() else 0,
                'memory_allocated': torch.cuda.memory_allocated() if torch.cuda.is_available() else 0,
                'memory_cached': torch.cuda.memory_reserved() if torch.cuda.is_available() else 0
            },
            'model': {
                'loaded': True,  # This would be set based on actual model state
                'accuracy': MODEL_ACCURACY._value.get() if MODEL_ACCURACY._value else 0.0
            }
        }
        
        return health_data

def track_inference(func):
    """Decorator to track model inference metrics"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        status = 'success'
        resp_obj = None
        try:
            result = func(*args, **kwargs)
            try:
                resp_obj = make_response(result)
            except Exception:
                resp_obj = None
            return result
        except Exception as e:
            status = 'failure'
            MODEL_INFERENCE_FAILURES.inc()
            raise
        finally:
            duration = time.time() - start_time
            MODEL_INFERENCE_DURATION.observe(duration)
            
            # Extract label from result if available
            label = 'unknown'
            payload = None
            try:
                if resp_obj is not None:
                    payload = resp_obj.get_json(silent=True)
                    if isinstance(payload, dict):
                        label = payload.get('label', 'unknown')
            except Exception:
                payload = None
            
            MODEL_INFERENCE_COUNT.labels(label=label, status=status).inc()
            try:
                if log_prediction_history and isinstance(payload, dict):
                    meta = {
                        "request_id": request.headers.get("X-Request-Id"),
                        "user_id": request.headers.get("X-User-Id"),
                        "error_message": None if status == 'success' else 'inference_failed'
                    }
                    log_prediction_history(payload, duration, status, meta)
            except Exception:
                pass
    
    return wrapper

def update_model_accuracy(accuracy: float):
    """Update model accuracy metric"""
    MODEL_ACCURACY.set(accuracy)

# Data Quality Monitoring
DATA_QUALITY_SCORE = Gauge(
    'data_quality_score',
    'Overall data quality score (0-100)'
)

MISSING_DATA_RATE = Gauge(
    'missing_data_rate',
    'Rate of missing data in incoming requests'
)

DUPLICATE_DATA_RATE = Gauge(
    'duplicate_data_rate',
    'Rate of duplicate data detected'
)

DATA_DRIFT_SCORE = Gauge(
    'data_drift_score',
    'Data drift detection score'
)

VALIDATION_ERRORS = Counter(
    'validation_errors_total',
    'Total validation errors',
    ['error_type']
)

class DataQualityMonitor:
    """Data quality monitoring and validation"""
    
    def __init__(self):
        self.data_buffer = deque(maxlen=1000)
        self.baseline_stats = {}
        self.validation_rules = {
            'image_size_range': (10, 16 * 1024 * 1024),  # 10 bytes to 16MB
            'allowed_formats': ['jpg', 'jpeg', 'png', 'gif', 'webp'],
            'max_text_length': 1000,
            'required_fields': ['image', 'timestamp']
        }
        self.duplicate_detector = set()
        self.drift_detector = None
    
    def validate_request_data(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate incoming request data"""
        validation_result = {
            'is_valid': True,
            'errors': [],
            'warnings': [],
            'quality_score': 100
        }
        
        try:
            # Check required fields
            for field in self.validation_rules['required_fields']:
                if field not in request_data:
                    validation_result['errors'].append(f"Missing required field: {field}")
                    validation_result['is_valid'] = False
                    VALIDATION_ERRORS.labels(error_type='missing_field').inc()
            
            # Validate image data if present
            if 'image' in request_data:
                image_errors = self._validate_image_data(request_data['image'])
                validation_result['errors'].extend(image_errors)
                if image_errors:
                    validation_result['is_valid'] = False
                    VALIDATION_ERRORS.labels(error_type='image_validation').inc()
            
            # Check for duplicates
            if self._is_duplicate(request_data):
                validation_result['warnings'].append("Potential duplicate data detected")
                validation_result['quality_score'] -= 10
            
            # Calculate quality score
            validation_result['quality_score'] = max(0, validation_result['quality_score'] - len(validation_result['errors']) * 20 - len(validation_result['warnings']) * 5)
            
            # Update metrics
            self._update_quality_metrics(validation_result)
            
            # Store for drift detection
            self.data_buffer.append({
                'timestamp': datetime.now(),
                'data': request_data,
                'quality_score': validation_result['quality_score']
            })
            
            # Trigger anomaly detection if available
            if anomaly_system:
                quality_data = {
                    'missing_rate': len([e for e in validation_result['errors'] if 'missing' in e.lower()]) / max(len(self.validation_rules['required_fields']), 1),
                    'duplicate_rate': 1.0 if self._is_duplicate(request_data) else 0.0,
                    'quality_score': validation_result['quality_score'] / 100.0,
                    'validation_errors': len(validation_result['errors'])
                }
                anomalies = anomaly_system.detect_anomalies(quality_data)
                if anomalies:
                    validation_result['anomalies'] = [a.to_dict() for a in anomalies]
            
        except Exception as e:
            validation_result['errors'].append(f"Validation error: {str(e)}")
            validation_result['is_valid'] = False
            VALIDATION_ERRORS.labels(error_type='system_error').inc()
        
        return validation_result
    
    def _validate_image_data(self, image_data: Any) -> List[str]:
        """Validate image data"""
        errors = []
        
        try:
            # Check image size
            if hasattr(image_data, 'seek') and hasattr(image_data, 'tell'):
                image_data.seek(0, 2)  # Seek to end
                size = image_data.tell()
                image_data.seek(0)  # Reset position
                
                min_size, max_size = self.validation_rules['image_size_range']
                if size < min_size or size > max_size:
                    errors.append(f"Image size {size} bytes is outside valid range [{min_size}, {max_size}]")
            
            # Check file format if filename is available
            if hasattr(image_data, 'filename') and image_data.filename:
                ext = image_data.filename.rsplit('.', 1)[1].lower() if '.' in image_data.filename else ''
                if ext not in self.validation_rules['allowed_formats']:
                    errors.append(f"Unsupported image format: {ext}")
        
        except Exception as e:
            errors.append(f"Image validation error: {str(e)}")
        
        return errors
    
    def _is_duplicate(self, request_data: Dict[str, Any]) -> bool:
        """Check for duplicate data"""
        try:
            # Create a hash of key fields for duplicate detection
            key_fields = []
            if 'image' in request_data and hasattr(request_data['image'], 'filename'):
                key_fields.append(request_data['image'].filename)
            if 'timestamp' in request_data:
                key_fields.append(str(request_data['timestamp']))
            
            if key_fields:
                data_hash = hash(tuple(key_fields))
                if data_hash in self.duplicate_detector:
                    return True
                self.duplicate_detector.add(data_hash)
                
                # Clean old hashes to prevent memory issues
                if len(self.duplicate_detector) > 10000:
                    # Keep only recent half
                    self.duplicate_detector = set(list(self.duplicate_detector)[-5000:])
        
        except Exception:
            pass
        
        return False
    
    def _update_quality_metrics(self, validation_result: Dict[str, Any]):
        """Update data quality metrics"""
        try:
            # Update quality score
            DATA_QUALITY_SCORE.set(validation_result['quality_score'])
            
            # Calculate missing data rate
            missing_rate = len([e for e in validation_result['errors'] if 'missing' in e.lower()]) / max(len(self.validation_rules['required_fields']), 1)
            MISSING_DATA_RATE.set(missing_rate)
            
            # Calculate duplicate rate
            duplicate_rate = 1.0 if any('duplicate' in w.lower() for w in validation_result['warnings']) else 0.0
            DUPLICATE_DATA_RATE.set(duplicate_rate)
            
        except Exception:
            pass
    
    def detect_data_drift(self) -> Dict[str, Any]:
        """Detect data drift using statistical methods"""
        try:
            if len(self.data_buffer) < 100:
                return {'drift_detected': False, 'reason': 'Insufficient data'}
            
            # Get recent and historical data
            recent_data = list(self.data_buffer)[-50:]  # Last 50 records
            historical_data = list(self.data_buffer)[:-50]  # Everything before recent
            
            if len(historical_data) < 50:
                return {'drift_detected': False, 'reason': 'Insufficient historical data'}
            
            # Compare quality scores
            recent_scores = [d['quality_score'] for d in recent_data]
            historical_scores = [d['quality_score'] for d in historical_data]
            
            # Statistical test for drift
            recent_mean = np.mean(recent_scores)
            historical_mean = np.mean(historical_scores)
            
            # Calculate drift score
            drift_score = abs(recent_mean - historical_mean) / max(historical_mean, 1)
            
            # Update drift metric
            DATA_DRIFT_SCORE.set(drift_score)
            
            drift_detected = drift_score > 0.15  # 15% change threshold
            
            return {
                'drift_detected': drift_detected,
                'drift_score': drift_score,
                'recent_mean': recent_mean,
                'historical_mean': historical_mean,
                'sample_sizes': {'recent': len(recent_data), 'historical': len(historical_data)}
            }
        
        except Exception as e:
            return {'drift_detected': False, 'error': str(e)}
    
    def get_quality_report(self) -> Dict[str, Any]:
        """Generate comprehensive data quality report"""
        try:
            if not self.data_buffer:
                return {'status': 'no_data', 'message': 'No data available for analysis'}
            
            recent_data = list(self.data_buffer)[-100:]  # Last 100 records
            
            # Calculate statistics
            quality_scores = [d['quality_score'] for d in recent_data]
            timestamps = [d['timestamp'] for d in recent_data]
            
            report = {
                'summary': {
                    'total_records': len(recent_data),
                    'avg_quality_score': np.mean(quality_scores),
                    'min_quality_score': np.min(quality_scores),
                    'max_quality_score': np.max(quality_scores),
                    'time_range': {
                        'start': min(timestamps).isoformat(),
                        'end': max(timestamps).isoformat()
                    }
                },
                'trends': {
                    'quality_trend': 'improving' if len(quality_scores) > 1 and quality_scores[-1] > quality_scores[0] else 'declining',
                    'data_volume_trend': 'increasing' if len(recent_data) > 50 else 'stable'
                },
                'issues': {
                    'low_quality_records': len([s for s in quality_scores if s < 70]),
                    'validation_errors': sum(1 for d in recent_data if d.get('validation_errors', 0) > 0),
                    'duplicate_warnings': sum(1 for d in recent_data if 'duplicate' in str(d.get('warnings', [])))
                },
                'drift_analysis': self.detect_data_drift()
            }
            
            return report
        
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

# Global data quality monitor instance
data_quality_monitor = DataQualityMonitor()

def validate_data_quality(func):
    """Decorator to validate data quality for API endpoints"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            # Get request data
            request_data = {}
            
            # Extract data from request
            if request.files:
                request_data.update(request.files.to_dict())
            if request.form:
                request_data.update(request.form.to_dict())
            if request.get_json():
                request_data.update(request.get_json())
            
            # Add metadata
            request_data['timestamp'] = datetime.now().isoformat()
            request_data['ip_address'] = request.remote_addr
            request_data['user_agent'] = request.headers.get('User-Agent', '')
            
            # Validate data quality
            validation_result = data_quality_monitor.validate_request_data(request_data)
            
            # Store validation result in request context for later use
            request.data_quality = validation_result
            
            # If data quality is too low, you might want to reject the request
            if validation_result['quality_score'] < 30:
                return {
                    'error': 'Data quality too low',
                    'quality_score': validation_result['quality_score'],
                    'errors': validation_result['errors']
                }, 400
            
            return func(*args, **kwargs)
        
        except Exception as e:
            return {'error': f'Data quality validation failed: {str(e)}'}, 500
    
    return wrapper
