import os
import tempfile
from subprocess import run


MEMBEROF_DISABLE_LDIF = """
dn: cn=MemberOf Plugin,cn=plugins,cn=config
changetype: modify
replace: nsslapd-pluginEnabled
nsslapd-pluginEnabled: off
"""
MEMBEROF_DISABLE = """
dn: cn=MemberOf Plugin,cn=plugins,cn=config
only: nsslapd-pluginEnabled: off
"""

MEMBEROF_ENABLE_LDIF = """
dn: cn=MemberOf Plugin,cn=plugins,cn=config
changetype: modify
replace: nsslapd-pluginEnabled
nsslapd-pluginEnabled: on
"""
MEMBEROF_ENABLE = """
dn: cn=MemberOf Plugin,cn=plugins,cn=config
only: nsslapd-pluginEnabled: on
"""

MEMBEROF_TASK_LDIF = """
dn: cn=fas2ipa $TIME, cn=memberof task, cn=tasks, cn=config
changetype: add
objectClass: top
objectClass: extensibleObject
cn: FAS2IPA
basedn: $SUFFIX
filter: (objectclass=*)
ttl: 3600
"""
MEMBEROF_TASK = """
dn: cn=fas2ipa $TIME, cn=memberof task, cn=tasks, cn=config
default: objectClass: top
default: objectClass: extensibleObject
default: cn: FAS2IPA
default: basedn: $SUFFIX
default: filter: (objectclass=*)
default: ttl: 3600
"""

DS_SERVICE = "dirsrv@FEDORAPROJECT-ORG.service"


def disable_memberof_plugin():
    with tempfile.TemporaryDirectory() as tempdir:
        ldif = os.path.join(tempdir, "update.ldif")
        with open(ldif, "w") as f:
            f.write(MEMBEROF_DISABLE)
        print("Disabling MemberOf")
        run(["ipa-ldap-updater", ldif], check=True, universal_newlines=True)
        print("Restarting DS")
        run(["systemctl", "restart", "dirsrv.target"], check=True)


def enable_memberof_plugin():
    with tempfile.TemporaryDirectory() as tempdir:
        ldif = os.path.join(tempdir, "update.ldif")
        with open(ldif, "w") as f:
            f.write(MEMBEROF_ENABLE)
        print("Enabling MemberOf")
        run(["ipa-ldap-updater", ldif], check=True, universal_newlines=True)
        print("Restarting DS")
        run(["systemctl", "restart", "dirsrv.target"], check=True)
        with open(ldif, "w") as f:
            f.write(MEMBEROF_TASK)
        print("Updating MemberOf attributes")
        run(["ipa-ldap-updater", ldif], check=True, universal_newlines=True)
