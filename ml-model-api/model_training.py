#!/usr/bin/env python3
"""
Automated Model Training System for FlavorSnap
Handles automated training, validation, and model registration
"""

import os
import time
import json
import logging
import threading
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, Dataset
import torchvision.transforms as transforms
from torchvision import models
from PIL import Image
import yaml
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/model_training.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class TrainingStatus(Enum):
    """Training status states"""
    QUEUED = "queued"
    PREPARING = "preparing"
    TRAINING = "training"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class ModelType(Enum):
    """Model types"""
    RESNET18 = "resnet18"
    RESNET50 = "resnet50"
    EFFICIENTNET = "efficientnet"
    CUSTOM = "custom"

@dataclass
class TrainingConfig:
    """Configuration for model training"""
    # Model settings
    model_type: ModelType = ModelType.RESNET18
    num_classes: int = 101  # Food classes
    pretrained: bool = True
    
    # Training hyperparameters
    epochs: int = 50
    batch_size: int = 32
    learning_rate: float = 0.001
    weight_decay: float = 1e-4
    momentum: float = 0.9
    
    # Data settings
    data_dir: str = "dataset"
    validation_split: float = 0.2
    test_split: float = 0.1
    image_size: Tuple[int, int] = (224, 224)
    
    # Augmentation settings
    enable_augmentation: bool = True
    rotation_range: float = 30.0
    horizontal_flip: bool = True
    vertical_flip: bool = False
    brightness_range: Tuple[float, float] = (0.8, 1.2)
    contrast_range: Tuple[float, float] = (0.8, 1.2)
    
    # Early stopping
    early_stopping: bool = True
    patience: int = 10
    min_delta: float = 0.001
    
    # Checkpointing
    save_checkpoints: bool = True
    checkpoint_interval: int = 5
    
    # Hardware settings
    device: str = "auto"  # auto, cpu, cuda
    num_workers: int = 4
    
    # Validation settings
    validation_metrics: List[str] = None
    
    def __post_init__(self):
        if self.validation_metrics is None:
            self.validation_metrics = ["accuracy", "precision", "recall", "f1"]

@dataclass
class TrainingJob:
    """Training job information"""
    job_id: str
    config: TrainingConfig
    status: TrainingStatus = TrainingStatus.QUEUED
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    current_epoch: int = 0
    total_epochs: int = 0
    best_accuracy: float = 0.0
    best_loss: float = float('inf')
    model_version: Optional[str] = None
    metrics_history: Dict[str, List[float]] = None
    error_message: Optional[str] = None
    
    def __post_init__(self):
        if self.metrics_history is None:
            self.metrics_history = {
                "train_loss": [],
                "train_accuracy": [],
                "val_loss": [],
                "val_accuracy": []
            }

class FoodDataset(Dataset):
    """Custom dataset for food images"""
    
    def __init__(self, data_dir: str, transform=None, split: str = "train"):
        self.data_dir = Path(data_dir)
        self.transform = transform
        self.split = split
        self.images = []
        self.labels = []
        self.class_to_idx = {}
        
        self._load_data()
    
    def _load_data(self):
        """Load image paths and labels"""
        classes = sorted([d.name for d in self.data_dir.iterdir() if d.is_dir()])
        self.class_to_idx = {cls: idx for idx, cls in enumerate(classes)}
        
        for class_name in classes:
            class_dir = self.data_dir / class_name
            class_idx = self.class_to_idx[class_name]
            
            for img_path in class_dir.glob("*.jpg"):
                self.images.append(str(img_path))
                self.labels.append(class_idx)
    
    def __len__(self):
        return len(self.images)
    
    def __getitem__(self, idx):
        img_path = self.images[idx]
        label = self.labels[idx]
        
        # Load image
        image = Image.open(img_path).convert('RGB')
        
        # Apply transforms
        if self.transform:
            image = self.transform(image)
        
        return image, label

