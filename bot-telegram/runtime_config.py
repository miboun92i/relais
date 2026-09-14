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
    path.mkdir(parents=True, exist_ok=True)
    return path
