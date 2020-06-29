import random

import click
import python_freeipa
import progressbar

from .status import Status, print_status
from .utils import re_auth, chunks


def create_agreements(config, ipa):
    click.echo("Creating Agreements")
    for agreement in config.get("agreement"):
        with open(agreement["description_file"], "r") as f:
            agreement_description = f.read()
        try:
            ipa._request(
                "fasagreement_add",
                agreement["name"],
                {"description": agreement_description},
            )
        except python_freeipa.exceptions.DuplicateEntry as e:
            print_status(Status.SKIPPED, str(e))


def record_user_signatures(config, instances, agreements_to_usernames):
    if config["skip_user_signature"]:
        return

    for agreement in config.get("agreement"):
        click.echo(f"Recording signers of the {agreement['name']} agreement")
        signers = agreements_to_usernames.get(agreement["name"], [])
        if not signers:
            click.echo("Nothing to do.")
            continue
        counter = 0
        with progressbar.ProgressBar(
            max_value=len(signers), redirect_stdout=True
        ) as bar:
            for chunk in chunks(signers, config["group_chunks"]):
                counter += len(chunk)
                if counter % config["reauth_every"] == 0:
                    re_auth(config, instances)
                ipa = random.choice(instances)
                response = ipa._request(
                    "fasagreement_add_user", agreement["name"], {"user": chunk},
                )
                for msg in response["failed"]["memberuser"]["user"]:
                    if msg[1] != "This entry is already a member":
                        print_status(
                            Status.FAILED,
                            f"Could not mark {msg[0]} as having signed "
                            f"{agreement['name']}: {msg[1]}",
                        )
                bar.update(counter)


def find_requirements(groups, prereq_id):
    dependent_groups = []
    for group in groups:
        if group["prerequisite_id"] == prereq_id:
            dependent_groups.append(group["name"])
            subdeps = find_requirements(groups, group["id"])
            dependent_groups.extend(subdeps)
    return dependent_groups


def record_group_requirements(config, fas, ipa, groups):
    for agreement in config.get("agreement"):

        toplevel_prereq = fas.send_request(
            "/group/list",
            req_params={"search": agreement["group_prerequisite"]},
            auth=True,
            timeout=240,
        )["groups"][0]["id"]

        agreement_required = find_requirements(groups, toplevel_prereq)

        for dep_name in progressbar.progressbar(
            agreement_required, redirect_stdout=True
        ):
            result = ipa._request(
                "fasagreement_add_group", agreement["name"], {"group": dep_name}
            )
            if result["completed"]:
                print_status(
                    Status.ADDED, f"Added {dep_name} to the {agreement['name']}"
                )
            else:
                error_msg = result["failed"]["member"]["group"][0][1]
                if error_msg == "This entry is already a member":
                    print_status(
                        Status.SKIPPED,
                        f"{dep_name} already requires {agreement['name']}",
                    )
                elif error_msg == "no such entry":
                    print_status(Status.FAILED, f"No group named {dep_name}")
                else:
                    print(result["failed"])
