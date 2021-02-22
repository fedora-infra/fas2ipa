import os
import pathlib
from urllib.parse import parse_qs, urlencode

import click
import munch
import vcr
from python_freeipa import ClientLegacy as Client
from fedora.client.fas2 import AccountSystem
from requests.exceptions import ConnectionError

from .config import get_config
from .statistics import Stats
from .users import Users
from .groups import Groups
from .agreements import Agreements
from .utils import load_data, report_conflicts, save_data
from .ipa_plugin import disable_memberof_plugin, enable_memberof_plugin


class FASWrapper:

    _remove_from_request_body = ("_csrf_token", "user_name", "password", "login")

    def __init__(self, config, inst_conf):
        self.inst_conf = inst_conf
        self.fas = AccountSystem(
            inst_conf["url"],
            username=inst_conf["username"],
            password=inst_conf["password"],
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
            for attempt in range(self.inst_conf["retries"] + 1):
                try:
                    return self.fas.send_request(url, *args, **kwargs)
                except ConnectionError:
                    if attempt < self.inst_conf["retries"]:
                        click.echo(f"Retry #{attempt + 1}")
                    else:
                        click.echo("Giving up.")
                        raise

        cassette_path = self._vcr_get_cassette_path(url, *args, **kwargs)
        with self._recorder.use_cassette(cassette_path, match_on=["fas2ipa"]):
            return self.fas.send_request(url, *args, **kwargs)


@click.command(context_settings={"help_option_names": ("-h", "--help")})
@click.option("--pull/--no-pull", default=None, help="Whether to pull data from FAS.")
@click.option("--push/--no-push", default=None, help="Whether to push data to IPA.")
@click.option(
    "--check/--no-check", default=None, help="Whether to check for conflicts."
)
@click.option(
    "--dataset-file",
    type=click.Path(file_okay=True),
    help="Write data into/read data from this file.",
)
@click.option(
    "--conflicts-file", default=None, help="Write found conflicts into this file."
)
@click.option("--force-overwrite", is_flag=True, help="Overwrite file if it exists.")
@click.option("--skip-groups", is_flag=True, help="Skip group creation.")
@click.option(
    "--skip-user-add", is_flag=True, help="Don't add or update users.",
)
@click.option(
    "--skip-user-membership", is_flag=True, help="Don't add users to groups.",
)
@click.option(
    "--skip-user-signature",
    is_flag=True,
    help="Don't store users' signatures of agreements.",
)
@click.option("--users-start-at", help="Start migrating users at that (partial) name.")
@click.option(
    "--restrict-users",
    "-u",
    multiple=True,
    help="Restrict users to supplied glob pattern(s).",
)
def cli(
    pull,
    push,
    check,
    dataset_file,
    conflicts_file,
    force_overwrite,
    skip_groups,
    skip_user_add,
    skip_user_membership,
    skip_user_signature,
    users_start_at,
    restrict_users,
):
    _orig_check = check

    if check is False:
        if conflicts_file:
            raise click.BadOptionUsage(
                option_name="--check",
                message="--no-check and --conflicts-file can't be combined",
            )
    elif check is True:
        if not (pull or push or conflicts_file or dataset_file):
            raise click.BadOptionUsage(
                option_name="--check",
                message=(
                    "--check without --pull or --push needs --conflicts-file or"
                    " --dataset-file"
                ),
            )
    else:
        check = True

    if pull is None and push is None:
        if not check:
            raise click.BadOptionUsage(
                option_name=("--pull", "--push", "--check"),
                message="Neither pulling, pushing nor, checking. Bailing out.",
            )
        if _orig_check is None:
            pull = push = True
    elif False in (pull, push):
        pull = pull is not False
        push = push is not False
    elif True in (pull, push):
        push = bool(push)
        pull = bool(pull)

    if not dataset_file and pull != push:
        raise click.BadOptionUsage(
            option_name="--dataset-file",
            message="Pulling without pushing or vice versa needs --dataset-file",
        )

    config = get_config()
    config["skip_groups"] = skip_groups
    config["skip_user_add"] = skip_user_add
    config["skip_user_membership"] = skip_user_membership
    config["skip_user_signature"] = skip_user_signature

    # If dataset or conlicts files should be written later, bail out before overwriting
    # an existing file (unless force_overwrite is set). This will be checked again later
    # to avoid race conditions.

    if dataset_file:
        dataset_file = pathlib.Path(dataset_file)

        if pull and dataset_file.exists() and not force_overwrite:
            raise click.ClickException(
                f"Refusing to overwrite '{dataset_file}', use --force-overwrite to override."
            )

    if conflicts_file:
        conflicts_file = pathlib.Path(conflicts_file)

        if check and dataset_file and conflicts_file.exists() and not force_overwrite:
            raise click.ClickException(
                f"Refusing to overwrite '{conflicts_file}', use --force-overwrite to override."
            )

    if dataset_file and not pull:
        dataset = load_data(dataset_file)
    else:
        dataset = {}

    fas_instances = {}

    if pull:
        for inst_name, inst_conf in config["fas"].items():
            fas = FASWrapper(config, inst_conf)
            fas_instances[inst_name] = fas
            click.echo(f"Logged into FAS ({inst_name}): {inst_conf['url']}")

    if push:
        ipa_instances = []
        for instance in config["ipa"]["instances"]:
            ipa = Client(host=instance, verify_ssl=config["ipa"]["cert_path"])
            ipa.login(config["ipa"]["username"], config["ipa"]["password"])
            ipa_instances.append(ipa)
        click.echo("Logged into IPA")
    else:
        ipa_instances = None

    stats = Stats()

    agreements_mgr = Agreements(config, ipa_instances, fas_instances)
    users_mgr = Users(config, ipa_instances, fas_instances, agreements=agreements_mgr)
    groups_mgr = Groups(config, ipa_instances, fas_instances, agreements=agreements_mgr)

    if pull:
        if not skip_groups:
            dataset["groups"] = groups_mgr.pull_from_fas()

        dataset["users"] = users_mgr.pull_from_fas(
            users_start_at=users_start_at, restrict_users=restrict_users
        )

    conflicts = {"users": [], "groups": []}
    if check:
        if dataset_file:
            users_to_conflicts = users_mgr.find_user_conflicts(dataset["users"])
            groups_to_conflicts = groups_mgr.find_group_conflicts(dataset["groups"])

            conflicts = {}
            if users_to_conflicts:
                conflicts["users"] = users_to_conflicts
            if groups_to_conflicts:
                conflicts["groups"] = groups_to_conflicts

            if conflicts_file:
                save_data(conflicts, conflicts_file, force_overwrite=force_overwrite)
        elif conflicts_file and os.path.exists(conflicts_file):
            conflicts = load_data(conflicts_file)
        report_conflicts(conflicts)

    if pull and dataset_file:
        save_data(
            munch.unmunchify(dataset), dataset_file, force_overwrite=force_overwrite
        )

    if push:
        if any(fas.get("agreement") for fas in config["fas"].values()):
            # Create agreements, relations to users and groups will be done later
            agreements_mgr.push_to_ipa()

        if not skip_groups:
            groups_stats = groups_mgr.push_to_ipa(
                dataset["groups"], conflicts["groups"]
            )
            stats.update(groups_stats)

        # Disable and re-enable the memberof plugin to speed up import if and
        # only if we're doing a full run
        optimize_memberof = not users_start_at and not restrict_users
        if optimize_memberof:
            disable_memberof_plugin()

        users_stats = users_mgr.push_to_ipa(
            dataset["users"], users_start_at, restrict_users, conflicts["users"]
        )
        stats.update(users_stats)

        if optimize_memberof:
            enable_memberof_plugin()

    stats.print()
