import string
from urllib.parse import parse_qs, urlencode

import click
import vcr
from python_freeipa import ClientLegacy as Client
from fedora.client.fas2 import AccountSystem

from .config import get_config
from .statistics import Stats
from .users import migrate_users
from .groups import migrate_groups
from .agreements import create_agreements


class FASWrapper:

    _remove_from_request_body = ("_csrf_token", "user_name", "password", "login")

    def __init__(self, config):
        self.fas = AccountSystem(
            config["fas"]["url"],
            username=config["fas"]["username"],
            password=config["fas"]["password"],
        )
        self._replay = config["replay"]
        self._recorder = vcr.VCR(
            ignore_hosts=config["ipa"]["instances"],
            record_mode="new_episodes",
            filter_post_data_parameters=self._remove_from_request_body,
        )
        self._recorder.register_matcher("fas2ipa", self._vcr_match_request)

    def _vcr_match_request(self, r1, r2):
        assert r1.query == r2.query
        body1 = parse_qs(r1.body)
        body2 = parse_qs(r2.body)
        for param in self._remove_from_request_body:
            for body in (body1, body2):
                try:
                    del body[param]
                except KeyError:
                    pass
        assert body1 == body2

    def _vcr_get_cassette_path(self, url, *args, **kwargs):
        params = kwargs.get("req_params", {})
        cassette_path = [
            "fixtures/fas-",
            url[1:].replace("/", "_"),
            ".yaml",
        ]
        if params:
            cassette_path[2:2] = [
                "-",
                urlencode(params, doseq=True),
            ]
        return "".join(cassette_path)

    def send_request(self, url, *args, **kwargs):
        if not self._replay:
            return self.fas.send_request(url, *args, **kwargs)

        cassette_path = self._vcr_get_cassette_path(url, *args, **kwargs)
        with self._recorder.use_cassette(cassette_path, match_on=["fas2ipa"]):
            return self.fas.send_request(url, *args, **kwargs)


@click.command()
@click.option("--skip-groups", is_flag=True, help="Skip group creation")
@click.option(
    "--skip-user-add", is_flag=True, help="Don't add or update users",
)
@click.option(
    "--skip-user-membership", is_flag=True, help="Don't add users to groups",
)
@click.option(
    "--skip-user-signature",
    is_flag=True,
    help="Don't store users signatures of agreements",
)
@click.option("--users-start-at", help="Start migrating users at that letter")
def cli(
    skip_groups,
    skip_user_add,
    skip_user_membership,
    skip_user_signature,
    users_start_at,
):
    config = get_config()
    config["skip_groups"] = skip_groups
    config["skip_user_add"] = skip_user_add
    config["skip_user_membership"] = skip_user_membership
    config["skip_user_signature"] = skip_user_signature

    fas = FASWrapper(config)
    click.echo("Logged into FAS")

    instances = []
    for instance in config["ipa"]["instances"]:
        ipa = Client(host=instance, verify_ssl=config["ipa"]["cert_path"])
        ipa.login(config["ipa"]["username"], config["ipa"]["password"])
        instances.append(ipa)
    click.echo("Logged into IPA")

    stats = Stats()

    if config.get("agreement"):
        create_agreements(config, ipa)

    groups_stats = migrate_groups(config, fas, ipa)
    stats.update(groups_stats)

    alphabet = list(string.ascii_lowercase)
    if users_start_at:
        start_index = alphabet.index(users_start_at.lower())
        del alphabet[:start_index]

    for letter in alphabet:
        click.echo(f"finding users starting with {letter}")
        users = fas.send_request(
            "/user/list", req_params={"search": letter + "*"}, auth=True, timeout=240
        )
        users_stats = migrate_users(config, users["people"], instances)
        stats.update(users_stats)

    stats.print()
