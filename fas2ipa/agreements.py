import click
import python_freeipa
import progressbar

from .status import Status, print_status
from .utils import ObjectManager


def find_requirements(groups, prereq_id):
    dependent_groups = []
    for group in groups:
        if group["prerequisite_id"] == prereq_id:
            dependent_groups.append(group["name"])
            subdeps = find_requirements(groups, group["id"])
            dependent_groups.extend(subdeps)
    return dependent_groups


class Agreements(ObjectManager):
    def push_to_ipa(self):
        click.echo("Creating Agreements")
        for agreement in self.config.get("agreement"):
            with open(agreement["description_file"], "r") as f:
                agreement_description = f.read()
            try:
                self.ipa._request(
                    "fasagreement_add",
                    agreement["name"],
                    {"description": agreement_description},
                )
            except python_freeipa.exceptions.DuplicateEntry as e:
                print_status(Status.SKIPPED, str(e))

    def record_user_signatures(self, agreements_to_usernames):
        if self.config["skip_user_signature"]:
            return

        for agreement in self.config.get("agreement"):
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

    def record_group_requirements(self, groups):
        for agreement in self.config.get("agreement"):

            for group in groups:
                if group["name"] == agreement["group_prerequisite"]:
                    toplevel_prereq = group["id"]
                    break
            else:
                raise RuntimeError(
                    f"Toplevel prerequisite {agreement['group_prerequisite']} for"
                    f" agreement {agreement['name']!r} not found."
                )

            agreement_required = find_requirements(groups, toplevel_prereq)

            for dep_name in progressbar.progressbar(
                agreement_required, redirect_stdout=True
            ):
                result = self.ipa._request(
                    "fasagreement_add_group",
                    agreement["name"],
                    {"group": self.config["groups"]["prefix"] + dep_name},
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
