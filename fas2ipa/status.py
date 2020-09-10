from enum import Enum

from colorama import Fore, Style


class Status(Enum):
    ADDED = "ADDED"
    UPDATED = "UPDATED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    UNMODIFIED = "UNMODIFIED"
    REMOVED = "REMOVED"


def print_status(status, text=None):
    if status == Status.ADDED:
        color = Style.BRIGHT + Fore.GREEN
    elif status == Status.UPDATED:
        color = Style.BRIGHT + Fore.CYAN
    elif status == Status.FAILED:
        color = Style.BRIGHT + Fore.RED
    elif status == Status.SKIPPED:
        color = Style.BRIGHT + Fore.BLUE
    elif status == Status.UNMODIFIED:
        color = Style.BRIGHT + Fore.YELLOW
    elif status == Status.REMOVED:
        color = Style.NORMAL + Fore.MAGENTA
    else:
        raise ValueError(f"Unknown status: {status!r}")
    print(f"{color}{text or status.value}{Style.RESET_ALL}")
