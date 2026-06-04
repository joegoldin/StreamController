"""
Marshal GTK calls onto the main thread.

GTK4 (and the GListModels backing list widgets) are not thread-safe: every
widget/model mutation must happen on the thread that owns the GTK main loop.
StreamController, however, runs action lifecycle callbacks on worker threads
(see ``DeckController.own_actions_ready``/``_update``/``_tick``/
``_event_callback``), so any combo-row mutation they trigger races the main
thread and corrupts the GtkListView tile structure -> ``gtk_list_tile_split``
assertion -> ``abort()``.

``run_on_main_sync`` executes a callable on the main thread while preserving
synchronous call semantics: if already on the main thread it runs inline,
otherwise it is scheduled via ``GLib.idle_add`` and the calling thread blocks
until it finishes, propagating the return value and any exception.
"""

import functools
import threading

from gi.repository import GLib


def run_on_main_sync(func, *args, **kwargs):
    """Run ``func(*args, **kwargs)`` on the GTK main thread and return its result."""
    if threading.current_thread() is threading.main_thread():
        return func(*args, **kwargs)

    done = threading.Event()
    box = {}

    def _invoke():
        try:
            box["result"] = func(*args, **kwargs)
        except BaseException as exc:  # propagated to the calling thread below
            box["error"] = exc
        finally:
            done.set()
        return GLib.SOURCE_REMOVE

    GLib.idle_add(_invoke)
    done.wait()

    if "error" in box:
        raise box["error"]
    return box.get("result")


def run_on_main(method):
    """Decorator that runs ``method`` on the GTK main thread (see ``run_on_main_sync``).

    Nested calls between decorated methods are safe: once execution is on the
    main thread the inner calls run inline, so a decorated ``populate`` that calls
    a decorated ``set_selected_item`` performs a single, atomic main-thread hop.
    """

    @functools.wraps(method)
    def wrapper(*args, **kwargs):
        return run_on_main_sync(method, *args, **kwargs)

    return wrapper
