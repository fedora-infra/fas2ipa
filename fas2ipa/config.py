from copy import deepcopy

import click
import toml


INPUT_IF_EMTPY = {
    "fas": ["username", "password"],
    "ipa": ["username", "password"],
}

DEFAULT_CONFIG = {
    # * for all
    "group_search": "*",
    # After too long a session can expire.
    # So we just trigger a re-atuh, every reauth_every account imports.
    "reauth_every": 150,
    # We batch our group membership queries.
    # How many members maximum should be in each request?
    "group_chunks": 30,
    # Which groups should we ignore when creating and mapping?
    "ignore_groups": ["cla_fpca", "cla_done", "cla_fedora"],
    # Record and replay requests to FAS (for testing)
    "replay": False,
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
    },
}


def get_config():
    config = deepcopy(DEFAULT_CONFIG)
    config.update(toml.load(["/etc/fas2ipa/config.toml", "config.toml"]))
    for section, keys in INPUT_IF_EMTPY.items():
        for key in keys:
            if config[section][key]:
                continue
            is_password = key == "password"
            config[section][key] = click.prompt(
                f"Enter {key} for {section}", hide_input=is_password
            )
    return config
