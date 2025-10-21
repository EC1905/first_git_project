import json
import os

def load_target_config(config_path):
    """Load target configuration from JSON file."""
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Target config file not found: {config_path}")
    
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)
