import json
from datetime import datetime, date
from typing import Any, Dict, Optional, List

from db_config import get_connection


def _ensure_uuid_func(cur):
    cur.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'uuid-ossp') THEN
                CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
            END IF;
        END$$;
        """
    )


def log_prediction_history(payload: Dict[str, Any], duration: float, status: str, request_meta: Optional[Dict[str, Any]] = None):
    conn = get_connection()
    if not conn:
        return
    try:
        with conn:
            with conn.cursor() as cur:
                _ensure_uuid_func(cur)
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS prediction_history (
                        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                        request_id TEXT,
                        user_id TEXT,
                        image_filename TEXT,
                        label TEXT,
                        confidence DOUBLE PRECISION,
                        all_predictions JSONB,
                        processing_time DOUBLE PRECISION,
                        model_version TEXT,
                        success BOOLEAN NOT NULL DEFAULT TRUE,
                        error_message TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    """
                )
                label = payload.get("label")
                confidence = payload.get("confidence")
                all_preds = payload.get("all_predictions") or payload.get("predictions")
                model_version = payload.get("model_version") or payload.get("model") or None
                image_filename = payload.get("filename") or payload.get("image") or None
                request_id = (request_meta or {}).get("request_id")
                user_id = (request_meta or {}).get("user_id")
                error_message = (request_meta or {}).get("error_message")

                cur.execute(
                    """
                    INSERT INTO prediction_history
                    (request_id, user_id, image_filename, label, confidence, all_predictions, processing_time, model_version, success, error_message)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                    """,
                    (
                        request_id,
                        user_id,
                        image_filename,
                        label,
                        confidence,
                        json.dumps(all_preds) if all_preds is not None else json.dumps([]),
                        duration,
                        model_version,
                        status == "success",
                        error_message,
                    ),
                )

                # Upsert model performance daily aggregates
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS model_performance_metrics (
                        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                        model_version TEXT NOT NULL,
                        metric_date DATE NOT NULL,
                        total_predictions INTEGER NOT NULL DEFAULT 0,
                        avg_confidence DOUBLE PRECISION,
                        avg_processing_time DOUBLE PRECISION,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        UNIQUE (model_version, metric_date)
                    );
                    """
                )
                metric_date = date.today()
                cur.execute(
                    """
                    INSERT INTO model_performance_metrics (model_version, metric_date, total_predictions, avg_confidence, avg_processing_time)
                    VALUES (%s, %s, 1, %s, %s)
                    ON CONFLICT (model_version, metric_date)
                    DO UPDATE SET
                        total_predictions = model_performance_metrics.total_predictions + 1,
                        avg_confidence = CASE
                            WHEN EXCLUDED.avg_confidence IS NULL THEN model_performance_metrics.avg_confidence
                            WHEN model_performance_metrics.avg_confidence IS NULL THEN EXCLUDED.avg_confidence
                            ELSE (model_performance_metrics.avg_confidence * model_performance_metrics.total_predictions + EXCLUDED.avg_confidence) / (model_performance_metrics.total_predictions + 1)
                        END,
                        avg_processing_time = CASE
                            WHEN EXCLUDED.avg_processing_time IS NULL THEN model_performance_metrics.avg_processing_time
                            WHEN model_performance_metrics.avg_processing_time IS NULL THEN EXCLUDED.avg_processing_time
                            ELSE (model_performance_metrics.avg_processing_time * model_performance_metrics.total_predictions + EXCLUDED.avg_processing_time) / (model_performance_metrics.total_predictions + 1)
                        END;
                    """,
                    (
                        model_version or "unknown",
                        metric_date,
                        confidence if isinstance(confidence, (int, float)) else None,
                        duration if isinstance(duration, (int, float)) else None,
                    ),
                )
    finally:
        try:
            conn.close()
        except Exception:
            pass


def purge_old_history(days: int) -> int:
    conn = get_connection()
    if not conn:
        return 0
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM prediction_history
                    WHERE created_at < NOW() - INTERVAL '%s days'
                    """,
                    (days,),
                )
                deleted = cur.rowcount
                return deleted or 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


# GDPR Compliance Functions

