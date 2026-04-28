"""
Advanced Queue Monitoring System for FlavorSnap
Provides comprehensive monitoring, analytics, and alerting for queue operations
"""

import time
import threading
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from collections import defaultdict, deque
from enum import Enum
import logging
import statistics

logger = logging.getLogger(__name__)

class AlertLevel(Enum):
    """Alert severity levels"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

class MetricType(Enum):
    """Types of metrics to track"""
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    TIMER = "timer"

@dataclass
class Metric:
    """Metric data point"""
    name: str
    value: float
    timestamp: datetime = field(default_factory=datetime.now)
    labels: Dict[str, str] = field(default_factory=dict)
    metric_type: MetricType = MetricType.GAUGE

@dataclass
class Alert:
    """Alert definition"""
    name: str
    level: AlertLevel
    condition: str
    threshold: float
    message: str
    enabled: bool = True
    last_triggered: Optional[datetime] = None
    trigger_count: int = 0

@dataclass
class QueueMetrics:
    """Queue-specific metrics"""
    queue_name: str
    total_tasks: int = 0
    pending_tasks: int = 0
    running_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    average_wait_time: float = 0.0
    average_processing_time: float = 0.0
    throughput: float = 0.0  # tasks per second
    error_rate: float = 0.0  # percentage
    last_updated: datetime = field(default_factory=datetime.now)

class MetricsCollector:
    """Collects and stores metrics"""
    
    def __init__(self, max_history: int = 10000):
        self.max_history = max_history
        self._metrics: Dict[str, deque] = defaultdict(lambda: deque(maxlen=max_history))
        self._counters: Dict[str, float] = defaultdict(float)
        self._gauges: Dict[str, float] = defaultdict(float)
        self._lock = threading.RLock()
    
    def record_counter(self, name: str, value: float = 1.0, labels: Dict[str, str] = None):
        """Record counter metric"""
        with self._lock:
            key = self._make_key(name, labels)
            self._counters[key] += value
            metric = Metric(name, self._counters[key], metric_type=MetricType.COUNTER, labels=labels or {})
            self._metrics[key].append(metric)
    
    def set_gauge(self, name: str, value: float, labels: Dict[str, str] = None):
        """Set gauge metric"""
        with self._lock:
            key = self._make_key(name, labels)
            self._gauges[key] = value
            metric = Metric(name, value, metric_type=MetricType.GAUGE, labels=labels or {})
            self._metrics[key].append(metric)
    
    def record_histogram(self, name: str, value: float, labels: Dict[str, str] = None):
        """Record histogram metric"""
        with self._lock:
            key = self._make_key(name, labels)
            metric = Metric(name, value, metric_type=MetricType.HISTOGRAM, labels=labels or {})
            self._metrics[key].append(metric)
    
    def record_timer(self, name: str, duration_ms: float, labels: Dict[str, str] = None):
        """Record timer metric"""
        with self._lock:
            key = self._make_key(name, labels)
            metric = Metric(name, duration_ms, metric_type=MetricType.TIMER, labels=labels or {})
            self._metrics[key].append(metric)
    
    def _make_key(self, name: str, labels: Dict[str, str] = None) -> str:
        """Create metric key from name and labels"""
        if not labels:
            return name
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}[{label_str}]"
    
    def get_metric_history(self, name: str, labels: Dict[str, str] = None, 
                          since: Optional[datetime] = None) -> List[Metric]:
        """Get metric history"""
        with self._lock:
            key = self._make_key(name, labels)
            metrics = list(self._metrics[key])
            
            if since:
                metrics = [m for m in metrics if m.timestamp >= since]
            
            return metrics
    
    def get_current_metrics(self) -> Dict[str, Any]:
        """Get current metric values"""
        with self._lock:
            result = {}
            
            # Add counters
            for key, value in self._counters.items():
                result[key] = {"type": "counter", "value": value}
            
            # Add gauges
            for key, value in self._gauges.items():
                result[key] = {"type": "gauge", "value": value}
            
            return result
    
    def calculate_percentiles(self, name: str, percentiles: List[float] = None,
                            labels: Dict[str, str] = None) -> Dict[str, float]:
        """Calculate percentiles for histogram/timer metrics"""
        if percentiles is None:
            percentiles = [50.0, 90.0, 95.0, 99.0]
        
        metrics = self.get_metric_history(name, labels)
        if not metrics:
            return {}
        
        values = [m.value for m in metrics]
        result = {}
        
        for p in percentiles:
            try:
                result[f"p{p}"] = statistics.percentile(values, p)
            except Exception as e:
                logger.error(f"Error calculating percentile {p}: {e}")
                result[f"p{p}"] = 0.0
        
        return result

class AlertManager:
    """Manages alerts and notifications"""
    
    def __init__(self):
        self._alerts: Dict[str, Alert] = {}
        self._alert_handlers: List[Callable] = []
        self._lock = threading.RLock()
    
    def add_alert(self, alert: Alert):
        """Add alert definition"""
        with self._lock:
            self._alerts[alert.name] = alert
    
    def remove_alert(self, name: str):
        """Remove alert"""
        with self._lock:
            self._alerts.pop(name, None)
    
    def add_alert_handler(self, handler: Callable[[Alert], None]):
        """Add alert notification handler"""
        self._alert_handlers.append(handler)
    
    def check_alerts(self, metrics_collector: MetricsCollector):
        """Check all alerts against current metrics"""
        current_metrics = metrics_collector.get_current_metrics()
        
        with self._lock:
            for alert in self._alerts.values():
                if not alert.enabled:
                    continue
                
                try:
                    if self._evaluate_condition(alert.condition, alert.threshold, current_metrics):
                        self._trigger_alert(alert)
                except Exception as e:
                    logger.error(f"Error evaluating alert {alert.name}: {e}")
    
    def _evaluate_condition(self, condition: str, threshold: float, metrics: Dict[str, Any]) -> bool:
        """Evaluate alert condition"""
        # Simple condition evaluation - can be extended with more complex logic
        metric_name = condition.split()[0]  # Extract metric name
        
        if metric_name in metrics:
            current_value = metrics[metric_name]["value"]
            
            if ">" in condition:
                return current_value > threshold
            elif "<" in condition:
                return current_value < threshold
            elif ">=" in condition:
                return current_value >= threshold
            elif "<=" in condition:
                return current_value <= threshold
            elif "==" in condition:
                return current_value == threshold
        
        return False
    
    def _trigger_alert(self, alert: Alert):
        """Trigger alert notification"""
        alert.last_triggered = datetime.now()
        alert.trigger_count += 1
        
        logger.warning(f"Alert triggered: {alert.name} - {alert.message}")
        
        for handler in self._alert_handlers:
            try:
                handler(alert)
            except Exception as e:
                logger.error(f"Error in alert handler: {e}")

class QueueMonitor:
    """Comprehensive queue monitoring system"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.metrics_collector = MetricsCollector(
            max_history=self.config.get('max_history', 10000)
        )
        self.alert_manager = AlertManager()
        
        # Queue metrics tracking
        self._queue_metrics: Dict[str, QueueMetrics] = {}
        self._lock = threading.RLock()
        
        # Performance tracking
        self._performance_window = deque(maxlen=1000)  # Last 1000 operations
        
        # Monitoring thread
        self._monitoring_active = False
        self._monitoring_thread = None
        
        # Setup default alerts
        self._setup_default_alerts()
    
    def _setup_default_alerts(self):
        """Setup default alert rules"""
        default_alerts = [
            Alert(
                name="high_queue_size",
                level=AlertLevel.WARNING,
                condition="queue_size >",
                threshold=1000,
                message="Queue size is getting large"
            ),
            Alert(
                name="high_error_rate",
                level=AlertLevel.ERROR,
                condition="error_rate >",
                threshold=10.0,
                message="Error rate is too high"
            ),
            Alert(
                name="low_throughput",
                level=AlertLevel.WARNING,
                condition="throughput <",
                threshold=1.0,
                message="Queue throughput is too low"
            ),
            Alert(
                name="high_processing_time",
                level=AlertLevel.WARNING,
                condition="avg_processing_time >",
                threshold=30000,  # 30 seconds in ms
                message="Average processing time is too high"
            ),
            # Rate limiting alerts
            Alert(
                name="high_rate_limit_violations",
                level=AlertLevel.WARNING,
                condition="rate_limit_violations >",
                threshold=100,
                message="High rate limit violation rate detected"
            ),
            Alert(
                name="many_blocked_users",
                level=AlertLevel.ERROR,
                condition="blocked_users >",
                threshold=50,
                message="Too many users are blocked"
            ),
            Alert(
                name="rate_limit_bypass_usage",
                level=AlertLevel.INFO,
                condition="bypass_usage >",
                threshold=10,
                message="High rate limit bypass usage detected"
            )
        ]
        
        for alert in default_alerts:
            self.alert_manager.add_alert(alert)
    
    def start_monitoring(self, interval_seconds: int = 30):
        """Start background monitoring"""
        if self._monitoring_active:
            return
        
        self._monitoring_active = True
        self._monitoring_thread = threading.Thread(
            target=self._monitoring_loop,
            args=(interval_seconds,),
            daemon=True
        )
        self._monitoring_thread.start()
        logger.info("Queue monitoring started")
    
    def stop_monitoring(self):
        """Stop background monitoring"""
        self._monitoring_active = False
        if self._monitoring_thread:
            self._monitoring_thread.join(timeout=5)
        logger.info("Queue monitoring stopped")
    
    def _monitoring_loop(self, interval_seconds: int):
        """Background monitoring loop"""
        while self._monitoring_active:
            try:
                self._collect_metrics()
                self.alert_manager.check_alerts(self.metrics_collector)
                time.sleep(interval_seconds)
            except Exception as e:
                logger.error(f"Monitoring loop error: {e}")
                time.sleep(5)
    
    def _collect_metrics(self):
        """Collect current metrics from all queues"""
        with self._lock:
            for queue_name, metrics in self._queue_metrics.items():
                labels = {"queue": queue_name}
                
                # Record queue metrics
                self.metrics_collector.set_gauge("queue_size", metrics.pending_tasks, labels)
                self.metrics_collector.set_gauge("running_tasks", metrics.running_tasks, labels)
                self.metrics_collector.set_gauge("completed_tasks", metrics.completed_tasks, labels)
                self.metrics_collector.set_gauge("failed_tasks", metrics.failed_tasks, labels)
                self.metrics_collector.set_gauge("error_rate", metrics.error_rate, labels)
                self.metrics_collector.set_gauge("throughput", metrics.throughput, labels)
                self.metrics_collector.set_gauge("avg_wait_time", metrics.average_wait_time, labels)
                self.metrics_collector.set_gauge("avg_processing_time", metrics.average_processing_time, labels)
            
            # Collect rate limiting metrics if available
            try:
                from security_config import get_rate_limiter
                rate_limiter = get_rate_limiter()
                rate_limit_stats = rate_limiter.get_analytics(1)  # Last hour
                
                # Rate limiting metrics
                rate_limit_analytics = rate_limit_stats.get('rate_limit_analytics', {})
                current_stats = rate_limit_analytics.get('current_stats', {})
                
                self.metrics_collector.set_gauge("rate_limit_total_requests", current_stats.get('total_requests', 0))
                self.metrics_collector.set_gauge("rate_limit_blocked_requests", current_stats.get('blocked_requests', 0))
                self.metrics_collector.set_gauge("rate_limit_active_clients", rate_limit_stats.get('active_clients', 0))
                self.metrics_collector.set_gauge("rate_limit_blocked_clients", rate_limit_stats.get('blocked_clients', 0))
                
                # Calculate violation rate
                total_requests = current_stats.get('total_requests', 0)
                blocked_requests = current_stats.get('blocked_requests', 0)
                violation_rate = (blocked_requests / total_requests * 100) if total_requests > 0 else 0
                self.metrics_collector.set_gauge("rate_limit_violation_rate", violation_rate)
                
                # Dynamic adjustment metrics
                dynamic_adjustments = rate_limit_stats.get('dynamic_adjustments', {})
                for user_type, adjustment_stats in dynamic_adjustments.items():
                    labels = {"user_type": user_type}
                    self.metrics_collector.set_gauge("rate_limit_load_factor", 
                                                    adjustment_stats.get('current_load_factor', 1.0), labels)
                
            except Exception as e:
                logger.debug(f"Could not collect rate limiting metrics: {e}")
    
    def update_queue_metrics(self, queue_name: str, **kwargs):
        """Update metrics for a specific queue"""
        with self._lock:
            if queue_name not in self._queue_metrics:
                self._queue_metrics[queue_name] = QueueMetrics(queue_name=queue_name)
            
            metrics = self._queue_metrics[queue_name]
            
            # Update metrics
            for key, value in kwargs.items():
                if hasattr(metrics, key):
                    setattr(metrics, key, value)
            
            metrics.last_updated = datetime.now()
            
            # Record performance data
            self._record_performance(queue_name, metrics)
    
    def _record_performance(self, queue_name: str, metrics: QueueMetrics):
        """Record performance data point"""
        performance_data = {
            'timestamp': datetime.now(),
            'queue': queue_name,
            'throughput': metrics.throughput,
            'error_rate': metrics.error_rate,
            'avg_processing_time': metrics.average_processing_time,
            'pending_tasks': metrics.pending_tasks
        }
        
        self._performance_window.append(performance_data)
    
    def record_task_event(self, queue_name: str, event_type: str, duration_ms: Optional[float] = None):
        """Record task-related events"""
        labels = {"queue": queue_name, "event": event_type}
        
        if event_type == "completed":
            self.metrics_collector.record_counter("tasks_completed", 1.0, labels)
            if duration_ms is not None:
                self.metrics_collector.record_timer("task_duration", duration_ms, labels)
        elif event_type == "failed":
            self.metrics_collector.record_counter("tasks_failed", 1.0, labels)
        elif event_type == "started":
            self.metrics_collector.record_counter("tasks_started", 1.0, labels)
        elif event_type == "queued":
            self.metrics_collector.record_counter("tasks_queued", 1.0, labels)
    
    def get_queue_metrics(self, queue_name: str) -> Optional[QueueMetrics]:
        """Get metrics for a specific queue"""
        with self._lock:
            return self._queue_metrics.get(queue_name)
    
    def get_all_queue_metrics(self) -> Dict[str, QueueMetrics]:
        """Get metrics for all queues"""
        with self._lock:
            return self._queue_metrics.copy()
    
    def get_performance_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Get performance summary for the last N hours"""
        cutoff_time = datetime.now() - timedelta(hours=hours)
        
        # Filter performance data
        recent_data = [
            data for data in self._performance_window
            if data['timestamp'] >= cutoff_time
        ]
        
        if not recent_data:
            return {}
        
        # Calculate summary statistics
        throughputs = [data['throughput'] for data in recent_data]
        error_rates = [data['error_rate'] for data in recent_data]
        processing_times = [data['avg_processing_time'] for data in recent_data]
        
        return {
            'period_hours': hours,
            'total_data_points': len(recent_data),
            'throughput': {
                'avg': statistics.mean(throughputs),
                'min': min(throughputs),
                'max': max(throughputs),
                'median': statistics.median(throughputs)
            },
            'error_rate': {
                'avg': statistics.mean(error_rates),
                'min': min(error_rates),
                'max': max(error_rates),
                'median': statistics.median(error_rates)
            },
            'processing_time': {
                'avg': statistics.mean(processing_times),
                'min': min(processing_times),
                'max': max(processing_times),
                'median': statistics.median(processing_times)
            }
        }
    
    def get_queue_analytics(self, queue_name: str, hours: int = 24) -> Dict[str, Any]:
        """Get detailed analytics for a specific queue"""
        metrics = self.get_queue_metrics(queue_name)
        if not metrics:
            return {}
        
        # Get task duration percentiles
        duration_percentiles = self.metrics_collector.calculate_percentiles(
            "task_duration", labels={"queue": queue_name}
        )
        
        # Get task counts by event type
        task_events = {}
        for event_type in ["completed", "failed", "started", "queued"]:
            history = self.metrics_collector.get_metric_history(
                "tasks_completed" if event_type == "completed" else f"tasks_{event_type}",
                labels={"queue": queue_name}
            )
            task_events[event_type] = len(history)
        
        return {
            'queue_name': queue_name,
            'current_metrics': {
                'pending_tasks': metrics.pending_tasks,
                'running_tasks': metrics.running_tasks,
                'completed_tasks': metrics.completed_tasks,
                'failed_tasks': metrics.failed_tasks,
                'error_rate': metrics.error_rate,
                'throughput': metrics.throughput,
                'avg_wait_time': metrics.average_wait_time,
                'avg_processing_time': metrics.average_processing_time
            },
            'task_events': task_events,
            'duration_percentiles': duration_percentiles,
            'last_updated': metrics.last_updated.isoformat()
        }
    
    def export_metrics(self, format: str = "json") -> str:
        """Export metrics in specified format"""
        all_metrics = self.metrics_collector.get_current_metrics()
        
        if format.lower() == "json":
            return json.dumps(all_metrics, indent=2, default=str)
        elif format.lower() == "prometheus":
            return self._export_prometheus_format(all_metrics)
        else:
            raise ValueError(f"Unsupported export format: {format}")
    
    def _export_prometheus_format(self, metrics: Dict[str, Any]) -> str:
        """Export metrics in Prometheus format"""
        lines = []
        
        for metric_name, metric_data in metrics.items():
            metric_type = metric_data["type"]
            value = metric_data["value"]
            
            # Add metric type
            if metric_type == "counter":
                lines.append(f"# TYPE {metric_name} counter")
            elif metric_type == "gauge":
                lines.append(f"# TYPE {metric_name} gauge")
            
            # Add metric value
            lines.append(f"{metric_name} {value}")
        
        return "\n".join(lines)
    
    def reset_metrics(self):
        """Reset all metrics"""
        with self._lock:
            self.metrics_collector = MetricsCollector(
                max_history=self.config.get('max_history', 10000)
            )
            self._queue_metrics.clear()
            self._performance_window.clear()
        
        logger.info("All metrics reset")

# Alert handlers
def console_alert_handler(alert: Alert):
    """Simple console alert handler"""
    print(f"[{alert.level.value.upper()}] {alert.name}: {alert.message}")

def log_alert_handler(alert: Alert):
    """Logging alert handler"""
    if alert.level == AlertLevel.INFO:
        logger.info(f"Alert: {alert.name} - {alert.message}")
    elif alert.level == AlertLevel.WARNING:
        logger.warning(f"Alert: {alert.name} - {alert.message}")
    elif alert.level == AlertLevel.ERROR:
        logger.error(f"Alert: {alert.name} - {alert.message}")
    elif alert.level == AlertLevel.CRITICAL:
        logger.critical(f"Alert: {alert.name} - {alert.message}")

class QueueDashboard:
    """Simple dashboard data provider for queue monitoring"""
    
    def __init__(self, monitor: QueueMonitor):
        self.monitor = monitor
    
    def get_dashboard_data(self) -> Dict[str, Any]:
        """Get data for dashboard display"""
        queue_metrics = self.monitor.get_all_queue_metrics()
        performance_summary = self.monitor.get_performance_summary()
        
        return {
            'timestamp': datetime.now().isoformat(),
            'queues': {
                name: {
                    'pending_tasks': metrics.pending_tasks,
                    'running_tasks': metrics.running_tasks,
                    'completed_tasks': metrics.completed_tasks,
                    'failed_tasks': metrics.failed_tasks,
                    'error_rate': metrics.error_rate,
                    'throughput': metrics.throughput,
                    'avg_processing_time': metrics.average_processing_time
                }
                for name, metrics in queue_metrics.items()
            },
            'performance_summary': performance_summary,
            'total_queues': len(queue_metrics),
            'monitoring_active': self.monitor._monitoring_active
        }
