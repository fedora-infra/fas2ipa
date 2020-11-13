import json
import pathlib
import random
from collections import defaultdict
from typing import Union

import click
import toml


# def chunks(data, n):
#     return [data[x : x + n] for x in range(0, len(data), n)]


def re_auth(config, instances):
    click.echo("Re-authenticating")
    for ipa in instances:
        ipa.logout()
        ipa.login(config["ipa"]["username"], config["ipa"]["password"])


class ObjectManager:
    def __init__(self, config, ipa_instances, fas_instances):
        self.config = config
        self.ipa_instances = ipa_instances
        self.fas_instances = fas_instances

    @property
    def ipa(self):
        return random.choice(self.ipa_instances)

    def check_reauth(self, counter):
        if counter % self.config["ipa"]["reauth_every"] == 0:
            re_auth(self.config, self.ipa_instances)

    def chunks(self, items):
        size = self.config["chunks"]
        return [items[x : x + size] for x in range(0, len(items), size)]


def load_data(fpath: Union[str, pathlib.Path]) -> dict:
    """Load dictionary data from a JSON, YAML, or TOML file.

    The file format will be determined from the extension of the file name.

    :param fpath:   The file path from which to load.

    :return:        The loaded data as a dictionary.
    """
    if not isinstance(fpath, pathlib.Path):
        fpath = pathlib.Path(fpath)

    suffix = fpath.suffix.lower()

    if suffix == ".toml":
        data = toml.loads(fpath.read_text())
    elif suffix == ".yaml":
        import yaml
        with fpath.open("r") as fobj:
            data = yaml.safe_load(fobj)
    else:
        data = json.loads(fpath.read_text())

    return data


def save_data(data: dict, fpath: Union[str, pathlib.Path], force_overwrite: bool = False):
    """Save a dictionary object to a JSON, YAML, or TOML file.

    The file format will be determined from the extension of the file name.

    :param data:            The data to be saved.
    :param fpath:           The file path to be saved into.
    :param force_overwrite: Whether an existing file should be overwritten.
    """
    if not isinstance(fpath, pathlib.Path):
        fpath = pathlib.Path(fpath)

    suffix = fpath.suffix.lower()

    if force_overwrite:
        mode = "w"
    else:
        mode = "x"

    with fpath.open(mode) as fobj:
        if suffix == ".toml":
            toml.dump(data, fobj)
        elif suffix == ".yaml":
            import yaml
            yaml.add_representer(set, yaml.representer.SafeRepresenter.represent_list)
            yaml.add_representer(defaultdict, yaml.representer.SafeRepresenter.represent_dict)
            yaml.dump(data, fobj)
        else:
            json.dump(data, fobj, indent=2)
