



@app.route('/predict', methods=['POST'])
@limiter.limit("10 per minute")
@track_inference
def predict():
    start_time = time.time()
    

    if 'image' not in request.files:
        return jsonify({'error': 'No image provided'}), 400
    
    file = request.files['image']
    
    try:


# Register all management endpoints
register_all_endpoints(app, model_registry, ab_test_manager, deployment_manager, model_validator)

# Register batch processing endpoints
register_batch_endpoints(app, batch_processor)

if __name__ == '__main__':

