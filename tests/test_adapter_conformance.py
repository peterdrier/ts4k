"""Interface-conformance checks across all concrete adapters.

The command dispatcher calls every adapter's ``whatsnew`` and
``list_messages`` with keyword arguments defined by ``BaseAdapter``.  An
adapter whose signature drifts from the base interface fails at call time
with ``unexpected keyword argument`` — silently dropping that source from
listings (issue #106).  These tests catch the drift statically.
"""

from __future__ import annotations

import inspect

import pytest

from ts4k.adapters.base import BaseAdapter
from ts4k.adapters.caldav_cal import CaldavAdapter
from ts4k.adapters.gcal import GcalAdapter
from ts4k.adapters.github import GitHubAdapter
from ts4k.adapters.gmail import GmailAdapter
from ts4k.adapters.http import HTTPAdapter
from ts4k.adapters.o365 import O365Adapter
from ts4k.adapters.o365cal import O365CalAdapter
from ts4k.adapters.whatsapp import WhatsAppAdapter

ADAPTER_CLASSES = [
    CaldavAdapter,
    GcalAdapter,
    GitHubAdapter,
    GmailAdapter,
    HTTPAdapter,
    O365Adapter,
    O365CalAdapter,
    WhatsAppAdapter,
]

# The kwargs the dispatcher (commands.py) actually passes to each method.
DISPATCHED_KWARGS = {
    "whatsnew": {"since": None, "sender": None, "domain": None, "count": 200},
    "list_messages": {
        "query": None,
        "count": 20,
        "page_token": None,
        "sender": None,
        "domain": None,
    },
}


@pytest.mark.parametrize("adapter_cls", ADAPTER_CLASSES)
@pytest.mark.parametrize("method_name", sorted(DISPATCHED_KWARGS))
def test_signature_accepts_dispatched_kwargs(adapter_cls, method_name):
    """Every adapter method must bind the kwargs the dispatcher passes."""
    method = getattr(adapter_cls, method_name)
    sig = inspect.signature(method)
    try:
        # 'self' placeholder + the dispatcher's kwargs.
        sig.bind(object(), **DISPATCHED_KWARGS[method_name])
    except TypeError as exc:
        pytest.fail(
            f"{adapter_cls.__name__}.{method_name}{sig} does not accept the "
            f"dispatcher's kwargs {sorted(DISPATCHED_KWARGS[method_name])}: {exc}"
        )


@pytest.mark.parametrize("adapter_cls", ADAPTER_CLASSES)
@pytest.mark.parametrize("method_name", sorted(DISPATCHED_KWARGS))
def test_signature_matches_base_interface(adapter_cls, method_name):
    """Adapter signatures carry every parameter the base interface declares.

    Stricter than the bind check above: catches an adapter that renames a
    parameter or drops one the base declares, even if extra ``**kwargs``
    would make a bind succeed.
    """
    base_params = inspect.signature(getattr(BaseAdapter, method_name)).parameters
    impl_params = inspect.signature(getattr(adapter_cls, method_name)).parameters
    missing = [name for name in base_params if name not in impl_params]
    assert not missing, (
        f"{adapter_cls.__name__}.{method_name} is missing base interface "
        f"parameter(s): {missing}"
    )
