import random

import click


# def chunks(data, n):
#     return [data[x : x + n] for x in range(0, len(data), n)]


def re_auth(config, instances):
    click.echo("Re-authenticating")
    for ipa in instances:
        ipa.logout()
        ipa.login(config["ipa"]["username"], config["ipa"]["password"])


class ObjectManager:
    def __init__(self, config, ipa_instances, fas):
        self.config = config
        self.ipa_instances = ipa_instances
        self.fas = fas

    @property
    def ipa(self):
        return random.choice(self.ipa_instances)

    def check_reauth(self, counter):
        if counter % self.config["ipa"]["reauth_every"] == 0:
            re_auth(self.config, self.ipa_instances)

    def chunks(self, items):
        size = self.config["chunks"]
        return [items[x : x + size] for x in range(0, len(items), size)]
