import pytest

import fakeredis
from fakeredis import FakeStrictRedis

from sentry_sdk.integrations.redis import RedisIntegration
from sentry_sdk.integrations.redis.utils import _get_safe_key
from sentry_sdk.utils import parse_version
import sentry_sdk


FAKEREDIS_VERSION = parse_version(fakeredis.__version__)


def test_no_cache_basic(sentry_init, capture_events):
    sentry_init(
        integrations=[
            RedisIntegration(),
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    connection = FakeStrictRedis()
    with sentry_sdk.start_transaction():
        connection.get("mycachekey")

    (event,) = events
    spans = event["spans"]
    assert len(spans) == 1
    assert spans[0]["op"] == "db.redis"


def test_cache_basic(sentry_init, capture_events):
    sentry_init(
        integrations=[
            RedisIntegration(
                cache_prefixes=["mycache"],
            ),
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    connection = FakeStrictRedis()
    with sentry_sdk.start_transaction():
        connection.hget("mycachekey", "myfield")
        connection.get("mycachekey")
        connection.set("mycachekey1", "bla")
        connection.setex("mycachekey2", 10, "blub")
        connection.mget("mycachekey1", "mycachekey2")

    (event,) = events
    spans = event["spans"]
    assert len(spans) == 9

    # no cache support for hget command
    assert spans[0]["op"] == "db.redis"
    assert spans[0]["tags"]["redis.command"] == "HGET"

    assert spans[1]["op"] == "cache.get"
    assert spans[2]["op"] == "db.redis"
    assert spans[2]["tags"]["redis.command"] == "GET"

    assert spans[3]["op"] == "cache.put"
    assert spans[4]["op"] == "db.redis"
    assert spans[4]["tags"]["redis.command"] == "SET"

    assert spans[5]["op"] == "cache.put"
    assert spans[6]["op"] == "db.redis"
    assert spans[6]["tags"]["redis.command"] == "SETEX"

    assert spans[7]["op"] == "cache.get"
    assert spans[8]["op"] == "db.redis"
    assert spans[8]["tags"]["redis.command"] == "MGET"


def test_cache_keys(sentry_init, capture_events):
    sentry_init(
        integrations=[
            RedisIntegration(
                cache_prefixes=["bla", "blub"],
            ),
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    connection = FakeStrictRedis()
    with sentry_sdk.start_transaction():
        connection.get("somethingelse")
        connection.get("blub")
        connection.get("blubkeything")
        connection.get("bl")

    (event,) = events
    spans = event["spans"]
    assert len(spans) == 6
    assert spans[0]["op"] == "db.redis"
    assert spans[0]["description"] == "GET 'somethingelse'"

    assert spans[1]["op"] == "cache.get"
    assert spans[1]["description"] == "blub"
    assert spans[2]["op"] == "db.redis"
    assert spans[2]["description"] == "GET 'blub'"

    assert spans[3]["op"] == "cache.get"
    assert spans[3]["description"] == "blubkeything"
    assert spans[4]["op"] == "db.redis"
    assert spans[4]["description"] == "GET 'blubkeything'"

    assert spans[5]["op"] == "db.redis"
    assert spans[5]["description"] == "GET 'bl'"


def test_cache_data(sentry_init, capture_events):
    sentry_init(
        integrations=[
            RedisIntegration(
                cache_prefixes=["mycache"],
            ),
        ],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    connection = FakeStrictRedis(host="mycacheserver.io", port=6378)
    with sentry_sdk.start_transaction():
        connection.get("mycachekey")
        connection.set("mycachekey", "事实胜于雄辩")
        connection.get("mycachekey")

    (event,) = events
    spans = event["spans"]

    assert len(spans) == 6

    assert spans[0]["op"] == "cache.get"
    assert spans[0]["description"] == "mycachekey"
    assert spans[0]["data"]["cache.key"] == "mycachekey"
    assert spans[0]["data"]["cache.hit"] == False  # noqa: E712
    assert "cache.item_size" not in spans[0]["data"]
    # very old fakeredis can not handle port and/or host.
    # only applicable for Redis v3
    if FAKEREDIS_VERSION <= (2, 7, 1):
        assert "network.peer.port" not in spans[0]["data"]
    else:
        assert spans[0]["data"]["network.peer.port"] == 6378
    if FAKEREDIS_VERSION <= (1, 7, 1):
        assert "network.peer.address" not in spans[0]["data"]
    else:
        assert spans[0]["data"]["network.peer.address"] == "mycacheserver.io"

    assert spans[1]["op"] == "db.redis"  # we ignore db spans in this test.

    assert spans[2]["op"] == "cache.put"
    assert spans[2]["description"] == "mycachekey"
    assert spans[2]["data"]["cache.key"] == "mycachekey"
    assert "cache.hit" not in spans[1]["data"]
    assert spans[2]["data"]["cache.item_size"] == 18
    # very old fakeredis can not handle port.
    # only used with redis v3
    if FAKEREDIS_VERSION <= (2, 7, 1):
        assert "network.peer.port" not in spans[2]["data"]
    else:
        assert spans[2]["data"]["network.peer.port"] == 6378
    if FAKEREDIS_VERSION <= (1, 7, 1):
        assert "network.peer.address" not in spans[2]["data"]
    else:
        assert spans[2]["data"]["network.peer.address"] == "mycacheserver.io"

    assert spans[3]["op"] == "db.redis"  # we ignore db spans in this test.

    assert spans[4]["op"] == "cache.get"
    assert spans[4]["description"] == "mycachekey"
    assert spans[4]["data"]["cache.key"] == "mycachekey"
    assert spans[4]["data"]["cache.hit"] == True  # noqa: E712
    assert spans[4]["data"]["cache.item_size"] == 18
    # very old fakeredis can not handle port.
    # only used with redis v3
    if FAKEREDIS_VERSION <= (2, 7, 1):
        assert "network.peer.port" not in spans[4]["data"]
    else:
        assert spans[4]["data"]["network.peer.port"] == 6378
    if FAKEREDIS_VERSION <= (1, 7, 1):
        assert "network.peer.address" not in spans[4]["data"]
    else:
        assert spans[4]["data"]["network.peer.address"] == "mycacheserver.io"

    assert spans[5]["op"] == "db.redis"  # we ignore db spans in this test.


@pytest.mark.parametrize(
    "method_name,args,kwargs,expected_key",
    [
        (None, None, None, ""),
        ("", None, None, ""),
        ("set", ["bla", "valuebla"], None, "bla"),
        ("setex", ["bla", 10, "valuebla"], None, "bla"),
        ("get", ["bla"], None, "bla"),
        ("mget", ["bla", "blub", "foo"], None, "bla, blub, foo"),
        ("set", [b"bla", "valuebla"], None, "bla"),
        ("setex", [b"bla", 10, "valuebla"], None, "bla"),
        ("get", [b"bla"], None, "bla"),
        ("mget", [b"bla", "blub", "foo"], None, "bla, blub, foo"),
    ],
)
def test_get_safe_key(method_name, args, kwargs, expected_key):
    assert _get_safe_key(method_name, args, kwargs) == expected_key
