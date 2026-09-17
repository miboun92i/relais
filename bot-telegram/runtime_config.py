"""Resolve the same persistent directory in bootstrap and both runtimes."""
import os
from pathlib import Path
from dotenv import load_dotenv


def data_directory(root):
    load_dotenv(Path(root) / '.env')
    configured = os.getenv('DATA_DIR') or os.getenv('RAILWAY_VOLUME_MOUNT_PATH')
    if not configured and os.getenv('RAILWAY_ENVIRONMENT_ID'):
        raise RuntimeError('Stockage persistant requis : configurez DATA_DIR sur le volume Railway.')
    path = Path(configured or root).resolve()
    if os.getenv('RAILWAY_ENVIRONMENT_ID') and os.getenv('COMMERCIAL_TEST_MODE', '0') != '1':
        mount = os.getenv('RAILWAY_VOLUME_MOUNT_PATH')
        if not mount or not path.is_relative_to(Path(mount).resolve()):
            raise RuntimeError('DATA_DIR doit être sur un volume Railway monté.')
    path.mkdir(parents=True, exist_ok=True)
    return path
