import time
import psutil
import torch
import sqlite3
import os
import sys
import json
import threading
import logging
from datetime import datetime, timedelta
from functools import wraps
from typing import Dict, Any, Optional, List, Callable
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from flask import Flask, Response, request, make_response
from dataclasses import dataclass, asdict
import pytz

# Optional imports
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    redis = None
try:
    from persistence import log_prediction_history
except Exception:
    log_prediction_history = None

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

DATABASE_CONNECTIONS = Gauge(
    'database_connection_pool_active',
    'Active database connections'
)

REDIS_CONNECTION_STATUS = Gauge(
    'redis_connection_status',
    'Redis connection status (1=connected, 0=disconnected)'
)

MODEL_LOAD_TIME = Gauge(
    'model_load_time_seconds',
    'Time taken to load the model'
)

HEALTH_CHECK_STATUS = Gauge(
    'health_check_status',
    'Overall health check status (1=healthy, 0=unhealthy)'
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
        
        # Add individual health check endpoints
        @app.route('/health/database')
        def database_health():
            return self._check_database_health()
        
        @app.route('/health/redis')
        def redis_health():
            return self._check_redis_health()
        
        @app.route('/health/model')
        def model_health():
            return self._check_model_health()
        
        @app.route('/health/system')
        def system_health():
            return self._check_system_health()
        
        @app.route('/health/dependencies')
        def dependencies_health():
            return self._check_dependencies_health()
    
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

    def _check_database_health(self) -> Dict[str, Any]:
        """Check database connectivity and performance"""
        try:
            # Try to connect to SQLite database (default for this app)
            db_path = os.environ.get('DATABASE_PATH', 'predictions.db')
            start_time = time.time()
            
            conn = sqlite3.connect(db_path, timeout=5)
            cursor = conn.cursor()
            cursor.execute('SELECT 1')
            cursor.fetchone()
            conn.close()
            
            connection_time = time.time() - start_time
            
            # Check database file size and permissions
            if os.path.exists(db_path):
                db_size = os.path.getsize(db_path)
                db_writable = os.access(db_path, os.W_OK)
            else:
                db_size = 0
                db_writable = False
            
            DATABASE_CONNECTIONS.set(1)
            
            return {
                'status': 'healthy',
                'connection_time_ms': round(connection_time * 1000, 2),
                'database': {
                    'path': db_path,
                    'size_bytes': db_size,
                    'writable': db_writable,
                    'connected': True
                }
            }
            
        except Exception as e:
            DATABASE_CONNECTIONS.set(0)
            return {
                'status': 'unhealthy',
                'error': str(e),
                'database': {
                    'connected': False
                }
            }

    def _check_redis_health(self) -> Dict[str, Any]:
        """Check Redis connectivity if available"""
        if not REDIS_AVAILABLE:
            REDIS_CONNECTION_STATUS.set(0)
            return {
                'status': 'unavailable',
                'error': 'Redis package not installed',
                'redis': {
                    'connected': False,
                    'available': False
                }
            }
        
        try:
            redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379')
            start_time = time.time()
            
            r = redis.from_url(redis_url, socket_timeout=5)
            r.ping()
            
            connection_time = time.time() - start_time
            info = r.info()
            
            REDIS_CONNECTION_STATUS.set(1)
            
            return {
                'status': 'healthy',
                'connection_time_ms': round(connection_time * 1000, 2),
                'redis': {
                    'connected': True,
                    'available': True,
                    'version': info.get('redis_version'),
                    'used_memory': info.get('used_memory_human'),
                    'connected_clients': info.get('connected_clients'),
                    'uptime_seconds': info.get('uptime_in_seconds')
                }
            }
            
        except Exception as e:
            REDIS_CONNECTION_STATUS.set(0)
            return {
                'status': 'unhealthy',
                'error': str(e),
                'redis': {
                    'connected': False,
                    'available': True
                }
            }

    def _check_model_health(self) -> Dict[str, Any]:
        """Check ML model status and performance"""
        try:
            model_path = 'model.pth'
            model_loaded = os.path.exists(model_path)
            
            model_info = {
                'loaded': model_loaded,
                'path': model_path,
                'size_bytes': os.path.getsize(model_path) if model_loaded else 0,
                'last_modified': os.path.getmtime(model_path) if model_loaded else None
            }
            
            # Check GPU availability and memory
            gpu_info = {
                'available': torch.cuda.is_available(),
                'device_count': torch.cuda.device_count() if torch.cuda.is_available() else 0,
                'memory_allocated': torch.cuda.memory_allocated() if torch.cuda.is_available() else 0,
                'memory_cached': torch.cuda.memory_reserved() if torch.cuda.is_available() else 0
            }
            
            # Get model accuracy if available
            accuracy = MODEL_ACCURACY._value.get() if MODEL_ACCURACY._value else 0.0
            
            status = 'healthy' if model_loaded else 'unhealthy'
            
            return {
                'status': status,
                'model': model_info,
                'gpu': gpu_info,
                'accuracy': accuracy,
                'inference_metrics': {
                    'total_inferences': MODEL_INFERENCE_COUNT._value.get() if MODEL_INFERENCE_COUNT._value else 0,
                    'average_duration': MODEL_INFERENCE_DURATION.observe if hasattr(MODEL_INFERENCE_DURATION, 'observe') else 0,
                    'failure_count': MODEL_INFERENCE_FAILURES._value.get() if MODEL_INFERENCE_FAILURES._value else 0
                }
            }
            
        except Exception as e:
            return {
                'status': 'unhealthy',
                'error': str(e),
                'model': {
                    'loaded': False
                }
            }

    def _check_system_health(self) -> Dict[str, Any]:
        """Check system resources and performance"""
        try:
            # CPU information
            cpu_percent = psutil.cpu_percent(interval=1)
            cpu_count = psutil.cpu_count()
            cpu_freq = psutil.cpu_freq()
            
            # Memory information
            memory = psutil.virtual_memory()
            swap = psutil.swap_memory()
            
            # Disk information
            disk = psutil.disk_usage('/')
            disk_io = psutil.disk_io_counters()
            
            # Network information
            network_io = psutil.net_io_counters()
            
            # Process information
            process = psutil.Process()
            process_memory = process.memory_info()
            
            # Temperature if available
            temps = {}
            try:
                temps = psutil.sensors_temperatures()
            except AttributeError:
                pass
            
            # Boot time
            boot_time = psutil.boot_time()
            
            return {
                'status': 'healthy',
                'timestamp': time.time(),
                'uptime_seconds': time.time() - boot_time,
                'cpu': {
                    'percent': cpu_percent,
                    'count': cpu_count,
                    'frequency_mhz': cpu_freq.current if cpu_freq else None,
                    'load_average': os.getloadavg() if hasattr(os, 'getloadavg') else None
                },
                'memory': {
                    'total': memory.total,
                    'available': memory.available,
                    'percent': memory.percent,
                    'used': memory.used,
                    'swap_total': swap.total,
                    'swap_used': swap.used,
                    'swap_percent': swap.percent
                },
                'disk': {
                    'total': disk.total,
                    'used': disk.used,
                    'free': disk.free,
                    'percent': (disk.used / disk.total) * 100,
                    'read_bytes': disk_io.read_bytes if disk_io else 0,
                    'write_bytes': disk_io.write_bytes if disk_io else 0
                },
                'network': {
                    'bytes_sent': network_io.bytes_sent if network_io else 0,
                    'bytes_recv': network_io.bytes_recv if network_io else 0,
                    'packets_sent': network_io.packets_sent if network_io else 0,
                    'packets_recv': network_io.packets_recv if network_io else 0
                },
                'process': {
                    'pid': process.pid,
                    'memory_rss': process_memory.rss,
                    'memory_vms': process_memory.vms,
                    'cpu_percent': process.cpu_percent(),
                    'num_threads': process.num_threads(),
                    'create_time': process.create_time()
                },
                'temperature': temps
            }
            
        except Exception as e:
            return {
                'status': 'unhealthy',
                'error': str(e)
            }

    def _check_dependencies_health(self) -> Dict[str, Any]:
        """Check external dependencies and services"""
        dependencies = {}
        
        # Check Python version
        dependencies['python'] = {
            'version': sys.version,
            'version_info': sys.version_info,
            'status': 'healthy'
        }
        
        # Check key packages
        packages = ['flask', 'torch', 'psutil', 'prometheus_client', 'sqlite3']
        if REDIS_AVAILABLE:
            packages.append('redis')
        
        for package in packages:
            try:
                if package == 'sqlite3':
                    import sqlite3
                    version = sqlite3.sqlite_version
                elif package == 'redis':
                    version = redis.__version__ if redis else 'unknown'
                else:
                    module = __import__(package)
                    version = getattr(module, '__version__', 'unknown')
                
                dependencies[package] = {
                    'version': version,
                    'status': 'healthy'
                }
            except ImportError as e:
                dependencies[package] = {
                    'version': None,
                    'status': 'unhealthy',
                    'error': str(e)
                }
        
        # Check environment variables
        required_env_vars = ['SECRET_KEY', 'FLASK_ENV']
        env_status = {}
        for var in required_env_vars:
            env_status[var] = {
                'set': var in os.environ,
                'value': os.environ.get(var, '')[:8] + '...' if var in os.environ and var == 'SECRET_KEY' else os.environ.get(var, '')
            }
        
        dependencies['environment'] = env_status
        
        # Overall status
        overall_status = 'healthy'
        for dep in dependencies.values():
            if isinstance(dep, dict) and dep.get('status') == 'unhealthy':
                overall_status = 'degraded'
                break
        
        return {
            'status': overall_status,
            'dependencies': dependencies
        }

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

# Advanced Backup and Recovery Monitoring Metrics
BACKUP_OPERATIONS = Counter(
    'backup_operations_total',
    'Total backup operations',
    ['backup_type', 'status']
)

BACKUP_DURATION = Histogram(
    'backup_duration_seconds',
    'Backup operation duration in seconds',
    ['backup_type'],
    buckets=[60, 300, 900, 1800, 3600, 7200]
)

BACKUP_SIZE = Gauge(
    'backup_size_bytes',
    'Current backup size in bytes'
)

BACKUP_RETENTION_DAYS = Gauge(
    'backup_retention_days',
    'Backup retention period in days'
)

RECOVERY_OPERATIONS = Counter(
    'recovery_operations_total',
    'Total recovery operations',
    ['recovery_type', 'status']
)

RECOVERY_DURATION = Histogram(
    'recovery_duration_seconds',
    'Recovery operation duration in seconds',
    ['recovery_type'],
    buckets=[300, 900, 1800, 3600, 7200, 14400]
)

DISASTER_EVENTS = Counter(
    'disaster_events_total',
    'Total disaster events',
    ['disaster_level', 'status']
)

DISASTER_RECOVERY_TIME = Histogram(
    'disaster_recovery_time_seconds',
    'Disaster recovery time in seconds',
    ['disaster_level'],
    buckets=[300, 900, 1800, 3600, 7200, 86400]
)

BACKUP_VERIFICATION_RESULTS = Counter(
    'backup_verification_results_total',
    'Backup verification results',
    ['result']
)

RECOVERY_TEST_RESULTS = Counter(
    'recovery_test_results_total',
    'Recovery test results',
    ['result']
)

@dataclass
class BackupMonitoringConfig:
    """Configuration for backup monitoring"""
    enable_backup_metrics: bool = True
    enable_recovery_metrics: bool = True
    enable_disaster_metrics: bool = True
    backup_alert_threshold_gb: float = 50.0
    recovery_alert_threshold_minutes: float = 30.0
    disaster_alert_threshold_minutes: float = 15.0

class BackupMonitoringSystem:
    """Advanced backup and recovery monitoring system"""
    
    def __init__(self, config: BackupMonitoringConfig):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.monitoring_active = False
        self.monitoring_thread = None
        self.backup_metrics_cache = {}
        self.recovery_metrics_cache = {}
        
        # Initialize monitoring database
        self.db_path = '/tmp/backup_monitoring.db'
        self._init_monitoring_db()
        
        self.logger.info("BackupMonitoringSystem initialized")
    
    def _init_monitoring_db(self):
        """Initialize monitoring database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS backup_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                backup_id TEXT NOT NULL,
                backup_type TEXT NOT NULL,
                duration_seconds REAL,
                size_bytes INTEGER,
                status TEXT NOT NULL,
                verification_result TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS recovery_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                recovery_id TEXT NOT NULL,
                recovery_type TEXT NOT NULL,
                duration_seconds REAL,
                status TEXT NOT NULL,
                backup_id TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS disaster_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_id TEXT NOT NULL,
                disaster_level TEXT NOT NULL,
                recovery_time_seconds REAL,
                status TEXT NOT NULL
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def record_backup_operation(self, backup_id: str, backup_type: str, 
                              duration_seconds: float, size_bytes: int, 
                              status: str, verification_result: Optional[str] = None):
        """Record backup operation metrics"""
        if not self.config.enable_backup_metrics:
            return
        
        try:
            # Update Prometheus metrics
            BACKUP_OPERATIONS.labels(backup_type=backup_type, status=status).inc()
            BACKUP_DURATION.labels(backup_type=backup_type).observe(duration_seconds)
            BACKUP_SIZE.set(size_bytes)
            
            # Record to database
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO backup_metrics 
                (timestamp, backup_id, backup_type, duration_seconds, size_bytes, status, verification_result)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                datetime.now(pytz.UTC).isoformat(),
                backup_id,
                backup_type,
                duration_seconds,
                size_bytes,
                status,
                verification_result
            ))
            
            conn.commit()
            conn.close()
            
            # Cache metrics
            self.backup_metrics_cache[backup_id] = {
                'timestamp': datetime.now(pytz.UTC),
                'backup_type': backup_type,
                'duration_seconds': duration_seconds,
                'size_bytes': size_bytes,
                'status': status,
                'verification_result': verification_result
            }
            
            # Check for alerts
            self._check_backup_alerts(backup_id, duration_seconds, size_bytes)
            
            self.logger.info(f"Recorded backup operation: {backup_id}")
            
        except Exception as e:
            self.logger.error(f"Failed to record backup operation: {str(e)}")
    
    def record_recovery_operation(self, recovery_id: str, recovery_type: str,
                                duration_seconds: float, status: str,
                                backup_id: Optional[str] = None):
        """Record recovery operation metrics"""
        if not self.config.enable_recovery_metrics:
            return
        
        try:
            # Update Prometheus metrics
            RECOVERY_OPERATIONS.labels(recovery_type=recovery_type, status=status).inc()
            RECOVERY_DURATION.labels(recovery_type=recovery_type).observe(duration_seconds)
            
            # Record to database
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO recovery_metrics 
                (timestamp, recovery_id, recovery_type, duration_seconds, status, backup_id)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                datetime.now(pytz.UTC).isoformat(),
                recovery_id,
                recovery_type,
                duration_seconds,
                status,
                backup_id
            ))
            
            conn.commit()
            conn.close()
            
            # Cache metrics
            self.recovery_metrics_cache[recovery_id] = {
                'timestamp': datetime.now(pytz.UTC),
                'recovery_type': recovery_type,
                'duration_seconds': duration_seconds,
                'status': status,
                'backup_id': backup_id
            }
            
            # Check for alerts
            self._check_recovery_alerts(recovery_id, duration_seconds)
            
            self.logger.info(f"Recorded recovery operation: {recovery_id}")
            
        except Exception as e:
            self.logger.error(f"Failed to record recovery operation: {str(e)}")
    
    def record_disaster_event(self, event_id: str, disaster_level: str,
                            recovery_time_seconds: float, status: str):
        """Record disaster event metrics"""
        if not self.config.enable_disaster_metrics:
            return
        
        try:
            # Update Prometheus metrics
            DISASTER_EVENTS.labels(disaster_level=disaster_level, status=status).inc()
            DISASTER_RECOVERY_TIME.labels(disaster_level=disaster_level).observe(recovery_time_seconds)
            
            # Record to database
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO disaster_metrics 
                (timestamp, event_id, disaster_level, recovery_time_seconds, status)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                datetime.now(pytz.UTC).isoformat(),
                event_id,
                disaster_level,
                recovery_time_seconds,
                status
            ))
            
            conn.commit()
            conn.close()
            
            # Check for alerts
            self._check_disaster_alerts(event_id, disaster_level, recovery_time_seconds)
            
            self.logger.info(f"Recorded disaster event: {event_id}")
            
        except Exception as e:
            self.logger.error(f"Failed to record disaster event: {str(e)}")
    
    def record_backup_verification(self, backup_id: str, result: str):
        """Record backup verification result"""
        try:
            BACKUP_VERIFICATION_RESULTS.labels(result=result).inc()
            
            # Update cached metrics
            if backup_id in self.backup_metrics_cache:
                self.backup_metrics_cache[backup_id]['verification_result'] = result
            
            self.logger.info(f"Recorded backup verification: {backup_id} - {result}")
            
        except Exception as e:
            self.logger.error(f"Failed to record backup verification: {str(e)}")
    
    def record_recovery_test(self, test_id: str, result: str):
        """Record recovery test result"""
        try:
            RECOVERY_TEST_RESULTS.labels(result=result).inc()
            self.logger.info(f"Recorded recovery test: {test_id} - {result}")
            
        except Exception as e:
            self.logger.error(f"Failed to record recovery test: {str(e)}")
    
    def _check_backup_alerts(self, backup_id: str, duration_seconds: float, size_bytes: float):
        """Check for backup-related alerts"""
        try:
            # Check backup size
            size_gb = size_bytes / (1024**3)
            if size_gb > self.config.backup_alert_threshold_gb:
                self._send_alert(f"Large backup detected: {backup_id} ({size_gb:.2f} GB)")
            
            # Check backup duration (if unusually long)
            if duration_seconds > 7200:  # 2 hours
                self._send_alert(f"Long backup duration: {backup_id} ({duration_seconds:.0f} seconds)")
            
        except Exception as e:
            self.logger.error(f"Failed to check backup alerts: {str(e)}")
    
    def _check_recovery_alerts(self, recovery_id: str, duration_seconds: float):
        """Check for recovery-related alerts"""
        try:
            if duration_seconds > self.config.recovery_alert_threshold_minutes * 60:
                self._send_alert(f"Long recovery time: {recovery_id} ({duration_seconds:.0f} seconds)")
            
        except Exception as e:
            self.logger.error(f"Failed to check recovery alerts: {str(e)}")
    
    def _check_disaster_alerts(self, event_id: str, disaster_level: str, recovery_time_seconds: float):
        """Check for disaster-related alerts"""
        try:
            if disaster_level in ['high', 'critical']:
                self._send_alert(f"High-level disaster: {event_id} ({disaster_level})")
            
            if recovery_time_seconds > self.config.disaster_alert_threshold_minutes * 60:
                self._send_alert(f"Long disaster recovery: {event_id} ({recovery_time_seconds:.0f} seconds)")
            
        except Exception as e:
            self.logger.error(f"Failed to check disaster alerts: {str(e)}")
    
    def _send_alert(self, message: str):
        """Send monitoring alert"""
        try:
            # Log alert
            self.logger.warning(f"ALERT: {message}")
            
            # Here you could integrate with external alerting systems
            # like PagerDuty, Slack, email, etc.
            
        except Exception as e:
            self.logger.error(f"Failed to send alert: {str(e)}")
    
    def get_backup_metrics_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Get backup metrics summary for the last N hours"""
        try:
            cutoff_time = datetime.now(pytz.UTC) - timedelta(hours=hours)
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT 
                    backup_type,
                    status,
                    COUNT(*) as count,
                    AVG(duration_seconds) as avg_duration,
                    AVG(size_bytes) as avg_size,
                    MAX(size_bytes) as max_size
                FROM backup_metrics 
                WHERE timestamp > ?
                GROUP BY backup_type, status
            ''', (cutoff_time.isoformat(),))
            
            results = cursor.fetchall()
            conn.close()
            
            summary = {}
            for row in results:
                backup_type, status, count, avg_duration, avg_size, max_size = row
                
                if backup_type not in summary:
                    summary[backup_type] = {}
                
                summary[backup_type][status] = {
                    'count': count,
                    'avg_duration_seconds': avg_duration,
                    'avg_size_bytes': avg_size,
                    'max_size_bytes': max_size
                }
            
            return summary
            
        except Exception as e:
            self.logger.error(f"Failed to get backup metrics summary: {str(e)}")
            return {}
    
    def get_recovery_metrics_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Get recovery metrics summary for the last N hours"""
        try:
            cutoff_time = datetime.now(pytz.UTC) - timedelta(hours=hours)
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT 
                    recovery_type,
                    status,
                    COUNT(*) as count,
                    AVG(duration_seconds) as avg_duration
                FROM recovery_metrics 
                WHERE timestamp > ?
                GROUP BY recovery_type, status
            ''', (cutoff_time.isoformat(),))
            
            results = cursor.fetchall()
            conn.close()
            
            summary = {}
            for row in results:
                recovery_type, status, count, avg_duration = row
                
                if recovery_type not in summary:
                    summary[recovery_type] = {}
                
                summary[recovery_type][status] = {
                    'count': count,
                    'avg_duration_seconds': avg_duration
                }
            
            return summary
            
        except Exception as e:
            self.logger.error(f"Failed to get recovery metrics summary: {str(e)}")
            return {}
    
    def get_disaster_metrics_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Get disaster metrics summary for the last N hours"""
        try:
            cutoff_time = datetime.now(pytz.UTC) - timedelta(hours=hours)
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT 
                    disaster_level,
                    status,
                    COUNT(*) as count,
                    AVG(recovery_time_seconds) as avg_recovery_time
                FROM disaster_metrics 
                WHERE timestamp > ?
                GROUP BY disaster_level, status
            ''', (cutoff_time.isoformat(),))
            
            results = cursor.fetchall()
            conn.close()
            
            summary = {}
            for row in results:
                disaster_level, status, count, avg_recovery_time = row
                
                if disaster_level not in summary:
                    summary[disaster_level] = {}
                
                summary[disaster_level][status] = {
                    'count': count,
                    'avg_recovery_time_seconds': avg_recovery_time
                }
            
            return summary
            
        except Exception as e:
            self.logger.error(f"Failed to get disaster metrics summary: {str(e)}")
            return {}

# Global backup monitoring instance
backup_monitoring_config = BackupMonitoringConfig()
backup_monitoring_system = BackupMonitoringSystem(backup_monitoring_config)
