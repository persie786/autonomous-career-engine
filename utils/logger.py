import logging
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(PROJECT_ROOT, "app.log")


def setup_logger(name: str = __name__) -> logging.Logger:
    """
    Returns a logger that writes to both app.log and the console.
    Safe to call repeatedly (e.g. on every Streamlit rerun) without
    duplicating log lines.
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        # Streamlit re-executes the whole script on every interaction.
        # Without this guard, every rerun would attach a fresh set of
        # handlers, and every future log line would print N times.
        return logger

    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger