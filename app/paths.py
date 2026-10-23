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


def install_fingerprint():
    """当前项目目录的稳定指纹；供停止工具确认端口上的服务确实属于本目录。"""
    import hashlib
    return hashlib.sha256(os.path.normcase(os.path.realpath(ROOT_DIR))
                          .encode('utf-8')).hexdigest()[:16]


def _tcp_port(value, source):
    """只接受合法 TCP 端口，不把 bool/小数或空值默默变成默认端口。"""
    if isinstance(value, str):
        value = value.strip()
        if value.isascii() and value.isdecimal():
            value = int(value)
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError('%s 必须是 1～65535 的整数端口' % source)
    return value


def discover_panel_port(explicit=None, cfg=None):
    """显式端口 → data/port → 配置默认。只读，不创建目录、不加载身份。

    仅「文件不存在」才回退；损坏/不可读必须报错，不能让并发工具误判面板已停。
    cfg 可传入已加载的 Config；未传时仅在需要回退时只读 config.json。
    """
    if explicit is not None:
        return _tcp_port(explicit, '--port')
    try:
        with open(os.path.join(DATA_DIR, 'port'), encoding='utf-8-sig') as fh:
            value = fh.read(33)
        if len(value) > 32:
            raise ValueError('data/port 内容过长，拒绝忽略尾部内容')
        return _tcp_port(value, 'data/port')
    except FileNotFoundError:
        pass

    from app.config import DEFAULTS
    default = DEFAULTS['server']['port']
    if cfg is not None:
        return _tcp_port(cfg.get('server', 'port', default=default), 'server.port')

    import json
    try:
        with open(os.path.join(DATA_DIR, 'config.json'), encoding='utf-8-sig') as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return default
    except ValueError:
        raise ValueError('无法解析面板端口配置') from None
    if not isinstance(raw, dict) or not isinstance(raw.get('server', {}), dict):
        raise ValueError('面板端口配置必须是对象')
    return _tcp_port(raw.get('server', {}).get('port', default), 'server.port')


def local_mqtt_endpoint(mqtt_cfg):
    """从已加载的 MQTT 配置取本机端点；不做 DNS 探测，不支持远程 broker。"""
    host = mqtt_cfg.get('host', '127.0.0.1')
    if host not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('MQTT host 必须是本机回环地址')
    return host, _tcp_port(mqtt_cfg.get('port', 13688), 'MQTT port')
