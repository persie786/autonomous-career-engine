import threading

_ctx = threading.local()


def set_current_user(user_id: int):
    _ctx.user_id = user_id


def get_current_user() -> int:
    user_id = getattr(_ctx, "user_id", None)
    if user_id is None:
        raise RuntimeError(
            "No user logged in — set_current_user() must be called after login."
        )
    return user_id


def clear_current_user():
    _ctx.user_id = None
