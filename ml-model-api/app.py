import torch
import torch.nn as nn
from torchvision import models, transforms
from PIL import Image
import io
import os
import time

from flask import request, jsonify

# =========================
# MODEL CONFIGURATION
# =========================

MODEL_PATH = os.getenv("MODEL_PATH", "ml-model-api/model.pth")
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# ⚠️ Ensure this matches your trained model classes
CLASS_NAMES = [
    "Amala", "Eba", "Egusi Soup", "Jollof Rice",
    "Moi Moi", "Pounded Yam", "Suya"
]

def load_model():
    try:
        model = models.resnet18(pretrained=False)
        num_ftrs = model.fc.in_features
        model.fc = nn.Linear(num_ftrs, len(CLASS_NAMES))

        model.load_state_dict(torch.load(MODEL_PATH, map_location=DEVICE))
        model.to(DEVICE)
        model.eval()

        print("✅ Model loaded successfully")
        return model

    except Exception as e:
        print(f"❌ Model loading failed: {str(e)}")
        return None

# Load model once at startup
model = load_model()

# =========================
# IMAGE PREPROCESSING
# =========================

transform = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize(
        mean=[0.485, 0.456, 0.406],
        std=[0.229, 0.224, 0.225]
    )
])

# =========================
# PREDICTION ENDPOINT
# =========================

@app.route('/predict', methods=['POST'])
@limiter.limit("10 per minute")
@track_inference
def predict():
    start_time = time.time()

    if model is None:
        return jsonify({'error': 'Model not loaded'}), 500

    if 'image' not in request.files:
        return jsonify({'error': 'No image provided'}), 400
    
    file = request.files['image']
    
    try:
        # Read image
        image_bytes = file.read()
        image = Image.open(io.BytesIO(image_bytes)).convert('RGB')

        # Preprocess image
        input_tensor = transform(image).unsqueeze(0).to(DEVICE)

        # Run inference
        with torch.no_grad():
            outputs = model(input_tensor)
            probabilities = torch.nn.functional.softmax(outputs[0], dim=0)

        # Get prediction
        confidence, predicted_class = torch.max(probabilities, 0)

        predicted_label = CLASS_NAMES[predicted_class.item()]
        confidence_score = float(confidence.item())

        inference_time = time.time() - start_time

        return jsonify({
            'prediction': predicted_label,
            'confidence': round(confidence_score, 4),
            'inference_time': round(inference_time, 3)
        })

    except Exception as e:
        return jsonify({
            'error': 'Invalid image or processing failed',
            'details': str(e)
        }), 400


# =========================
# MANAGEMENT ENDPOINTS
# =========================

# Register all management endpoints
register_all_endpoints(app, model_registry, ab_test_manager, deployment_manager, model_validator)


# =========================
# ANALYTICS ENDPOINTS
# =========================

@app.route('/analytics/usage', methods=['GET'])
def get_usage_stats():
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    data = analytics.get_usage_stats(start_date, end_date)
    return jsonify(data)

@app.route('/analytics/performance', methods=['GET'])
def get_model_performance():
    data = analytics.get_model_performance()
    return jsonify(data)

@app.route('/analytics/engagement', methods=['GET'])
def get_user_engagement():
    data = analytics.get_user_engagement()
    return jsonify(data)

@app.route('/analytics/activity', methods=['GET'])
def get_real_time_activity():
    data = analytics.get_real_time_activity()
    return jsonify(data)

@app.route('/analytics/stats', methods=['GET'])
def get_stats_cards():
    data = analytics.get_stats_cards()
    return jsonify(data)

@app.route('/analytics/export', methods=['GET'])
def export_analytics():
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    data = analytics.export_data(start_date, end_date)
    return jsonify(data)

@app.route('/analytics', methods=['GET'])
def get_all_analytics():
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    data = {
        'usageStats': analytics.get_usage_stats(start_date, end_date),
        'modelPerformance': analytics.get_model_performance(),
        'userEngagement': analytics.get_user_engagement(),
        'statsCards': analytics.get_stats_cards(),
        'realTimeActivity': analytics.get_real_time_activity()
    }
    return jsonify(data)


# =========================
# HEALTH CHECK
# =========================

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'healthy',
        'model_loaded': model is not None,
        'version': '1.0.0',
        'analytics_enabled': True
    })


# =========================
# ENTRY POINT
# =========================

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
    