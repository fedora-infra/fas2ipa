import os
from copy import deepcopy

import toml


CONFIG_FILES = ["/etc/fas2ipa/config.toml", "config.toml"]

DEFAULT_CONFIG = {
    # We batch our queries (groups, users, memberships, etc).
    # How many objects maximum should be in each request?
    "chunks": 30,
    # Record and replay requests to FAS (for testing)
    "replay": False,
    # Users configuration
    "users": {"skip_spam": True, "skip_disabled": False},
    # Groups configuration
    "groups": {
        # * for all
        "search": "*",
        # Prefix the group names on import
        "prefix": "",
    },
    # FAS configuration
    "fas": {
        "fedora": {
            "url": "https://admin.fedoraproject.org/accounts",
            "username": None,
            "password": None,
            "groups": {
                # Which groups should we ignore when creating and mapping?
                "ignore": ["cla_fpca", "cla_done", "cla_fedora"],
            },
        },
    },
    # IPA configuration
    "ipa": {
        "instances": ["ipa.fas2ipa.test"],
        "cert_path": None,
        "username": None,
        "password": None,
        # After too long a session can expire.
        # So we just trigger a re-auth, every reauth_every imports.
        "reauth_every": 300,
    },
}


def merge_dicts(d1: dict, d2: dict) -> dict:
    """Merge nested dictionaries in depth.

    :param d1: First dictionary to merge
    :param d2: Second dictionary to merge, takes precedence over the first
    :return: A dictionary merged from d1 and d2
    """
    d3 = {}
    d1keys = set(d1)
    d2keys = set(d2)

    for k in d1keys - d2keys:
        d3[k] = d1[k]

    for k in d2keys - d1keys:
        d3[k] = d2[k]

    for k in d1keys & d2keys:
        v1 = d1[k]
        v2 = d2[k]
        if isinstance(v1, dict) and isinstance(v2, dict):
            d3[k] = merge_dicts(v1, v2)
        else:
            d3[k] = v2

    return d3


def get_config(config_file=None):
    config = deepcopy(DEFAULT_CONFIG)
    if config_file is not None:
        config_files = [config_file]
    else:
        config_files = CONFIG_FILES[:]
    config.update(toml.load([f for f in config_files if os.path.exists(f)]))

    # Copy defaults into FAS instance configurations
    defaults = config.copy()
    defaults.pop("fas", None)
    defaults.pop("ipa", None)

    new_fas_confs = {}

    for fas_name, fas_conf in config["fas"].items():
        new_fas_confs[fas_name] = merge_dicts(defaults, fas_conf)

    config["fas"] = new_fas_confs

    return config
