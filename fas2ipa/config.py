import os
from copy import deepcopy

import click
import toml


CONFIG_FILES = ["/etc/fas2ipa/config.toml", "config.toml"]

INPUT_IF_EMTPY = {
    "fas": ["username", "password"],
    "ipa": ["username", "password"],
}

DEFAULT_CONFIG = {
    # We batch our queries (groups, users, memberships, etc).
    # How many objects maximum should be in each request?
    "chunks": 30,
    # Record and replay requests to FAS (for testing)
    "replay": False,
    # Users configuration
    "users": {"skip_spam": True},
    # Groups configuration
    "groups": {
        # * for all
        "search": "*",
        # Which groups should we ignore when creating and mapping?
        "ignore": ["cla_fpca", "cla_done", "cla_fedora"],
        # Prefix the group names on import
        "prefix": "",
    },
    # FAS configuration
    "fas": {
        "url": "https://admin.fedoraproject.org/accounts",
        "username": None,
        "password": None,
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


def get_config():
    config = deepcopy(DEFAULT_CONFIG)
    config.update(toml.load([f for f in CONFIG_FILES if os.path.exists(f)]))
    for section, keys in INPUT_IF_EMTPY.items():
        for key in keys:
            if config[section][key]:
                continue
            is_password = key == "password"
            config[section][key] = click.prompt(
                f"Enter {key} for {section}", hide_input=is_password
            )
    return config