def create_user_consent_table():
    """Create table for storing user consent records"""
    conn = get_connection()
    if not conn:
        return False
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_consent (
                        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                        user_id TEXT NOT NULL,
                        consent_type TEXT NOT NULL,
                        granted BOOLEAN NOT NULL,
                        timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        ip_address TEXT,
                        user_agent TEXT,
                        UNIQUE (user_id, consent_type)
                    );
                    """
                )
                return True
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return False


def store_user_consent(user_id: str, consent_type: str, granted: bool, ip_address: str = None, user_agent: str = None) -> bool:
    """Store or update user consent"""
    conn = get_connection()
    if not conn:
        return False
    try:
        with conn:
            with conn.cursor() as cur:
                create_user_consent_table()
                cur.execute(
                    """
                    INSERT INTO user_consent (user_id, consent_type, granted, ip_address, user_agent)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, consent_type)
                    DO UPDATE SET
                        granted = EXCLUDED.granted,
                        timestamp = NOW(),
                        ip_address = EXCLUDED.ip_address,
                        user_agent = EXCLUDED.user_agent;
                    """,
                    (user_id, consent_type, granted, ip_address, user_agent)
                )
                return True
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return False


def get_user_consent(user_id: str) -> List[Dict[str, Any]]:
    """Get all consent records for a user"""
    conn = get_connection()
    if not conn:
        return []
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT consent_type, granted, timestamp, ip_address, user_agent
                    FROM user_consent
                    WHERE user_id = %s
                    ORDER BY timestamp DESC;
                    """,
                    (user_id,)
                )
                results = cur.fetchall()
                return [
                    {
                        "consent_type": row[0],
                        "granted": row[1],
                        "timestamp": row[2].isoformat() if row[2] else None,
                        "ip_address": row[3],
                        "user_agent": row[4]
                    }
                    for row in results
                ]
    finally:
        try:
            conn.close()
        except Exception:
            pass


def export_user_data(user_id: str) -> Dict[str, Any]:
    """Export all user data for GDPR compliance"""
    conn = get_connection()
    if not conn:
        return {}
    
    try:
        with conn:
            with conn.cursor() as cur:
                # Export prediction history
                cur.execute(
                    """
                    SELECT request_id, image_filename, label, confidence, all_predictions,
                           processing_time, model_version, success, error_message, created_at
                    FROM prediction_history
                    WHERE user_id = %s
                    ORDER BY created_at DESC;
                    """,
                    (user_id,)
                )
                predictions = cur.fetchall()
                
                # Export consent records
                cur.execute(
                    """
                    SELECT consent_type, granted, timestamp, ip_address, user_agent
                    FROM user_consent
                    WHERE user_id = %s
                    ORDER BY timestamp DESC;
                    """,
                    (user_id,)
                )
                consents = cur.fetchall()
                
                return {
                    "user_id": user_id,
                    "export_timestamp": datetime.utcnow().isoformat(),
                    "prediction_history": [
                        {
                            "request_id": row[0],
                            "image_filename": row[1],
                            "label": row[2],
                            "confidence": row[3],
                            "all_predictions": row[4],
                            "processing_time": row[5],
                            "model_version": row[6],
                            "success": row[7],
                            "error_message": row[8],
                            "created_at": row[9].isoformat() if row[9] else None
                        }
                        for row in predictions
                    ],
                    "consent_records": [
                        {
                            "consent_type": row[0],
                            "granted": row[1],
                            "timestamp": row[2].isoformat() if row[2] else None,
                            "ip_address": row[3],
                            "user_agent": row[4]
                        }
                        for row in consents
                    ]
                }
    finally:
        try:
            conn.close()
        except Exception:
            pass


def delete_user_data(user_id: str) -> Dict[str, int]:
    """Delete all user data for GDPR compliance"""
    conn = get_connection()
    if not conn:
        return {"prediction_history": 0, "user_consent": 0}
    
    deletion_counts = {}
    
    try:
        with conn:
            with conn.cursor() as cur:
                # Delete from prediction_history
                cur.execute(
                    "DELETE FROM prediction_history WHERE user_id = %s",
                    (user_id,)
                )
                deletion_counts["prediction_history"] = cur.rowcount
                
                # Delete from user_consent
                cur.execute(
                    "DELETE FROM user_consent WHERE user_id = %s",
                    (user_id,)
                )
                deletion_counts["user_consent"] = cur.rowcount
                
                return deletion_counts
    finally:
        try:
            conn.close()
        except Exception:
            pass