class ModelTrainer:
    """Automated model training system"""
    
    def __init__(self, registry_path: str = "model_registry.db"):
        self.registry_path = registry_path
        self.training_jobs = {}
        self.active_training = None
        self.training_lock = threading.Lock()
        
        # Device setup
        self.device = self._setup_device()
        
        # Initialize database
        self._init_database()
        
        # Load food classes
        self._load_food_classes()
        
        logger.info("ModelTrainer initialized")
    
    def _setup_device(self) -> torch.device:
        """Setup training device"""
        if torch.cuda.is_available():
            device = torch.device("cuda")
            logger.info(f"Using CUDA device: {torch.cuda.get_device_name()}")
        else:
            device = torch.device("cpu")
            logger.info("Using CPU device")
        return device
    
    def _init_database(self):
        """Initialize training database"""
        os.makedirs("logs", exist_ok=True)
        
        with sqlite3.connect(self.registry_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS training_jobs (
                    job_id TEXT PRIMARY KEY,
                    config TEXT NOT NULL,
                    status TEXT NOT NULL,
                    start_time TEXT,
                    end_time TEXT,
                    current_epoch INTEGER DEFAULT 0,
                    total_epochs INTEGER DEFAULT 0,
                    best_accuracy REAL DEFAULT 0.0,
                    best_loss REAL DEFAULT 1.0,
                    model_version TEXT,
                    metrics_history TEXT,
                    error_message TEXT
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS training_checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    model_path TEXT NOT NULL,
                    accuracy REAL,
                    loss REAL,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (job_id) REFERENCES training_jobs (job_id)
                )
            """)
    
    def _load_food_classes(self):
        """Load food classes from file"""
        try:
            with open("food_classes.txt", "r") as f:
                self.food_classes = [line.strip() for line in f.readlines() if line.strip()]
            logger.info(f"Loaded {len(self.food_classes)} food classes")
        except FileNotFoundError:
            # Default food classes
            self.food_classes = [
                "Akara", "Bread", "Egusi", "Moi Moi", "Rice and Stew", "Yam"
            ] + [f"Food_{i}" for i in range(7, 101)]
            logger.warning("Using default food classes")
    
    def start_training(self, config: TrainingConfig) -> str:
        """Start a new training job"""
        with self.training_lock:
            if self.active_training:
                logger.warning("Training already in progress")
                return self.active_training.job_id
            
            # Create training job
            job_id = f"training_{int(time.time())}"
            job = TrainingJob(
                job_id=job_id,
                config=config,
                total_epochs=config.epochs
            )
            
            self.training_jobs[job_id] = job
            self.active_training = job
            
            # Save job to database
            self._save_job(job)
            
            # Start training in background thread
            training_thread = threading.Thread(
                target=self._run_training,
                args=(job_id,),
                daemon=True
            )
            training_thread.start()
            
            logger.info(f"Training started: {job_id}")
            return job_id
    
    def _run_training(self, job_id: str):
        """Run the training process"""
        job = self.training_jobs[job_id]
        
        try:
            # Prepare data
            job.status = TrainingStatus.PREPARING
            job.start_time = datetime.now()
            self._save_job(job)
            
            train_loader, val_loader, test_loader = self._prepare_data(job.config)
            
            # Create model
            model = self._create_model(job.config)
            model.to(self.device)
            
            # Setup training components
            criterion = nn.CrossEntropyLoss()
            optimizer = optim.Adam(
                model.parameters(),
                lr=job.config.learning_rate,
                weight_decay=job.config.weight_decay
            )
            scheduler = optim.lr_scheduler.ReduceLROnPlateau(
                optimizer, mode='min', factor=0.5, patience=5
            )
            
            # Training loop
            job.status = TrainingStatus.TRAINING
            best_val_loss = float('inf')
            patience_counter = 0
            
            for epoch in range(job.config.epochs):
                job.current_epoch = epoch + 1
                
                # Training phase
                train_loss, train_accuracy = self._train_epoch(
                    model, train_loader, criterion, optimizer
                )
                
                # Validation phase
                val_loss, val_accuracy = self._validate_epoch(
                    model, val_loader, criterion
                )
                
                # Update metrics
                job.metrics_history["train_loss"].append(train_loss)
                job.metrics_history["train_accuracy"].append(train_accuracy)
                job.metrics_history["val_loss"].append(val_loss)
                job.metrics_history["val_accuracy"].append(val_accuracy)
                
                # Update best metrics
                if val_accuracy > job.best_accuracy:
                    job.best_accuracy = val_accuracy
                    job.best_loss = val_loss
                    
                    # Save best model
                    self._save_checkpoint(model, job, epoch, is_best=True)
                
                # Learning rate scheduling
                scheduler.step(val_loss)
                
                # Save checkpoint
                if job.config.save_checkpoints and (epoch + 1) % job.config.checkpoint_interval == 0:
                    self._save_checkpoint(model, job, epoch)
                
                # Early stopping
                if job.config.early_stopping:
                    if val_loss < best_val_loss - job.config.min_delta:
                        best_val_loss = val_loss
                        patience_counter = 0
                    else:
                        patience_counter += 1
                        if patience_counter >= job.config.patience:
                            logger.info(f"Early stopping at epoch {epoch + 1}")
                            break
                
                # Log progress
                logger.info(
                    f"Epoch {epoch + 1}/{job.config.epochs} - "
                    f"Train Loss: {train_loss:.4f}, Train Acc: {train_accuracy:.4f}, "
                    f"Val Loss: {val_loss:.4f}, Val Acc: {val_accuracy:.4f}"
                )
                
                # Save progress
                self._save_job(job)
            
            # Final evaluation
            job.status = TrainingStatus.VALIDATING
            test_loss, test_accuracy = self._evaluate_model(model, test_loader, criterion)
            
            # Register model
            model_version = self._register_model(model, job, test_accuracy, test_loss)
            job.model_version = model_version
            
            # Complete training
            job.status = TrainingStatus.COMPLETED
            job.end_time = datetime.now()
            
            logger.info(f"Training completed: {job_id}, Model: {model_version}")
            
        except Exception as e:
            job.status = TrainingStatus.FAILED
            job.error_message = str(e)
            job.end_time = datetime.now()
            logger.error(f"Training failed: {job_id} - {e}")
        
        finally:
            self._save_job(job)
            self.active_training = None
    
    def _prepare_data(self, config: TrainingConfig) -> Tuple[DataLoader, DataLoader, DataLoader]:
        """Prepare data loaders"""
        # Data transforms
        if config.enable_augmentation:
            train_transform = transforms.Compose([
                transforms.Resize(config.image_size),
                transforms.RandomRotation(config.rotation_range),
                transforms.RandomHorizontalFlip() if config.horizontal_flip else transforms.Lambda(lambda x: x),
                transforms.RandomVerticalFlip() if config.vertical_flip else transforms.Lambda(lambda x: x),
                transforms.ColorJitter(
                    brightness=config.brightness_range,
                    contrast=config.contrast_range
                ),
                transforms.ToTensor(),
                transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
            ])
        else:
            train_transform = transforms.Compose([
                transforms.Resize(config.image_size),
                transforms.ToTensor(),
                transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
            ])
        
        val_test_transform = transforms.Compose([
            transforms.Resize(config.image_size),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
        ])
        
        # Create datasets
        full_dataset = FoodDataset(config.data_dir, transform=None)
        
        # Split dataset
        total_size = len(full_dataset)
        test_size = int(total_size * config.test_split)
        val_size = int(total_size * config.validation_split)
        train_size = total_size - val_size - test_size
        
        train_dataset, val_dataset, test_dataset = torch.utils.data.random_split(
            full_dataset, [train_size, val_size, test_size]
        )
        
        # Apply transforms
        train_dataset.dataset.transform = train_transform
        val_dataset.dataset.transform = val_test_transform
        test_dataset.dataset.transform = val_test_transform
        
        # Create data loaders
        train_loader = DataLoader(
            train_dataset, batch_size=config.batch_size, shuffle=True, num_workers=config.num_workers
        )
        val_loader = DataLoader(
            val_dataset, batch_size=config.batch_size, shuffle=False, num_workers=config.num_workers
        )
        test_loader = DataLoader(
            test_dataset, batch_size=config.batch_size, shuffle=False, num_workers=config.num_workers
        )
        
        return train_loader, val_loader, test_loader
    
    def _create_model(self, config: TrainingConfig) -> nn.Module:
        """Create model based on configuration"""
        if config.model_type == ModelType.RESNET18:
            model = models.resnet18(pretrained=config.pretrained)
            model.fc = nn.Linear(model.fc.in_features, config.num_classes)
        elif config.model_type == ModelType.RESNET50:
            model = models.resnet50(pretrained=config.pretrained)
            model.fc = nn.Linear(model.fc.in_features, config.num_classes)
        elif config.model_type == ModelType.EFFICIENTNET:
            model = models.efficientnet_b0(pretrained=config.pretrained)
            model.classifier[1] = nn.Linear(model.classifier[1].in_features, config.num_classes)
        else:
            # Custom model
            model = self._create_custom_model(config)
        
        return model
    
    def _create_custom_model(self, config: TrainingConfig) -> nn.Module:
        """Create custom CNN model"""
        class CustomCNN(nn.Module):
            def __init__(self, num_classes):
                super().__init__()
                self.features = nn.Sequential(
                    nn.Conv2d(3, 64, kernel_size=3, padding=1),
                    nn.ReLU(inplace=True),
                    nn.MaxPool2d(kernel_size=2),
                    nn.Conv2d(64, 128, kernel_size=3, padding=1),
                    nn.ReLU(inplace=True),
                    nn.MaxPool2d(kernel_size=2),
                    nn.Conv2d(128, 256, kernel_size=3, padding=1),
                    nn.ReLU(inplace=True),
                    nn.MaxPool2d(kernel_size=2),
                    nn.Conv2d(256, 512, kernel_size=3, padding=1),
                    nn.ReLU(inplace=True),
                    nn.AdaptiveAvgPool2d((1, 1))
                )
                self.classifier = nn.Sequential(
                    nn.Dropout(0.5),
                    nn.Linear(512, 256),
                    nn.ReLU(inplace=True),
                    nn.Dropout(0.5),
                    nn.Linear(256, num_classes)
                )
            
            def forward(self, x):
                x = self.features(x)
                x = torch.flatten(x, 1)
                x = self.classifier(x)
                return x
        
        return CustomCNN(config.num_classes)
    
    def _train_epoch(self, model: nn.Module, train_loader: DataLoader, 
                     criterion: nn.Module, optimizer: optim.Optimizer) -> Tuple[float, float]:
        """Train for one epoch"""
        model.train()
        running_loss = 0.0
        correct = 0
        total = 0
        
        for batch_idx, (data, targets) in enumerate(train_loader):
            data, targets = data.to(self.device), targets.to(self.device)
            
            optimizer.zero_grad()
            outputs = model(data)
            loss = criterion(outputs, targets)
            loss.backward()
            optimizer.step()
            
            running_loss += loss.item()
            _, predicted = outputs.max(1)
            total += targets.size(0)
            correct += predicted.eq(targets).sum().item()
        
        epoch_loss = running_loss / len(train_loader)
        epoch_accuracy = correct / total
        
        return epoch_loss, epoch_accuracy
    
    def _validate_epoch(self, model: nn.Module, val_loader: DataLoader, 
                        criterion: nn.Module) -> Tuple[float, float]:
        """Validate for one epoch"""
        model.eval()
        running_loss = 0.0
        correct = 0
        total = 0
        
        with torch.no_grad():
            for data, targets in val_loader:
                data, targets = data.to(self.device), targets.to(self.device)
                outputs = model(data)
                loss = criterion(outputs, targets)
                
                running_loss += loss.item()
                _, predicted = outputs.max(1)
                total += targets.size(0)
                correct += predicted.eq(targets).sum().item()
        
        epoch_loss = running_loss / len(val_loader)
        epoch_accuracy = correct / total
        
        return epoch_loss, epoch_accuracy
    
    def _evaluate_model(self, model: nn.Module, test_loader: DataLoader, 
                        criterion: nn.Module) -> Tuple[float, float]:
        """Evaluate model on test set"""
        model.eval()
        running_loss = 0.0
        correct = 0
        total = 0
        
        with torch.no_grad():
            for data, targets in test_loader:
                data, targets = data.to(self.device), targets.to(self.device)
                outputs = model(data)
                loss = criterion(outputs, targets)
                
                running_loss += loss.item()
                _, predicted = outputs.max(1)
                total += targets.size(0)
                correct += predicted.eq(targets).sum().item()
        
        test_loss = running_loss / len(test_loader)
        test_accuracy = correct / total
        
        return test_loss, test_accuracy
    
    def _save_checkpoint(self, model: nn.Module, job: TrainingJob, epoch: int, is_best: bool = False):
        """Save model checkpoint"""
        try:
            # Create checkpoint directory
            checkpoint_dir = Path("checkpoints") / job.job_id
            checkpoint_dir.mkdir(parents=True, exist_ok=True)
            
            # Save checkpoint
            checkpoint_path = checkpoint_dir / f"epoch_{epoch + 1}.pth"
            torch.save({
                'epoch': epoch + 1,
                'model_state_dict': model.state_dict(),
                'optimizer_state_dict': None,  # Could save optimizer state if needed
                'loss': job.best_loss,
                'accuracy': job.best_accuracy,
                'config': asdict(job.config)
            }, checkpoint_path)
            
            # Save as best model
            if is_best:
                best_path = checkpoint_dir / "best_model.pth"
                torch.save({
                    'epoch': epoch + 1,
                    'model_state_dict': model.state_dict(),
                    'loss': job.best_loss,
                    'accuracy': job.best_accuracy,
                    'config': asdict(job.config)
                }, best_path)
            
            # Save to database
            with sqlite3.connect(self.registry_path) as conn:
                conn.execute("""
                    INSERT INTO training_checkpoints 
                    (job_id, epoch, model_path, accuracy, loss, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    job.job_id,
                    epoch + 1,
                    str(checkpoint_path),
                    job.best_accuracy,
                    job.best_loss,
                    datetime.now().isoformat()
                ))
            
            logger.info(f"Checkpoint saved: {checkpoint_path}")
            
        except Exception as e:
            logger.error(f"Failed to save checkpoint: {e}")
    
    def _register_model(self, model: nn.Module, job: TrainingJob, 
                       test_accuracy: float, test_loss: float) -> str:
        """Register trained model in registry"""
        try:
            # Generate model version
            model_version = f"v{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
            # Save model
            models_dir = Path("models")
            models_dir.mkdir(exist_ok=True)
            model_path = models_dir / f"{model_version}.pth"
            
            torch.save({
                'model_state_dict': model.state_dict(),
                'config': asdict(job.config),
                'accuracy': test_accuracy,
                'loss': test_loss,
                'classes': self.food_classes,
                'training_job_id': job.job_id
            }, model_path)
            
            # Register in model registry
            from model_registry import ModelRegistry
            registry = ModelRegistry()
            
            registry.register_model(
                version=model_version,
                model_path=str(model_path),
                accuracy=test_accuracy,
                loss=test_loss,
                description=f"Trained model - {job.config.model_type.value}",
                created_by="automated_training"
            )
            
            logger.info(f"Model registered: {model_version}")
            return model_version
            
        except Exception as e:
            logger.error(f"Failed to register model: {e}")
            return None
    
    def _save_job(self, job: TrainingJob):
        """Save training job to database"""
        with sqlite3.connect(self.registry_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO training_jobs 
                (job_id, config, status, start_time, end_time, current_epoch,
                 total_epochs, best_accuracy, best_loss, model_version,
                 metrics_history, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job.job_id,
                json.dumps(asdict(job.config)),
                job.status.value,
                job.start_time.isoformat() if job.start_time else None,
                job.end_time.isoformat() if job.end_time else None,
                job.current_epoch,
                job.total_epochs,
                job.best_accuracy,
                job.best_loss,
                job.model_version,
                json.dumps(job.metrics_history),
                job.error_message
            ))
    
    def get_training_status(self, job_id: str) -> Dict[str, Any]:
        """Get training job status"""
        if job_id in self.training_jobs:
            job = self.training_jobs[job_id]
            return {
                'job_id': job.job_id,
                'status': job.status.value,
                'current_epoch': job.current_epoch,
                'total_epochs': job.total_epochs,
                'best_accuracy': job.best_accuracy,
                'best_loss': job.best_loss,
                'model_version': job.model_version,
                'start_time': job.start_time.isoformat() if job.start_time else None,
                'end_time': job.end_time.isoformat() if job.end_time else None,
                'error_message': job.error_message,
                'metrics_history': job.metrics_history
            }
        else:
            # Load from database
            with sqlite3.connect(self.registry_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM training_jobs WHERE job_id = ?", (job_id,)
                )
                row = cursor.fetchone()
                
                if row:
                    return {
                        'job_id': row['job_id'],
                        'status': row['status'],
                        'current_epoch': row['current_epoch'],
                        'total_epochs': row['total_epochs'],
                        'best_accuracy': row['best_accuracy'],
                        'best_loss': row['best_loss'],
                        'model_version': row['model_version'],
                        'start_time': row['start_time'],
                        'end_time': row['end_time'],
                        'error_message': row['error_message'],
                        'metrics_history': json.loads(row['metrics_history']) if row['metrics_history'] else {}
                    }
                else:
                    return {'error': 'Job not found'}
    
    def cancel_training(self, job_id: str) -> bool:
        """Cancel training job"""
        if job_id in self.training_jobs:
            job = self.training_jobs[job_id]
            if job.status in [TrainingStatus.QUEUED, TrainingStatus.PREPARING, TrainingStatus.TRAINING]:
                job.status = TrainingStatus.CANCELLED
                job.end_time = datetime.now()
                self._save_job(job)
                
                if self.active_training and self.active_training.job_id == job_id:
                    self.active_training = None
                
                logger.info(f"Training cancelled: {job_id}")
                return True
        
        return False
    
    def list_training_jobs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List training jobs"""
        with sqlite3.connect(self.registry_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM training_jobs 
                ORDER BY start_time DESC 
                LIMIT ?
            """, (limit,))
            
            jobs = []
            for row in cursor.fetchall():
                jobs.append({
                    'job_id': row['job_id'],
                    'status': row['status'],
                    'current_epoch': row['current_epoch'],
                    'total_epochs': row['total_epochs'],
                    'best_accuracy': row['best_accuracy'],
                    'best_loss': row['best_loss'],
                    'model_version': row['model_version'],
                    'start_time': row['start_time'],
                    'end_time': row['end_time'],
                    'error_message': row['error_message']
                })
            
            return jobs
    
    def get_training_metrics(self, job_id: str) -> Dict[str, Any]:
        """Get detailed training metrics"""
        status = self.get_training_status(job_id)
        if 'error' in status:
            return status
        
        # Get additional metrics from checkpoints
        with sqlite3.connect(self.registry_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM training_checkpoints 
                WHERE job_id = ? 
                ORDER BY epoch
            """, (job_id,))
            
            checkpoints = []
            for row in cursor.fetchall():
                checkpoints.append({
                    'epoch': row['epoch'],
                    'model_path': row['model_path'],
                    'accuracy': row['accuracy'],
                    'loss': row['loss'],
                    'timestamp': row['timestamp']
                })
        
        return {
            **status,
            'checkpoints': checkpoints
        }

# CLI interface
def main():
    """Main CLI interface"""
    import argparse
    
    parser = argparse.ArgumentParser(description="FlavorSnap Model Training")
    parser.add_argument("--start", action="store_true", help="Start training")
    parser.add_argument("--status", type=str, help="Get training status")
    parser.add_argument("--list", action="store_true", help="List training jobs")
    parser.add_argument("--cancel", type=str, help="Cancel training job")
    parser.add_argument("--config", type=str, help="Training config file")
    
    args = parser.parse_args()
    
    trainer = ModelTrainer()
    
    if args.start:
        if args.config:
            with open(args.config, 'r') as f:
                config_data = yaml.safe_load(f)
                config = TrainingConfig(**config_data)
        else:
            config = TrainingConfig()
        
        job_id = trainer.start_training(config)
        print(f"Training started: {job_id}")
    
    elif args.status:
        status = trainer.get_training_status(args.status)
        print(json.dumps(status, indent=2))
    
    elif args.list:
        jobs = trainer.list_training_jobs()
        print(json.dumps(jobs, indent=2))
    
    elif args.cancel:
        success = trainer.cancel_training(args.cancel)
        print(f"Cancel {'successful' if success else 'failed'}")
    
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
