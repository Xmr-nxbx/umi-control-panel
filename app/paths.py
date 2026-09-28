"""运行路径与数据目录。"""
import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))          # ...\Umi-control-panel\app
ROOT_DIR = os.path.dirname(APP_DIR)                            # ...\Umi-control-panel
DATA_DIR = os.path.join(ROOT_DIR, 'data')
WEB_DIR = os.path.join(APP_DIR, 'web')
RUNTIME_DIR = os.path.join(ROOT_DIR, 'runtime')


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)
    return DATA_DIR


def data_path(name):
    return os.path.join(ensure_data_dir(), name)


def self_exe():
    """当前 python 解释器路径（便携运行时）。"""
    return sys.executable
