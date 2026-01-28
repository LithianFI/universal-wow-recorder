# fix_config.py
import configparser
from pathlib import Path

def fix_config_file(config_path):
    """Fix common issues in config files"""
    config = configparser.ConfigParser()
    
    try:
        config.read(config_path)
        print(f"Read config from: {config_path}")
        
        # Fix paths with trailing slashes
        if 'General' in config and 'log_dir' in config['General']:
            path = config['General']['log_dir'].strip()
            if path.endswith('/') or path.endswith('\\'):
                config['General']['log_dir'] = path.rstrip('/\\')
                print(f"Fixed trailing slash in log_dir: {config['General']['log_dir']}")
        
        if 'Recording' in config and 'recording_path_fallback' in config['Recording']:
            path = config['Recording']['recording_path_fallback'].strip()
            if path.endswith('/') or path.endswith('\\'):
                config['Recording']['recording_path_fallback'] = path.rstrip('/\\')
                print(f"Fixed trailing slash in recording_path_fallback: {config['Recording']['recording_path_fallback']}")
        
        # Save the fixed config
        with open(config_path, 'w') as f:
            config.write(f)
        print(f"Fixed config saved to: {config_path}")
        
    except Exception as e:
        print(f"Error fixing config: {e}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        fix_config_file(Path(sys.argv[1]))
    else:
        print("Usage: python fix_config.py <config_file_path>")