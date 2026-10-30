"""Compatibility shim: behavioural learning lives in ``memory.lessons``.

Importing this module returns the exact same object as ``memory.lessons``, so
monkeypatching and module-level state stay in sync.
"""

import sys

import memory.lessons as _impl

sys.modules[__name__] = _impl