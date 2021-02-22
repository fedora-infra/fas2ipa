from typing import Any, Dict, List, Sequence

import click
import progressbar
import python_freeipa

from .status import Status, print_status
from .utils import ObjectManager


def find_requirements(groups: Sequence[dict], prereq_id: int) -> List[str]:
    dependent_groups = []
    for group in groups:
        if group["prerequisite_id"] == prereq_id:
            dependent_groups.append(group["name"])
            subdeps = find_requirements(groups, group["id"])
            dependent_groups.extend(subdeps)
    return dependent_groups


class Agreements(ObjectManager):
    def _create_agreement(self, name, description, group_name):
        # Create agreement
        try:
            self.ipa._request(
                "fasagreement_add", name, {"description": description},
            )
        except python_freeipa.exceptions.DuplicateEntry as e:
            print_status(Status.SKIPPED, str(e))
        # Create the corresponding group
        try:
            self.ipa.group_add(group_name, description=f"Signers of the {name}")
        except python_freeipa.exceptions.DuplicateEntry:
            pass
        # Add the automember rule
        try:
            self.ipa._request(
                "automember_add", group_name, {"type": "group"},
            )
        except python_freeipa.exceptions.DuplicateEntry:
            pass
        else:
            self.ipa._request(
                "automember_add_condition",
                group_name,
                {
                    "type": "group",
                    "key": "memberof",
                    "automemberinclusiveregex": f"^cn={name},cn=fasagreements,",
                },
            )

    def push_to_ipa(self):
        click.echo("Creating Agreements")
        for fas_name, fas_config in self.config["fas"].items():
            for agreement in fas_config.get("agreement", ()):
                with open(agreement["description_file"], "r") as f:
                    agreement_description = f.read()
                group_name = agreement["signer_group"]
                self._create_agreement(
                    agreement["name"], agreement_description, group_name
                )

    def record_user_signatures(self, agreements_to_usernames: Dict[str, List[str]]):
        if self.config["skip_user_signature"]:
            return

        for fas_name, fas_conf in self.config["fas"].items():
            for agreement in fas_conf.get("agreement", ()):
                click.echo(f"Recording signers of the {agreement['name']} agreement")
                signers = agreements_to_usernames.get(agreement["name"], [])
                if not signers:
                    click.echo("Nothing to do.")
                    continue
                counter = 0
                with progressbar.ProgressBar(
                    max_value=len(signers), redirect_stdout=True
                ) as bar:
                    for chunk in self.chunks(signers):
                        counter += len(chunk)
                        self.check_reauth(counter)
                        response = self.ipa._request(
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

    def record_group_requirements(self, groups: Dict[str, List[Dict[str, Any]]]):
        for fas_name, fas_conf in self.config["fas"].items():
            for agreement in fas_conf.get("agreement", ()):
                for group in groups[fas_name]:
                    if group["name"] == agreement["group_prerequisite"]:
                        toplevel_prereq = group["id"]
                        break
                else:
                    raise RuntimeError(
                        f"Toplevel prerequisite {agreement['group_prerequisite']} for"
                        f" agreement {agreement['name']!r} not found."
                    )

                agreement_required = find_requirements(
                    groups[fas_name], toplevel_prereq
                )

                for dep_name in progressbar.progressbar(
                    agreement_required, redirect_stdout=True
                ):
                    result = self.ipa._request(
                        "fasagreement_add_group",
                        agreement["name"],
                        {"group": fas_conf["groups"].get("prefix", "") + dep_name},
                    )
                    if result["completed"]:
                        print_status(
                            Status.ADDED,
                            f"Marking {dep_name} as requiring the {agreement['name']}",
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
