"""Tests for performance optimizations — caching, connection reuse, compact mode."""
import time


def test_iphone_tree_cache_exists():
    from iphone_harness import helpers
    assert hasattr(helpers, '_tree_cache')
    assert hasattr(helpers, '_tree_cache_time')
    assert hasattr(helpers, '_TREE_TTL')
    assert helpers._TREE_TTL > 0


def test_android_tree_cache_exists():
    from android_harness import helpers
    assert hasattr(helpers, '_tree_cache')
    assert hasattr(helpers, '_tree_cache_time')
    assert hasattr(helpers, '_TREE_TTL')
    assert helpers._TREE_TTL > 0


def test_iphone_invalidate_tree_cache():
    from iphone_harness.helpers import _tree_cache_time, invalidate_tree_cache
    invalidate_tree_cache()
    from iphone_harness import helpers
    assert helpers._tree_cache is None
    assert helpers._tree_cache_time == 0.0


def test_android_invalidate_tree_cache():
    from android_harness.helpers import invalidate_tree_cache
    invalidate_tree_cache()
    from android_harness import helpers
    assert helpers._tree_cache is None
    assert helpers._tree_cache_time == 0.0


def test_iphone_conn_cache_exists():
    from iphone_harness import helpers
    assert hasattr(helpers, '_cached_sock')
    assert hasattr(helpers, '_get_conn')
    assert hasattr(helpers, '_drop_conn')


def test_android_conn_cache_exists():
    from android_harness import helpers
    assert hasattr(helpers, '_cached_sock')
    assert hasattr(helpers, '_get_conn')
    assert hasattr(helpers, '_drop_conn')


def test_iphone_drop_conn():
    from iphone_harness import helpers
    helpers._drop_conn()
    assert helpers._cached_sock is None
    assert helpers._cached_token is None


def test_android_drop_conn():
    from android_harness import helpers
    helpers._drop_conn()
    assert helpers._cached_sock is None
    assert helpers._cached_token is None


def test_iphone_retry_delay_low():
    from iphone_harness.helpers import RETRY_DELAY
    assert RETRY_DELAY <= 0.5


def test_android_retry_delay_low():
    from android_harness.helpers import RETRY_DELAY
    assert RETRY_DELAY <= 0.5


def test_iphone_find_accepts_tree_param():
    import inspect

    from iphone_harness.helpers import find, find_all
    sig_find = inspect.signature(find)
    sig_findall = inspect.signature(find_all)
    assert '_tree' in sig_find.parameters
    assert '_tree' in sig_findall.parameters


def test_android_find_accepts_tree_param():
    import inspect

    from android_harness.helpers import find, find_all
    sig_find = inspect.signature(find)
    sig_findall = inspect.signature(find_all)
    assert '_tree' in sig_find.parameters
    assert '_tree' in sig_findall.parameters


def test_iphone_ui_tree_compact_param():
    import inspect

    from iphone_harness.helpers import ui_tree
    sig = inspect.signature(ui_tree)
    assert 'compact' in sig.parameters


def test_android_ui_tree_compact_param():
    import inspect

    from android_harness.helpers import ui_tree
    sig = inspect.signature(ui_tree)
    assert 'compact' in sig.parameters


def test_cli_auto_detect_timeout():
    """Verify auto-detect uses fast timeouts."""
    import inspect

    from mobile_use import cli
    src = inspect.getsource(cli._detect_platform)
    assert "timeout=1.5" in src


def test_agent_helpers_lazy_flag():
    from android_harness import helpers as anh
    from iphone_harness import helpers as iph
    assert hasattr(iph, '_agent_helpers_loaded')
    assert hasattr(anh, '_agent_helpers_loaded')
