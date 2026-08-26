import random
import pytest

from patrol.common.config import Config


@pytest.fixture(scope="session")
def cfg():
    return Config.load()


@pytest.fixture()
def free_ports():
    """给每个测试一组不冲突的 tcp 端口，避免并行时抢占。"""
    base = random.randint(21000, 44000)
    return {"detection": "tcp://127.0.0.1:%d" % base,
            "command": "tcp://127.0.0.1:%d" % (base + 1),
            "status": "tcp://127.0.0.1:%d" % (base + 2)}


@pytest.fixture()
def cfg_ports(free_ports):
    return Config.load(overrides={"bus": free_ports})
